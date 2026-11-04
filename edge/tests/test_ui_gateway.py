import json
import unittest
import uuid
from unittest.mock import AsyncMock,patch
import numpy as np
from fastapi.testclient import TestClient
from spectrum_agent.common import Context,dump
from spectrum_agent.ui_gateway import app


class GatewayTests(unittest.TestCase):
    def test_agile_display_preserves_each_frequency_width_and_fixed_evidence(self):
        from spectrum_agent.ui_gateway import drone_display_evidence
        fixed=dict(evidence_id='fixed',profile=dict(zc_length=293),references=[dict(center_hz=5803.5e6)])
        report=dict(drone_video_evidence=[fixed],drone_hopping_evidence=[dict(evidence_id='agile',profiles={
            'narrow':dict(nominal_channel_bandwidth_hz=10e6),'wide':dict(nominal_channel_bandwidth_hz=40e6)},
            references=[dict(profile_id='narrow',center_hz=5750e6),dict(profile_id='wide',center_hz=5820e6),dict(profile_id='narrow',center_hz=5780e6)])])
        before=json.dumps(report,sort_keys=True);rows=drone_display_evidence(report)
        self.assertIs(rows[0],fixed)
        self.assertEqual([r['center_hz'] for r in rows[1]['references']],[5750e6,5780e6])
        self.assertEqual(rows[2]['profile']['nominal_channel_bandwidth_hz'],40e6)
        self.assertEqual(rows[2]['references'][0]['center_hz'],5820e6)
        self.assertEqual(len({r['evidence_id'] for r in rows}),3)
        self.assertEqual(before,json.dumps(report,sort_keys=True))

    def setUp(self): self.client=TestClient(app)

    def test_recording_only_view_never_computes_waterfall(self):
        ctx=Context('record_only_view_'+uuid.uuid4().hex[:10])
        aid,path=ctx.artifact('capture','cf32');np.zeros(64,np.complex64).tofile(path)
        meta=dict(center_hz=5800e6,sample_rate_hz=200e6,requested_span_hz=160e6,
            sample_count=64,complete=True,simulated=False,frontend_nominal_bandwidth_hz=160e6,
            segments=[dict(file_start=0,count=64,hardware_start_s=None)])
        dump(path.with_suffix('.json'),meta);ctx.register(aid,path,kind='capture',metadata_path=str(path.with_suffix('.json')))
        dump(ctx.run/'report.json',dict(captures=[dict(artifact_id=aid,metadata=meta)],task_intent=dict(analysis_requested=False)))
        with patch('spectrum_agent.ui_gateway.waterfall',side_effect=AssertionError('No STFT for recording-only')):
            response=self.client.get('/edge/tasks/'+ctx.task_id+'/visual')
        self.assertEqual(response.status_code,200,response.text)
        self.assertTrue(response.json()['capture_only'])
        self.assertIsNone(response.json()['visual'])

    def test_cross_origin_cannot_submit_job(self):
        with patch('spectrum_agent.ui_gateway.proxy',new_callable=AsyncMock) as proxy:
            result=self.client.post('/edge/tasks',json={'source':'usrp'},headers={'Origin':'https://unrelated.example'})
            self.assertEqual(result.status_code,403)
            proxy.assert_not_awaited()

    def test_no_synthetic_task_submission_or_path_escape(self):
        self.assertEqual(self.client.post('/edge/tasks',json={'source':'synthetic'}).status_code,422)
        self.assertEqual(self.client.get('/edge/tasks/bad%20id/visual').status_code,422)
        self.assertEqual(self.client.get('/docs').status_code,404)

    def test_real_task_proxy_and_no_browser_api_key(self):
        with patch('spectrum_agent.ui_gateway.proxy',new_callable=AsyncMock,return_value={'task_id':'task_fixture','status':'queued'}) as proxy:
            result=self.client.post('/edge/tasks',json={'source':'usrp','prompt':'fixture','idempotency_key':'fixture'})
            self.assertEqual(result.status_code,202)
            self.assertEqual(result.json()['task_id'],'task_fixture')
            proxy.assert_awaited_once()
        html=self.client.get('/').text
        self.assertIn('SPECTRA',html)
        self.assertNotIn('deepseek_api_key',html)

    def test_visual_bounds_unknown_labels_and_cache(self):
        ctx=Context('ui_test_'+uuid.uuid4().hex[:10])
        rng=np.random.default_rng(62);iq=(rng.normal(size=32768)+1j*rng.normal(size=32768)).astype('<c8')
        aid,path=ctx.artifact('fixture','cf32');iq.tofile(path)
        meta=dict(center_hz=2400e6,sample_rate_hz=4e6,requested_span_hz=3.2e6,sample_count=len(iq),duration_s=len(iq)/4e6,
            simulated=False,segments=[dict(file_start=0,count=len(iq),hardware_start_s=0)],gaps=[],overflow_count=0)
        dump(path.with_suffix('.json'),meta);ctx.register(aid,path,kind='capture',metadata_path=str(path.with_suffix('.json')))
        group=dict(group_id='g0',freq_low_hz=2399.5e6,freq_high_hz=2400.5e6,sample_start=1000,sample_stop=5000,snr_db=5)
        dump(ctx.run/'report.json',dict(captures=[dict(artifact_id=aid,metadata=meta)],signal_groups=[group],verified_protocols=[],frame_associations=[],scene_refinements=[]))
        result=self.client.get('/edge/tasks/'+ctx.task_id+'/visual');self.assertEqual(result.status_code,200)
        value=result.json();self.assertEqual(value['protocol_count'],0)
        self.assertEqual(value['signals'][0]['status'],'unknown')
        self.assertEqual(value['visual']['low_mhz'],2398)
        self.assertEqual(value['visual']['columns'],512)
        self.assertEqual(len(value['visual']['db']),192)
        self.assertTrue((ctx.run/'ui-waterfall-v1.json').exists())
        self.assertEqual(self.client.get('/edge/tasks/'+ctx.task_id+'/visual').status_code,200)
        detail=self.client.get('/edge/tasks/'+ctx.task_id+'/waterfall-window?start_ms=0&end_ms=4')
        self.assertEqual(detail.status_code,200)
        self.assertEqual(detail.json()['source'],'recorded-iq-window-stft')
        self.assertGreater(detail.json()['high_time_percent'],detail.json()['low_time_percent'])
        self.assertEqual(self.client.get('/edge/tasks/'+ctx.task_id+'/waterfall-window?start_ms=0&end_ms=25').status_code,422)
        meta['simulated']=True
        dump(ctx.run/'report.json',dict(captures=[dict(artifact_id=aid,metadata=meta)]))
        self.assertEqual(self.client.get('/edge/tasks/'+ctx.task_id+'/visual').status_code,422)
