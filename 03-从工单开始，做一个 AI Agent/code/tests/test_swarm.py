import copy
import tempfile
import threading
import unittest
from ticket_agent.domain import Scope
from ticket_agent.swarm import Part,InvestigationInput,Gateway,ToolRejected,run_parts,aggregate,demo,validate_plan,model_worker

CTX=InvestigationInput(Scope('demo-brand','store-001'),4,6,'优惠券问题','synthetic-material-hash')
E={'id':'test-evidence','source':'fixture://test','observed_at':'2026-09-18T14:00:00+08:00','event_at':None,'quote':'合成工具观察，不是结论'}


def handler(args,scope):return {'status':'retrieved','evidence':[copy.deepcopy(E)]}

def worker(part,context,gateway,dependencies):
    result=gateway.call('search_knowledge',{})
    return {'part_id':part.id,'snapshot':context.digest,'evidence':result['evidence']}


class SwarmTests(unittest.TestCase):
    def run_case(self,parts=None,worker=worker,**kw):
        return run_parts(parts or [Part('r','rules')],CTX,{'search_knowledge':handler},worker=worker,**kw)

    def test_parallel_workers_really_overlap(self):
        barrier=threading.Barrier(2,timeout=2)
        def overlap(part,ctx,gateway,dependencies):
            barrier.wait();return worker(part,ctx,gateway,dependencies)
        result=self.run_case([Part('a','rules'),Part('b','rules')],worker=overlap)
        self.assertTrue(result['coverage_complete']);self.assertEqual(result['tool_calls'],2)
        self.assertEqual(len(result['evidence']),1)

    def test_dependency_receives_parent_output(self):
        def dependent(part,ctx,gateway,dependencies):
            if part.id=='b':self.assertIn('a',dependencies);self.assertEqual(dependencies['a']['state'],'ok')
            return worker(part,ctx,gateway,dependencies)
        self.assertTrue(self.run_case([Part('a','rules'),Part('b','rules',('a',))],worker=dependent)['coverage_complete'])

    def test_failed_parent_blocks_child(self):
        def failed(*args):raise TimeoutError()
        result=self.run_case([Part('a','rules'),Part('b','rules',('a',))],worker=failed)
        self.assertEqual(result['parts']['a']['state'],'failed');self.assertEqual(result['parts']['b']['state'],'blocked')

    def test_cycle_and_duplicate_ids_rejected(self):
        for parts in [[Part('a','rules'),Part('a','rules')],[Part('a','rules',('b',)),Part('b','rules',('a',))]]:
            with self.assertRaises(ValueError):validate_plan(parts,5)

    def test_global_budget_rejected_before_tools(self):
        with self.assertRaisesRegex(ValueError,'global_call_budget_exceeded'):self.run_case([Part('a','rules',calls=3),Part('b','rules',calls=3)])

    def test_role_cannot_borrow_other_tools(self):
        gateway=Gateway(Part('r','rules'),CTX,{})
        with self.assertRaisesRegex(ToolRejected,'tool_not_allowed'):gateway.call('backend_read',{})

    def test_per_part_budget(self):
        def repeat(part,ctx,gateway,deps):
            worker(part,ctx,gateway,deps);return worker(part,ctx,gateway,deps)
        result=self.run_case(worker=repeat)
        self.assertEqual(result['parts']['r']['state'],'failed');self.assertEqual(result['tool_calls'],1)
        self.assertEqual(len(result['evidence']),1)

    def test_foreign_snapshot_rejected_without_losing_tool_evidence(self):
        def foreign(part,ctx,gateway,deps):
            value=worker(part,ctx,gateway,deps);value['snapshot']='another-input';return value
        result=self.run_case(worker=foreign)
        self.assertEqual(result['parts']['r']['reason'],'worker_result_mismatch');self.assertEqual(len(result['evidence']),1)

    def test_changed_citation_metadata_rejected(self):
        def altered(part,ctx,gateway,deps):
            value=worker(part,ctx,gateway,deps);value['evidence'][0]['source']='unverified://elsewhere';return value
        self.assertEqual(self.run_case(worker=altered)['parts']['r']['reason'],'worker_result_mismatch')

    def test_omission_rejected(self):
        def omitted(part,ctx,gateway,deps):
            value=worker(part,ctx,gateway,deps);value['evidence']=[];return value
        self.assertFalse(self.run_case(worker=omitted)['coverage_complete'])

    def test_topic_or_board_change_marks_result_stale(self):
        for versions in [(5,6),(4,7)]:self.assertEqual(self.run_case(current_versions=lambda:versions)['status'],'stale_input')

    def test_conflicting_quotes_are_not_voted_away(self):
        parts=[Part('a','rules'),Part('b','rules')]
        results={'a':{'state':'ok','evidence':[E]},'b':{'state':'ok','evidence':[{**E,'quote':'另一条冲突记录'}]}}
        result=aggregate(parts,results)
        self.assertEqual(len(result['conflicts']),1);self.assertEqual(result['conflicts'][0]['left'],E['quote'])

    def test_frozen_context(self):
        with self.assertRaises(Exception):CTX.topic_version=99

    def test_serial_parallel_preserve_same_actual_evidence(self):
        with tempfile.TemporaryDirectory() as folder:
            a=demo(folder,1);b=demo(folder,2)
            project=lambda x:sorted((e['id'],e['quote']) for e in x['evidence'])
            self.assertEqual(project(a),project(b));self.assertEqual(a['snapshot'],b['snapshot'])
            self.assertEqual(a['tool_calls'],5);self.assertEqual(b['tool_calls'],5)
            self.assertTrue(a['coverage_complete']);self.assertTrue(b['coverage_complete'])

    def test_empty_worker_cannot_claim_completion(self):
        def noop(part,ctx,gateway,deps):return {'part_id':part.id,'snapshot':ctx.digest,'evidence':[]}
        self.assertEqual(self.run_case(worker=noop)['parts']['r']['reason'],'no_tool_observation')

    def test_model_adapter_uses_role_tools_with_scripted_provider(self):
        from test_agent import Sequence,call,final
        factory=lambda role:Sequence(call('{}',name='search_knowledge'),final([{'evidence_id':E['id'],'quote':E['quote']}]))
        result=self.run_case(worker=model_worker(factory))
        self.assertTrue(result['coverage_complete']);self.assertEqual(result['tool_calls'],1)
        self.assertEqual(result['mode'],'provider_workers')

    def test_required_source_artifact_not_just_any_success(self):
        result=self.run_case([Part('a','rules',required_paths=('pricing.mjs',))])
        self.assertEqual(result['parts']['a']['reason'],'missing_required_source')
