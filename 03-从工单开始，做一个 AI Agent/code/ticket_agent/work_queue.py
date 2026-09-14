"""Durable bounded queue, conservative expired leases, and candidate incident grouping."""
import argparse
from contextlib import contextmanager
import hashlib
import json
import math
from pathlib import Path
import sqlite3
import time
import uuid


class QueueError(ValueError):pass


def incident_key(job):
    keys=('tenant','brand','service','stage','error_code','deployment','campaign')
    # A missing discriminating field must not merge unrelated "slow" reports.
    if any(not job.get(k) or job[k]=='unknown' for k in keys):return None
    fields={k:job[k] for k in keys};fields['bucket']=int(job['created']//300)
    return hashlib.sha256(json.dumps(fields,sort_keys=True).encode()).hexdigest()[:20]


class WorkQueue:
    def __init__(self,path,capacity=100,global_slots=2,tenant_slots=1,lease_seconds=30):
        if not all(type(x)==int and x>0 for x in (capacity,global_slots,tenant_slots,lease_seconds)):raise QueueError('invalid_limits')
        self.path=Path(path);self.capacity=capacity;self.global_slots=global_slots;self.tenant_slots=tenant_slots;self.lease_seconds=lease_seconds
        with self.connect() as db:
            db.executescript("""
            CREATE TABLE IF NOT EXISTS jobs(id TEXT PRIMARY KEY,tenant TEXT,brand TEXT,store TEXT,topic TEXT,version INTEGER,created REAL,priority INTEGER,incident TEXT,payload TEXT,payload_hash TEXT,state TEXT,owner TEXT,generation INTEGER,lease_until REAL,started REAL,finished REAL,result TEXT,UNIQUE(tenant,topic,version));
            CREATE INDEX IF NOT EXISTS queue_ready ON jobs(state,created);
            CREATE TABLE IF NOT EXISTS queue_events(n INTEGER PRIMARY KEY AUTOINCREMENT,job_id TEXT,kind TEXT,at REAL,detail TEXT);
            """)

    @contextmanager
    def connect(self):
        db=sqlite3.connect(self.path,timeout=2);db.row_factory=sqlite3.Row
        try:
            with db:yield db
        finally:db.close()

    def _event(self,db,jid,kind,now,**detail):
        db.execute('INSERT INTO queue_events(job_id,kind,at,detail) VALUES(?,?,?,?)',(jid,kind,now,json.dumps(detail,ensure_ascii=False)))

    def enqueue(self,job):
        required={'tenant','brand','store','topic','version','created','priority','service','stage','error_code','deployment','campaign'}
        if not isinstance(job,dict) or set(job)!=required:raise QueueError('invalid_job')
        if type(job['version'])!=int or job['version']<1 or type(job['priority'])!=int or not 0<=job['priority']<=30:raise QueueError('invalid_priority_or_version')
        if not isinstance(job['created'],(int,float)) or not math.isfinite(job['created']):raise QueueError('invalid_time')
        if not all(isinstance(job[k],str) and 0<len(job[k])<=100 for k in ('tenant','brand','store','topic')):raise QueueError('invalid_identity')
        if not all(job[k] is None or (isinstance(job[k],str) and len(job[k])<=100) for k in ('service','stage','error_code','deployment','campaign')):raise QueueError('invalid_grouping_fields')
        raw=json.dumps(job,sort_keys=True,ensure_ascii=False);digest=hashlib.sha256(raw.encode()).hexdigest()
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            old=db.execute('SELECT id,payload_hash FROM jobs WHERE tenant=? AND topic=? AND version=?',(job['tenant'],job['topic'],job['version'])).fetchone()
            if old:
                if old['payload_hash']!=digest:raise QueueError('idempotency_conflict')
                return old['id']
            count=db.execute("SELECT count(*) FROM jobs WHERE state IN ('ready','running','uncertain')").fetchone()[0]
            replaceable=db.execute("SELECT count(*) FROM jobs WHERE tenant=? AND topic=? AND state='ready'",(job['tenant'],job['topic'])).fetchone()[0]
            if count-replaceable>=self.capacity:raise QueueError('queue_full')
            newest=db.execute('SELECT max(version) FROM jobs WHERE tenant=? AND topic=?',(job['tenant'],job['topic'])).fetchone()[0]
            if newest and job['version']<newest:raise QueueError('old_input_version')
            db.execute("UPDATE jobs SET state='superseded' WHERE tenant=? AND topic=? AND state='ready'",(job['tenant'],job['topic']))
            jid=uuid.uuid4().hex
            db.execute('INSERT INTO jobs VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',(jid,job['tenant'],job['brand'],job['store'],job['topic'],job['version'],job['created'],job['priority'],incident_key(job),raw,digest,'ready',None,0,None,None,None,None))
            self._event(db,jid,'enqueued',job['created'],version=job['version'])
            return jid

    def _expire(self,db,now):
        rows=db.execute("SELECT id,generation FROM jobs WHERE state='running' AND lease_until<=?",(now,)).fetchall()
        for row in rows:
            db.execute("UPDATE jobs SET state='uncertain' WHERE id=?",(row['id'],))
            self._event(db,row['id'],'lease_uncertain',now,generation=row['generation'])

    def claim(self,owner,now=None):
        now=time.time() if now is None else now
        if not isinstance(owner,str) or not owner:raise QueueError('owner_required')
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE');self._expire(db,now)
            active=db.execute("SELECT tenant,count(*) AS total FROM jobs WHERE state IN ('running','uncertain') GROUP BY tenant").fetchall()
            used={x['tenant']:x['total'] for x in active}
            if sum(used.values())>=self.global_slots:return None
            rows=db.execute("SELECT * FROM jobs WHERE state='ready' AND created<=? ORDER BY priority+CAST((?-created)/60 AS INTEGER) DESC,created,id",(now,now)).fetchall()
            row=next((x for x in rows if used.get(x['tenant'],0)<self.tenant_slots),None)
            if not row:return None
            generation=row['generation']+1
            db.execute("UPDATE jobs SET state='running',owner=?,generation=?,lease_until=?,started=? WHERE id=?",(owner,generation,now+self.lease_seconds,now,row['id']))
            self._event(db,row['id'],'claimed',now,generation=generation,owner=owner,queue_wait_ms=round((now-row['created'])*1000))
            return {**dict(row),'state':'running','owner':owner,'generation':generation,'lease_until':now+self.lease_seconds,'started':now}

    def complete(self,job_id,owner,generation,result,now=None):
        now=time.time() if now is None else now
        raw=json.dumps(result,ensure_ascii=False)
        if len(raw)>100000:raise QueueError('result_budget_exceeded')
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE');self._expire(db,now)
            row=db.execute('SELECT * FROM jobs WHERE id=?',(job_id,)).fetchone()
            if not row or row['state']!='running' or (row['owner'],row['generation'])!=(owner,generation):return 'stale_lease'
            if now<row['started']:raise QueueError('clock_before_start')
            version=db.execute('SELECT max(version) FROM jobs WHERE tenant=? AND topic=?',(row['tenant'],row['topic'])).fetchone()[0]
            state=('failed' if isinstance(result,dict) and result.get('status')=='worker_failed' else 'done') if version==row['version'] else 'stale_input'
            db.execute('UPDATE jobs SET state=?,finished=?,result=? WHERE id=?',(state,now,raw,job_id))
            usage=result.get('usage') if isinstance(result,dict) else None
            tokens=usage.get('prompt_tokens') if isinstance(usage,dict) else None
            if type(tokens)!=int or tokens<0:tokens=None
            self._event(db,job_id,state,now,generation=generation,run_ms=round((now-row['started'])*1000),prompt_tokens=tokens)
            return state

    def resolve_uncertain(self,job_id,generation,stopped,outcome='retry',now=None):
        now=time.time() if now is None else now
        if stopped is not True or outcome not in ('retry','fail'):raise QueueError('confirmed_stop_required')
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE');self._expire(db,now)
            row=db.execute('SELECT * FROM jobs WHERE id=?',(job_id,)).fetchone()
            if not row or row['state']!='uncertain' or row['generation']!=generation:raise QueueError('not_current_uncertain_attempt')
            newest=db.execute('SELECT max(version) FROM jobs WHERE tenant=? AND topic=?',(row['tenant'],row['topic'])).fetchone()[0]
            state='superseded' if newest!=row['version'] else 'ready' if outcome=='retry' and generation<3 else 'failed'
            db.execute('UPDATE jobs SET state=?,owner=NULL,lease_until=NULL WHERE id=?',(state,job_id))
            self._event(db,job_id,'uncertain_resolved',now,new_state=state,generation=generation)
            return state

    def execute_one(self,worker,owner='local-worker'):
        claimed=self.claim(owner)
        if not claimed:return None
        before=time.monotonic()
        try:
            result=worker(json.loads(claimed['payload']))
            if not isinstance(result,dict):raise QueueError('invalid_worker_result')
        except Exception:result={'status':'worker_failed','usage':None}
        result={**result,'queue_worker_elapsed_ms':round((time.monotonic()-before)*1000)}
        disposition=self.complete(claimed['id'],owner,claimed['generation'],result)
        return {'job_id':claimed['id'],'disposition':disposition,'result':result}

    def candidates(self,tenant,brand):
        with self.connect() as db:
            rows=db.execute('SELECT incident,topic,store,version FROM jobs j WHERE tenant=? AND brand=? AND incident IS NOT NULL AND version=(SELECT max(version) FROM jobs newer WHERE newer.tenant=j.tenant AND newer.topic=j.topic) ORDER BY created',(tenant,brand)).fetchall()
        groups={}
        for row in rows:groups.setdefault(row['incident'],[]).append(dict(row))
        return [{'candidate':key,'members':members,'confirmed_incident':False} for key,members in groups.items() if len({x['topic'] for x in members})>1]

    def snapshot(self):
        with self.connect() as db:
            return {'jobs':[dict(x) for x in db.execute('SELECT id,tenant,store,topic,version,state,generation,started,finished FROM jobs ORDER BY created,id')],
                'events':[{**dict(x),'detail':json.loads(x['detail'])} for x in db.execute('SELECT job_id,kind,at,detail FROM queue_events ORDER BY n')]}


def replay(path):
    queue=WorkQueue(path,tenant_slots=2)
    def job(topic,store,priority=0,stage='paid'):
        return {'tenant':'tenant-demo','brand':'demo-brand','store':store,'topic':topic,'version':1,'created':1000,'priority':priority,
                'service':'checkout','stage':stage,'error_code':'TIMEOUT','deployment':'release-demo','campaign':'campaign-a'}
    for item in [job('ordinary-a','store-001'),job('ordinary-b','store-002'),job('group-order','store-003',30),job('not-paid','store-004',stage='unpaid')]:queue.enqueue(item)
    order=[];clock=1005
    while True:
        claimed=queue.claim('local-worker',clock)
        if not claimed:break
        order.append(claimed['topic'])
        queue.complete(claimed['id'],'local-worker',claimed['generation'],{'status':'needs_human','usage':None},clock+2)
        clock+=3
    return {'mode':'logical_clock_queue_replay','clock_is_simulated':True,'dispatch_order':order,
            'candidates':queue.candidates('tenant-demo','demo-brand'),'state':queue.snapshot()}


def main():
    p=argparse.ArgumentParser(description='持久化队列与事故候选回放；不调用模型和飞书')
    p.add_argument('--db',required=True);a=p.parse_args()
    print(json.dumps(replay(a.db),ensure_ascii=False,indent=2))


if __name__=='__main__':main()
