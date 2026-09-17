import copy
import json
from pathlib import Path
import unittest
from ticket_agent.evaluation import EvalConfig, EvaluationError, EvaluationSuite, evaluate, normalize_output, replay_input, run_holdout
from ticket_agent.evaluation_demo import CONFIG, policy, replay


class EvaluationTests(unittest.TestCase):
    def setUp(self):
        self.path = Path(__file__).resolve().parents[1] / "fixtures" / "evaluation-cases.json"
        self.raw = json.loads(self.path.read_text())
        self.suite = EvaluationSuite(self.raw)

    def test_same_incident_never_crosses_partitions(self):
        split = self.suite.split()
        development = {case.incident_id for case in split["development"]}
        holdout = {case.incident_id for case in split["holdout"]}
        self.assertFalse(development & holdout)
        self.assertEqual(split["allocation"]["incident-version-boundary"], "holdout")
        self.assertIn("ticket-boundary-001", [case.ticket_id for case in split["holdout"]])

    def test_future_confirmation_is_not_in_policy_input(self):
        case = next(case for case in self.suite.cases if case.ticket_id == "ticket-coupon-001")
        input_data = replay_input(case)
        self.assertEqual([message.event_id for message in input_data.messages], ["coupon-001-user"])
        self.assertNotIn("活动范围", "\n".join(message.text for message in input_data.messages))
        self.assertFalse(hasattr(input_data, "labels"))

    def test_late_visible_event_is_excluded_even_if_its_content_mentions_confirmation(self):
        raw = copy.deepcopy(self.raw)
        event = raw["cases"][0]["events"][0]
        raw["cases"][0]["events"].append({"id": "late", "text": "后来确认根因", "occurred_at": event["occurred_at"], "visible_at": "2026-09-10T10:05:00+08:00", "audience": "agent"})
        case = EvaluationSuite(raw).cases[0]
        self.assertEqual([x.event_id for x in replay_input(case).messages], ["print-001-user"])

    def test_labels_cannot_be_malformed_or_promote_unknown_root(self):
        raw = copy.deepcopy(self.raw)
        raw["cases"][0]["labels"]["root_cause"] = "printer_offline"
        with self.assertRaisesRegex(EvaluationError, "unknown_case"):
            EvaluationSuite(raw)

    def test_all_case_ids_and_event_ids_are_unique(self):
        raw = copy.deepcopy(self.raw)
        raw["cases"][1]["id"] = raw["cases"][0]["id"]
        with self.assertRaisesRegex(EvaluationError, "duplicate_ticket"):
            EvaluationSuite(raw)
        raw = copy.deepcopy(self.raw)
        raw["cases"][0]["events"].append(copy.deepcopy(raw["cases"][0]["events"][0]))
        with self.assertRaisesRegex(EvaluationError, "duplicate_event"):
            EvaluationSuite(raw)

    def test_policy_output_rejects_tampered_quote_and_unbounded_budget(self):
        with self.assertRaisesRegex(EvaluationError, "invalid_policy"):
            normalize_output({})
        with self.assertRaisesRegex(EvaluationError, "invalid_tool_calls"):
            normalize_output({"action": "ask", "facts": [], "root_cause": None, "questions": [], "tool_calls": -1, "usage": None})

    def test_score_detects_invalid_quote_unsupported_cause_and_budget(self):
        case = next(case for case in self.suite.cases if case.ticket_id == "ticket-boundary-001")
        output = normalize_output({"action": "handoff", "facts": [{"evidence_id": "O1001:dispatch", "quote": "改写过的内容"}], "root_cause": "printer_offline", "questions": [], "tool_calls": 3, "usage": None})
        from ticket_agent.evaluation import score
        result = score(case, output, CONFIG)
        self.assertFalse(result["citation_valid"])
        self.assertFalse(result["no_unsupported_cause"])
        self.assertFalse(result["tool_budget_ok"])

    def test_unknown_cause_is_scored_without_fake_root_accuracy(self):
        case = next(case for case in self.suite.cases if case.ticket_id == "ticket-login-001")
        output = normalize_output(policy(replay_input(case), CONFIG))
        from ticket_agent.evaluation import score
        result = score(case, output, CONFIG)
        self.assertEqual(result["root_cause_claim"], "not_applicable")
        self.assertTrue(result["no_unsupported_cause"])

    def test_completed_run_has_separate_dimensions_and_unknown_usage(self):
        result = replay()
        self.assertEqual(result["development_ticket_ids"], ["ticket-print-001", "ticket-print-002"])
        self.assertEqual(len(result["records"]), 5)
        self.assertEqual(result["summary"]["invalid_or_failed"], 0)
        self.assertEqual(result["summary"]["usage"]["unknown_runs"], 5)
        self.assertNotIn("overall_score", result["summary"])

    def test_fixture_policy_exposes_the_missing_campaign_question(self):
        record = next(item for item in replay()["records"] if item["ticket_id"] == "ticket-coupon-002")
        self.assertFalse(record["score"]["question_coverage"])
        self.assertEqual(record["output"]["questions"], ["channel", "product"])

    def test_policy_failure_remains_in_denominator(self):
        split = self.suite.split()
        records = evaluate(split["holdout"], CONFIG, lambda *_: (_ for _ in ()).throw(RuntimeError("boom")))
        self.assertEqual(len(records), len(split["holdout"]))
        self.assertTrue(all(record["status"] == "invalid_or_failed" for record in records))

    def test_config_identity_changes_digest(self):
        changed = EvalConfig("local-ticket-router", "fixture-r2", None, tool_budget=2, max_rounds=4, seconds=45)
        self.assertNotEqual(CONFIG.digest, changed.digest)

    def test_suite_digest_changes_when_labels_or_visible_event_change(self):
        labels = copy.deepcopy(self.raw)
        labels["cases"][0]["labels"]["sample_type"] = "changed"
        visible = copy.deepcopy(self.raw)
        visible["cases"][0]["events"][0]["text"] = "changed message"
        self.assertNotEqual(self.suite.digest, EvaluationSuite(labels).digest)
        self.assertNotEqual(self.suite.digest, EvaluationSuite(visible).digest)

    def test_runner_receives_only_public_replay_input(self):
        seen = []
        def inspect(input_data, config):
            seen.append(set(input_data.__dict__))
            return {"action": "handoff", "facts": [], "root_cause": None, "questions": [], "tool_calls": 0, "usage": None}
        run_holdout(self.suite, CONFIG, inspect)
        self.assertTrue(all("labels" not in fields for fields in seen))

    def test_citation_catalog_is_not_available_to_policy(self):
        case = next(case for case in self.suite.cases if case.ticket_id == "ticket-boundary-001")
        input_data = replay_input(case)
        self.assertFalse(any("O1001:dispatch" in message.text for message in input_data.messages))


if __name__ == "__main__":
    unittest.main()
