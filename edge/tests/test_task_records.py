import contextlib,hashlib,json,sqlite3,tempfile,unittest
from pathlib import Path
from unittest.mock import patch,AsyncMock
from fastapi.testclient import TestClient
from spectrum_agent import task_records,common,api,ui_gateway
from spectrum_agent.common import DomainError,dump


class TaskRecordTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix='spectra_record_test_');self.root=Path(self.temp.name)
        self.stack=contextlib.ExitStack()
        for module in [task_records,common,ui_gateway]:self.stack.enter_context(patch.object(module,'ROOT',self.root))
        self.stack.enter_context(patch.object(api,'credentials',return_value=dict(api_token='offline-test-only')))
        self.tid='task_record_fixture';self.run=self.root/'runs'/self.tid;self.run.mkdir(parents=True)
        self.iq=self.root/'artifacts'/self.tid/'capture.cf32';self.iq.parent.mkdir(parents=True);self.iq.write_bytes(b'IQ-retention-fixture')
        self.digest=hashlib.sha256(self.iq.read_bytes()).hexdigest()
        dump(self.run/'outcome.json',dict(status='succeeded'))
        dump(self.run/'report.json',dict(captures=[dict(artifact_id='capture_fixture',metadata=dict(center_hz=2400e6,sample_rate_hz=200e6,requested_span_hz=100e6,simulated=False))]))
        self.database=self.root/'runs/tasks.sqlite'
        with contextlib.closing(sqlite3.connect(self.database)) as db:
            db.execute('CREATE TABLE tasks (id TEXT,request TEXT,status TEXT,created TEXT,idem TEXT)')
            db.execute('INSERT INTO tasks VALUES(?,?,?,?,?)',(self.tid,json.dumps(dict(source='usrp',constraints=dict(center_hz=2400e6,span_hz=100e6))),'succeeded','2026-10-26','record-fixture'));db.commit()
        self.client=TestClient(api.app);self.headers={'Authorization':'Bearer offline-test-only'}

    def tearDown(self):self.client.close();self.stack.close();self.temp.cleanup()

    def test_remove_restore_retains_iq_analysis_and_idempotency_row(self):
        original=(self.run/'report.json').read_bytes()
        result=self.client.delete('/v1/tasks/'+self.tid,headers=self.headers)
        self.assertEqual(result.status_code,200,result.text);self.assertTrue(result.json()['iq_retained'])
        first=(self.run/task_records.MARKER).read_bytes()
        self.client.delete('/v1/tasks/'+self.tid,headers=self.headers)
        self.assertEqual((self.run/task_records.MARKER).read_bytes(),first)
        self.assertEqual(hashlib.sha256(self.iq.read_bytes()).hexdigest(),self.digest)
        self.assertEqual((self.run/'report.json').read_bytes(),original)
        with contextlib.closing(sqlite3.connect(self.database)) as db:self.assertEqual(db.execute('SELECT status FROM tasks').fetchone()[0],'succeeded')
        self.assertEqual(self.client.post('/v1/tasks/'+self.tid+'/restore',headers=self.headers).status_code,200)
        self.assertFalse(task_records.removed(self.tid))
        self.assertEqual(self.client.post('/v1/tasks/'+self.tid+'/restore',headers=self.headers).status_code,200)

    def test_active_status_overrides_old_outcome_and_is_rejected(self):
        for status in ['queued','running','cancelling']:
            with contextlib.closing(sqlite3.connect(self.database)) as db:db.execute('UPDATE tasks SET status=?',(status,));db.commit()
            result=self.client.delete('/v1/tasks/'+self.tid,headers=self.headers)
            self.assertEqual(result.status_code,409);self.assertFalse(task_records.removed(self.tid))

    def test_history_omits_both_report_and_database_views_then_restores(self):
        self.assertEqual([t['id'] for t in ui_gateway.history()['tasks']],[self.tid])
        task_records.set_removed(self.tid)
        self.assertEqual(ui_gateway.history()['tasks'],[])
        task_records.set_removed(self.tid,False)
        self.assertEqual([t['id'] for t in ui_gateway.history()['tasks']],[self.tid])

    def test_legacy_terminal_record_without_queue_row_and_invalid_paths(self):
        with contextlib.closing(sqlite3.connect(self.database)) as db:db.execute('DELETE FROM tasks');db.commit()
        self.assertTrue(task_records.set_removed(self.tid)['record_removed'])
        with self.assertRaises(DomainError):task_records.set_removed('../outside')
        self.assertEqual(self.client.delete('/v1/tasks/missing_record',headers=self.headers).status_code,404)

    def test_gateway_delete_is_same_origin_only(self):
        with patch.object(ui_gateway,'proxy',new_callable=AsyncMock) as proxy:
            with TestClient(ui_gateway.app) as client:
                response=client.delete('/edge/tasks/'+self.tid,headers={'Origin':'https://unrelated.example'})
                self.assertEqual(response.status_code,403);proxy.assert_not_awaited()
