import concurrent.futures
import copy
import tempfile
import unittest
from pathlib import Path
from ticket_agent.work_queue import WorkQueue,QueueError,incident_key,replay


def job(topic='topic-a',tenant='tenant-a',store='store-1',priority=0,created=1000,**extra):
    return {'tenant':tenant,'brand':'demo-brand','store':store,'topic':topic,'version':1,'created':created,'priority':priority,
            'service':'checkout','stage':'paid','error_code':'TIMEOUT','deployment':'v1','campaign':'a',**extra}


class QueueTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.path=Path(self.temp.name)/'queue.sqlite3';self.queue=WorkQueue(self.path)
    def tearDown(self):self.temp.cleanup()

    def test_group_order_priority(self):
        self.queue.enqueue(job());self.queue.enqueue(job('group',priority=30))
        self.assertEqual(self.queue.claim('w',1001)['topic'],'group')

    def test_aging_prevents_permanent_low_priority(self):
        self.queue.enqueue(job('old',created=0));self.queue.enqueue(job('fresh',priority=30,created=2000))
        self.assertEqual(self.queue.claim('w',2000)['topic'],'old')

    def test_idempotency_and_conflict(self):
        a=self.queue.enqueue(job());self.assertEqual(a,self.queue.enqueue(job()))
        with self.assertRaisesRegex(QueueError,'idempotency_conflict'):self.queue.enqueue(job(priority=1))

    def test_queue_capacity_and_replaceable_version(self):
        q=WorkQueue(self.path,capacity=1);q.enqueue(job())
        with self.assertRaisesRegex(QueueError,'queue_full'):q.enqueue(job('other'))
        q.enqueue(job(version=2));self.assertEqual(q.claim('w',1001)['version'],2)

    def test_global_and_tenant_limits(self):
        for x in [job('a'),job('b'),job('c',tenant='tenant-b'),job('d',tenant='tenant-c')]:self.queue.enqueue(x)
        a=self.queue.claim('w1',1001);b=self.queue.claim('w2',1001)
        self.assertNotEqual(a['tenant'],b['tenant']);self.assertIsNone(self.queue.claim('w3',1001))

    def test_concurrent_claims_do_not_duplicate(self):
        for n in range(6):self.queue.enqueue(job(str(n),tenant='t'+str(n)))
        with concurrent.futures.ThreadPoolExecutor(max_workers=6) as pool:
            claims=list(pool.map(lambda n:WorkQueue(self.path).claim('w'+str(n),1001),range(6)))
        ids=[c['id'] for c in claims if c];self.assertEqual(len(ids),2);self.assertEqual(len(set(ids)),2)

    def test_expired_worker_holds_slot_until_confirmed_stopped(self):
        q=WorkQueue(self.path,global_slots=1);q.enqueue(job('a'));q.enqueue(job('b'))
        a=q.claim('w',1000)
        self.assertIsNone(q.claim('other',1031))
        self.assertEqual(q.snapshot()['jobs'][0]['state'],'uncertain')
        with self.assertRaisesRegex(QueueError,'confirmed_stop_required'):q.resolve_uncertain(a['id'],1,False,now=1031)

    def test_fencing_rejects_late_completion_after_retry(self):
        self.queue.enqueue(job());a=self.queue.claim('old',1000)
        self.queue.resolve_uncertain(a['id'],1,True,now=1031)
        b=self.queue.claim('new',1032);self.assertEqual(b['generation'],2)
        self.assertEqual(self.queue.complete(a['id'],'old',1,{},1033),'stale_lease')
        self.assertEqual(self.queue.complete(b['id'],'new',2,{},1034),'done')

    def test_expired_completion_is_not_accepted(self):
        self.queue.enqueue(job());a=self.queue.claim('w',1000)
        self.assertEqual(self.queue.complete(a['id'],'w',1,{},1031),'stale_lease')

    def test_new_input_makes_running_result_stale(self):
        self.queue.enqueue(job());a=self.queue.claim('w',1000);self.queue.enqueue(job(version=2))
        self.assertEqual(self.queue.complete(a['id'],'w',1,{},1001),'stale_input')

    def test_old_version_rejected(self):
        self.queue.enqueue(job(version=2))
        with self.assertRaisesRegex(QueueError,'old_input_version'):self.queue.enqueue(job())

    def test_candidate_group_respects_stage_version_and_tenant(self):
        a=job();b=job('b',store='store-2')
        self.assertEqual(incident_key(a),incident_key(b))
        for changed in [job(stage='unpaid'),job(deployment='v2'),job(tenant='other'),job(created=1300)]:self.assertNotEqual(incident_key(a),incident_key(changed))
        self.assertIsNone(incident_key(job(stage='unknown')))

    def test_candidate_is_not_confirmed_incident(self):
        self.queue.enqueue(job());self.queue.enqueue(job('b',store='store-2'))
        result=self.queue.candidates('tenant-a','demo-brand')
        self.assertEqual(len(result),1);self.assertFalse(result[0]['confirmed_incident'])
        self.assertEqual(self.queue.candidates('other','demo-brand'),[])

    def test_latest_version_only_in_groups(self):
        self.queue.enqueue(job());self.queue.enqueue(job('b',store='store-2'))
        self.queue.enqueue(job(version=2,stage='unpaid'))
        self.assertEqual(self.queue.candidates('tenant-a','demo-brand'),[])

    def test_restart_preserves_ready_and_running(self):
        self.queue.enqueue(job());self.queue.claim('w',1000)
        self.assertEqual(WorkQueue(self.path).snapshot(),self.queue.snapshot())

    def test_unknown_tokens_not_zero(self):
        self.queue.enqueue(job());a=self.queue.claim('w',1001);self.queue.complete(a['id'],'w',1,{'usage':None},1003)
        event=self.queue.snapshot()['events'][-1]
        self.assertIsNone(event['detail']['prompt_tokens']);self.assertEqual(event['detail']['run_ms'],2000)

    def test_actual_worker_adapter(self):
        import time
        self.queue.enqueue(job(created=time.time()))
        value=self.queue.execute_one(lambda j:{'status':'needs_human','tool_calls':1})
        self.assertEqual(value['disposition'],'done');self.assertGreaterEqual(value['result']['queue_worker_elapsed_ms'],0)

    def test_retry_budget(self):
        self.queue.enqueue(job());a=self.queue.claim('w',1000)
        for generation in range(1,4):
            state=self.queue.resolve_uncertain(a['id'],generation,True,now=1000+generation*31)
            if generation<3:a=self.queue.claim('w',1000+generation*31)
        self.assertEqual(state,'failed')

    def test_invalid_worker_result_is_recorded_as_failure(self):
        import time
        self.queue.enqueue(job(created=time.time()))
        result=self.queue.execute_one(lambda j:None)
        self.assertEqual(result['disposition'],'failed')
        self.assertEqual(result['result']['status'],'worker_failed')

    def test_worker_exception_does_not_leave_running_job(self):
        import time
        self.queue.enqueue(job(created=time.time()))
        def broken(j):raise RuntimeError('private diagnostic')
        result=self.queue.execute_one(broken)
        self.assertEqual(result['disposition'],'failed')
        self.assertNotIn('private diagnostic',str(result))

    def test_existing_tool_loop_runs_through_queue(self):
        from ticket_agent.queue_experiment import experiment
        r=experiment()
        self.assertEqual(r['scheduling']['dispatch_order'][0],'group-order')
        self.assertEqual(len(r['scheduling']['candidate_groups']),1)
        self.assertEqual(r['actual_worker']['status'],'needs_human')
        self.assertEqual(r['actual_worker']['evidence_count'],4)
        self.assertIsNone(r['actual_worker']['usage'])
