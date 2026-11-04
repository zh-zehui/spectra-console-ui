import json,unittest,uuid
from fractions import Fraction
import numpy as np
from scipy import signal
from spectrum_agent.common import Context,dump,DomainError
from spectrum_agent.drone_video import profile
from spectrum_agent.drone_hopping import analyze,project,coverage_complete,FFTChannelBank,compact_timings
from spectrum_agent.worker import report,execute


def make_agile_capture(ctx,noise_only=False):
    fs=200e6;fc=5800e6;n=round(.024*fs)
    rng=np.random.default_rng(735108);x=(.0001*(rng.normal(size=n)+1j*rng.normal(size=n))).astype(np.complex64)
    schedule=[(.0012,'zc293_u123_30k_observed_v1',5747.5e6),(.0037,'zc601_u252_30k_observed_v1',5798.5e6),
        (.00999,'zc1201_u504_30k_observed_v1',5828.5e6),(.0127,'zc293_u123_30k_observed_v1',5747.5e6),
        (.0162,'zc601_u252_30k_observed_v1',5798.5e6),(.0212,'zc1201_u504_30k_observed_v1',5828.5e6)]
    if not noise_only:
        for t,pid,center in schedule:
            p=profile(pid);length=p['zc_length'];fft_size=p['fft_size'];cp=p['cyclic_prefix_samples'];rate=p['sample_rate_hz'];k=np.arange(length)
            bins=np.zeros(fft_size,complex);bins[fft_size//2-length//2:fft_size//2+length//2+1]=np.exp(-1j*np.pi*p['zc_root']*k*(k+1)/length)
            symbols=[]
            for i in range(10):
                actual=bins if i==2 else bins*((2*rng.integers(0,2,fft_size)-1)+1j*(2*rng.integers(0,2,fft_size)-1))
                sym=np.roll(np.fft.ifft(np.fft.ifftshift(actual)),148);sym/=np.sqrt(np.mean(abs(sym)**2))
                symbols.append(np.r_[sym[-cp:],sym])
            canonical=np.concatenate(symbols).astype(np.complex64);ratio=Fraction(fs/rate).limit_denominator(100000)
            raw=signal.resample_poly(canonical,ratio.numerator,ratio.denominator).astype(np.complex64)
            raw*=np.exp(2j*np.pi*(center-fc)/fs*np.arange(len(raw))).astype(np.complex64)
            start=round(t*fs);x[start:start+len(raw)]+=.05*raw
    aid,path=ctx.artifact('synthetic_hopping','cf32');x.tofile(path)
    meta=dict(kind='synthetic',simulated=True,center_hz=fc,sample_rate_hz=fs,requested_span_hz=160e6,
        sample_count=len(x),duration_s=len(x)/fs,segments=[dict(file_start=0,count=len(x),hardware_start_s=0)],gaps=[],gain_db=20)
    dump(path.with_suffix('.json'),meta);ctx.register(aid,path,kind='capture',metadata_path=str(path.with_suffix('.json')))
    dump(ctx.run/'request.json',dict(prompt='Synthetic agile test only',source='synthetic',constraints=dict(center_hz=fc,span_hz=160e6),
        task_intent=dict(status='resolved',analysis_requested=True,drone_video_analysis_requested=True,drone_hopping_analysis_requested=True,
            protocol_analysis_requested=False,protocol_workflow_version=2,analysis_start_s=0,analysis_stop_s=meta['duration_s'])))
    return aid,ctx.json_artifact('priors',dict(simulated=True)),schedule


class DroneHoppingTests(unittest.TestCase):
    def test_legacy_timing_compaction_retains_exact_sums_and_probe_counts(self):
        old=[dict(stage='discovery',unit_index=0,elapsed_ms=7),
            dict(stage='reference_probe',unit_index=0,frontend_ms=2.,core_ms=3.),
            dict(stage='reference_probe',unit_index=0,frontend_ms=5.,core_ms=11.),
            dict(stage='reference_probe',unit_index=1,frontend_ms=13.,core_ms=17.,probe_count=4)]
        result=compact_timings(old);self.assertEqual(len(result),3)
        self.assertEqual(result[1]['frontend_ms'],7.);self.assertEqual(result[1]['core_ms'],14.)
        self.assertEqual(result[1]['probe_count'],2);self.assertEqual(result[2]['probe_count'],4)
        self.assertEqual(compact_timings(result),result)

    def test_shared_fft_channel_bank_amplitude_frequency_and_out_of_band_rejection(self):
        fs=200e6;p=profile('zc601_u252_30k_observed_v1');t=np.arange(200000)/fs
        x=(np.exp(2j*np.pi*25.5e6*t)+.4*np.exp(-2j*np.pi*64e6*t)).astype(np.complex64)
        bank=FFTChannelBank(x,fs,{'mode':p});y,offset=bank.extract('mode',25e6)
        expected=np.exp(2j*np.pi*.5e6*np.arange(len(y))/p['sample_rate_hz'])
        self.assertEqual(offset,25e6)
        np.testing.assert_allclose(y,expected,atol=3e-5,rtol=3e-5)

    def test_within_window_hops_width_changes_and_checkpoint_resume(self):
        ctx=Context('hop_test_'+uuid.uuid4().hex[:10]);aid,prior,schedule=make_agile_capture(ctx)
        modes={pid:profile(pid) for pid in sorted({v[1] for v in schedule})}
        ctx.json_artifact('drone_hopping_analysis',dict(capture_id=aid,analysis_key='legacy_prefix',profiles=modes,
            coverage=dict(sample_start=0,sample_stop=4800000,complete=False),matched_references=0,references=[]))
        reason='Synthetic test of variable frequency and bandwidth; no RF truth claim'
        first=execute(ctx.task_id,'analyze_drone_hopping',dict(capture_id=aid,reason=reason,evidence_ids=[prior],max_probes=1))
        self.assertFalse(first['result']['coverage']['complete'])
        with self.assertRaisesRegex(DomainError,'Finish analyze_drone_hopping'):
            report(ctx,stop_reason='requested_checks_complete',stop_detail='Cannot call partial scanning complete')
        result=None
        for _ in range(30):
            result=execute(ctx.task_id,'analyze_drone_hopping',dict(capture_id=aid,reason=reason,evidence_ids=[prior],max_blocks=8,max_probes=512))
            if result['result']['coverage']['complete']:break
        self.assertTrue(result['result']['coverage']['complete'])
        evidence=project(ctx);self.assertEqual(len(evidence),1);rows=evidence[0]['references']
        self.assertIn('legacy_prefix',evidence[0]['supersedes_analysis_keys'])
        for t,pid,center in schedule:
            self.assertTrue(any(r['profile_id']==pid and abs(r['center_hz']-center)<=1e6 and t<=r['reference_time_s']<t+.0004 for r in rows),(t,pid,center,rows))
        self.assertEqual(len(rows),len(schedule))
        self.assertTrue(evidence[0]['sequence']['multiple_frequency_candidates'])
        self.assertTrue(evidence[0]['sequence']['multiple_bandwidth_candidates'])
        self.assertFalse(evidence[0]['sequence']['aircraft_identity_confirmed'])
        report(ctx,stop_reason='requested_checks_complete',stop_detail='Synthetic full proposed scope completed; not a live RF claim')
        final=json.loads((ctx.run/'report.json').read_text());self.assertEqual(final['drone_video_evidence'],[])
        self.assertEqual(final['frame_associations'],[]);self.assertEqual(final['drone_hopping_evidence'][0]['matched_references'],6)

    def test_noise_is_not_drone_and_fixed_tool_cannot_satisfy_hopping_intent(self):
        ctx=Context('hop_noise_'+uuid.uuid4().hex[:10]);aid,prior,_=make_agile_capture(ctx,noise_only=True)
        result=analyze(ctx,aid,'Synthetic noise negative control only',[prior],max_blocks=8,max_probes=512)
        self.assertTrue(result['coverage']['complete']);self.assertEqual(result['matched_references'],0)
        self.assertFalse(coverage_complete(dict(drone_video_evidence=[dict(coverage=dict(complete=True))]),dict(drone_hopping_analysis_requested=True)))
        self.assertTrue(coverage_complete(dict(drone_video_evidence=[dict(coverage=dict(complete=True))]),{}))


if __name__=='__main__':unittest.main()
