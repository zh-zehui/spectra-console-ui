import json,unittest,uuid
from unittest.mock import patch
from pathlib import Path
from spectrum_agent.common import Context,DomainError,dump
from spectrum_agent.service import normalize
from spectrum_agent.task_intent import resolve
from spectrum_agent.worker import execute,report
from spectrum_agent.visualization import plot
from spectrum_agent.ui_gateway import visual


class PlotReplayTests(unittest.TestCase):
    def setUp(self):
        self.source=Context('plot_source_'+uuid.uuid4().hex[:10])
        self.target=Context('plot_target_'+uuid.uuid4().hex[:10])
        aid,p=self.source.artifact('capture','cf32');p.write_bytes(b'\0'*80000)
        meta=p.with_suffix('.json')
        dump(meta,dict(sample_rate_hz=4e6,center_hz=2402e6,requested_span_hz=3.2e6,
            sample_count=10000,duration_s=.0025,complete=True,simulated=False,
            segments=[dict(file_start=0,count=10000)]))
        self.source.register(aid,p,kind='capture',metadata_path=str(meta));self.cap=aid
        dump(self.source.run/'request.json',dict(constraints=dict(center_hz=2402e6,span_hz=3.2e6)))
        dump(self.source.run/'outcome.json',dict(status='succeeded'))
        self.prompt=self.source.task_id+'，根据这个task采集的数据，不分析，只画出时频图'
        dump(self.target.run/'request.json',normalize(dict(prompt=self.prompt)))

    def resolve(self):
        return resolve(self.target,evidence_quotes=[self.prompt],reason='Reuse explicitly named task IQ and only render the waterfall.',
            source_task_id=self.source.task_id,analysis_requested=False,visualization_requested=True,acquisition_requested=False)

    def test_saved_iq_plot_in_existing_web_control_without_detection(self):
        self.resolve()
        req=json.loads((self.target.run/'request.json').read_text())
        self.assertEqual(req['constraints']['center_hz'],2402e6)
        self.assertEqual(req['constraints']['sample_rate_hz'],4e6)
        self.assertEqual(Path(self.target.resolve(self.cap)['path']).stat().st_ino,
            Path(self.source.resolve(self.cap)['path']).stat().st_ino)
        with self.assertRaisesRegex(DomainError,'plot_spectrogram'):report(self.target)
        plot(self.target,self.cap);report(self.target,stop_reason='other',stop_detail='Only requested STFT plotting; no recognition requested.')
        result=visual(self.target.task_id)
        self.assertEqual(result['visual']['rows'],192)
        self.assertEqual(len(result['visual']['db']),192)
        self.assertEqual(result['signals'],[])
        self.assertEqual(result['report']['segmentations'],[])
        self.assertEqual(result['report']['verification_attempts'],[])
        with patch('spectrum_agent.acquisition.capture') as capture:
            with self.assertRaises(DomainError):execute(self.target.task_id,'capture_iq',{})
            capture.assert_not_called()

    def test_missing_source_never_falls_back_to_capture(self):
        (self.source.run/'outcome.json').unlink()
        with self.assertRaises(DomainError):self.resolve()
        req=json.loads((self.target.run/'request.json').read_text())
        self.assertTrue(req['rf_capture_forbidden'])
        # Even if another intent is subsequently resolved, the persisted prohibition survives.
        req['task_intent']['status']='resolved';dump(self.target.run/'request.json',req)
        with patch('spectrum_agent.acquisition.capture') as capture:
            with self.assertRaisesRegex(DomainError,'forbids new acquisition'):
                execute(self.target.task_id,'capture_iq',{})
            capture.assert_not_called()
