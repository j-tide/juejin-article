import copy
import json
import tempfile
import unittest
from pathlib import Path
from ticket_agent.conversation import ConversationInbox
from ticket_agent.investigation import InvestigationBoard,BoardError,message,replay,board_handler

BINDINGS={'tenant-demo:chat-demo':{'brand':'demo-brand','store':'store-001','operators':['csr','backend']}}


class InvestigationTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.path=Path(self.temp.name)/'conversation.sqlite3'
        self.inbox=ConversationInbox(self.path,BINDINGS)
        self.inbox.accept(message(1,'数量少两杯。'),BINDINGS,'app-demo')
        self.board=InvestigationBoard(self.inbox,'tenant-demo','group-root',['csr','backend'])
    def tearDown(self):self.temp.cleanup()
    def item(self,id='count',kind='reported',text='数量少两杯。',source='group-message-1',depends=None):
        return {'id':id,'kind':kind,'issue':'quantity','text':text,'source_id':source,'depends_on':depends or []}
    def add(self,item=None,revision=0,topic=1,op='op1'):
        return self.board.apply(op,{'action':'add','item':item or self.item()},'csr',revision,topic)

    def test_authorization_and_unknown_topic(self):
        with self.assertRaisesRegex(BoardError,'operator_required'):self.board.apply('a',{},'customer',0,1)
        with self.assertRaisesRegex(BoardError,'topic_not_found'):InvestigationBoard(self.inbox,'other-tenant','group-root',['csr'])

    def test_quote_and_source_validation(self):
        for item in [self.item(text='已全部送达'),self.item(source='another-topic-message')]:
            with self.assertRaises(BoardError):self.add(item)
        self.assertEqual(self.board.snapshot()['board_revision'],0)

    def test_idempotent_operation_and_collision(self):
        self.assertEqual(self.add(),1);self.assertEqual(self.add(),1)
        with self.assertRaisesRegex(BoardError,'operation_id_conflict'):self.add(self.item(id='other'))

    def test_compare_and_swap_protects_other_operator(self):
        self.add()
        with self.assertRaisesRegex(BoardError,'stale_board_revision'):self.add(self.item(id='other'),op='op2')

    def test_new_topic_input_blocks_stale_mutation(self):
        self.inbox.accept(message(2,'补充一张新清单'),BINDINGS,'app-demo')
        with self.assertRaisesRegex(BoardError,'stale_topic_input'):self.add()
        self.assertEqual(self.board.snapshot()['status'],'needs_update')

    def test_correction_invalidates_transitive_dependents(self):
        self.add()
        self.add(self.item(id='guess',kind='hypothesis',depends=['count']),revision=1,op='op2')
        self.add(self.item(id='task',kind='todo',depends=['guess']),revision=2,op='op3')
        self.inbox.accept(message(2,'更正，数量没有少。'),BINDINGS,'app-demo')
        self.board.apply('fix',{'action':'correct','target':'count','item':self.item(id='new',text='数量没有少。',source='group-message-2')},'csr',3,2)
        snapshot=self.board.snapshot()
        self.assertEqual(snapshot['reported'][0]['id'],'new');self.assertEqual(snapshot['tasks'],[])
        self.assertEqual({x['id']:x['validity'] for x in snapshot['invalidated']},{'count':'superseded','guess':'stale','task':'stale'})

    def test_invalid_correction_rolls_back(self):
        self.add()
        with self.assertRaises(BoardError):self.board.apply('bad',{'action':'correct','target':'count','item':self.item(id='new',text='凭空修复')},'csr',1,1)
        self.assertEqual(self.board.snapshot()['reported'][0]['id'],'count')

    def test_hypothesis_not_promoted_to_fact(self):
        self.add(self.item(kind='hypothesis'))
        snapshot=self.board.snapshot();self.assertEqual(snapshot['reported'],[]);self.assertEqual(len(snapshot['hypotheses']),1)

    def test_claim_is_not_completion(self):
        self.add(self.item(kind='todo'))
        self.board.apply('claim',{'action':'claim','target':'count'},'backend',1,1)
        self.assertEqual(self.board.snapshot()['tasks'][0]['task_status'],'claimed')
        with self.assertRaisesRegex(BoardError,'task_already_claimed'):
            self.board.apply('claim2',{'action':'claim','target':'count'},'csr',2,1)

    def test_only_owner_can_complete_with_own_source(self):
        self.add(self.item(kind='todo'))
        self.board.apply('claim',{'action':'claim','target':'count'},'backend',1,1)
        self.inbox.accept(message(2,'已核对，结果见当前记录。','backend'),BINDINGS,'app-demo')
        operation={'action':'complete','target':'count','result_source':'group-message-2'}
        with self.assertRaises(BoardError):self.board.apply('done',operation,'csr',2,2)
        self.board.apply('done',operation,'backend',2,2)
        self.assertEqual(self.board.snapshot()['tasks'][0]['task_status'],'done')

    def test_restart_preserves_audit_and_state(self):
        self.add();other=InvestigationBoard(ConversationInbox(self.path,BINDINGS),'tenant-demo','group-root',['csr','backend'])
        self.assertEqual(other.snapshot(),self.board.snapshot())
        with self.inbox.connect() as db:self.assertEqual(db.execute('SELECT count(*) FROM investigation_operations').fetchone()[0],1)

    def test_budget_is_explicit_not_silent_omission(self):
        self.add();result=self.board.snapshot(budget=10)
        self.assertEqual(result['status'],'needs_selection');self.assertNotIn('reported',result)

    def test_dependency_must_be_current(self):
        with self.assertRaisesRegex(BoardError,'inactive_dependency'):self.add(self.item(depends=['missing']))

    def test_replay_splits_quantity_and_flavor(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'state.sqlite3';result=replay(path);again=replay(path)
            self.assertEqual(result,again)
            s=result['snapshot'];self.assertEqual(s['status'],'ready');self.assertEqual(s['board_revision'],6)
            self.assertEqual({x['issue'] for x in s['reported']},{'quantity','flavor'})
            self.assertEqual(s['tasks'][0]['owner'],'backend');self.assertEqual(s['tasks'][0]['task_status'],'claimed')

    def test_read_tool_keeps_report_unverified(self):
        from ticket_agent.domain import Scope
        self.add();result=board_handler(self.board)({},Scope('demo-brand','store-001'))
        self.assertEqual(result['status'],'investigation_snapshot')
        self.assertIn('尚非系统核验事实',result['evidence'][0]['quote'])

    def test_read_tool_rejects_wrong_scope(self):
        from ticket_agent.domain import Scope
        self.add();result=board_handler(self.board)({},Scope('demo-brand','store-002'))
        self.assertEqual(result['status'],'not_found');self.assertEqual(result['evidence'],[])

    def test_read_tool_cannot_reuse_stale_board(self):
        from ticket_agent.domain import Scope
        self.add();self.inbox.accept(message(2,'更新信息'),BINDINGS,'app-demo')
        result=board_handler(self.board)({},Scope('demo-brand','store-001'))
        self.assertEqual(result['status'],'needs_update');self.assertEqual(result['evidence'],[])
