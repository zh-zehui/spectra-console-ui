import json,unittest,uuid
from unittest.mock import patch
from spectrum_agent.common import Context,DomainError,dump
from spectrum_agent.dsp import synthetic
from spectrum_agent.protocol_batch import plan,run,progress,wifi_centers
from spectrum_agent.worker import report


class ProtocolBatchTests(unittest.TestCase):
    def test_continuation_inherits_completed_protocol_proof_without_decoding(self):
        import os
        from pathlib import Path
        from spectrum_agent.common import ROOT
        from spectrum_agent.continuation import attach_replay,inherit_protocol_coverage
        from spectrum_agent.protocol_batch import summary
        p=self.plan();run(self.ctx,p['plan_id'],max_units=16)
        dump(self.ctx.run/'outcome.json',dict(status='succeeded'))
        target=Context('protocol_inherit_'+uuid.uuid4().hex[:10]);request=json.loads((self.ctx.run/'request.json').read_text())
        request['replay_from_task_id']=self.ctx.task_id;dump(target.run/'request.json',request);attach_replay(target,request)
        base=ROOT/'artifacts'/target.task_id
        for path in (self.ctx.run/'index').glob('*.json'):
            row=json.loads(path.read_text())
            if row['kind'] in ('capture','protocol_plan'):continue
            copied=dict(row,task_id=target.task_id)
            for key in ('path','metadata_path'):
                if key in row:
                    dest=base/Path(row[key]).name
                    if not dest.exists():os.link(row[key],dest)
                    copied[key]=str(dest)
            dump(target.run/'index'/(row['artifact_id']+'.json'),copied)
        receipts=inherit_protocol_coverage(target,self.ctx.task_id)
        self.assertEqual(receipts[0]['completed_units'],3);self.assertTrue(summary(target)['complete'])
        with patch('spectrum_agent.protocol_batch.extract',side_effect=AssertionError('Do not rerun inherited checks')):
            self.assertTrue(run(target,p['plan_id'])['complete'])
        checkpoint=self.ctx.run/('protocol-progress-'+p['plan_id']+'.json');bad=json.loads(checkpoint.read_text());bad['completed'][0]['valid_frames']+=1;dump(checkpoint,bad)
        with self.assertRaisesRegex(DomainError,'does not match decoder evidence'):inherit_protocol_coverage(target,self.ctx.task_id)

    def setUp(self):
        self.ctx=Context('protocol_batch_test_'+uuid.uuid4().hex[:10])
        cap=synthetic(self.ctx,sample_rate_hz=4e6,center_hz=2402e6,duration_s=.25);self.cap=cap['capture_id']
        dump(self.ctx.run/'request.json',dict(constraints=dict(center_hz=2402e6,span_hz=3.2e6),
            task_intent=dict(status='resolved',protocol_workflow_version=2,protocol_analysis_requested=True,analysis_start_s=0,analysis_stop_s=.25)))
        self.prior=self.ctx.json_artifact('priors',dict(observed=False))
        self.check=dict(profile='ble_1m_advertising',center_hz=2402e6,bandwidth_hz=1.5e6,output_rate_hz=4e6,channel=37,reason='Primary advertising candidate from technical prior')

    def plan(self):return plan(self.ctx,self.cap,[self.check],'Full saved time on this evidence-selected BLE channel',[self.prior])

    def test_24ghz_ble_coverage_checkpoint_and_no_recompute(self):
        p=self.plan();self.assertEqual(p['total_units'],3)
        first=run(self.ctx,p['plan_id'],max_units=1)
        self.assertEqual(first['completed_units'],1);self.assertFalse(first['complete'])
        self.assertEqual(self.plan()['plan_id'],p['plan_id'])
        final=run(self.ctx,p['plan_id'],max_units=16)
        self.assertTrue(final['complete']);self.assertEqual(final['processed_iq_samples'],1000000)
        with patch('spectrum_agent.protocol_batch.extract',side_effect=AssertionError('Must not re-extract')):
            self.assertTrue(run(self.ctx,p['plan_id'])['complete'])

    def test_decoder_errors_remain_incomplete_and_retryable(self):
        p=self.plan()
        with patch('spectrum_agent.protocol_batch.isolated_decode',side_effect=DomainError('TEST_DECODER_ERROR','Controlled decoder failure')):
            state=run(self.ctx,p['plan_id'],max_units=1)
        self.assertEqual(state['failed_units'],1);self.assertFalse(state['complete'])
        state=run(self.ctx,p['plan_id'],max_units=16)
        self.assertEqual(state['completed_units'],2);self.assertFalse(state['complete'])
        state=run(self.ctx,p['plan_id'],max_units=16,retry_errors=True)
        self.assertTrue(state['complete']);self.assertEqual(state['failed_units'],0)

    def test_segmentation_or_features_cannot_claim_protocol_completion(self):
        with self.assertRaisesRegex(DomainError,'Create and finish'):
            report(self.ctx,stop_reason='requested_checks_complete',stop_detail='All general features inspected but no actual protocol plan.')
        report(self.ctx,stop_reason='other',stop_detail='Partial result,protocol coverage not attempted yet.')
        r=json.loads((self.ctx.run/'report.json').read_text())
        self.assertFalse(r['protocol_coverage']['complete'])

    def test_channel_raster_covers_both_bands_and_time_subset_is_explicit(self):
        self.assertIn(2412e6,wifi_centers(2400e6,2483.5e6))
        self.assertEqual(wifi_centers(5720e6,5880e6),[v*1e6 for v in (5745,5765,5785,5805,5825,5845,5865)])
        req=json.loads((self.ctx.run/'request.json').read_text());req['task_intent'].update(analysis_start_s=.01,analysis_stop_s=.03);dump(self.ctx.run/'request.json',req)
        p=self.plan();self.assertEqual(p['requested_interval'],dict(sample_start=40000,sample_stop=120000))

    def test_comprehensive_survey_cannot_omit_weak_applicable_candidates(self):
        req=json.loads((self.ctx.run/'request.json').read_text());req['task_intent']['comprehensive_protocol_survey']=True;dump(self.ctx.run/'request.json',req)
        # This recording also contains IEEE802.15.4 channel11's technical envelope
        # only when its full2MHz passband fits. Check the independent5.8GHz list.
        from spectrum_agent.protocol_batch import obligations
        required=obligations(self.ctx,5720e6,5880e6)
        self.assertEqual(len(required),7)
        req['task_intent']['wide_wifi_headers_requested']=True;dump(self.ctx.run/'request.json',req)
        self.assertTrue(all(r['profiles']==['wifi_gr20_siso'] for r in obligations(self.ctx,5720e6,5880e6)))
        candidates=obligations(self.ctx,2400e6,2450e6)
        self.assertTrue(any(r['profiles']==['wifi_legacy_ofdm'] and r['center_hz']==2412e6 for r in candidates))
        self.assertTrue(any(r['family']=='Wi-Fi DSSS/CCK' and r['center_hz']==2412e6 for r in candidates))
        self.assertTrue(any(r['family']=='BLE primary' for r in candidates))
        self.assertTrue(any(r['family']=='IEEE802.15.4' for r in candidates))
        import numpy as np
        metadata=dict(center_hz=2412e6,sample_rate_hz=40e6,requested_span_hz=32e6,segments=[dict(file_start=0,count=4000)])
        with patch('spectrum_agent.protocol_batch.load_iq',return_value=(np.zeros(4000,dtype=np.complex64),metadata)):
            with self.assertRaisesRegex(DomainError,'missing_candidates'):
                self.plan()

    def test_numerical_bundle_keeps_unknown_and_individual_sample_coverage(self):
        from spectrum_agent.signal_batch import analyze
        result=analyze(self.ctx,self.cap,[dict(center_hz=2402e6,bandwidth_hz=1.5e6,output_rate_hz=4e6,
            sample_start=40000,sample_stop=80000,metrics=['features','bursts','ofdm'])],
            'Measure residual structure without inferring a protocol identity',[self.prior])
        self.assertEqual(result['protocol'],'unknown')
        self.assertEqual(set(result['results'][0]['measurements']),{'features','bursts','ofdm'})
        self.assertEqual(result['results'][0]['measurements']['ofdm']['protocol'],'unknown')

    def test_wifi_baseline_preserves_validated_filter_and_semantic_retry(self):
        import numpy as np
        req=json.loads((self.ctx.run/'request.json').read_text())
        req['task_intent']['comprehensive_protocol_survey']=True
        dump(self.ctx.run/'request.json',req)
        metadata=dict(center_hz=5745e6,sample_rate_hz=40e6,requested_span_hz=32e6,segments=[dict(file_start=0,count=4000)])
        check=dict(profile='wifi_gr20_siso',center_hz=5745e6,reason='Validated whole-record protocol baseline')
        with patch('spectrum_agent.protocol_batch.load_iq',return_value=(np.zeros(4000,dtype=np.complex64),metadata)):
            default=plan(self.ctx,self.cap,[check],'Use validated profile defaults for a reproducible baseline',[self.prior])
            explicit=plan(self.ctx,self.cap,[dict(check,bandwidth_hz=18e6,output_rate_hz=20e6,reason='Same receiver selection with different prose')],
                'Retry must reuse completed work rather than duplicate the sweep',[self.prior])
            self.assertEqual(default['plan_id'],explicit['plan_id'])
            with self.assertRaisesRegex(DomainError,'validated18MHz'):
                plan(self.ctx,self.cap,[dict(check,bandwidth_hz=17.5e6,output_rate_hz=20e6)],'Do not silently narrow the extraction passband on retry',[self.prior])
            with self.assertRaisesRegex(DomainError,'missing_candidates'):
                plan(self.ctx,self.cap,[dict(check,profile='wifi_crafter_dsss')],'A DSSS-only decoder cannot cover the OFDM candidate',[self.prior])

    def test_provisional_duration_cannot_silently_crop_saved_recording(self):
        from spectrum_agent.service import normalize
        from spectrum_agent.task_intent import resolve
        dump(self.ctx.run/'outcome.json',dict(status='succeeded'))
        target=Context('protocol_scope_test_'+uuid.uuid4().hex[:10])
        prompt='复用 '+self.ctx.task_id+' 的全部已记录时间，不重新采集'
        dump(target.run/'request.json',normalize(dict(prompt=prompt,source='synthetic')))
        arguments=dict(source_task_id=self.ctx.task_id,evidence_quotes=[self.ctx.task_id],reason='Reuse all recorded time,never infer a crop from the default duration',acquisition_requested=False)
        with self.assertRaisesRegex(DomainError,'ENTIRE actual saved duration'):
            resolve(target,analysis_stop_s=.1,**arguments)
        resolve(target,**arguments)
        self.assertEqual(json.loads((target.run/'request.json').read_text())['task_intent']['analysis_stop_s'],.25)
