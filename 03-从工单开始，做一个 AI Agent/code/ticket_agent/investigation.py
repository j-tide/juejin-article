"""Versioned investigation notes over the existing ConversationInbox database.

Mutations are explicit authorized operator actions. No natural-language fact extraction.
"""
import argparse
import copy
from datetime import datetime,timezone
import hashlib
import json
import re
from .conversation import ConversationInbox


class BoardError(ValueError):pass


class InvestigationBoard:
    def __init__(self,inbox,tenant,root,operators):
        self.inbox=inbox;self.key=(tenant,root);self.operators=set(operators)
        with inbox.connect() as db:
            db.executescript("""
            CREATE TABLE IF NOT EXISTS investigation_board(tenant TEXT,root_id TEXT,revision INTEGER,state TEXT,PRIMARY KEY(tenant,root_id));
            CREATE TABLE IF NOT EXISTS investigation_operations(tenant TEXT,root_id TEXT,operation_id TEXT,request_hash TEXT,result_revision INTEGER,body TEXT,PRIMARY KEY(tenant,root_id,operation_id));
            """)
            if not db.execute('SELECT 1 FROM topics WHERE tenant=? AND root_id=?',self.key).fetchone():raise BoardError('topic_not_found')
            db.execute('INSERT OR IGNORE INTO investigation_board VALUES(?,?,0,?)',(*self.key,json.dumps({'items':{},'basis_topic_version':0})))

    def _messages(self,db):
        result={}
        for row in db.execute('SELECT message_id,sender,payload FROM inbox WHERE tenant=? AND root_id=?',self.key):
            payload=json.loads(row['payload'])
            if payload['message_type']=='text':
                result[row['message_id']]={'text':payload['content']['text'],'sender':row['sender']}
        return result

    def _item(self,item,state,sources):
        required={'id','kind','issue','text','source_id','depends_on'}
        if not isinstance(item,dict) or set(item)!=required:raise BoardError('invalid_item')
        if not isinstance(item['id'],str) or not re.fullmatch(r'[a-z0-9_-]{1,40}',item['id']):raise BoardError('invalid_item_id')
        if item['id'] in state['items']:raise BoardError('duplicate_item')
        if item['kind'] not in ('reported','hypothesis','todo') or item['issue'] not in ('quantity','flavor'):raise BoardError('invalid_kind_or_issue')
        if not isinstance(item['source_id'],str) or item['source_id'] not in sources:raise BoardError('source_not_found')
        if not isinstance(item['text'],str) or not 1<=len(item['text'])<=300 or item['text'] not in sources[item['source_id']]['text']:raise BoardError('quote_mismatch')
        dependencies=item['depends_on']
        if not isinstance(dependencies,list) or len(dependencies)>8 or not all(isinstance(x,str) for x in dependencies):raise BoardError('invalid_dependencies')
        if any(x not in state['items'] or state['items'][x]['validity']!='active' for x in dependencies):raise BoardError('inactive_dependency')
        if len(state['items'])>=100:raise BoardError('item_budget_exceeded')
        state['items'][item['id']]={**copy.deepcopy(item),'validity':'active','task_status':'open' if item['kind']=='todo' else None,'owner':None,'result_source':None}

    def apply(self,operation_id,operation,actor,expected_revision,topic_version):
        if actor not in self.operators:raise BoardError('operator_required')
        if not isinstance(operation_id,str) or not re.fullmatch(r'[a-z0-9_-]{1,60}',operation_id):raise BoardError('invalid_operation_id')
        raw=json.dumps({'operation':operation,'actor':actor,'expected_revision':expected_revision,'topic_version':topic_version},ensure_ascii=False,sort_keys=True)
        digest=hashlib.sha256(raw.encode()).hexdigest()
        with self.inbox.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            old=db.execute('SELECT * FROM investigation_operations WHERE tenant=? AND root_id=? AND operation_id=?',(*self.key,operation_id)).fetchone()
            if old:
                if old['request_hash']!=digest:raise BoardError('operation_id_conflict')
                return old['result_revision']
            topic=db.execute('SELECT version FROM topics WHERE tenant=? AND root_id=?',self.key).fetchone()
            row=db.execute('SELECT revision,state FROM investigation_board WHERE tenant=? AND root_id=?',self.key).fetchone()
            if topic['version']!=topic_version:raise BoardError('stale_topic_input')
            if row['revision']!=expected_revision:raise BoardError('stale_board_revision')
            state=json.loads(row['state']);sources=self._messages(db)
            action=operation.get('action') if isinstance(operation,dict) else None
            if action=='add' and set(operation)=={'action','item'}:
                self._item(operation['item'],state,sources)
            elif action=='correct' and set(operation)=={'action','target','item'}:
                target=state['items'].get(operation['target'])
                if not target or target['validity']!='active' or target['kind']!='reported':raise BoardError('invalid_correction')
                if operation['item'].get('kind')!='reported' or operation['item'].get('issue')!=target['issue']:raise BoardError('correction_issue_mismatch')
                invalid={operation['target']}
                while True:
                    more={key for key,value in state['items'].items() if set(value['depends_on']) & invalid}
                    if more<=invalid:break
                    invalid|=more
                for key in invalid:state['items'][key]['validity']='superseded' if key==operation['target'] else 'stale'
                self._item(operation['item'],state,sources)
            elif action in ('claim','complete') and set(operation)==({'action','target'} if action=='claim' else {'action','target','result_source'}):
                item=state['items'].get(operation['target'])
                if not item or item['kind']!='todo' or item['validity']!='active':raise BoardError('invalid_task')
                if action=='claim':
                    if item['task_status']!='open':raise BoardError('task_already_claimed')
                    item.update(task_status='claimed',owner=actor)
                else:
                    source=sources.get(operation['result_source'])
                    if item['task_status']!='claimed' or item['owner']!=actor or not source or source['sender']!=actor:raise BoardError('completion_not_authorized')
                    item.update(task_status='done',result_source=operation['result_source'])
            else:raise BoardError('invalid_operation')
            state['basis_topic_version']=topic_version
            revision=row['revision']+1
            db.execute('UPDATE investigation_board SET revision=?,state=? WHERE tenant=? AND root_id=?',(revision,json.dumps(state,ensure_ascii=False),*self.key))
            db.execute('INSERT INTO investigation_operations VALUES(?,?,?,?,?,?)',(*self.key,operation_id,digest,revision,raw))
            return revision

    def snapshot(self,budget=6000):
        with self.inbox.connect() as db:
            db.execute('BEGIN')
            topic=db.execute('SELECT version FROM topics WHERE tenant=? AND root_id=?',self.key).fetchone()
            row=db.execute('SELECT revision,state FROM investigation_board WHERE tenant=? AND root_id=?',self.key).fetchone()
        state=json.loads(row['state']);items=list(state['items'].values())
        result={'topic_version':topic['version'],'basis_topic_version':state['basis_topic_version'],'board_revision':row['revision'],
                'reported':[x for x in items if x['validity']=='active' and x['kind']=='reported'],
                'hypotheses':[x for x in items if x['validity']=='active' and x['kind']=='hypothesis'],
                'tasks':[x for x in items if x['validity']=='active' and x['kind']=='todo'],
                'invalidated':[{'id':x['id'],'validity':x['validity']} for x in items if x['validity']!='active']}
        raw=json.dumps(result,ensure_ascii=False)
        if len(raw)>budget:return {'status':'needs_selection','topic_version':topic['version'],'board_revision':row['revision'],'required_chars':len(raw)}
        return {'status':'ready' if state['basis_topic_version']==topic['version'] else 'needs_update',**result}

    def expand(self,source_id):
        with self.inbox.connect() as db:return self._messages(db).get(source_id)


