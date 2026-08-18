import copy
import json
import tempfile
import unittest
from pathlib import Path
from ticket_agent.inbox import Inbox
from ticket_agent.domain import OrderReader
from ticket_agent.providers import Replay
from ticket_agent.runtime import run_ticket

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"
BINDINGS = json.loads((FIXTURES / "bindings.example.json").read_text())


class InboxTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "runtime.sqlite3"
        self.inbox = Inbox(self.path)
        self.event = json.loads((FIXTURES / "feishu-events.json").read_text())[0]

    def tearDown(self):
        self.temp.cleanup()

    def accept(self, event=None):
        return self.inbox.accept(event or self.event, BINDINGS, "app-demo")

    def process(self):
        return self.inbox.investigate_one(lambda text, scope: run_ticket(text, scope, Replay(), OrderReader()))

    def test_event_and_message_dedupe_survive_restart(self):
        self.assertEqual(self.accept(), "accepted")
        self.inbox = Inbox(self.path)
        self.assertEqual(self.accept(), "duplicate")
        self.event["header"]["event_id"] = "redelivered-with-different-event"
        self.assertEqual(self.accept(), "duplicate")
        self.assertTrue(self.process())
        self.assertFalse(self.process())

    def test_unbound_chat_and_other_application_are_rejected(self):
        self.event["event"]["message"]["chat_id"] = "other-chat"
        self.assertEqual(self.accept(), "unbound_chat")
        self.event["header"]["app_id"] = "other-app"
        self.assertEqual(self.accept(), "ignored")

    def test_bot_does_not_trigger_itself(self):
        self.event["event"]["sender"]["sender_type"] = "app"
        self.assertEqual(self.accept(), "ignored")

    def test_thread_reply_targets_root_not_last_child(self):
        self.event["event"]["message"].update(root_id="root-1", parent_id="child-1")
        self.accept(); self.process()
        sent = []
        def sender(root, body, uid):
            sent.append(root)
            return "reply-id"
        self.inbox.send_one(sender)
        self.assertEqual(sent, ["root-1"])

    def test_unknown_root_does_not_fall_back_to_chat_send(self):
        self.event["event"]["message"]["parent_id"] = "child-1"
        self.assertEqual(self.accept(), "missing_root")

    def test_media_is_stored_and_not_sent_to_text_model(self):
        self.event["event"]["message"].update(message_type="media", content='{"file_key":"synthetic-video"}')
        self.accept()
        self.inbox.investigate_one(lambda *args: self.fail("text model must not pretend to read video"))
        with self.inbox.connect() as db:
            out = db.execute("SELECT body,result FROM outbox").fetchone()
        self.assertEqual(json.loads(out["result"])["status"], "media_pending")
        self.assertIn("尚未解析", out["body"])

    def test_send_timeout_is_uncertain_not_automatically_retried(self):
        self.accept(); self.process()
        def timeout(*args):
            raise TimeoutError()
        self.assertTrue(self.inbox.send_one(timeout))
        self.assertFalse(self.inbox.send_one(lambda *args: self.fail("duplicate send")))
        self.assertEqual(self.inbox.summary()["outbox"], [{"status": "uncertain", "count": 1}])

    def test_crash_during_send_does_not_reset_to_pending(self):
        self.accept(); self.process()
        with self.inbox.connect() as db:
            db.execute("UPDATE outbox SET status='sending'")
        self.inbox.recover_single_worker()
        self.assertEqual(self.inbox.summary()["outbox"][0]["status"], "uncertain")

    def test_crash_during_read_can_be_recovered(self):
        self.accept()
        with self.inbox.connect() as db:
            db.execute("UPDATE inbox SET status='running'")
        self.inbox.recover_single_worker()
        self.assertTrue(self.process())

    def test_integer_timestamp_supported(self):
        self.event["event"]["message"]["create_time"] = 1789711500000
        self.assertEqual(self.accept(), "accepted")

    def test_invalid_content_is_rejected_before_queue(self):
        self.event["event"]["message"]["content"] = '"not-an-object"'
        self.assertEqual(self.accept(), "invalid_event")

    def test_two_topics_do_not_share_destination(self):
        self.accept()
        other = copy.deepcopy(self.event)
        other["header"]["event_id"] = "event-other"
        other["event"]["message"]["message_id"] = "root-other"
        self.accept(other)
        self.process(); self.process()
        with self.inbox.connect() as db:
            roots = {r[0] for r in db.execute("SELECT root_id FROM outbox")}
        self.assertEqual(roots, {"message-001", "root-other"})


if __name__ == "__main__":
    unittest.main()
