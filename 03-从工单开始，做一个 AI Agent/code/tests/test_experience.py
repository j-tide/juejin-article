import copy
import json
from pathlib import Path
import tempfile
import unittest
from ticket_agent.experience import ExperienceStore, MemoryError
from ticket_agent.experience_demo import seed, NOW, replay
from ticket_agent.domain import Scope


class ExperienceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name)/"memory.sqlite3"
        self.store = ExperienceStore(self.path, {"reviewer"})
        self.body = json.loads((Path(__file__).resolve().parents[1]/"fixtures/experience.json").read_text())["cases"][0]
        self.context = self.body["conditions"]

    def tearDown(self):
        self.temp.cleanup()

    def propose(self):
        return self.store.propose("case", self.body, "reviewer", NOW)

    def test_candidate_cannot_be_reused(self):
        self.assertIsNone(self.store.acquire(self.propose(),self.context,NOW,"run"))

    def test_customer_recovery_alone_cannot_be_approved(self):
        self.body["sources"] = [self.body["sources"][-1]]
        mid=self.propose()
        with self.assertRaisesRegex(MemoryError,"diagnosis_and_counterevidence"):
            self.store.approve(mid,"reviewer",NOW,"客户说好了")

    def test_unauthorized_operator(self):
        with self.assertRaisesRegex(MemoryError,"reviewer_required"):
            self.store.propose("case",self.body,"customer",NOW)

    def test_unknown_scope_refused(self):
        self.body["conditions"]["deployment"]="unknown"
        with self.assertRaisesRegex(MemoryError,"unknown_condition"):self.propose()

    def test_exact_dedup_and_immutable_revision(self):
        a=self.propose();self.assertEqual(a,self.propose())
        self.body["diagnosis"]="更正后的调查描述"
        b=self.propose();self.assertNotEqual(a,b)

    def test_context_isolation_all_fields(self):
        ids,sid,ctx=seed(self.store)
        for field in ctx:
            self.assertIsNone(self.store.acquire(sid,{**ctx,field:"different"},NOW,"run"))

    def test_expiry_and_future_review(self):
        ids,sid,ctx=seed(self.store)
        self.assertIsNone(self.store.acquire(sid,ctx,"2026-10-01T00:00:00+08:00","expired"))
        self.assertIsNone(self.store.acquire(sid,ctx,"2026-09-17T00:00:00+08:00","past"))

    def test_rejected_hypothesis_preserved(self):
        mid=self.propose();self.store.approve(mid,"reviewer",NOW,"checked")
        self.assertIn("缓存假设",self.store.acquire(mid,self.context,NOW,"run")["body"]["counterevidence"])

    def test_single_incident_not_enough_for_skill(self):
        mid=self.propose();self.store.approve(mid,"reviewer",NOW,"checked")
        body=copy.deepcopy(self.body);body.update(parents=[mid],steps=[{"tool":"search_knowledge","arguments":{"query":"优惠券"}}])
        sid=self.store.propose("skill",body,"reviewer",NOW)
        with self.assertRaisesRegex(MemoryError,"two_distinct"):
            self.store.approve(sid,"reviewer",NOW,"checked")

    def test_recipe_cannot_execute_shell(self):
        self.body.update(parents=["x"],steps=[{"tool":"shell","arguments":{"query":"rm"}}])
        with self.assertRaisesRegex(MemoryError,"read_only_recipe"):
            self.store.propose("skill",self.body,"reviewer",NOW)

    def test_revocation_blocks_dependent_skill_and_reports_uses(self):
        ids,sid,ctx=seed(self.store)
        self.store.acquire(sid,ctx,NOW,"affected-run")
        result=self.store.revoke(ids[0],"reviewer",NOW,"source wrong")
        self.assertIn(sid,result["affected_memories"])
        self.assertEqual(result["review_runs"],["affected-run"])
        self.assertIsNone(self.store.acquire(sid,ctx,NOW,"later"))

    def test_revocation_cannot_be_reapproved_or_recreated(self):
        mid=self.propose();self.store.revoke(mid,"reviewer",NOW,"wrong")
        self.assertEqual(self.propose(),mid)
        with self.assertRaisesRegex(MemoryError,"candidate_required"):
            self.store.approve(mid,"reviewer",NOW,"retry")

    def test_persistence(self):
        ids,sid,ctx=seed(self.store)
        other=ExperienceStore(self.path,{"reviewer"})
        self.assertEqual(other.acquire(sid,ctx,NOW,"restart")["id"],sid)

    def test_real_knowledge_retrieval_and_fallbacks(self):
        r=replay()
        self.assertEqual(r["matching_context"]["status"],"needs_review")
        self.assertGreater(len(r["matching_context"]["evidence"]),0)
        for key in ("changed_deployment","missing_conditions","after_revocation"):
            self.assertEqual(r[key]["status"],"fallback")

    def test_scope_checked_before_tool(self):
        ids,sid,ctx=seed(self.store)
        with self.assertRaisesRegex(MemoryError,"execution_scope_mismatch"):
            self.store.execute(sid,ctx,NOW,"run",{},Scope("other","store-001"))

    def test_mid_execution_revocation_invalidates_return(self):
        ids,sid,ctx=seed(self.store)
        def tool(args,scope):
            self.store.revoke(ids[0],"reviewer",NOW,"wrong")
            return {"status":"retrieved","evidence":[{"id":"x","quote":"test","conditions":{"status":"match"}}]}
        result=self.store.execute(sid,ctx,NOW,"run",{"search_knowledge":tool},Scope(ctx["brand"],ctx["store"]))
        self.assertEqual(result["reason"],"memory_revoked")

    def test_failure_returns_to_generic_investigation(self):
        ids,sid,ctx=seed(self.store)
        def broken(args,scope):raise TimeoutError()
        result=self.store.execute(sid,ctx,NOW,"run",{"search_knowledge":broken},Scope(ctx["brand"],ctx["store"]))
        self.assertEqual(result["reason"],"tool_failed")

    def test_search_filters_before_ranking(self):
        ids,sid,ctx=seed(self.store)
        self.assertTrue(self.store.find("优惠券",ctx,NOW))
        self.assertEqual(self.store.find("优惠券",{**ctx,"tenant":"other"},NOW),[])
        self.store.revoke(ids[0],"reviewer",NOW,"wrong")
        self.assertNotIn(sid,[x["id"] for x in self.store.find("优惠券",ctx,NOW)])

    def test_two_records_of_same_incident_do_not_promote_skill(self):
        mid=self.propose();self.store.approve(mid,"reviewer",NOW,"checked")
        self.body["title"]="same incident another summary"
        other=self.propose();self.store.approve(other,"reviewer",NOW,"checked")
        body=copy.deepcopy(self.body);body.update(parents=[mid,other],steps=[{"tool":"search_knowledge","arguments":{"query":"优惠券"}}])
        sid=self.store.propose("skill",body,"reviewer",NOW)
        with self.assertRaisesRegex(MemoryError,"two_distinct"):
            self.store.approve(sid,"reviewer",NOW,"checked")