BOARD_TOOL={'type':'function','function':{'name':'read_investigation','description':'读取绑定话题当前交接记录；客户报告、猜测与待办分别标记。',
    'parameters':{'type':'object','properties':{},'additionalProperties':False}}}


def board_handler(board):
    def read(args,scope):
        if args!={}:return {'status':'invalid_arguments','evidence':[]}
        with board.inbox.connect() as db:
            row=db.execute('SELECT brand,store FROM topics WHERE tenant=? AND root_id=?',board.key).fetchone()
        if not row or (row['brand'],row['store'])!=(scope.brand,scope.store):return {'status':'not_found','evidence':[]}
        snapshot=board.snapshot()
        if snapshot['status']!='ready':return {'status':snapshot['status'],'evidence':[]}
        evidence=[]
        labels={'reported':'人员报告，尚非系统核验事实','hypotheses':'未验证假设','tasks':'待办记录'}
        for group in ('reported','hypotheses','tasks'):
            for item in snapshot[group]:
                text=labels[group]+f' [{item["issue"]}] '+item['text']
                if group=='tasks':text+=f'；状态={item["task_status"]}；负责人={item["owner"]}'
                evidence.append({'id':f'board:{board.key[0]}:{board.key[1]}:{snapshot["board_revision"]}:{item["id"]}',
                    'source':'message://'+item['source_id'],'observed_at':datetime.now(timezone.utc).isoformat(),
                    'event_at':None,'quote':text})
        return {'status':'investigation_snapshot','evidence':evidence,'topic_version':snapshot['topic_version'],'board_revision':snapshot['board_revision']}
    return read


