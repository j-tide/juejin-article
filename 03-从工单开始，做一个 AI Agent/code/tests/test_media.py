import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from ticket_agent.domain import Scope
from ticket_agent.media import MediaReader,MediaError,sample_frames,observe,media_handler,MEDIA_TOOL
from ticket_agent.runtime import run_ticket
from test_agent import Sequence,call,final

SCOPE=Scope('demo-brand','store-001')
ASSET={'id':'test','observed_at':'2026-09-18T14:10:00+08:00','sha256':'test-digest','message_id':'m1'}


class MediaTests(unittest.TestCase):
    def test_sparse_sampling_can_miss_transient_notice(self):
        selected=sample_frames([i/10 for i in range(30)],1)
        self.assertEqual([t for i,t in selected],[0,1,2])
        self.assertFalse(any(1.1<=t<1.4 for i,t in selected))

    def test_local_refinement_contains_notice_frame(self):
        self.assertEqual(sample_frames([i/10 for i in range(30)],5,.8,1.6),[(8,.8),(10,1.0),(12,1.2),(14,1.4)])

    def test_sampling_budget_rejected_not_silently_truncated(self):
        with self.assertRaises(MediaError):sample_frames([i/10 for i in range(100)],10)

    def test_invalid_or_nonfinite_parameters_rejected(self):
        for fps in (0,-1,float('nan'),11):
            with self.subTest(fps=fps),self.assertRaises(ValueError):sample_frames([0,1],fps)

    def test_low_confidence_not_promoted_to_certain_observation(self):
        evidence=observe([{'text':'价格已更新','confidence':.5,'box':[.1,.2,.5,.1]}],ASSET,12,1.2)
        self.assertEqual(evidence[0]['certainty'],'uncertain_text')
        self.assertIsNone(evidence[0]['event_at'])
        self.assertEqual(evidence[0]['media_time_s'],1.2)

    def test_missing_or_cross_store_attachment_same_response(self):
        reader=MediaReader('/not-installed')
        self.assertEqual(reader.inspect('missing',SCOPE),reader.inspect('checkout',Scope('demo-brand','other')))

    def test_mutated_attachment_is_not_read(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d);(p/'image.png').write_bytes(b'changed')
            (p/'catalog.json').write_text(json.dumps({'assets':[{**ASSET,'file':'image.png','kind':'image','brand':SCOPE.brand,'store':SCOPE.store}]}))
            self.assertEqual(MediaReader('/missing',p).inspect('test',SCOPE)['status'],'hash_mismatch')

    def test_path_outside_asset_directory_is_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d);(p/'catalog.json').write_text(json.dumps({'assets':[{**ASSET,'file':'../private.png','kind':'image','brand':SCOPE.brand,'store':SCOPE.store}]}))
            self.assertEqual(MediaReader('/missing',p).inspect('test',SCOPE)['status'],'attachment_unavailable')

    def test_missing_ocr_dependency_is_visible(self):
        self.assertEqual(MediaReader('/not-installed').inspect('notice',SCOPE)['status'],'dependency_or_processing_failure')

    def test_text_not_found_does_not_prove_no_notice(self):
        class Empty:
            def inspect(self,*args):return {'status':'observed','evidence':[],'sampled_frames':3,'limitations':['unsampled intervals unknown']}
        result=media_handler(Empty(),'notice')({'query':'价格'},SCOPE)
        self.assertEqual(result['status'],'no_matching_text')
        self.assertIn('unsampled intervals unknown',result['limitations'])

    def test_agent_tool_preserves_uncertainty_and_source(self):
        evidence=observe([{'text':'价格已更新','confidence':.5,'box':[.1,.2,.5,.1]}],ASSET,12,1.2)
        class Reader:
            def inspect(self,*args):return {'status':'observed','evidence':evidence,'sampled_frames':4,'limitations':[]}
        provider=Sequence(call('{"query":"价格"}',name='inspect_media'),final([{'evidence_id':evidence[0]['id'],'quote':evidence[0]['quote']}]))
        result=run_ticket('回查提示',SCOPE,provider,None,tools=[MEDIA_TOOL],handlers={'inspect_media':media_handler(Reader(),'test')})
        self.assertEqual(result['status'],'needs_human')
        self.assertIn('uncertain_text',result['answer']['facts'][0]['quote'])

    @unittest.skipUnless(os.getenv('TICKET_OCR_BINARY'),'native OCR integration requires compiled Swift helper and FFmpeg')
    def test_native_video_ocr_reproduces_sampling_difference(self):
        reader=MediaReader(os.environ['TICKET_OCR_BINARY'])
        coarse=reader.inspect('checkout',SCOPE,1)
        dense=reader.inspect('checkout',SCOPE,5,.8,1.6)
        self.assertFalse(any('价格已更新' in e['text'] for e in coarse['evidence']))
        self.assertTrue(any('价格已更新' in e['text'] and e['media_time_s']==1.2 for e in dense['evidence']))
