"""Scoped read-only SQL templates over synthetic order and event records."""
import argparse
from contextlib import contextmanager
from datetime import datetime,timezone
import json
from pathlib import Path
import re
import sqlite3
import time
from .domain import Scope


def epoch(value):
    t=datetime.fromisoformat(value)
    if t.tzinfo is None:raise ValueError('timezone required')
    return t.timestamp()


def iso(value):return datetime.fromtimestamp(value,timezone.utc).isoformat()


def initialize(path,fixture=None):
    path=Path(path)
    if path.exists():raise ValueError('database already exists')
    data=json.loads(Path(fixture or Path(__file__).resolve().parents[1]/'fixtures/database.json').read_text())
    db=sqlite3.connect(path)
    try:
        db.executescript("""
        CREATE TABLE orders(brand TEXT,store TEXT,order_id TEXT,payment_id TEXT,payment TEXT,dispatch TEXT,updated REAL,customer_phone TEXT,PRIMARY KEY(brand,store,order_id));
        CREATE INDEX payment_scope ON orders(brand,store,payment_id);
        CREATE TABLE events(brand TEXT,store TEXT,order_id TEXT,event_id TEXT,kind TEXT,trace_id TEXT,attempt INTEGER,happened REAL,ingested REAL,PRIMARY KEY(brand,store,event_id));
        CREATE INDEX event_scope ON events(brand,store,order_id,happened);
        CREATE TABLE watermarks(brand TEXT,store TEXT,through REAL,PRIMARY KEY(brand,store));
        """)
        for x in data['orders']:db.execute('INSERT INTO orders VALUES(?,?,?,?,?,?,?,?)',(x['brand'],x['store'],x['order_id'],x['payment_id'],x['payment'],x['dispatch'],epoch(x['updated_at']),x['customer_phone']))
        for x in data['events']:db.execute('INSERT INTO events VALUES(?,?,?,?,?,?,?,?,?)',(x['brand'],x['store'],x['order_id'],x['event_id'],x['kind'],x['trace_id'],x['attempt'],epoch(x['event_at']),epoch(x['ingested_at'])))
        for x in data['watermarks']:db.execute('INSERT INTO watermarks VALUES(?,?,?)',(x['brand'],x['store'],epoch(x['through'])))
        db.commit()
    finally:db.close()


COLUMNS={'orders':{'brand','store','order_id','payment_id','payment','dispatch','updated'},
         'events':{'brand','store','order_id','event_id','kind','trace_id','attempt','happened','ingested'},
         'watermarks':{'brand','store','through'}}


def authorize(action,table,column,database,trigger):
    if action==sqlite3.SQLITE_SELECT:return sqlite3.SQLITE_OK
    if action==sqlite3.SQLITE_READ and database=='main' and column in COLUMNS.get(table,set()):return sqlite3.SQLITE_OK
    return sqlite3.SQLITE_DENY


@contextmanager
def readonly(path,seconds=0.2):
    db=sqlite3.connect(Path(path).resolve().as_uri()+'?mode=ro',uri=True,timeout=0.1)
    try:
        db.row_factory=sqlite3.Row
        db.execute('PRAGMA query_only=ON');db.execute('BEGIN')
        db.set_authorizer(authorize)
        deadline=time.monotonic()+seconds
        db.set_progress_handler(lambda: int(time.monotonic()>=deadline),100)
        yield db
    finally:db.close()


