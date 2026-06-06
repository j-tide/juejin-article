"""Acceptance snapshot and fail-closed invariants beyond the case matrix."""

from dataclasses import replace
from pathlib import Path
import tempfile
import unittest

from harness_lab import Contract, FINISH, GOOD, Harness, READ_A, READ_B, ReplayPolicy, digest, write


class RuntimeInvariants(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)

    def tearDown(self):
        self.temporary.cleanup()

    def test_acceptance_is_bound_to_snapshot_not_mutable_path(self):
        runtime = Harness(self.root)
        state = runtime.run(ReplayPolicy([READ_A, READ_B, write(GOOD), FINISH]))
        runtime.draft.write_text("修改后的不合格内容")
        self.assertEqual(state.accepted_bytes.decode(), GOOD)
        self.assertEqual(state.receipt["artifact_sha256"], digest(GOOD.encode()))
        self.assertNotEqual(digest(runtime.draft.read_bytes()), state.receipt["artifact_sha256"])

    def test_rejected_finish_returns_feedback_to_policy(self):
        seen = []
        actions = iter([FINISH, READ_A, READ_B, write(GOOD), FINISH])

        def policy(observation):
            seen.append(observation)
            return next(actions)

        state = Harness(self.root).run(policy)
        self.assertEqual(seen[1]["feedback"], ["artifact_missing"])
        self.assertEqual(state.phase, "REVIEW_READY")
        self.assertEqual(state.turns, 5)

    def test_empty_required_section_is_rejected(self):
        content = GOOD.split("## 局限")[0] + "## 局限\n   \n"
        state = Harness(self.root).run(ReplayPolicy([READ_A, READ_B, write(content), FINISH]))
        self.assertEqual(state.phase, "EXHAUSTED")
        self.assertIn("section_missing_or_empty:局限", state.feedback)

    def test_tool_budget_is_checked_before_execution(self):
        contract = replace(Contract(), max_tool_calls=1)
        state = Harness(self.root, contract).run(ReplayPolicy([READ_A, READ_B]))
        self.assertEqual(state.tool_calls, 1)
        self.assertEqual(set(state.observed), {"a"})
        self.assertEqual(state.phase, "EXHAUSTED")

    def test_finish_cannot_override_acceptance_contract(self):
        state = Harness(self.root).run(ReplayPolicy([{"kind": "finish", "verified": True}]))
        self.assertEqual(state.phase, "BLOCKED")
        self.assertIsNone(state.receipt)

    def test_invalid_arguments_do_not_create_report(self):
        state = Harness(self.root).run(ReplayPolicy([{"kind": "write_report", "text": None}]))
        self.assertFalse((self.root / "draft.md").exists())
        self.assertEqual(state.phase, "EXHAUSTED")

    def test_non_string_action_kind_is_denied(self):
        state = Harness(self.root).run(ReplayPolicy([{"kind": ["finish"]}]))
        self.assertEqual(state.phase, "BLOCKED")
        self.assertIsNone(state.receipt)

    def test_unknown_and_malformed_citations_are_rejected(self):
        for index, citation in enumerate(("[E:UNKNOWN]", "[E:broken", "[E:]")):
            runtime = Harness(self.root / str(index))
            state = runtime.run(ReplayPolicy([READ_A, READ_B, write(GOOD + citation), FINISH]))
            self.assertEqual(state.phase, "EXHAUSTED")
            self.assertIsNone(state.accepted_bytes)

    def test_new_task_cannot_silently_accept_previous_report(self):
        Harness(self.root).run(ReplayPolicy([READ_A, READ_B, write(GOOD), FINISH]))
        with self.assertRaisesRegex(ValueError, "clean workspace"):
            Harness(self.root)


if __name__ == "__main__":
    unittest.main()