def message(number,text,sender='customer'):
    return {'schema':'2.0','header':{'event_id':f'group-event-{number}','app_id':'app-demo','tenant_key':'tenant-demo','event_type':'im.message.receive_v1'},
        'event':{'sender':{'sender_type':'user','sender_id':{'open_id':sender}},'message':{
            'message_id':f'group-message-{number}','root_id':'group-root','parent_id':'group-root','chat_id':'chat-demo','message_type':'text',
            'create_time':str(number),'content':json.dumps({'text':text},ensure_ascii=False)}}}


def replay(path):
    bindings={'tenant-demo:chat-demo':{'brand':'demo-brand','store':'store-001','operators':['csr','backend']}}
    inbox=ConversationInbox(path,bindings)
    texts=[('清单 v1 显示少两杯。','customer'),('可能是页面缓存，先核对数量。','csr'),('我来查修改记录。','backend'),
           ('更正：数量没少，两杯口味不对，按清单 v2 核对。','customer')]
    for i,(text,sender) in enumerate(texts,1):inbox.accept(message(i,text,sender),bindings,'app-demo')
    board=InvestigationBoard(inbox,'tenant-demo','group-root',['csr','backend'])
    def item(i,kind,issue,text,source,depends=None):return {'id':i,'kind':kind,'issue':issue,'text':text,'source_id':f'group-message-{source}','depends_on':depends or []}
    operations=[{'action':'add','item':item('count-v1','reported','quantity',texts[0][0],1)},
        {'action':'add','item':item('cache','hypothesis','quantity',texts[1][0],2,['count-v1'])},
        {'action':'add','item':item('changes','todo','flavor',texts[2][0],3)},
        {'action':'claim','target':'changes'},
        {'action':'correct','target':'count-v1','item':item('count-v2','reported','quantity',texts[3][0],4)},
        {'action':'add','item':item('flavor','reported','flavor','两杯口味不对',4)}]
    for index,operation in enumerate(operations):
        board.apply(f'group-op-{index}',operation,'backend' if index==3 else 'csr',index,4)
    return {'mode':'explicit_operator_replay','snapshot':board.snapshot(),'source_example':board.expand('group-message-4')}


def main():
    p=argparse.ArgumentParser(description='在现有话题数据库中回放团餐更正和交接记录')
    p.add_argument('--db',required=True);a=p.parse_args()
    print(json.dumps(replay(a.db),ensure_ascii=False,indent=2))


if __name__=='__main__':main()
