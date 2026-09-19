import tempfile
import unittest

from ticket_agent.policy_rollout import RolloutError, RolloutRegistry


SCOPE = {"tenant": "demo-tenant", "brand": "demo-brand", "store": "store-002"}
OTHER_SCOPE = {"tenant": "demo-tenant", "brand": "demo-brand", "store": "store-003"}


def bundle(bundle_id, revision, experiences=(), skills=(), retrieval="retrieval-coupon-r1", swarm="swarm-single-agent-r1"):
    return {
        "id": bundle_id,
        "revision": revision,
        "experience_ids": list(experiences),
        "skill_ids": list(skills),
        "retrieval_config_id": retrieval,
        "swarm_plan_id": swarm,
    }


class PolicyRolloutTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.registry = RolloutRegistry(f"{directory.name}/rollout.sqlite3", reviewers={"ops-zhou"})
        self.stable = bundle("bundle-r1", "coupon-intake-r1")
        self.candidate = bundle(
            "bundle-r2",
            "coupon-intake-r2",
            experiences=("experience-coupon-missing-campaign-r1",),
            skills=("skill-coupon-intake-r1",),
        )
        self.registry.register_bundle(self.stable, "2026-09-19T10:00:00+08:00")
        self.registry.register_bundle(self.candidate, "2026-09-19T10:00:01+08:00")
        self.registry.set_stable_bundle("bundle-r1", "ops-zhou", "2026-09-19T10:00:02+08:00", "reviewed baseline")

    def start_shadow(self, rollout_id="rollout-r2", shadow_limit=2):
        return self.registry.create_shadow(
            rollout_id,
            "bundle-r2",
            SCOPE,
            shadow_limit,
            "policy-update-fixture-r1",
            "ops-zhou",
            "2026-09-19T10:01:00+08:00",
        )

    def record_shadow(self, ticket_id="topic-shadow"):
        self.registry.record_shadow(
            ticket_id,
            {"action": "ask", "questions": ["channel", "product"], "note": "baseline"},
            {"action": "ask", "questions": ["campaign"], "note": "candidate"},
            "2026-09-19T10:02:01+08:00",
        )

    def promote(self):
        self.registry.approve_canary(
            "rollout-r2", 2, "ops-zhou", "2026-09-19T10:03:00+08:00", "reviewed shadow record"
        )

    def test_bundle_id_is_immutable(self):
        repeated = self.registry.register_bundle(self.stable, "2026-09-19T10:00:03+08:00")
        self.assertFalse(repeated["registered"])
        changed = {**self.stable, "revision": "coupon-intake-r1-edited"}
        with self.assertRaisesRegex(RolloutError, "bundle_id_content_mismatch"):
            self.registry.register_bundle(changed, "2026-09-19T10:00:04+08:00")

    def test_shadow_delivers_stable_and_only_observes_candidate(self):
        self.start_shadow(shadow_limit=1)
        assignment = self.registry.route_ticket("topic-shadow", SCOPE, "2026-09-19T10:02:00+08:00")
        self.assertEqual((assignment["mode"], assignment["bundle_id"], assignment["shadow_bundle_id"]), ("shadow", "bundle-r1", "bundle-r2"))
        delivery = self.registry.mark_delivered("topic-shadow", "2026-09-19T10:02:00+08:00")
        self.assertEqual(delivery["delivered_bundle_id"], "bundle-r1")
        self.record_shadow()
        trace = self.registry.trace("topic-shadow")
        self.assertEqual(trace["shadow"]["candidate_result"]["questions"], ["campaign"])

    def test_scope_and_shadow_limit_fall_back_to_stable(self):
        self.start_shadow(shadow_limit=1)
        outside = self.registry.route_ticket("topic-outside", OTHER_SCOPE, "2026-09-19T10:02:00+08:00")
        first = self.registry.route_ticket("topic-first", SCOPE, "2026-09-19T10:02:01+08:00")
        second = self.registry.route_ticket("topic-second", SCOPE, "2026-09-19T10:02:02+08:00")
        self.assertEqual(outside["mode"], "stable")
        self.assertEqual(first["mode"], "shadow")
        self.assertEqual(second["mode"], "stable")

    def test_canary_requires_recorded_shadow_and_reviewer(self):
        self.start_shadow()
        with self.assertRaisesRegex(RolloutError, "shadow_evidence_required"):
            self.promote()
        self.registry.route_ticket("topic-shadow", SCOPE, "2026-09-19T10:02:00+08:00")
        self.record_shadow()
        with self.assertRaisesRegex(RolloutError, "reviewer_required"):
            self.registry.approve_canary("rollout-r2", 2, "support-lin", "2026-09-19T10:03:00+08:00", "not authorized")
        self.promote()

    def test_ticket_assignment_does_not_switch_after_canary_promotion(self):
        self.start_shadow()
        first = self.registry.route_ticket("topic-shadow", SCOPE, "2026-09-19T10:02:00+08:00")
        self.record_shadow()
        self.promote()
        retained = self.registry.route_ticket("topic-shadow", SCOPE, "2026-09-19T10:04:00+08:00")
        new_ticket = self.registry.route_ticket("topic-canary", SCOPE, "2026-09-19T10:04:01+08:00")
        self.assertEqual(first["bundle_id"], "bundle-r1")
        self.assertTrue(retained["reused_assignment"])
        self.assertEqual(retained["bundle_id"], "bundle-r1")
        self.assertEqual((new_ticket["mode"], new_ticket["bundle_id"]), ("canary", "bundle-r2"))

    def test_reported_feedback_cannot_trigger_rollback(self):
        self.start_shadow()
        self.registry.route_ticket("topic-shadow", SCOPE, "2026-09-19T10:02:00+08:00")
        self.record_shadow()
        self.promote()
        self.registry.route_ticket("topic-canary", SCOPE, "2026-09-19T10:04:00+08:00")
        self.registry.record_feedback(
            "reported-1", "topic-canary", "reported_issue", "operator noticed a version change", "support-lin", "2026-09-19T10:05:00+08:00"
        )
        with self.assertRaisesRegex(RolloutError, "verified_regression_required"):
            self.registry.rollback("rollout-r2", "reported-1", "ops-zhou", "2026-09-19T10:05:01+08:00", "must verify first")

    def test_verified_regression_stops_new_canary_but_preserves_existing_assignment(self):
        self.start_shadow()
        self.registry.route_ticket("topic-shadow", SCOPE, "2026-09-19T10:02:00+08:00")
        self.record_shadow()
        self.promote()
        canary = self.registry.route_ticket("topic-canary", SCOPE, "2026-09-19T10:04:00+08:00")
        self.registry.record_feedback(
            "verified-1", "topic-canary", "verified_regression", "reviewer confirmed stale configuration conditions", "ops-zhou", "2026-09-19T10:05:00+08:00"
        )
        result = self.registry.rollback("rollout-r2", "verified-1", "ops-zhou", "2026-09-19T10:05:01+08:00", "candidate conditions are stale")
        after = self.registry.route_ticket("topic-after", SCOPE, "2026-09-19T10:06:00+08:00")
        retained = self.registry.route_ticket("topic-canary", SCOPE, "2026-09-19T10:06:01+08:00")
        states = {(item["kind"], item["id"]): item["state"] for item in self.registry.component_states("bundle-r2")}
        self.assertEqual(canary["bundle_id"], "bundle-r2")
        self.assertEqual(result["new_ticket_bundle_id"], "bundle-r1")
        self.assertEqual(after["bundle_id"], "bundle-r1")
        self.assertEqual(retained["bundle_id"], "bundle-r2")
        self.assertEqual(states[("experience", "experience-coupon-missing-campaign-r1")], "needs_review")
        self.assertEqual(states[("skill", "skill-coupon-intake-r1")], "needs_review")
        self.assertEqual(states[("retrieval", "retrieval-coupon-r1")], "active")

    def test_trace_keeps_review_and_version_evidence(self):
        self.start_shadow()
        self.registry.route_ticket("topic-shadow", SCOPE, "2026-09-19T10:02:00+08:00")
        self.record_shadow()
        self.promote()
        self.registry.route_ticket("topic-canary", SCOPE, "2026-09-19T10:04:00+08:00")
        self.registry.record_feedback(
            "verified-1", "topic-canary", "verified_regression", "reviewer confirmed stale configuration conditions", "ops-zhou", "2026-09-19T10:05:00+08:00"
        )
        self.registry.rollback("rollout-r2", "verified-1", "ops-zhou", "2026-09-19T10:05:01+08:00", "candidate conditions are stale")
        trace = self.registry.trace("topic-canary")
        self.assertEqual(trace["assignment"]["bundle_id"], "bundle-r2")
        self.assertEqual(trace["rollout"]["phase"], "rolled_back")
        self.assertEqual(trace["feedback"][0]["classification"], "verified_regression")
        self.assertIn("rolled_back", [event["action"] for event in trace["events"]])


if __name__ == "__main__":
    unittest.main()
