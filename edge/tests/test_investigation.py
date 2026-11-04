import json
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch
from spectrum_agent.common import Context,DomainError,dump
from spectrum_agent import investigation as inv


class InvestigationTests(unittest.TestCase):
    def setUp(self):
        self.ctx=Context('investigation_test_'+uuid.uuid4().hex[:10])
        self.cap=self.make_iq('capture',dict(sample_rate_hz=1e6,sample_count=100000,center_hz=2400e6,requested_span_hz=100e6))
        self.groups=[]
        for i,(width,duration,snr) in enumerate([(20e6,.01,50),(20e6,.01,40),(1e6,.0001,5),(4e6,.003,12),(1e6,.01,8),(20e6,.0001,30)]):
            low=2360e6+i*12e6
            self.groups.append(dict(group_id='g'+str(i),sample_start=i*12000,sample_stop=i*12000+int(duration*1e6),
                freq_low_hz=low,freq_high_hz=low+width,bandwidth_hz=width,duration_s=duration,snr_db=snr,protocol='unknown'))
        self.seg=self.ctx.json_artifact('segmentation',dict(capture_id=self.cap,signal_groups=self.groups,fft_size=512))
        self.sub=self.make_iq('subband',dict(parent_artifact_id=self.cap,parent_sample_start=0,actual_parent_sample_stop=10000,
            sample_rate_hz=1e6,center_hz=2370e6,requested_span_hz=20e6))

    def make_iq(self,kind,meta):
        aid,path=self.ctx.artifact(kind,'cf32');path.write_bytes(b'\0'*16)
        mp=path.with_suffix('.json');dump(mp,meta);self.ctx.register(aid,path,kind=kind,metadata_path=str(mp));return aid

    def test_queue_preserves_all_unknowns_and_schedules_diverse_representatives(self):
        q=inv.plan(self.ctx,self.seg,4)
        saved=json.loads(Path(self.ctx.resolve(q['queue_id'])['path']).read_text())
        self.assertEqual(saved['total_regions'],6);self.assertEqual(len(saved['regions']),6)
        self.assertTrue(all(r['protocol']=='unknown' for r in saved['regions']))
        tags=[r['diversity'] for r in q['selected_targets']]
        self.assertTrue(any('weak' in t for t in tags));self.assertTrue(any('strong' in t for t in tags))
        self.assertTrue(any('short' in t for t in tags));self.assertTrue(any('wide' in t for t in tags))
        self.assertEqual(len({r['family_id'] for r in q['selected_targets']}),len(q['selected_targets']))

    def test_locally_checked_parent_yields_priority_to_unchecked_targets(self):
        self.ctx.json_artifact('scene_refinement',dict(parent_segmentation_id=self.seg,parent_group_id='g0',capture_id=self.cap,
            observed_parent_group=self.groups[0],requested_parent_interval=[0,10000],fine_target_frequency_hz=[2360e6,2380e6]))
        q=inv.plan(self.ctx,self.seg,1)
        self.assertNotEqual(q['selected_targets'][0]['group_id'],'g0')
        self.assertEqual(q['total_regions'],6)

    def test_zero_frame_attempt_is_window_specific_and_repeat_is_blocked(self):
        def decoder(ctx,**kw):
            v=dict(artifact_id=kw['artifact_id'],protocol='IEEE802.11',profile='test',valid_frames=0,frames=[])
            eid=ctx.json_artifact('wifi_verification',v);return dict(v,evidence_id=eid)
        with patch('spectrum_agent.protocols.verify',side_effect=decoder):
            out=inv.verify(self.ctx,self.sub,'wifi_legacy_ofdm')
            q=inv.project(self.ctx,self.seg)
            g0=q['regions'][0];self.assertEqual(g0['evidence_state'],'attempted_no_valid_frame')
            self.assertEqual(len(g0['verification_attempts']),1)
            self.assertEqual(g0['verification_attempts'][0]['scope']['sample_stop'],10000)
            self.assertEqual(q['regions'][2]['evidence_state'],'unverified')
            duplicate=self.make_iq('subband',json.loads(Path(self.ctx.resolve(self.sub)['metadata_path']).read_text()))
            with self.assertRaisesRegex(DomainError,'already attempted'):inv.verify(self.ctx,duplicate,'wifi_legacy_ofdm')
        self.assertTrue(out['attempt_id'])

    def test_stage_and_partial_coverage_survive_attempt_audit(self):
        stages=dict(strongest_stage='header_plausible',counts=dict(crc_failed=3))
        coverage=dict(input_samples=10000,processed_samples=5000,complete=False)
        with patch('spectrum_agent.protocols.verify',return_value=dict(
                valid_frames=0,verification_stages=stages,analysis_coverage=coverage)):
            out=inv.verify(self.ctx,self.sub,'ble_1m_advertising')
        saved=inv.read(self.ctx,out['attempt_id'])
        self.assertEqual(saved['verification_stages'],stages)
        self.assertEqual(saved['analysis_coverage'],coverage)
        self.assertEqual(saved['status'],'no_valid_frames')

    def test_imported_legacy_verification_without_audit_still_blocks_equivalent_retry(self):
        self.ctx.json_artifact('wifi_verification',dict(artifact_id=self.sub,protocol='IEEE802.11',profile='legacy',frames=[],valid_frames=0))
        with patch('spectrum_agent.protocols.verify') as decoder:
            with self.assertRaisesRegex(DomainError,'already attempted'):
                inv.verify(self.ctx,self.sub,'wifi_legacy_ofdm')
            decoder.assert_not_called()

    def test_unsupported_and_tool_errors_are_distinct_audits(self):
        with patch('spectrum_agent.protocols.verify',return_value=dict(status='unsupported',error_code='DEPENDENCY_MISSING')):
            inv.verify(self.ctx,self.sub,'unsupported_phy')
        with patch('spectrum_agent.protocols.verify',side_effect=DomainError('INVALID_RATE','Wrong sample rate')):
            with self.assertRaises(DomainError):inv.verify(self.ctx,self.sub,'ble_1m_advertising')
        attempts=inv.project(self.ctx,self.seg)['regions'][0]['verification_attempts']
        self.assertEqual({a['status'] for a in attempts},{'unsupported','tool_error'})
        self.assertEqual({a['error_code'] for a in attempts},{'DEPENDENCY_MISSING','INVALID_RATE'})

    def test_defer_retains_unknown_and_new_relevant_evidence_reopens(self):
        q=inv.plan(self.ctx,self.seg)
        inv.decide(self.ctx,q['queue_id'],'g0','defer','Allocate the limited analysis budget to other representative activity.',[],
            'Need a longer recording or a supported protocol synchronization test.')
        r=inv.project(self.ctx,self.seg);self.assertEqual(r['regions'][0]['investigation_state'],'deferred')
        self.assertEqual(r['regions'][0]['protocol'],'unknown')
        self.ctx.json_artifact('features',dict(artifact_id=self.sub,evaluated_samples=5000))
        r=inv.project(self.ctx,self.seg);self.assertEqual(r['regions'][0]['investigation_state'],'pending')
        self.assertTrue(r['regions'][0]['decision_stale'])

    def test_stale_queue_and_unrelated_evidence_rejected(self):
        q=inv.plan(self.ctx,self.seg)
        eid=self.ctx.json_artifact('features',dict(artifact_id=self.sub,evaluated_samples=5000))
        with self.assertRaisesRegex(DomainError,'refresh plan'):inv.decide(self.ctx,q['queue_id'],'g0','continue','Inspect the newly measured feature in this target window.',[eid],'Choose a focused supported protocol test.')
        q=inv.plan(self.ctx,self.seg)
        with self.assertRaisesRegex(DomainError,'overlap this target'):inv.decide(self.ctx,q['queue_id'],'g2','continue','Evidence from another frequency/time region cannot identify this target.',[eid],'Use a fresh subband centered on this target.')
        inv.decide(self.ctx,q['queue_id'],'g0','continue','Use observed features only as candidates and keep protocol unknown.',[eid],'Choose a targeted decoder after checking feature evidence.')

    def test_prefix_coverage_does_not_mark_late_activity_analyzed(self):
        # Keep the 1MS/s prefix fixture physically inside the first target;
        # its former 100MHz metadata span exceeded the saved digital band.
        rec=self.ctx.resolve(self.cap);meta=json.loads(Path(rec['metadata_path']).read_text())
        meta['center_hz']=2370e6;dump(rec['metadata_path'],meta)
        eid=self.ctx.json_artifact('features',dict(artifact_id=self.cap,evaluated_samples=100))
        rows=inv.project(self.ctx,self.seg)['regions']
        self.assertIn(eid,rows[0]['relevant_evidence_ids'])
        self.assertNotIn(eid,rows[-1]['relevant_evidence_ids'])

    def test_positive_frame_updates_evidence_without_labeling_parent(self):
        def decoder(ctx,**kw):
            v=dict(artifact_id=kw['artifact_id'],protocol='IEEE802.11',profile='test',valid_frames=1,
                frames=[dict(subband_sample_start=100,subband_sample_stop=200,center_hz=2370e6,occupied_bandwidth_hz=17e6,fcs_pass=True)])
            eid=ctx.json_artifact('wifi_verification',v);return dict(v,evidence_id=eid)
        with patch('spectrum_agent.protocols.verify',side_effect=decoder):out=inv.verify(self.ctx,self.sub,'wifi_legacy_ofdm')
        r=inv.project(self.ctx,self.seg)['regions'][0]
        self.assertEqual(r['evidence_state'],'valid_frames_overlap');self.assertEqual(r['protocol'],'unknown')
        self.assertEqual(r['packet_overlap_count'],1)
        audit=json.loads(Path(self.ctx.resolve(out['attempt_id'])['path']).read_text())
        self.assertIsNone(audit['arguments']['channel']);self.assertEqual(audit['sample_rate_hz'],1e6)
        from spectrum_agent.local_review import packet_quality
        q=packet_quality(self.ctx,self.cap,0,1000,2360e6,2380e6,[dict(sample_start=50,sample_stop=250,freq_low_hz=2360e6,freq_high_hz=2380e6)])
        self.assertAlmostEqual(q['packet_time_diagnostics']['median_best_time_iou'],.5)
        self.assertAlmostEqual(q['packet_time_diagnostics']['median_best_time_expansion'],2)

    def test_decisions_obey_distinct_target_budget(self):
        dump(self.ctx.run/'request.json',dict(budgets=dict(max_candidates=1)))
        q=inv.plan(self.ctx,self.seg,4);self.assertEqual(len(q['selected_targets']),1)
        inv.decide(self.ctx,q['queue_id'],'g0','defer','Keep the unresolved target while allocating a bounded budget.',[],'Need additional evidence in a future observation.')
        with self.assertRaisesRegex(DomainError,'candidate budget'):
            inv.decide(self.ctx,q['queue_id'],'g1','defer','The second distinct target must not bypass the candidate budget.',[],'Keep unknown until the next explicitly budgeted observation.')


if __name__=='__main__':unittest.main()
