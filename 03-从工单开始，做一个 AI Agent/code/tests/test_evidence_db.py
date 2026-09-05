import hashlib
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from ticket_agent.evidence_db import SqlOrderReader,initialize,readonly
from ticket_agent.domain import Scope
from ticket_agent.providers import Replay
from ticket_agent.runtime import run_ticket,render

SCOPE=Scope('demo-brand','store-001')
START='2026-09-18T14:00:00+08:00';END='2026-09-18T14:00:15+08:00'


class DatabaseTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.path=Path(self.temp.name)/'evidence.sqlite3'
        initialize(self.path)
    def tearDown(self):self.temp.cleanup()
    def reader(self,as_of='2026-09-18T14:00:30+08:00',**kw):return SqlOrderReader(self.path,START,END,as_of,**kw)
    def lookup(self,reference='P1001',**kw):return self.reader(**kw).lookup({'reference':reference},SCOPE)

    def test_same_snapshot_different_history(self):
        a=self.lookup();b=self.lookup('P1002')
        self.assertEqual(a['evidence'][0]['quote'],b['evidence'][0]['quote'])
        self.assertFalse(a['print_receipt_observed']);self.assertTrue(b['print_receipt_observed'])
        self.assertIn('DEVICE_OFFLINE',[x.get('kind') for x in b['evidence']])

    def test_late_ingestion_not_visible_before_asof(self):
        early=self.lookup('P1002',as_of=END);late=self.lookup('P1002')
        self.assertFalse(early['print_receipt_observed']);self.assertTrue(late['print_receipt_observed'])
        ack=next(e for e in late['evidence'] if e.get('kind')=='PRINT_ACK')
        self.assertLess(ack['event_at'],ack['ingested_at'])

    def test_payment_reference_and_order_reference_agree(self):
        a=self.lookup('P1001');b=self.lookup('O1001')
        self.assertEqual([e['id'] for e in a['evidence']],[e['id'] for e in b['evidence']])

    def test_scope_is_mandatory(self):
        for ref,scope in [('P2001',SCOPE),('P1001',Scope('demo-brand','store-002'))]:
            self.assertEqual(self.reader().lookup({'reference':ref},scope)['status'],'not_observed')

    def test_sql_and_scope_injection_rejected(self):
        for args in [{'reference':"P1001' OR 1=1"},{'reference':'P1001','store':'store-002'},{'sql':'SELECT * FROM orders'}]:
            self.assertEqual(self.reader().lookup(args,SCOPE)['status'],'invalid_arguments')

    def test_readonly_does_not_modify_bytes(self):
        before=hashlib.sha256(self.path.read_bytes()).hexdigest()
        self.lookup();self.lookup('P1002')
        self.assertEqual(before,hashlib.sha256(self.path.read_bytes()).hexdigest())

    def test_write_and_private_columns_denied(self):
        for statement in ['DELETE FROM orders','SELECT customer_phone FROM orders','PRAGMA query_only=OFF',"ATTACH DATABASE ':memory:' AS x"]:
            with self.subTest(statement=statement),readonly(self.path) as db:
                with self.assertRaises(sqlite3.DatabaseError):db.execute(statement)

    def test_window_budgets_and_timezone(self):
        for start,end,as_of in [(START,'2026-09-18T15:00:00+08:00','2026-09-18T16:00:00+08:00'),(END,START,END),(START,END,START),('2026-09-18T14:00:00',END,END)]:
            with self.assertRaises(ValueError):SqlOrderReader(self.path,start,end,as_of)

    def test_row_limit_is_explicit(self):
        result=self.lookup('P1002',row_limit=2)
        self.assertTrue(result['truncated']);self.assertFalse(result['print_receipt_observed'])
        self.assertIn('结果截断=True',result['evidence'][-1]['quote'])

    def test_watermark_not_completeness_claim(self):
        result=self.lookup('P1002')
        self.assertTrue(result['replica_lag_possible'])
        self.assertIn('未证明日志完整',result['evidence'][-1]['quote'])

    def test_missing_database_does_not_create_one(self):
        self.path.unlink()
        self.assertEqual(self.lookup()['status'],'query_unavailable')
        self.assertFalse(self.path.exists())

    def test_progress_handler_interrupts_query(self):
        with readonly(self.path,seconds=0) as db:
            with self.assertRaises(sqlite3.OperationalError) as error:
                db.execute('SELECT a.event_id,b.event_id,c.event_id FROM events a,events b,events c ORDER BY c.happened').fetchall()
            self.assertIn('interrupted',str(error.exception))

    def test_agent_output_keeps_missing_receipt_unknown(self):
        result=run_ticket('核对 P1001',SCOPE,Replay(),self.reader())
        self.assertEqual(result['status'],'needs_human')
        self.assertIn('不证明门店已打印',render(result));self.assertIn('未查到不能证明没有发生',render(result))

    def test_demo_initializer_never_overwrites(self):
        with self.assertRaises(ValueError):initialize(self.path)
