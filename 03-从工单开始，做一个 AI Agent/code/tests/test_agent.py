import copy
import json
import unittest
from ticket_agent.domain import OrderReader, Scope
from ticket_agent.providers import DeepSeek, ProviderError, Replay
from ticket_agent.runtime import run_ticket, render

SCOPE = Scope("demo-brand", "store-001")


class Sequence:
    mode = "scripted_test"

    def __init__(self, *messages):
        self.messages = iter(messages)

    def complete(self, messages, tools, timeout):
        return next(self.messages), {}


def call(arguments='{"reference":"P1001"}', name="lookup_order", call_id="c1"):
    return {"role": "assistant", "content": None, "tool_calls": [
        {"id": call_id, "type": "function", "function": {"name": name, "arguments": arguments}}]}


def final(facts=None, next_step="handoff"):
    return {"role": "assistant", "content": json.dumps({"facts": facts or [], "next_step": next_step})}


class AgentTests(unittest.TestCase):
    def run_case(self, provider=None, text="客户付款 P1001，门店没出单", reader=None, **kw):
        return run_ticket(text, SCOPE, provider or Replay(), reader or OrderReader(), **kw)

    def test_payment_reference_resolves_order_without_claiming_printed(self):
        result = self.run_case()
        self.assertEqual(result["status"], "needs_human")
        self.assertEqual(len(result["answer"]["facts"]), 4)
        self.assertIn("不能确认是否已打印", render(result))
        self.assertEqual(result["mode"], "scripted_replay")

    def test_order_reference_and_payment_reference_agree(self):
        reader = OrderReader()
        a = reader.lookup({"reference": "O1001"}, SCOPE)
        b = reader.lookup({"reference": "P1001"}, SCOPE)
        self.assertEqual(a["evidence"], b["evidence"])

    def test_missing_reference_requests_input(self):
        result = self.run_case(text="顾客说已经付款，店长没找到单")
        self.assertEqual(result["status"], "needs_input")
        self.assertEqual(result["queries"], [])

    def test_cross_tenant_indistinguishable_from_not_found(self):
        reader = OrderReader()
        for ref in ("P2001", "P9999"):
            result = reader.lookup({"reference": ref}, SCOPE)
            self.assertEqual(result["status"], "not_found")
            self.assertEqual(result["evidence"], [])

    def test_same_brand_wrong_store_cannot_read(self):
        result = OrderReader().lookup({"reference": "P1001"}, Scope("demo-brand", "store-002"))
        self.assertEqual(result["status"], "not_found")

    def test_injected_scope_is_rejected(self):
        provider = Sequence(call('{"reference":"P2001","brand":"other-brand"}'), final())
        result = self.run_case(provider)
        self.assertEqual(result["queries"][0]["status"], "invalid_arguments")
        self.assertEqual(result["evidence"], [])

    def test_forbidden_tool_is_not_executed(self):
        result = self.run_case(Sequence(call(name="refund"), final()))
        self.assertEqual(result["queries"][0]["status"], "tool_not_allowed")

    def test_invalid_json_types_and_duplicate_keys(self):
        for args in ('[]', '{', '{"reference":1234}', '{"reference":"P1001","reference":"P2001"}'):
            with self.subTest(args=args):
                result = self.run_case(Sequence(call(args), final()))
                self.assertEqual(result["queries"][0]["status"], "invalid_arguments")

    def test_false_quote_with_real_id_is_rejected(self):
        facts = [{"evidence_id": "O1001:payment", "quote": "门店已打印"}]
        result = self.run_case(Sequence(call(), final(facts)))
        self.assertEqual(result["status"], "invalid_answer")
        self.assertNotIn("门店已打印", render(result))

    def test_unknown_evidence_is_rejected(self):
        result = self.run_case(Sequence(final([{"evidence_id": "fake", "quote": "已退款"}], "ask_reference")))
        self.assertEqual(result["status"], "invalid_answer")

    def test_omitting_unknown_stage_is_rejected(self):
        result = self.run_case(Sequence(call(), final()))
        self.assertEqual(result["status"], "invalid_answer")

    def test_model_call_budget(self):
        result = self.run_case(Sequence(call(), call(call_id="c2"), call(call_id="c3")))
        self.assertEqual(result["status"], "tool_budget_exceeded")
        self.assertEqual(len(result["queries"]), 2)

    def test_round_limit_stops_loop(self):
        result = self.run_case(Sequence(call()), max_rounds=1)
        self.assertEqual(result["status"], "round_budget_exceeded")

    def test_tool_timeout_is_not_negative_evidence(self):
        class Slow:
            def lookup(self, arguments, scope):
                raise TimeoutError()
        result = self.run_case(Sequence(call(), final()), reader=Slow())
        self.assertEqual(result["queries"][0]["status"], "tool_timeout")
        self.assertIn("不能据此判断订单不存在", render(result))

    def test_deadline_checked_before_call(self):
        self.assertEqual(self.run_case(Sequence(), seconds=0)["status"], "deadline_exceeded")

    def test_model_transport_failure_is_visible(self):
        class Unavailable:
            mode = "scripted_test"
            def complete(self, *args, **kwargs):
                raise ProviderError("http_429")
        self.assertEqual(self.run_case(Unavailable())["status"], "model_unavailable")

    def test_live_adapter_roundtrip_matches_tool_call_id(self):
        requests = []
        facts = [{"evidence_id": e["id"], "quote": e["quote"]} for e in
                 OrderReader().lookup({"reference": "P1001"}, SCOPE)["evidence"]]
        messages = [call(), final(facts)]
        def fake_http(payload, timeout):
            requests.append(copy.deepcopy(payload))
            return {"choices": [{"message": messages[len(requests)-1], "finish_reason":
                                  "tool_calls" if len(requests) == 1 else "stop"}],
                    "usage": {"prompt_tokens": 100, "completion_tokens": 20}}
        result = self.run_case(DeepSeek("test-only", transport=fake_http))
        self.assertEqual(result["status"], "needs_human")
        self.assertEqual(requests[1]["messages"][-1]["tool_call_id"], "c1")
        self.assertEqual(requests[1]["messages"][-2]["tool_calls"][0]["id"], "c1")
        self.assertEqual(result["usage"]["prompt_tokens"], 200)

    def test_truncated_completion_is_not_a_final_answer(self):
        provider = DeepSeek("test-only", transport=lambda *args: {
            "choices": [{"message": final(), "finish_reason": "length"}]})
        self.assertEqual(self.run_case(provider)["status"], "model_unavailable")

    def test_duplicate_call_ids_rejected_before_second_read(self):
        result = self.run_case(Sequence(call(), call()))
        self.assertEqual(result["status"], "invalid_tool_call")
        self.assertEqual(len(result["queries"]), 1)


if __name__ == "__main__":
    unittest.main()
