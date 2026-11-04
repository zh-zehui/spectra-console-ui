import json
import unittest
import uuid
from pathlib import Path
from spectrum_agent.common import Context,DomainError,dump
from spectrum_agent import continuation,investigation,analysis_budget
from spectrum_agent.service import normalize as _normalize,dsh_prompt

# These fixtures exercise replay after model interpretation, not prompt parsing.
def normalize(request):return _normalize(request,model_resolved=True)


class ContinuationTests(unittest.TestCase):
    def setUp(self):
        self.source=Context('continue_source_'+uuid.uuid4().hex[:10])
        self.target=Context('continue_target_'+uuid.uuid4().hex[:10])
        aid,path=self.source.artifact('capture','cf32');path.write_bytes(b'\0'*80000)
        meta=path.with_suffix('.json');dump(meta,dict(sample_rate_hz=4e6,center_hz=2402e6,requested_span_hz=4e6,
            sample_count=10000,duration_s=.0025,simulated=False,complete=True,segments=[dict(file_start=0,count=10000)]))
        self.source.register(aid,path,kind='capture',metadata_path=str(meta));self.cap=aid
        self.g=dict(group_id='g0',sample_start=0,sample_stop=10000,freq_low_hz=2401e6,freq_high_hz=2403e6,
            bandwidth_hz=2e6,duration_s=.0025,protocol='unknown',snr_db=10)
        self.base=self.source.json_artifact('segmentation',dict(capture_id=aid,segmentation_level='coarse_activity',signal_groups=[self.g],events=[self.g],fft_size=512,frequency_resolution_hz=1953.125))
        self.ref=self.source.json_artifact('scene_refinement',dict(parent_segmentation_id=self.base,parent_group_id='g0',capture_id=aid,
            observed_parent_group=self.g,requested_parent_interval=[1000,2000],fine_target_frequency_hz=[2401e6,2402e6]))
        dump(self.source.run/'report.json',dict(initial_segmentation_id=self.base))
        dump(self.source.run/'outcome.json',dict(status='succeeded'))

    def request(self):return normalize(dict(prompt='Continue unexplained activity',continue_from_task_id=self.source.task_id))

    def test_fresh_replay_imports_only_immutable_iq_not_prior_analysis(self):
        req=normalize(dict(prompt='Analyze saved IQ from scratch',replay_from_task_id=self.source.task_id))
        self.assertEqual(req['recording_capture_id'],self.cap)
        self.assertNotIn('continue_from_task_id',req)
        self.assertNotIn('continuation_base_id',req)
        dump(self.target.run/'request.json',req)
        result=continuation.attach_replay(self.target,req)
        self.assertFalse(result['new_rf_capture']);self.assertFalse(result['inherited_analysis'])
        self.assertEqual(len(list((self.target.run/'index').glob('*.json'))),1)
        target=self.target.resolve(self.cap);original=self.source.resolve(self.cap)
        self.assertEqual(target['sha256'],original['sha256'])
        self.assertEqual(Path(target['path']).stat().st_ino,Path(original['path']).stat().st_ino)
        self.assertIn('Do NOT call capture_iq',dsh_prompt(req))
        from spectrum_agent.worker import execute
        with self.assertRaisesRegex(DomainError,'Recorded-IQ replay'):
            execute(self.target.task_id,'capture_iq',dict(center_hz=2402e6,span_hz=4e6,duration_s=.01))
        with self.assertRaises(DomainError):normalize(dict(req,continue_from_task_id=self.source.task_id))

    def test_recording_replay_does_not_require_previous_segmentation(self):
        (self.source.run/'report.json').unlink()
        (self.source.run/'index'/(self.base+'.json')).unlink()
        req=normalize(dict(prompt='Analyze saved IQ',replay_from_task_id=self.source.task_id))
        self.assertEqual(req['recording_capture_id'],self.cap)
        with self.assertRaises(DomainError):normalize(dict(req,constraints=dict(center_hz=2500e6)))
        Path(self.source.resolve(self.cap)['path']).write_bytes(b'changed')
        with self.assertRaisesRegex(DomainError,'checksum'):continuation.attach_replay(self.target,req)

    def attach(self):
        request=self.request();dump(self.target.run/'request.json',request)
        return continuation.attach(self.target,request)

    def test_infers_recorded_scope_and_keeps_hardware_off(self):
        request=self.request()
        self.assertEqual(request['constraints']['center_hz'],2402e6)
        self.assertEqual(request['recording_capture_id'],self.cap)
        self.assertIn('do not rerun whole-record segmentation',dsh_prompt(request))
        with self.assertRaisesRegex(DomainError,'retains analysis scope'):
            normalize(dict(prompt='continue',continue_from_task_id=self.source.task_id,constraints=dict(span_hz=20e6)))

    def test_imports_owned_artifacts_without_mutating_source_and_is_idempotent(self):
        before=Path(self.source.resolve(self.cap)['path']).read_bytes()
        manifest=self.attach();second=continuation.attach(self.target,self.request())
        self.assertEqual(manifest['imported_artifact_ids'],second['imported_artifact_ids'])
        rec=self.target.resolve(self.cap)
        self.assertEqual(rec['origin_task_id'],self.source.task_id)
        self.assertTrue(Path(rec['path']).is_relative_to(Path(self.target.resolve(self.base)['path']).parent))
        self.assertEqual(before,Path(self.source.resolve(self.cap)['path']).read_bytes())

    def test_old_targets_do_not_spend_new_budget_and_window_coverage_is_union(self):
        self.attach()
        state=analysis_budget.snapshot(self.target)
        self.assertEqual(state['local_attempts'],0)
        self.assertEqual(state['distinct_locally_reviewed_regions'],0)
        self.assertEqual(state['inherited_local_refinement_count'],1)
        queue=investigation.plan(self.target,self.base,1)
        row=queue['selected_targets'][0]
        self.assertAlmostEqual(row['local_checked_area_fraction'],.05)
        self.assertEqual(row['local_checked_windows'][0]['origin_task_id'],self.source.task_id)

    def test_old_deferral_reopens_and_does_not_block_new_target_decisions(self):
        queue=investigation.plan(self.source,self.base,1)
        investigation.decide(self.source,queue['queue_id'],'g0','defer','Time budget exhausted; retain unknown target.',[],next_step='Inspect another observed window next time.')
        self.attach()
        queue=investigation.plan(self.target,self.base,1)
        self.assertEqual(queue['deferred_count'],0)
        self.assertTrue(queue['selected_targets'][0]['reopened_prior_deferral'])
        investigation.decide(self.target,queue['queue_id'],'g0','defer','Need additional supported PHY evidence before identification.',[],next_step='Inspect remaining time/frequency windows.')

    def test_missing_active_changed_or_escaped_sources_are_rejected(self):
        with self.assertRaises(DomainError):continuation.inspect_source('../bad')
        with self.assertRaises(DomainError):continuation.inspect_source('missing_source')
        dump(self.source.run/'outcome.json',dict(status='running'))
        with self.assertRaisesRegex(DomainError,'not terminal'):continuation.inspect_source(self.source.task_id)
        dump(self.source.run/'outcome.json',dict(status='succeeded'))
        record=self.source.resolve(self.cap)
        dump(self.source.run/'index'/(self.cap+'.json'),dict(record,path=str(self.source.run/'report.json')))
        with self.assertRaisesRegex(DomainError,'outside its task'):continuation.inspect_source(self.source.task_id)
        dump(self.source.run/'index'/(self.cap+'.json'),record)
        path=Path(self.source.resolve(self.cap)['path']);path.write_bytes(b'changed')
        with self.assertRaisesRegex(DomainError,'checksum'):self.attach()
