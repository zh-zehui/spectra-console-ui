import json,unittest,uuid
from unittest.mock import patch
import numpy as np
from scipy import signal
from spectrum_agent.common import ROOT,Context,DomainError,dump
from spectrum_agent.drone_video import SignatureDetector,StreamingFrontend,profile,analyze,project,_resample
from spectrum_agent.worker import report


def waveform(root=123,scs=30000,noise=.002,p=None):
    p=p or profile();rate=p['sample_rate_hz'];nfft=p['fft_size'];length=p['zc_length'];cp=p['cyclic_prefix_samples']
    rng=np.random.default_rng(7351);count=round(.01*rate)
    out=(noise*(rng.normal(size=count)+1j*rng.normal(size=count))).astype(np.complex64)
    k=np.arange(length);bins=np.zeros(nfft,complex);bins[nfft//2-length//2:nfft//2+length//2+1]=np.exp(-1j*np.pi*root*k*(k+1)/length)
    sym=np.roll(np.fft.ifft(np.fft.ifftshift(bins)),148);sym/=np.sqrt(np.mean(abs(sym)**2))
    known=[]
    for t in (.0002,.0027,.0052,.0077):
        start=round(t*rate);reference=np.r_[sym[-cp:],sym]
        out[start:start+len(reference)]+=.1*reference
        known.append(start)
    out*=np.exp(2j*np.pi*3900/rate*np.arange(count)).astype(np.complex64)
    return out,known


class DroneVideoTests(unittest.TestCase):
    def test_intermediate_reference_matches_and_rejects_other_modes(self):
        p=profile('zc601_u252_30k_observed_v1');x,known=waveform(root=252,p=p)
        rows=SignatureDetector(p).detect(x)
        self.assertEqual(len(rows),4)
        self.assertTrue(all(r['signature_score']>.85 for r in rows))
        wrong,unused=waveform(root=253,p=p)
        self.assertEqual(SignatureDetector(p).detect(wrong),[])
        self.assertEqual(SignatureDetector().detect(signal.resample_poly(x,1,2)),[])
        wide=signal.resample_poly(x,2,1)
        self.assertEqual(SignatureDetector(profile('zc1201_u504_30k_observed_v1')).detect(wide),[])

    def test_wider_reference_is_distinct_and_old_mode_is_not_forced(self):
        p=profile('zc1201_u504_30k_observed_v1');x,known=waveform(root=504,p=p)
        rows=SignatureDetector(p).detect(x)
        self.assertEqual(len(rows),4)
        self.assertTrue(all(r['signature_score']>.85 for r in rows))
        wrong,unused=waveform(root=505,p=p)
        self.assertEqual(SignatureDetector(p).detect(wrong),[])
        self.assertEqual(SignatureDetector().detect(signal.resample_poly(x,1,4)),[])
        self.assertEqual(profile()['zc_length'],293)

    def test_reference_matches_under_cfo_and_is_not_protocol_crc(self):
        x,known=waveform();rows=SignatureDetector().detect(x)
        json.dumps(rows)  # NDJSON event consumers require plain serializable scalars.
        self.assertEqual(len(rows),4)
        for expected in known:
            self.assertTrue(any(abs(row['reference_sample_start']-expected)<40 for row in rows))
        self.assertTrue(all(row['signature_score']>.85 for row in rows))

    def test_noise_tone_wrong_root_and_nr_like_random_ofdm_are_negative(self):
        rng=np.random.default_rng(86);detector=SignatureDetector();rate=detector.rate
        noise=(rng.normal(size=153600)+1j*rng.normal(size=153600)).astype(np.complex64)
        tone=np.exp(2j*np.pi*.173*np.arange(len(noise))).astype(np.complex64)
        qpsk=(2*rng.integers(0,2,(260,512))-1)+1j*(2*rng.integers(0,2,(260,512))-1)
        symbols=np.fft.ifft(qpsk,axis=1);nr_like=np.concatenate((symbols[:,-36:],symbols),axis=1).ravel().astype(np.complex64)
        wrong,_=waveform(root=124)
        for label,x in [('noise',noise),('tone',tone),('wrong_root',wrong),('30k_random_ofdm',nr_like)]:
            with self.subTest(label=label):self.assertEqual(detector.detect(x),[])

    def test_stream_boundary_context_and_deduplication(self):
        x,known=waveform();split=known[1]+100;detector=SignatureDetector()
        rows=detector.process(x[:split])+detector.process(x[split:])
        self.assertEqual(len(rows),4)
        self.assertEqual(len({r['reference_sample_start'] for r in rows}),4)

    def test_stream_resampling_keeps_filter_and_phase_continuous(self):
        rng=np.random.default_rng(773);raw=(rng.normal(size=40003)+1j*rng.normal(size=40003)).astype(np.complex64)
        frontend=StreamingFrontend(20e6,5800e6,5803.5e6)
        pieces=[];next_sample=0
        for x in (raw[:7001],raw[7001:16003],raw[16003:]):
            out,origin=frontend.process(x);self.assertEqual(origin,next_sample);pieces.append(out);next_sample+=len(out)
        out,origin=frontend.process(np.empty(0,dtype=np.complex64),final=True);self.assertEqual(origin,next_sample);pieces.append(out)
        actual=np.concatenate(pieces)
        shifted=raw*np.exp(-2j*np.pi*3.5e6/20e6*np.arange(len(raw))).astype(np.complex64)
        expected=_resample(shifted,96,125)
        self.assertEqual(actual.shape,expected.shape)
        np.testing.assert_allclose(actual,expected,atol=4e-6,rtol=4e-6)

    def test_native_resampler_matches_reference_if_available(self):
        if not (ROOT/'runtime/drone-video/libspectrum_drone_resample.so').exists():self.skipTest('Optional native kernel not built in this environment')
        rng=np.random.default_rng(783);raw=(rng.normal(size=10003)+1j*rng.normal(size=10003)).astype(np.complex64)
        for up,down in ((48,625),(96,125),(624,625),(2,3)):
            taps=signal.firwin(20*max(up,down)+1,1/max(up,down),window=('kaiser',5.)).astype(np.float32)
            expected=signal.resample_poly(raw,up,down,window=taps)
            np.testing.assert_allclose(_resample(raw,up,down),expected,atol=4e-6,rtol=4e-6)

    def test_native_acquisition_keeps_python_reference_detections(self):
        detector=SignatureDetector()
        if not detector.native_handle:self.skipTest('Optional native acquisition not built')
        x,known=waveform();native=detector.detect(x)
        handle=detector.native_handle;detector.native_handle=None
        try:reference=detector.detect(x)
        finally:detector.native_handle=handle
        self.assertEqual([r['reference_sample_start'] for r in native],[r['reference_sample_start'] for r in reference])
        np.testing.assert_allclose([r['signature_score'] for r in native],[r['signature_score'] for r in reference],atol=1e-5)

    def test_checkpoint_coverage_and_report_separate_from_wifi(self):
        ctx=Context('drone_video_test_'+uuid.uuid4().hex[:10]);x,known=waveform()
        aid,path=ctx.artifact('synthetic','cf32');np.tile(x,2).tofile(path)
        m=dict(center_hz=5803.5e6,sample_rate_hz=15.36e6,requested_span_hz=12e6,sample_count=len(x)*2,duration_s=.02,
            simulated=True,segments=[dict(file_start=0,count=len(x)*2,hardware_start_s=0)],gaps=[],gain_db=20)
        dump(path.with_suffix('.json'),m);ctx.register(aid,path,kind='capture',metadata_path=str(path.with_suffix('.json')))
        dump(ctx.run/'request.json',dict(prompt='Only test target waveform',source='synthetic',constraints=dict(center_hz=5803.5e6,span_hz=12e6),
            task_intent=dict(status='resolved',analysis_requested=True,protocol_analysis_requested=False,protocol_workflow_version=2,
                drone_video_analysis_requested=True,analysis_start_s=0,analysis_stop_s=.02)))
        prior=ctx.json_artifact('priors',dict(observed=False));reason='Test full target time coverage and separate signature evidence'
        with self.assertRaisesRegex(DomainError,'Finish analyze_drone_video_link'):
            report(ctx,stop_reason='requested_checks_complete',stop_detail='Requested target must actually be checked')
        first=analyze(ctx,aid,5803.5e6,reason,[prior],max_blocks=1)
        self.assertFalse(first['coverage']['complete'])
        final=analyze(ctx,aid,5803.5e6,reason,[prior],max_blocks=1)
        self.assertTrue(final['coverage']['complete']);self.assertEqual(final['matched_references'],8)
        self.assertEqual(len(project(ctx)),1)
        with patch('spectrum_agent.drone_video._frontend',side_effect=AssertionError('No reprocessing')):
            self.assertTrue(analyze(ctx,aid,5803.5e6,reason,[prior])['coverage']['complete'])
        report(ctx,stop_reason='requested_checks_complete',stop_detail='Selected reference checked over all requested samples')
        value=json.loads((ctx.run/'report.json').read_text())
        self.assertEqual(value['frame_associations'],[]);self.assertEqual(value['verified_protocols'],[])
        self.assertEqual(value['drone_video_evidence'][0]['matched_references'],8)
        self.assertFalse(value['drone_video_evidence'][0]['attribution']['protocol_confirmed'])


if __name__=='__main__':unittest.main()
