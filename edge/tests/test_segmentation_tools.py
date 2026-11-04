import json
import unittest
import uuid
from pathlib import Path
from spectrum_agent import dsp,segmentation_tools as tools
from spectrum_agent.common import Context,DomainError,dump
from spectrum_agent.worker import execute,report
from spectrum_agent.service import dsh_prompt,normalize


class SegmentationToolsTests(unittest.TestCase):
    def setUp(self):
        self.ctx=Context('seg_decision_test_'+uuid.uuid4().hex[:10])

    def candidate(self,cap,groups):
        return self.ctx.json_artifact('segmentation',dict(capture_id=cap,signal_groups=groups,
            strategy='traditional',fft_size=1024,threshold_db=12,frequency_resolution_hz=3906.25,
            coverage=dict(observed_samples=10000)))

    def test_explicit_choice_does_not_duplicate_alternative_groups(self):
        g=dict(group_id='g0',protocol='unknown',duration_s=.00005,bandwidth_hz=20000,
            sample_start=0,sample_stop=200,freq_low_hz=2400e6,freq_high_hz=2400e6+20000,snr_db=15)
        a=self.candidate('capture_example',[g,g]);b=self.candidate('capture_example',[g])
        cmp=tools.compare(self.ctx,[a,b])
        self.assertEqual([c['group_count'] for c in cmp['candidates']],[2,1])
        tools.select(self.ctx,b,cmp['comparison_id'],'Retain the alternative as a working hypothesis; weak signal recall remains unmeasured.')
        report(self.ctx);value=json.loads((self.ctx.run/'report.json').read_text())
        self.assertEqual(len(value['segmentations']),2)
        self.assertEqual(len(value['signal_groups']),1)
        self.assertEqual(value['selected_segmentation_id'],b)
        self.assertEqual(value['segmentation_selections'][0]['decision_owner'],'deepseek_harness')

    def test_invalid_comparison_and_selection_rejected(self):
        a=self.candidate('capture_a',[]);b=self.candidate('capture_b',[])
        with self.assertRaises(DomainError):tools.compare(self.ctx,[a,b])
        cmp=tools.compare(self.ctx,[a])
        with self.assertRaises(DomainError):tools.select(self.ctx,b,cmp['comparison_id'],'Not in compared recording; must be rejected.')
        with self.assertRaises(DomainError):tools.compare(self.ctx,[a,a])

    def test_packet_coverage_regression_cannot_be_selected(self):
        a=self.candidate('capture_a',[])
        cmp=self.ctx.json_artifact('segmentation_comparison',dict(capture_id='capture_a',candidates=[
            dict(segmentation_id=a,packet_overlap_count=0),dict(segmentation_id='other',packet_overlap_count=2)]))
        with self.assertRaisesRegex(DomainError,'loses known packet'):
            tools.select(self.ctx,a,cmp,'This proposal loses known packet coverage and must be rejected.')

    def test_new_verification_requires_fresh_comparison(self):
        a=self.candidate('capture_a',[]);cmp=tools.compare(self.ctx,[a])
        self.ctx.json_artifact('ble_verification',dict(valid_frames=0,artifact_id='subband_example'))
        with self.assertRaisesRegex(DomainError,'compare again'):
            tools.select(self.ctx,a,cmp['comparison_id'],'A stale comparison must not ignore new protocol evidence.')

    def test_sparse_overview_and_real_strategy_dispatch(self):
        cap=dsp.synthetic(self.ctx,sample_rate_hz=1e6,duration_s=.01)
        obs=execute(self.ctx.task_id,'inspect_spectrum',dict(artifact_id=cap['capture_id']))['result']
        self.assertLessEqual(obs['sampled_window_count'],256)
        self.assertEqual(len(obs['frequency_bands']),32)
        self.assertIn('may miss',obs['warning'])
        for strategy in ('traditional','adaptive_hysteresis'):
            value=execute(self.ctx.task_id,'segment_spectrum',dict(capture_id=cap['capture_id'],strategy=strategy,
                decision_reason='Test explicit model-visible algorithm routing.',fft_size=512))['result']
            self.assertEqual(value['strategy'],strategy)
        execute(self.ctx.task_id,'segment_spectrum',dict(capture_id=cap['capture_id'],fft_size=1024))
        with self.assertRaisesRegex(DomainError,'three full-band'):
            execute(self.ctx.task_id,'segment_spectrum',dict(capture_id=cap['capture_id'],fft_size=2048))

    def test_prompt_leaves_strategy_to_harness(self):
        req=normalize(dict(prompt='Analyze 2.4GHz 100MHz',source='usrp'))
        text=dsh_prompt(req)
        self.assertIn('自主选择工具',text)
        self.assertNotIn('然后segment_spectrum并match_signal_hypotheses',text)
        req['recording_capture_id']='capture_owned'
        self.assertIn('Do NOT call capture_iq',dsh_prompt(req))


if __name__=='__main__':unittest.main()
