import json
import sys
import time
import unittest
import zlib
import uuid
import numpy as np
from spectrum_agent.common import ROOT,Context,dump
from spectrum_agent.wifi import module,receive_iq


def offline_wifi_packet(bad_crc=False):
    # Official upstream baseband transmitter only: no USRP sink or RF transmission.
    from gnuradio import gr,blocks
    import pmt
    ieee=module()
    sys.path.insert(0,str(ROOT/"runtime/generated"))
    from wifi_phy_hier import wifi_phy_hier
    payload=bytes.fromhex("08000000ffffffffffff0200000000010200000000010000")+b"spectrum-offline-test"
    crc=zlib.crc32(payload).to_bytes(4,"little")
    if bad_crc: crc=bytes([crc[0]^1])+crc[1:]
    raw=payload+crc
    pdu=pmt.cons(pmt.PMT_NIL,pmt.init_u8vector(len(raw),list(raw)))
    phy=wifi_phy_hier(bandwidth=20e6,frequency=2412e6,encoding=ieee.BPSK_1_2)
    zeros=blocks.vector_source_c([0j]*4096,True)
    throttle=blocks.throttle(gr.sizeof_gr_complex,20e6)
    strobe=blocks.message_strobe(pdu,100)
    sink=blocks.vector_sink_c()
    tb=gr.top_block()
    tb.connect(zeros,throttle,phy,sink)
    tb.msg_connect(strobe,"strobe",phy,"mac_in")
    tb.start()
    try:
        for _ in range(50):
            if sink.nitems_read(0)>1000:
                time.sleep(.05)
                break
            time.sleep(.02)
    finally:
        tb.stop();tb.wait()
    data=np.asarray(sink.data(),dtype=np.complex64)
    if not len(data): raise RuntimeError("Offline Wi-Fi transmitter produced no samples")
    return np.concatenate([np.zeros(2000,np.complex64),data,np.zeros(2000,np.complex64)]),payload


class WifiTests(unittest.TestCase):
    def test_source_sample_positions_survive_offsets_and_repeats(self):
        samples,payload=offline_wifi_packet()
        baseline=receive_iq(samples)
        self.assertGreater(len(baseline),0)
        first=int(np.flatnonzero(abs(samples)>1e-5)[0])
        self.assertLessEqual(abs(baseline[0]['subband_sample_start']-first),8)
        for pad in (1,127,8193):
            shifted=receive_iq(np.r_[np.zeros(pad),samples].astype(np.complex64))
            self.assertEqual(len(shifted),len(baseline))
            for a,b in zip(baseline,shifted):
                self.assertLessEqual(abs(b['subband_sample_start']-a['subband_sample_start']-pad),8)
        repeated=receive_iq(np.r_[samples,np.zeros(4096),samples].astype(np.complex64))
        self.assertEqual(len(repeated),2*len(baseline))
        self.assertGreater(repeated[-1]['subband_sample_start'],repeated[0]['subband_sample_stop'])

    def test_parent_sample_mapping_after_filter_and_resampling(self):
        from scipy.signal import resample_poly
        from spectrum_agent import dsp,wifi
        from spectrum_agent.worker import report
        ctx=Context('wifi_mapping_'+uuid.uuid4().hex[:10])
        samples,payload=offline_wifi_packet()
        iq=resample_poly(samples,2,1).astype(np.complex64)
        aid,path=ctx.artifact('fixture','cf32');iq.tofile(path)
        dump(path.with_suffix('.json'),dict(center_hz=2412e6,sample_rate_hz=40e6,requested_span_hz=32e6,
            sample_count=len(iq),duration_s=len(iq)/40e6,simulated=True,segments=[dict(file_start=0,count=len(iq),hardware_start_s=None)]))
        ctx.register(aid,path,kind='capture',metadata_path=str(path.with_suffix('.json')))
        sub=dsp.extract(ctx,aid,2412e6,17e6,output_rate_hz=20e6)
        verified=wifi.verify(ctx,sub['artifact_id'])
        self.assertGreater(verified['valid_frames'],0)
        report(ctx)
        result=json.loads((ctx.run/'report.json').read_text())
        links=result['frame_associations'];self.assertGreater(len(links),0)
        self.assertEqual(links[0]['association_level'],'packet_time_frequency_overlap')
        expected=2*int(np.flatnonzero(abs(samples)>1e-5)[0])
        self.assertLessEqual(abs(links[0]['sample_start']-expected),32)
        from spectrum_agent import scene
        sid=ctx.json_artifact('segmentation',dict(capture_id=aid,fft_size=128,frequency_resolution_hz=40e6/128,
            events=[],signal_groups=[dict(group_id='g0',sample_start=expected+400,sample_stop=expected+2000,
                freq_low_hz=2403.5e6,freq_high_hz=2420.5e6,bandwidth_hz=17e6,protocol='unknown')]))
        review=scene.assess(ctx,sid)
        self.assertTrue(any('verified_packet_extends_candidate_group' in g['flags'] for g in review['review_targets']))

    def test_known_frame_fcs_and_negative_controls(self):
        samples,payload=offline_wifi_packet()
        packets=receive_iq(samples)
        self.assertGreater(len(packets),0,"No frame decoded from upstream loopback")
        self.assertTrue(any(p["frame_hex"]==payload.hex() for p in packets))
        corrupt,_=offline_wifi_packet(bad_crc=True)
        self.assertEqual(receive_iq(corrupt),[])
        rng=np.random.default_rng(82)
        noise=(rng.normal(size=100000)+1j*rng.normal(size=100000)).astype(np.complex64)
        self.assertEqual(receive_iq(noise),[])
