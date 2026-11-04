import json
import unittest
import uuid
from unittest.mock import patch
from pathlib import Path
from spectrum_agent.common import Context,DomainError,dump
from spectrum_agent import fusion,scene,segmentation_tools
from spectrum_agent.worker import report


class FusionTests(unittest.TestCase):
    def setUp(self):
        self.ctx=Context('fusion_test_'+uuid.uuid4().hex[:10])
        self.parent=dict(group_id='g0',event_id='e0',sample_start=0,sample_stop=100,
            freq_low_hz=100,freq_high_hz=200,bandwidth_hz=100,duration_s=1,
            time_start_s=10,snr_db=15,protocol='unknown',event_count=1,event_ids=['e0'])

    def test_partition_preserves_every_parent_point_and_clips_context(self):
        events=[dict(sample_start=-10,sample_stop=45,freq_low_hz=110,freq_high_hz=150),
                dict(sample_start=40,sample_stop=110,freq_low_hz=140,freq_high_hz=170)]
        pieces,status=fusion.partition(self.parent,events,(20,80,120,180),100,'ref')
        self.assertEqual(status,'geometry_adopted_with_unresolved_residual')
        fine=[g for g in pieces if g['boundary_role']=='adopted_fine']
        for g in fine:
            self.assertGreaterEqual(g['sample_start'],20);self.assertLessEqual(g['sample_stop'],80)
            self.assertGreaterEqual(g['freq_low_hz'],120);self.assertLessEqual(g['freq_high_hz'],180)
        for t in range(100):
            for f in range(100,200):
                hits=[g for g in pieces if g['sample_start']<=t+.5<g['sample_stop'] and g['freq_low_hz']<=f+.5<g['freq_high_hz']]
                self.assertTrue(hits)
                self.assertLessEqual(sum(g['boundary_role']=='unresolved_residual' for g in hits),1)
                self.assertFalse(any(g['boundary_role']=='unresolved_residual' for g in hits) and any(g['boundary_role']=='adopted_fine' for g in hits))
        self.assertTrue(all(g['protocol']=='unknown' for g in pieces))
        self.assertEqual(self.parent['sample_stop'],100)

    def test_dense_empty_and_invalid_do_not_erase_parent(self):
        e=dict(sample_start=20,sample_stop=30,freq_low_hz=120,freq_high_hz=150)
        for events,reason in [([e]*201,'dense_fine_candidates'),([],'empty_fine_candidates'),([dict(e,sample_stop=10)],'invalid_fine_geometry')]:
            self.assertEqual(fusion.partition(self.parent,events,(0,100,100,200),100,'ref'),(None,reason))

    def setup_artifacts(self,dense=False):
        aid,path=self.ctx.artifact('capture','cf32');path.write_bytes(b'\0'*800)
        meta=path.with_suffix('.json');dump(meta,dict(sample_rate_hz=100,center_hz=150,requested_span_hz=100,sample_count=100,duration_s=1,simulated=True,segments=[dict(file_start=0,count=100,hardware_start_s=10)]))
        self.ctx.register(aid,path,kind='capture',metadata_path=str(meta))
        base=self.ctx.json_artifact('segmentation',dict(capture_id=aid,segmentation_level='coarse_activity',events=[self.parent],signal_groups=[self.parent],fft_size=512,threshold_db=6,frequency_resolution_hz=1,coverage={}))
        sid,sp=self.ctx.artifact('subband','cf32');sp.write_bytes(b'\0'*800)
        sm=sp.with_suffix('.json');dump(sm,dict(parent_artifact_id=aid,center_hz=150,requested_span_hz=100))
        self.ctx.register(sid,sp,kind='subband',metadata_path=str(sm))
        local=self.ctx.json_artifact('local_segmentation',dict(capture_id=sid))
        e=dict(sample_start=20,sample_stop=30,freq_low_hz=120,freq_high_hz=150)
        rid=self.ctx.json_artifact('scene_refinement',dict(parent_segmentation_id=base,parent_group_id='g0',capture_id=aid,
            local_segmentation_id=local,subband_id=sid,requested_parent_interval=[10,90],
            mapped_events=[e]*(201 if dense else 1),quality_comparison={},feedback_evidence_ids=[],reason='Controlled sparse/dense geometry acceptance fixture'))
        review=self.ctx.json_artifact('scene_review',dict(segmentation_id=base,refinements=[dict(refinement_id=rid)]))
        return base,rid,review

    def test_fusion_is_candidate_until_explicit_selection(self):
        base,rid,review=self.setup_artifacts()
        out=fusion.fuse(self.ctx,base,[rid],review,'Use sparse local geometry and preserve unexplained support.')
        self.assertEqual(out['fusion']['accepted_parent_count'],1)
        report(self.ctx);r=json.loads((self.ctx.run/'report.json').read_text())
        self.assertEqual(r['selected_segmentation_id'],base)
        cmp=segmentation_tools.compare(self.ctx,[base,out['segmentation_id']])
        segmentation_tools.select(self.ctx,out['segmentation_id'],cmp['comparison_id'],'Adopt local boundaries conservatively; all residual uncertainty remains.')
        report(self.ctx);r=json.loads((self.ctx.run/'report.json').read_text())
        self.assertEqual(r['boundary_composition'],'mixed')
        self.assertEqual(r['boundary_fusion']['accepted_parent_count'],1)
        self.assertEqual(r['initial_segmentation_id'],base)

    def test_dense_rejection_retains_exact_parent(self):
        base,rid,review=self.setup_artifacts(True)
        out=fusion.fuse(self.ctx,base,[rid],review,'Reject dense fine candidates while keeping the coarse parent.')
        data=scene.read(self.ctx,out['segmentation_id'])
        self.assertEqual(out['fusion']['accepted_parent_count'],0)
        self.assertEqual(fusion.bounds(data['signal_groups'][0]),fusion.bounds(self.parent))

    def test_three_parents_can_be_fused_without_erasing_residuals(self):
        base,rid,review=self.setup_artifacts()
        original=scene.read(self.ctx,base)
        parents=[dict(self.parent,group_id='g'+str(i),event_id='e'+str(i)) for i in range(3)]
        multi=self.ctx.json_artifact('segmentation',dict(original,signal_groups=parents,events=parents))
        template=scene.read(self.ctx,rid)
        refs=[self.ctx.json_artifact('scene_refinement',dict(template,parent_segmentation_id=multi,parent_group_id=g['group_id'])) for g in parents]
        review=self.ctx.json_artifact('scene_review',dict(segmentation_id=multi,refinements=[dict(refinement_id=r) for r in refs]))
        result=fusion.fuse(self.ctx,multi,refs,review,'Adopt three independently reviewed parent windows with all residual support retained.')
        self.assertEqual(result['fusion']['accepted_parent_count'],3)
        data=scene.read(self.ctx,result['segmentation_id'])
        self.assertEqual({g['parent_group_id'] for g in data['signal_groups']},{'g0','g1','g2'})
        self.assertTrue(any(g['boundary_role']=='unresolved_residual' for g in data['signal_groups']))

    def test_stale_review_and_recursive_fusion_rejected(self):
        base,rid,review=self.setup_artifacts()
        stale=self.ctx.json_artifact('scene_review',dict(segmentation_id=base,refinements=[]))
        with self.assertRaisesRegex(DomainError,'again after'):fusion.fuse(self.ctx,base,[rid],stale,'Stale scene evidence cannot justify replacing a coarse boundary.')
        out=fusion.fuse(self.ctx,base,[rid],review,'A valid original-parent fusion with uncertainty retained.')
        with self.assertRaisesRegex(DomainError,'original coarse'):fusion.fuse(self.ctx,out['segmentation_id'],[rid],review,'Recursive proposals must retain original coarse lineage.')

    def test_current_packet_references_can_reject_previously_safe_geometry(self):
        base,rid,review=self.setup_artifacts()
        with patch('spectrum_agent.local_review.current_quality',return_value=dict(known_packet_reference_count=1,known_packet_unmatched_count=1,verification_snapshot=['new-frame'])):
            out=fusion.fuse(self.ctx,base,[rid],review,'New packet evidence must be checked before adopting this local geometry.')
        self.assertEqual(out['fusion']['accepted_parent_count'],0)
        self.assertEqual(out['fusion']['decisions'][0]['status'],'known_packet_reference_missed')

    def test_local_comparison_rejects_changed_target_scope(self):
        from spectrum_agent.local_review import compare
        base,rid,review=self.setup_artifacts()
        data=scene.read(self.ctx,rid);data.update(count_scope='requested target only',context_event_count=1,
            fine_target_frequency_hz=[100,200],strategy='adaptive_hysteresis',fft_size=512,threshold_db=12)
        a=self.ctx.json_artifact('scene_refinement',data)
        b=self.ctx.json_artifact('scene_refinement',dict(data,strategy='local_power_tiles',fft_size=1024))
        comparison=compare(self.ctx,[a,b]);self.assertEqual(len(comparison['candidates']),2)
        c=self.ctx.json_artifact('scene_refinement',dict(data,requested_parent_interval=[20,40]))
        with self.assertRaisesRegex(DomainError,'identical parent'):compare(self.ctx,[a,c])


if __name__=='__main__':unittest.main()