class SqlOrderReader:
    def __init__(self,path,start,end,as_of,row_limit=10,query_seconds=0.2):
        self.path=path;self.start=epoch(start);self.end=epoch(end);self.as_of=epoch(as_of)
        if not 0<self.end-self.start<=1200 or self.end>self.as_of:raise ValueError('invalid query window')
        if type(row_limit)!=int or not 1<=row_limit<=10:raise ValueError('invalid row budget')
        if not 0<query_seconds<=1:raise ValueError('invalid query deadline')
        self.row_limit=row_limit;self.query_seconds=query_seconds

    def lookup(self,args,scope):
        if (not isinstance(args,dict) or set(args)!={'reference'} or not isinstance(args['reference'],str)
                or not re.fullmatch(r'[OP][0-9]{4,12}',args['reference'])):return {'status':'invalid_arguments','evidence':[]}
        try:
            with readonly(self.path,self.query_seconds) as db:
                row=db.execute('SELECT order_id,payment,dispatch,updated FROM orders WHERE brand=? AND store=? AND (order_id=? OR payment_id=?) AND updated<=? LIMIT 2',
                    (scope.brand,scope.store,args['reference'],args['reference'],self.as_of)).fetchall()
                if len(row)!=1:return {'status':'not_observed' if not row else 'ambiguous_reference','evidence':[],'reference':args['reference']}
                row=row[0];oid=row['order_id']
                logs=db.execute('SELECT event_id,kind,trace_id,attempt,happened,ingested FROM events WHERE brand=? AND store=? AND order_id=? AND happened>=? AND happened<? AND ingested<=? ORDER BY happened,event_id LIMIT ?',
                    (scope.brand,scope.store,oid,self.start,self.end,self.as_of,self.row_limit+1)).fetchall()
                water=db.execute('SELECT through FROM watermarks WHERE brand=? AND store=?',(scope.brand,scope.store)).fetchone()
            observed=datetime.now(timezone.utc).isoformat();evidence=[]
            def add(eid,quote,event_at=None,**extra):
                evidence.append({'id':eid,'source':'sql-fixture://'+eid,'observed_at':observed,'event_at':event_at,'quote':quote,**extra})
            add(oid+':snapshot',f'订单快照：支付={row["payment"]}，出单任务={row["dispatch"]}；快照不代表完整历史',iso(row['updated']))
            for x in logs[:self.row_limit]:
                labels={'PAYMENT_ACCEPTED':'支付记录已接受','ORDER_CREATED':'订单已创建','DISPATCH_SENT':'任务已发送，不证明门店已打印','DEVICE_OFFLINE':'记录设备离线','PRINT_ACK':'设备返回打印确认，不证明饮品已制作或交付'}
                add(oid+':event:'+x['event_id'],f'{iso(x["happened"])} {labels.get(x["kind"],"未知事件类型")}；尝试 {x["attempt"]}',
                    iso(x['happened']),trace_id=x['trace_id'],kind=x['kind'],ingested_at=iso(x['ingested']))
            receipt=any(x['kind']=='PRINT_ACK' for x in logs[:self.row_limit])
            add(oid+':receipt_observation','本次查询取得设备打印确认；制作和交付仍需单独证据' if receipt else '本次查询未取得设备打印回执；受窗口、入库延迟与行数限制，不能确认现场是否打印')
            truncated=len(logs)>self.row_limit
            through=water['through'] if water else None
            add(oid+':coverage',f'查询范围 [{iso(self.start)}, {iso(self.end)})；采集可见截至 {iso(self.as_of)}；'
                f'副本水位={iso(through) if through is not None else "未知"}；结果截断={truncated}；未证明日志完整，未查到不能证明没有发生',
                watermark=iso(through) if through is not None else None)
            return {'status':'observed_partial','reference':args['reference'],'order_id':oid,'evidence':evidence,
                    'truncated':truncated,'replica_lag_possible':through is None or through<self.end,
                    'print_receipt_observed':any(x['kind']=='PRINT_ACK' for x in logs[:self.row_limit])}
        except sqlite3.DatabaseError:
            return {'status':'query_unavailable','reference':args['reference'],'evidence':[]}


def main():
    from .providers import Replay
    from .runtime import run_ticket
    p=argparse.ArgumentParser(description='合成订单和日志的只读 SQL 取证')
    p.add_argument('--db',required=True);p.add_argument('--init-demo',action='store_true')
    p.add_argument('--reference',default='P1001');p.add_argument('--store',default='store-001')
    p.add_argument('--as-of',default='2026-09-18T14:00:30+08:00')
    a=p.parse_args()
    if a.init_demo:initialize(a.db)
    reader=SqlOrderReader(a.db,'2026-09-18T14:00:00+08:00','2026-09-18T14:00:15+08:00',a.as_of)
    result=run_ticket('核对支付与出单 '+a.reference,Scope('demo-brand',a.store),Replay(),reader)
    print(json.dumps(result,ensure_ascii=False,indent=2))


if __name__=='__main__':main()
