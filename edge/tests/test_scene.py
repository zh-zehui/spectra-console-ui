import json
import unittest
import uuid
from unittest.mock import patch
from pathlib import Path
import numpy as np
from spectrum_agent.common import Context,DomainError,dump
from spectrum_agent.worker import execute,report
from spectrum_agent import dsp,scene,protocols


class SceneTests(unittest.TestCase):
    def setUp(self):
        self.ctx=Context('scene_test_'+uuid.uuid4().hex[:10])
        dump(self.ctx.run/'request.json',dict(budgets=dict(max_local_refinements=2)))

    def test_hysteresis_recovers_edges_without_noise_component(self):
        rng=np.random.default_rng(5)
        db=rng.normal(0,.3,(100,128)).astype(np.float32)
        db[20:30,50:60]+=14
        db[18:32,48:62]+=7
        req=type('Request',(),dict(window_db=db))()
        events=scene.AdaptiveDetector(9).detect(req)
        self.assertEqual(len(events),1)
        box=events[0]['box']
        self.assertEqual((box['x1'],box['x2'],box['y1'],box['y2']),(48,61,18,31))
        self.assertEqual(scene.AdaptiveDetector(9).detect(type('R',(),dict(window_db=rng.normal(0,.3,(100,128))))()),[])

    def test_feedback_preserves_parent_and_maps_local_events(self):
        cap=dsp.synthetic(self.ctx,center_hz=2402e6)
        seg=dsp.segment(self.ctx,cap['capture_id'])
        original=Path(self.ctx.resolve(seg['segmentation_id'])['path']).read_bytes()
        sub=dsp.extract(self.ctx,cap['capture_id'],2402e6,2e6,output_rate_hz=4e6)
        evidence=protocols.decode_ble(self.ctx,sub['artifact_id'],37)
        self.assertEqual(evidence['valid_frames'],0)
        review=scene.assess(self.ctx,seg['segmentation_id'])
        self.assertTrue(review['verification_attempts'])
        self.assertGreater(review['review_target_count'],0)
        target=review['review_targets'][0]['group_id']
        args=dict(segmentation_id=seg['segmentation_id'],group_id=target,
                  reason='Failed verification overlaps this observed group; test local adaptive boundary.',
                  evidence_ids=[review['review_id'],evidence['evidence_id']])
        result=execute(self.ctx.task_id,'refine_signal_region',args)['result']
        self.assertTrue(result['initial_group_preserved'])
        self.assertEqual(result['quality_comparison']['accuracy_improvement'],'not_measured')
        self.assertLessEqual(len(result['mapped_events']),30)
        self.assertEqual(original,Path(self.ctx.resolve(seg['segmentation_id'])['path']).read_bytes())
        for e in result['mapped_events']:
            self.assertGreaterEqual(e['sample_start'],0)
            self.assertLessEqual(e['sample_stop'],cap['sample_count'])
            self.assertEqual(e['protocol'],'unknown')
        scene.assess(self.ctx,seg['segmentation_id'])
        report(self.ctx)
        value=json.loads((self.ctx.run/'report.json').read_text())
        self.assertEqual(len(value['segmentations']),1)
        self.assertEqual(len(value['scene_refinements']),1)
        self.assertEqual(value['analysis_coverage']['detected_groups'],seg['total_groups'])
        args['reason']+=' Second comparison.'
        execute(self.ctx.task_id,'refine_signal_region',args)
        args['reason']+=' Third attempt.'
        with self.assertRaisesRegex(DomainError,'local_call_budget'):
            execute(self.ctx.task_id,'refine_signal_region',args)

    def test_requires_bound_review(self):
        cap=dsp.synthetic(self.ctx)
        seg=dsp.segment(self.ctx,cap['capture_id'])
        fake=self.ctx.json_artifact('scene_review',dict(segmentation_id='different'))
        with self.assertRaisesRegex(DomainError,'selected segmentation'):
            scene.refine(self.ctx,seg['segmentation_id'],'g0','Observed quality issue',[fake])

    def test_invalid_rate_rejected_before_filtering_and_reports_bounds(self):
        cap=dsp.synthetic(self.ctx,center_hz=2402e6)
        with patch('gnuradio.gr.top_block') as graph:
            with self.assertRaisesRegex(DomainError,r'output_rate_hz outside allowed range \[2200000.0, 4000000'):
                dsp.extract(self.ctx,cap['capture_id'],2402e6,2e6,output_rate_hz=2e6)
            graph.assert_not_called()
        with self.assertRaisesRegex(DomainError,'center_hz must be within'):
            dsp.extract(self.ctx,cap['capture_id'],2403.5e6,2e6)

    def test_failed_refinements_also_consume_budget(self):
        for i in range(2):
            with self.assertRaises(DomainError):
                execute(self.ctx.task_id,'refine_signal_region',dict(segmentation_id='missing',group_id='g0',reason='Review missing input '+str(i),evidence_ids=['missing']))
        with self.assertRaisesRegex(DomainError,'local_call_budget'):
            execute(self.ctx.task_id,'refine_signal_region',dict(segmentation_id='missing',group_id='g0',reason='Third missing input',evidence_ids=['missing']))
