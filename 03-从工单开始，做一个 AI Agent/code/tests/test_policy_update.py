import copy
import json
from pathlib import Path
import unittest

from ticket_agent.evaluation import EvalConfig, replay_input
from ticket_agent.evaluation_demo import policy as baseline_policy
from ticket_agent.policy_update import PolicyUpdateError, PolicyUpdateSuite, candidate_policy, compare_candidate
from ticket_agent.policy_update_demo import run


class PolicyUpdateTests(unittest.TestCase):
    def setUp(self):
        self.path = Path(__file__).resolve().parents[1] / "fixtures" / "policy-update-cases.json"
        self.raw = json.loads(self.path.read_text())
        self.suite = PolicyUpdateSuite(self.raw)
        self.config = EvalConfig("local-ticket-router", "fixture-r1", None, tool_budget=2, max_rounds=4, seconds=45)

    def test_calibration_and_gate_share_neither_ticket_nor_incident(self):
        calibration = {case.ticket_id for case in self.suite.calibration.cases}
        gate = {case.ticket_id for case in self.suite.gate.cases}
        self.assertIn("ticket-coupon-002", calibration)
        self.assertFalse(calibration & gate)
        self.assertFalse(
            {case.incident_id for case in self.suite.calibration.cases}
            & {case.incident_id for case in self.suite.gate.cases}
        )

    def test_candidate_changes_only_the_explicit_negative_wording(self):
        cases = {case.ticket_id: case for case in self.suite.calibration.cases + self.suite.gate.cases}
        changed = []
        for ticket_id, case in cases.items():
            input_data = replay_input(case)
            before = baseline_policy(input_data, self.config)
            after = candidate_policy(input_data, self.config, self.suite.proposal.trigger_phrases)
            if before != after:
                changed.append(ticket_id)
        self.assertEqual(changed, ["ticket-coupon-002", "gate-coupon-missing-campaign"])

    def test_known_failure_is_fixed_but_only_eligible_for_shadow(self):
        result = run()
        source = next(item for item in result["calibration"]["baseline"]["records"] if item["ticket_id"] == "ticket-coupon-002")
        repaired = next(item for item in result["calibration"]["candidate"]["records"] if item["ticket_id"] == "ticket-coupon-002")
        self.assertFalse(source["score"]["question_coverage"])
        self.assertTrue(repaired["score"]["question_coverage"])
        self.assertEqual(result["decision"], "eligible_for_shadow")
        self.assertNotIn("active", result["decision"])

    def test_unseen_gate_improves_one_case_without_reducing_other_dimensions(self):
        result = compare_candidate(self.suite)
        baseline = next(item for item in result["gate"]["baseline"]["records"] if item["ticket_id"] == "gate-coupon-missing-campaign")
        candidate = next(item for item in result["gate"]["candidate"]["records"] if item["ticket_id"] == "gate-coupon-missing-campaign")
        self.assertFalse(baseline["score"]["question_coverage"])
        self.assertTrue(candidate["score"]["question_coverage"])
        self.assertTrue(all(result["gate"]["dimension_not_worse"].values()))
        self.assertEqual(result["gate"]["baseline_tool_calls"], result["gate"]["candidate_tool_calls"])

    def test_malformed_or_leaking_fixture_is_rejected(self):
        raw = copy.deepcopy(self.raw)
        raw["gate"]["cases"][0]["id"] = "ticket-coupon-002"
        with self.assertRaisesRegex(PolicyUpdateError, "ticket_reused"):
            PolicyUpdateSuite(raw)
        raw = copy.deepcopy(self.raw)
        raw["gate"]["cases"][0]["incident_id"] = raw["calibration"]["cases"][0]["incident_id"]
        with self.assertRaisesRegex(PolicyUpdateError, "incident_reused"):
            PolicyUpdateSuite(raw)

    def test_candidate_does_not_receive_labels(self):
        case = next(case for case in self.suite.gate.cases if case.ticket_id == "gate-coupon-missing-campaign")
        input_data = replay_input(case)
        self.assertFalse(hasattr(input_data, "labels"))
        output = candidate_policy(input_data, self.config, self.suite.proposal.trigger_phrases)
        self.assertEqual(output["questions"], ["campaign"])


if __name__ == "__main__":
    unittest.main()
