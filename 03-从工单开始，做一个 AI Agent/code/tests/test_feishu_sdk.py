"""Optional SDK shape test; neither authenticates nor sends a platform message."""
import importlib.util
import json
import unittest
from pathlib import Path


@unittest.skipUnless(importlib.util.find_spec("lark_oapi"), "install requirements-feishu.txt for SDK contract test")
class SDKTests(unittest.TestCase):
    def test_typed_event_and_reply_builder(self):
        import lark_oapi as lark
        from lark_oapi.api.im.v1 import P2ImMessageReceiveV1
        from ticket_agent.feishu import reply_request
        path = Path(__file__).resolve().parents[1] / "fixtures/feishu-events.json"
        event = P2ImMessageReceiveV1(json.loads(path.read_text())[0])
        normalized = json.loads(lark.JSON.marshal(event))
        self.assertEqual(normalized["header"]["app_id"], "app-demo")
        self.assertEqual(normalized["event"]["message"]["message_id"], "message-001")
        request = reply_request(lark, "root-1", "查询结果", "00000000-0000-0000-0000-000000000001")
        self.assertEqual(request.message_id, "root-1")
        self.assertTrue(request.request_body.reply_in_thread)
        self.assertEqual(json.loads(request.request_body.content), {"text": "查询结果"})
