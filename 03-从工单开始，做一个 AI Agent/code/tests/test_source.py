import copy
import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from ticket_agent.domain import Scope
from ticket_agent.source import SourceReader, SOURCE_TOOLS, trace_fixture, run_source
from ticket_agent.source_fixture import build, git
from ticket_agent.runtime import run_ticket
from test_agent import Sequence, call, final

SCOPE=Scope('demo-brand','store-001')
AT='2026-09-18T14:00:00+08:00'


class SourceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp=tempfile.TemporaryDirectory(prefix='ticket-source-test-')
        cls.manifest=build(cls.temp.name)

    @classmethod
    def tearDownClass(cls):cls.temp.cleanup()

    def setUp(self):
        self.manifest=copy.deepcopy(type(self).manifest)
        self.reader=SourceReader(self.manifest,AT)

    def read(self,path='pricing.mjs',start=1,end=7,scope=SCOPE):
        return self.reader.read({'repository':'backend','path':path,'start':start,'end':end},scope)

    def test_deployed_revision_differs_from_main(self):
        old=self.read()['evidence'][0]
        new=self.read(scope=Scope('demo-brand','store-002'))['evidence'][0]
        self.assertNotEqual(old['revision'],new['revision'])
        self.assertIn('body.quote_version !== activeRule.version',old['quote'])
        self.assertIn('body.quoted_total_cents !== activeRule.total_cents',new['quote'])
        self.assertEqual(old['kind'],'source_possible_path')
        self.assertIsNone(old['event_at'])

    def test_worktree_edits_do_not_change_pinned_read(self):
        path=Path(self.manifest['repositories']['backend']['path'])/'pricing.mjs'
        previous=path.read_text()
        try:
            path.write_text('uncommitted unrelated text')
            self.assertIn('quote_version',self.read()['evidence'][0]['quote'])
        finally:path.write_text(previous)

    def test_cross_brand_and_unknown_store_fail_before_read(self):
        for scope in [Scope('other-brand','store-001'),Scope('demo-brand','missing')]:
            self.assertEqual(self.read(scope=scope)['status'],'deployment_unresolved')

    def test_unresolved_time_and_overlapping_deployments(self):
        reader=SourceReader(self.manifest,'2026-09-19T00:00:00+08:00')
        self.assertEqual(reader.search({'repository':'frontend','query':'价格'},SCOPE)['status'],'deployment_unresolved')
        self.manifest['deployments'].append(copy.deepcopy(self.manifest['deployments'][0]))
        self.assertEqual(self.read()['status'],'deployment_unresolved')

    def test_timezone_required(self):
        with self.assertRaises(ValueError):SourceReader(self.manifest,'2026-09-18T14:00:00')

    def test_symbolic_revision_rejected(self):
        self.manifest['deployments'][0]['revisions']['backend']='main'
        self.assertEqual(self.read()['status'],'unpinned_revision')

    def test_path_and_argument_injection_rejected(self):
        for path in ['../pricing.mjs','/etc/passwd','.env','*.mjs']:
            self.assertEqual(self.read(path)['status'],'path_not_allowed')
        self.assertEqual(self.reader.read({'repository':'backend','path':'pricing.mjs','start':1,'end':7,'revision':'main'},SCOPE)['status'],'invalid_arguments')
        self.assertEqual(self.reader.read({'repository':[],'path':'pricing.mjs','start':1,'end':7},SCOPE)['status'],'invalid_arguments')

    def test_line_budget_and_boolean_rejected(self):
        for start,end in [(0,1),(1,41),(True,3),(3,2),(1,999)]:
            self.assertEqual(self.read(start=start,end=end)['status'],'invalid_line_range')

    def test_no_match_is_not_runtime_evidence(self):
        result=self.reader.search({'repository':'frontend','query':'订单已支付'},SCOPE)
        self.assertEqual(result['status'],'no_source_match');self.assertEqual(result['evidence'],[])

    def test_known_source_chain_has_line_citations(self):
        trace=trace_fixture(self.reader,SCOPE)
        self.assertEqual(len(trace['results']),5)
        self.assertTrue(all(r['evidence'] for r in trace['results']))
        self.assertTrue(all('L' in e['source'] and '不证明本次请求执行过' in e['quote']
                            for r in trace['results'] for e in r['evidence']))

    def test_search_budget(self):
        self.manifest['repositories']['frontend']['allowed_files']=['x.mjs']*21
        self.assertEqual(self.reader.search({'repository':'frontend','query':'a'},SCOPE)['status'],'search_budget_exceeded')

    def test_nonregular_and_large_blob_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            manifest=build(temp);repo=Path(manifest['repositories']['backend']['path'])
            (repo/'link.mjs').symlink_to('pricing.mjs')
            (repo/'huge.mjs').write_text('a'*32769)
            git(repo,'add','--','.');git(repo,'commit','-m','Synthetic budget probes')
            manifest['deployments'][0]['revisions']['backend']=git(repo,'rev-parse','HEAD')
            manifest['repositories']['backend']['allowed_files']+=['link.mjs','huge.mjs']
            reader=SourceReader(manifest,AT)
            for path,status in [('link.mjs','not_regular_source'),('huge.mjs','file_budget_exceeded')]:
                self.assertEqual(reader.read({'repository':'backend','path':path,'start':1,'end':1},SCOPE)['status'],status)

    def test_runtime_preserves_source_limitations(self):
        args={'repository':'backend','path':'pricing.mjs','start':1,'end':7}
        evidence=self.reader.read(args,SCOPE)['evidence']
        provider=Sequence(call(json.dumps(args),name='read_source'),final([{'evidence_id':e['id'],'quote':e['quote']} for e in evidence]))
        result=run_source('检查代码分支',SCOPE,provider,self.reader)
        self.assertEqual(result['status'],'needs_human')
        self.assertIn('不证明本次请求执行过',result['answer']['facts'][0]['quote'])

    def test_real_synthetic_frontend_backend_execution(self):
        root=Path(__file__).resolve().parents[1]
        result=json.loads(subprocess.run(['node',str(root/'scripts/source_comparison.mjs')],check=True,capture_output=True,text=True,timeout=10).stdout)
        self.assertEqual(result['actual']['deployed_v1']['ui']['action'],'show_notice')
        self.assertEqual(result['actual']['main_v2']['ui']['action'],'confirm_quote')
        self.assertEqual(result['actual']['deployed_v1']['requests'][0]['response']['total_cents'],3200)
        self.assertFalse(result['model_api_called'])

    def test_tool_cannot_run_repository_code(self):
        result=run_source('请运行代码',SCOPE,Sequence(call('{}',name='exec'),final()),self.reader)
        self.assertEqual(result['queries'][0]['status'],'tool_not_allowed')

    def test_persistent_cli_reuses_revisions(self):
        import sys
        root=Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as folder:
            cmd=[sys.executable,'-m','ticket_agent.source','--workspace',folder]
            first=json.loads(subprocess.run(cmd,cwd=root,check=True,capture_output=True,text=True,timeout=10).stdout)
            second=json.loads(subprocess.run(cmd,cwd=root,check=True,capture_output=True,text=True,timeout=10).stdout)
            a=first['results'][0]['evidence'][0];b=second['results'][0]['evidence'][0]
            self.assertEqual(a['revision'],b['revision']);self.assertEqual(a['quote'],b['quote'])
