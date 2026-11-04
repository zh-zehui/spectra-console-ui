import unittest,json,uuid
import numpy as np
from spectrum_agent.common import Context,dump
from spectrum_agent.dsp import synthetic
from spectrum_agent.window_analysis import analyze,stitch
from spectrum_agent.wifi_headers import parse,crc8
from spectrum_agent.ofdm_structure import measure


class WindowHeaderTests(unittest.TestCase):
    def test_wide_header_association_needs_all_slices_for_channel_center(self):
        from spectrum_agent.header_evidence import associate
        def h(center,start=100):return dict(capture_id='capture_a',header_bits='same',sample_start=start,sample_stop=1000,
            observed_center_hz=center,channel_width_hz=80e6,evidence_id=str(center),header_index=0,
            freq_low_hz=center-9e6,freq_high_hz=center+9e6,channel_center_hz=None)
        result=associate([h(c) for c in (5745e6,5765e6,5785e6,5805e6)],200e6)
        self.assertEqual(len(result),1);self.assertEqual(result[0]['channel_center_hz'],5775e6)
        result=associate([h(5745e6),h(5765e6)],200e6)
        self.assertIsNone(result[0]['channel_center_hz'])
        self.assertEqual(len(associate([h(5745e6),h(5745e6,10000)],200e6)),2)

    def test_control_subtypes_are_mac_bytes_not_new_protocols(self):
        from spectrum_agent.mac_details import subtype,label
        for code,name in ((4,'BRP'),(9,'BA'),(11,'RTS')):
            frame=dict(mac_header_hex=bytes([(code<<4)|4,0]).hex(),phy_format='legacy',mcs=4)
            self.assertEqual(subtype(frame),name);self.assertIn('L-SIG R24',label(frame))

    def test_window_context_and_checkpoint_preserve_original_iq(self):
        ctx=Context('window_test_'+uuid.uuid4().hex[:10])
        cap=synthetic(ctx,sample_rate_hz=4e6,duration_s=.025)
        first=analyze(ctx,cap['capture_id'],max_windows=1,fft_size=512,time_bin_s=.0001,frequency_bin_hz=250e3)
        self.assertEqual(first['remaining_windows'],2)
        final=analyze(ctx,cap['capture_id'],max_windows=8,fft_size=512,time_bin_s=.0001,frequency_bin_hz=250e3)
        self.assertTrue(final['complete']);self.assertEqual(final['total_windows'],3)
        from pathlib import Path
        value=json.loads(Path(ctx.resolve(final['segmentation_id'])['path']).read_text())
        self.assertEqual(value['coverage']['observed_samples'],100000)
        self.assertEqual(value['window_analysis']['windows'][-1]['core_stop'],100000)
        self.assertEqual(analyze(ctx,cap['capture_id'],max_windows=8,fft_size=512,time_bin_s=.0001,frequency_bin_hz=250e3)['segmentation_id'],final['segmentation_id'])

    def test_cross_window_overlap_merged_but_distinct_bursts_and_gaps_retained(self):
        def e(a,b):return dict(group_id='g0',sample_start=a,sample_stop=b,freq_low_hz=100,freq_high_hz=120)
        windows=[dict(core_start=0,core_stop=100,segmentation_id='one',events=[e(90,115)]),
                 dict(core_start=100,core_stop=200,segmentation_id='two',events=[e(90,115),e(150,160)])]
        result=stitch(windows,[dict(file_start=0,count=200)],1e6)
        self.assertEqual(len(result),2);self.assertEqual(len(result[0]['window_members']),2)
        windows[1]['events'][0]=e(101,115)
        result=stitch(windows,[dict(file_start=0,count=100),dict(file_start=100,count=100)],1e6)
        self.assertEqual(len(result),3)

    def test_vht80_crc_valid_header_is_not_mac_frame(self):
        bits=[0]*48
        for i in (1,2,23,33):bits[i]=1
        bits[34:42]=crc8(bits[:34])
        header=dict(format='VHT',bits=bits,sample_start=10,sample_stop=1000,lsig_mcs=0,lsig_length=100)
        value=parse(header,2000)
        self.assertEqual(value['channel_width_hz'],80e6);self.assertEqual(value['lsig_rate_mbps'],6)
        self.assertFalse(value['payload_decoded']);self.assertIsNone(value['mac_fcs_pass'])
        bits[4]^=1;self.assertIsNone(parse(header,2000))

    def test_cp_ofdm_positive_noise_and_tone_negative(self):
        rng=np.random.default_rng(77);symbols=rng.normal(size=(160,64))+1j*rng.normal(size=(160,64))
        x=np.concatenate([np.concatenate([s[-16:],s]) for s in symbols])
        result=measure(x,20e6)
        self.assertTrue(any(abs(v['cyclic_prefix_s']-.8e-6)<1e-8 for v in result))
        noise=rng.normal(size=len(x))+1j*rng.normal(size=len(x))
        self.assertEqual(measure(noise,20e6),[])
        self.assertEqual(measure(np.exp(2j*np.pi*.1*np.arange(len(x))),20e6),[])
