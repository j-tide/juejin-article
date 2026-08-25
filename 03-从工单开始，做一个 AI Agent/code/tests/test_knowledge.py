import unittest
from dataclasses import replace
from ticket_agent.domain import Scope
from ticket_agent.knowledge import KnowledgeContext,KnowledgeReader,run_knowledge,rrf
from ticket_agent.providers import Replay

CONTEXT=KnowledgeContext('campaign-a','2026-09-18T14:00:00+08:00','2026-09-18T14:10:00+08:00','miniapp','fruit-tea',3200)
SCOPE=Scope('demo-brand','store-001')


class KnowledgeTests(unittest.TestCase):
    def search(self,context=CONTEXT,scope=SCOPE,query='两杯水果茶，优惠券用不了'):
        return KnowledgeReader(context).search({'query':query},scope)

    def test_similar_question_has_different_applicability(self):
        a=self.search()
        b=self.search(replace(CONTEXT,campaign='campaign-b'),Scope('demo-brand','store-002'))
        self.assertEqual({e['conditions']['status'] for e in a['evidence']},{'mismatch'})
        self.assertEqual({e['conditions']['status'] for e in b['evidence']},{'match'})

    def test_expired_future_revoked_and_unapproved_not_returned(self):
        a=self.search();b=self.search(replace(CONTEXT,campaign='campaign-b'),Scope('demo-brand','store-002'))
        ids={e['id'] for e in a['evidence']+b['evidence']}
        self.assertEqual(ids,{'doc:rule-a@1','doc:case-a@1','doc:rule-b@1'})

    def test_access_scope_checked_before_retrieval(self):
        self.assertEqual(self.search(scope=Scope('demo-brand','store-009'))['evidence'],[])

    def test_missing_condition_stays_unknown(self):
        result=self.search(replace(CONTEXT,product=None))
        self.assertEqual(result['status'],'needs_context')
        self.assertIn('product',result['evidence'][0]['conditions']['unknown'])

    def test_missing_campaign_never_broadens_search(self):
        with self.assertRaises(ValueError):replace(CONTEXT,campaign='')

    def test_rule_conflict_not_resolved_by_rank(self):
        result=self.search(replace(CONTEXT,campaign='campaign-c'))
        self.assertEqual(result['status'],'conflicting_sources')
        self.assertTrue(all(e['conflict'] for e in result['evidence']))

    def test_case_does_not_become_incident_fact(self):
        e=next(e for e in self.search()['evidence'] if e['kind']=='case')
        self.assertIn('仅作排查线索',e['quote'])

    def test_scope_in_model_arguments_is_rejected(self):
        result=KnowledgeReader(CONTEXT).search({'query':'优惠券','store':'store-002'},SCOPE)
        self.assertEqual(result['status'],'invalid_arguments')

    def test_no_relevant_terms_returns_empty(self):
        self.assertEqual(self.search(query='打印机断电')['status'],'no_match')

    def test_naive_datetime_rejected(self):
        with self.assertRaises(ValueError):replace(CONTEXT,occurred_at='2026-09-18T14:00:00')

    def test_boundary_date_excludes_expired_rule(self):
        context=replace(CONTEXT,campaign='campaign-b',occurred_at='2026-09-18T00:00:00+08:00')
        self.assertNotIn('rule-b-expired',{d['id'] for d in KnowledgeReader(context).candidates(Scope('demo-brand','store-002'))})

    def test_rrf_uses_rank_and_stable_tie_break(self):
        self.assertEqual(rrf([['a','b'],['b','a']]),['a','b'])

    def test_shared_agent_loop_calls_search_and_preserves_citations(self):
        result=run_knowledge('优惠券用不了',SCOPE,CONTEXT,Replay())
        self.assertEqual(result['status'],'needs_human')
        self.assertEqual(result['queries'][0]['tool'],'search_knowledge')
        self.assertEqual(len(result['answer']['facts']),2)
