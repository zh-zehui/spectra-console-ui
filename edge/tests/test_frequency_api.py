"""Offline API tests; worker loop and HTTP proxy are mocked, never RF/model calls."""
import json,tempfile,unittest,uuid,gc
from unittest.mock import patch,AsyncMock
from fastapi.testclient import TestClient
from spectrum_agent import api,ui_gateway
from spectrum_agent.common import dump
from spectrum_agent.service import TaskStore
import test_frequency_scope as fixtures


class FrequencyAPITests(unittest.TestCase):
    def setUp(self):
        self.fixture=fixtures.FrequencyScopeTests();self.fixture.setUp();self.cap=self.fixture.fixture()
        self.source=self.fixture.ctx;dump(self.source.run/'outcome.json',dict(status='succeeded'))

    def test_recording_metadata_and_expanded_replay_request_without_worker(self):
        with tempfile.TemporaryDirectory(prefix='spectra_scope_api_') as directory:
            with patch.object(api,'credentials',return_value=dict(api_token='offline-test-only')),patch.object(api,'TaskStore',lambda:TaskStore(directory+'/tasks.sqlite')),patch.object(TaskStore,'loop',lambda self,stop:stop.wait()):
                with TestClient(api.app) as client:
                    headers={'Authorization':'Bearer offline-test-only'}
                    info=client.get('/v1/tasks/'+self.source.task_id+'/recording',headers=headers)
                    self.assertEqual(info.status_code,200,info.text)
                    self.assertEqual(info.json()['frequency_scope']['frontend_nominal_bandwidth_hz'],160e6)
                    body=dict(prompt='Offline replay',source='synthetic',replay_from_task_id=self.source.task_id,
                        constraints=dict(center_hz=2400e6,span_hz=160e6),idempotency_key=uuid.uuid4().hex)
                    result=client.post('/v1/tasks',json=body,headers=headers)
                    self.assertEqual(result.status_code,202,result.text)
                    self.assertEqual(result.json()['request']['constraints']['span_hz'],160e6)
                    self.assertEqual(result.json()['request']['recording_capture_id'],self.cap)
                    prompt_body=dict(body,prompt='分析范围2320-2480，分析时间上限30分钟',
                        constraints=dict(center_hz=2400e6,span_hz=100e6),idempotency_key=uuid.uuid4().hex)
                    from_prompt=client.post('/v1/tasks',json=prompt_body,headers=headers)
                    self.assertEqual(from_prompt.status_code,202,from_prompt.text)
                    effective=from_prompt.json()['request']
                    self.assertEqual(effective['constraints']['span_hz'],100e6)
                    self.assertEqual(effective['task_intent']['status'],'pending_model')
                    fresh=dict(prompt='中心频率2.4G，带宽160M、采样时间100ms、采样频率200M',source='usrp',
                        constraints=dict(center_hz=2450e6,span_hz=50e6,observation_s=.05,sample_rate_hz=100e6),idempotency_key=uuid.uuid4().hex)
                    fresh_result=client.post('/v1/tasks',json=fresh,headers=headers)
                    self.assertEqual(fresh_result.status_code,202,fresh_result.text)
                    c=fresh_result.json()['request']['constraints']
                    self.assertEqual([c[k] for k in ['center_hz','span_hz','observation_s','sample_rate_hz']],[2450e6,50e6,.05,100e6])
                    changed_recording=dict(body,prompt='中心频率2.4G，带宽160M，采样时间100ms',idempotency_key=uuid.uuid4().hex)
                    self.assertEqual(client.post('/v1/tasks',json=changed_recording,headers=headers).status_code,202)
                    bad=dict(body,constraints=dict(center_hz=2380e6,span_hz=160e6),idempotency_key=uuid.uuid4().hex)
                    rejected=client.post('/v1/tasks',json=bad,headers=headers)
                    self.assertEqual(rejected.status_code,422)
                    self.assertEqual(rejected.json()['detail']['error_code'],'ANALYSIS_OUT_OF_BAND')
            gc.collect()  # sqlite context managers commit; Windows needs cyclic handles released before temp cleanup.

    def test_recording_metadata_gateway_is_reachable_before_static_mount(self):
        with patch.object(ui_gateway,'proxy',new_callable=AsyncMock,return_value=dict(ok=True)) as proxy:
            with TestClient(ui_gateway.app) as client:
                response=client.get('/edge/tasks/'+self.source.task_id+'/recording')
                self.assertEqual(response.status_code,200,response.text);proxy.assert_awaited_once()
