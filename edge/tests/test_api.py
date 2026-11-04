import json
import unittest
import uuid
import tempfile
from unittest.mock import patch
from fastapi.testclient import TestClient
from spectrum_agent.api import app
from spectrum_agent.service import credentials,normalize,TaskStore


class APITests(unittest.TestCase):
    def setUp(self):
        self.database=tempfile.TemporaryDirectory(prefix='spectrum-api-test-')
        self.store_patch=patch('spectrum_agent.api.TaskStore',lambda:TaskStore(self.database.name+'/tasks.sqlite'))
        self.store_patch.start()
        self.patch=patch("spectrum_agent.service.TaskStore.loop",lambda self,stop:stop.wait(5))
        self.patch.start()
        self.client=TestClient(app)
        self.client.__enter__()
        self.headers={"Authorization":"Bearer "+credentials()["api_token"]}

    def tearDown(self):
        # Test-created queued tasks are cancelled; no RF or paid model call is started.
        for tid in getattr(self,"tasks",[]):
            self.client.post(f"/v1/tasks/{tid}/cancel",headers=self.headers)
        self.client.__exit__(None,None,None)
        self.patch.stop()
        self.store_patch.stop()
        self.database.cleanup()

    def test_auth_and_no_ui(self):
        self.assertEqual(self.client.get("/healthz").status_code,200)
        self.assertEqual(self.client.get("/docs").status_code,404)
        self.assertEqual(self.client.post("/v1/tasks",json={}).status_code,401)

    def test_capabilities_distinguish_classic_candidate_from_full_decoder(self):
        response=self.client.get('/v1/capabilities',headers=self.headers)
        self.assertEqual(response.status_code,200)
        value=response.json()
        self.assertIn('analyze_bluetooth_classic',value['tools'])
        self.assertEqual(len(value['tools']),30)
        self.assertEqual(value['protocol_plugins']['bluetooth_classic_full_decoder'],'not_validated')
        self.assertTrue(value['continuation']['supported'])

    def test_idempotency_conflict_and_cancel(self):
        key="api-test-"+uuid.uuid4().hex
        body=dict(prompt="中心2.4GHz，100MHz带宽内有什么信号",source="synthetic",idempotency_key=key)
        first=self.client.post("/v1/tasks",json=body,headers=self.headers)
        self.assertEqual(first.status_code,202,first.text)
        tid=first.json()["task_id"];self.tasks=[tid]
        duplicate=self.client.post("/v1/tasks",json=body,headers=self.headers)
        self.assertEqual(tid,duplicate.json()["task_id"])
        changed=self.client.post("/v1/tasks",json=dict(body,prompt="changed"),headers=self.headers)
        self.assertEqual(changed.status_code,409) # prompt interpretation is deferred; idempotency still rejects changed input
        body["constraints"]={"center_hz":2400000000,"span_hz":50000000}
        conflict=self.client.post("/v1/tasks",json=body,headers=self.headers)
        self.assertEqual(conflict.status_code,409)
        cancelled=self.client.post(f"/v1/tasks/{tid}/cancel",headers=self.headers)
        self.assertEqual(cancelled.json()["status"],"cancelled")
        self.assertEqual(self.client.get(f"/v1/tasks/{tid}/artifacts/no_such_id",headers=self.headers).status_code,422)

    def test_invalid_and_ambiguous_requests(self):
        pending=normalize({"prompt":"Please inspect spectrum"})
        self.assertEqual(pending['task_intent']['status'],'pending_model')
        with self.assertRaisesRegex(Exception,"outside allowed"):
            normalize(dict(prompt="test",constraints=dict(center_hz=2400000000,span_hz=500000000)))
        normalized=normalize({"prompt":"2.4GHz,100MHz"})
        self.assertEqual(normalized["constraints"]["center_hz"],2400000000)
        self.assertEqual(normalized["constraints"]["span_hz"],100000000)
