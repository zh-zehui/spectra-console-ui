import json,unittest,uuid
from spectrum_agent.common import Context,dump,DomainError
from spectrum_agent import dsp,scene,coverage
from spectrum_agent.service import normalize
from spectrum_agent.worker import report,execute


class CompleteCoverageTests(unittest.TestCase):
    def setUp(self):
        self.ctx=Context('coverage_test_'+uuid.uuid4().hex[:10])
        self.request=normalize(dict(prompt='test',source='synthetic',constraints=dict(center_hz=2.4e9,span_hz=3.2e6)),model_resolved=True)
        dump(self.ctx.run/'request.json',self.request)
        cap=dsp.synthetic(self.ctx,duration_s=.05)['capture_id']
        groups=[]
        for i,(start,stop) in enumerate([(0,200000),(2000,4000)]):
            groups.append(dict(group_id='g'+str(i),sample_start=start,sample_stop=stop,
                freq_low_hz=2399.5e6,freq_high_hz=2400.5e6,bandwidth_hz=1e6,
                duration_s=(stop-start)/4e6,snr_db=10,protocol='unknown'))
        self.seg=self.ctx.json_artifact('segmentation',dict(capture_id=cap,signal_groups=groups,events=[],
            fft_size=512,frequency_resolution_hz=4e6/512,segmentation_level='coarse_activity'))
        self.review=scene.assess(self.ctx,self.seg)['review_id']

    def test_complete_defaults_are_not_representative_budget(self):
        b=self.request['budgets']
        self.assertEqual(b['coverage_mode'],'complete')
        self.assertEqual(b['deadline_s'],3600)
        self.assertGreaterEqual(b['max_candidates'],4096)
        self.assertGreaterEqual(b['max_local_refinements'],8192)

    def test_long_parent_is_fully_reviewed_and_retry_reuses_completed_work(self):
        first=coverage.review_batch(self.ctx,self.seg,self.review,max_regions=1,fft_size=512)
        self.assertEqual(first['progress']['remaining_regions'],1)
        self.assertTrue(first['regions'][0]['full_parent_local_review'])
        self.assertEqual(len(first['regions'][0]['subband_ids']),3)
        second=coverage.review_batch(self.ctx,self.seg,self.review,fft_size=512)
        self.assertTrue(second['progress']['all_regions_reviewed'])
        self.assertFalse(second['progress']['protocol_recognition_complete'])
        self.assertEqual(coverage.review_batch(self.ctx,self.seg,self.review)['regions'],[])
        report(self.ctx,stop_reason='requested_checks_complete',stop_detail='All parents reviewed; unsupported protocols remain explicitly unknown.')
        r=json.loads((self.ctx.run/'report.json').read_text())
        self.assertTrue(r['catalog_review']['all_regions_reviewed'])

    def test_incomplete_catalog_cannot_be_declared_complete(self):
        with self.assertRaisesRegex(DomainError,'Continue review_activity_batch'):
            report(self.ctx,stop_reason='requested_checks_complete',stop_detail='Representative selection is sufficient for this incomplete report.')
        self.assertFalse((self.ctx.run/'analysis-stop.json').exists())
        report(self.ctx,stop_reason='time_budget',stop_detail='Time expired; preserve unfinished catalog as partial evidence.')
        self.assertEqual(json.loads((self.ctx.run/'report.json').read_text())['catalog_review']['remaining_regions'],2)

    def test_prompt_budget_waits_for_model_and_api_budget_stays_explicit(self):
        base=dict(source='synthetic',constraints=dict(center_hz=2.4e9,span_hz=3.2e6))
        req=normalize(dict(base,prompt='analysis30minutes'))
        self.assertEqual(req['budgets']['deadline_s'],3600)
        self.assertEqual(req['task_intent']['status'],'pending_model')
        req=normalize(dict(base,prompt='analysis30minutes',budgets=dict(deadline_s=600)))
        self.assertEqual(req['budgets']['deadline_s'],600)
        self.assertEqual(req['budget_resolution']['source'],'explicit_budget')

    def test_batch_calls_advance_even_with_identical_arguments(self):
        arguments=dict(segmentation_id=self.seg,review_id=self.review,max_regions=1,fft_size=512)
        first=execute(self.ctx.task_id,'review_activity_batch',arguments)
        second=execute(self.ctx.task_id,'review_activity_batch',arguments)
        self.assertFalse(second['cached'])
        self.assertEqual(first['result']['progress']['remaining_regions'],1)
        self.assertEqual(second['result']['progress']['remaining_regions'],0)

    def test_window_budget_checkpoints_long_parent_without_false_completion(self):
        cap=dsp.synthetic(self.ctx,duration_s=.2)['capture_id']
        group=dict(group_id='long',sample_start=0,sample_stop=800000,
            freq_low_hz=2399.5e6,freq_high_hz=2400.5e6,bandwidth_hz=1e6,
            duration_s=.2,snr_db=10,protocol='unknown')
        seg=self.ctx.json_artifact('segmentation',dict(capture_id=cap,signal_groups=[group],events=[],
            fft_size=512,frequency_resolution_hz=4e6/512,segmentation_level='coarse_activity'))
        review=scene.assess(self.ctx,seg)['review_id']
        first=coverage.review_batch(self.ctx,seg,review,fft_size=512)
        self.assertEqual(first['progress']['remaining_regions'],1)
        second=coverage.review_batch(self.ctx,seg,review,fft_size=512)
        self.assertTrue(second['progress']['all_regions_reviewed'])
