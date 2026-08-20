import json
import tempfile
import unittest
from pathlib import Path
from ticket_agent.conversation import ConversationInbox
from ticket_agent.domain import OrderReader
from ticket_agent.providers import Replay
from ticket_agent.runtime import run_ticket

BINDING = {"tenant-demo:chat-demo": {"brand":"demo-brand","store":"store-001","operators":["backend-oncall"]}}


def event(n, text, sender="user-demo", stamp=None):
    return {"schema":"2.0", "header":{"event_id":f"e{n}","app_id":"app-demo","tenant_key":"tenant-demo","event_type":"im.message.receive_v1"},
            "event":{"sender":{"sender_type":"user","sender_id":{"open_id":sender}},
            "message":{"message_id":f"m{n}","root_id":"root-demo","parent_id":"root-demo","chat_id":"chat-demo", "message_type":"text",
                       "create_time":str(stamp if stamp is not None else n),"content":json.dumps({"text":text},ensure_ascii=False)}}}


def investigate(text, scope):
    return run_ticket(text, scope, Replay(), OrderReader())


class ConversationTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(); self.path=Path(self.temp.name)/'state.sqlite3'
        self.inbox=ConversationInbox(self.path,BINDING)

    def tearDown(self):
        self.temp.cleanup()

    def add(self,n,text,**kwargs):
        return self.inbox.accept(event(n,text,**kwargs),BINDING,"app-demo")

    def topic(self):
        with self.inbox.connect() as db:
            return dict(db.execute('SELECT * FROM topics').fetchone())

    def drain(self):
        sent=[]
        def sender(root,body,uid):
            sent.append(body); return "local-reply"
        while self.inbox.send_one(sender):pass
        return sent

    def test_ask_once_persist_wait_and_resume_with_reference(self):
        self.add(1,"又没出来，帮忙看看")
        self.inbox.investigate_one(investigate)
        self.assertEqual(len(self.drain()),1)
        self.inbox=ConversationInbox(self.path,BINDING)
        self.inbox.recover_single_worker()
        self.assertFalse(self.inbox.investigate_one(investigate))
        self.assertEqual(self.topic()['status'],'waiting_input')
        self.add(2,'订单 P1001')
        self.inbox.investigate_one(investigate)
        self.assertIn('O1001:payment',self.drain()[0])

    def test_correction_during_query_discards_late_result(self):
        self.add(1,'没出单 P1001')
        def slow(text,scope):
            self.add(2,'/订单 P1002')
            return investigate(text,scope)
        self.inbox.investigate_one(slow)
        self.assertEqual(self.drain(),[])
        with self.inbox.connect() as db:
            self.assertEqual(db.execute('SELECT disposition FROM topic_runs').fetchone()[0],'stale')
        self.inbox.investigate_one(investigate)
        self.assertIn('O1002:payment',self.drain()[0])

    def test_new_message_before_send_invalidates_queued_reply(self):
        self.add(1,'没出单 P1001');self.inbox.investigate_one(investigate)
        self.add(2,'/清除订单')
        self.assertEqual(self.drain(),[])
        self.assertIsNone(self.topic()['reference'])

    def test_operator_claim_pauses_and_only_owner_can_resume(self):
        self.add(1,'P1001 没出单');self.add(2,'/接手',sender='backend-oncall')
        self.assertFalse(self.inbox.investigate_one(investigate))
        self.add(3,'/继续',sender='user-demo')
        self.assertFalse(self.inbox.investigate_one(investigate))
        self.add(4,'/继续',sender='backend-oncall')
        self.assertTrue(self.inbox.investigate_one(investigate))

    def test_untrusted_sender_cannot_claim_staff_role(self):
        self.add(1,'P1001');self.add(2,'/接手')
        self.assertIsNone(self.topic()['owner'])

    def test_late_old_message_does_not_restore_wrong_order(self):
        self.add(2,'/订单 P1002',stamp=20);self.add(1,'P1001',stamp=10)
        self.assertEqual(self.topic()['reference'],'P1002')
        with self.inbox.connect() as db:
            self.assertEqual(db.execute("SELECT status FROM inbox WHERE event_id='e1'").fetchone()[0],'late_needs_review')

    def test_conflicting_reference_requires_explicit_confirmation(self):
        self.add(1,'P1001');self.add(2,'也可能是 P1002')
        self.assertEqual(self.topic()['status'],'waiting_confirmation')
        self.add(3,'阿杰说 P1002')
        self.assertIsNone(self.topic()['reference'])
        self.add(4,'/订单 P1002')
        self.assertEqual(self.topic()['reference'],'P1002')

    def test_duplicate_event_does_not_increment_version(self):
        self.add(1,'P1001');v=self.topic()['version'];self.add(1,'P1001')
        self.assertEqual(self.topic()['version'],v)

    def test_natural_correction_stops_old_query_without_guessing_new_id(self):
        self.add(1,'P1001');self.add(2,'等下，不是这个单，这个已经取走了')
        self.assertIsNone(self.topic()['reference'])
        self.assertEqual(self.topic()['status'],'waiting_confirmation')

    def test_unrelated_problem_not_forced_to_be_an_order(self):
        self.add(1,'后台登录一直转圈')
        self.inbox.investigate_one(lambda *a:self.fail('no order tool should run'))
        self.assertIn('正在操作的功能',self.drain()[0])

    def test_unapplied_intake_is_recovered_before_sending(self):
        self.add(1,'P1001');self.inbox.investigate_one(investigate)
        from ticket_agent.inbox import Inbox
        Inbox.accept(self.inbox,event(2,'/订单 P1002'),BINDING,'app-demo')
        self.assertEqual(self.drain(),[])

    def test_unsent_question_is_recreated_after_new_message(self):
        self.add(1,'没有单');self.inbox.investigate_one(investigate)
        self.add(2,'我找一下编号');self.inbox.investigate_one(investigate)
        self.assertEqual(len(self.drain()),1)

    def test_unsupported_command_during_query_does_not_leave_topic_stuck(self):
        self.add(1,'P1001')
        def during(text,scope):
            self.add(2,'/接手',sender='not-an-operator')
            return investigate(text,scope)
        self.inbox.investigate_one(during)
        self.assertEqual(self.topic()['status'],'ready')
        self.assertEqual(self.drain(),[])
        self.inbox.investigate_one(investigate)
        self.assertEqual(len(self.drain()),1)
