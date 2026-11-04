import contextlib
import io
import sys
import unittest
import zlib
import uuid
import numpy as np
from spectrum_agent.common import ROOT
from spectrum_agent import wifi_modern


def fixture(fmt='HT',mcs=0,short_gi=False,bad_fcs=False,cfo=0.,aggregate_count=1):
    sys.path.insert(0,str(ROOT/'vendor/gr-wifi-modern/tools'))
    import phy80211,phy80211header as ph
    from pktGenExample import genMac80211UdpMPDU,genMac80211UdpAmpduVht
    payload=b'spectrum modern decoder acceptance'
    with contextlib.redirect_stdout(io.StringIO()):
        generator=phy80211.phy80211(ifDebug=False)
        settings=ph.modulation(phyFormat=getattr(ph.F,fmt),mcs=mcs,bw=ph.BW.BW20,nSTS=1,shortGi=short_gi)
        if fmt=='VHT':
            packet=genMac80211UdpAmpduVht([payload+bytes([i]) for i in range(aggregate_count)])
            if bad_fcs:
                corrupt=packet.index(payload)
                packet=packet[:corrupt]+bytes([packet[corrupt]^1])+packet[corrupt+1:]
            generator.genFromAmpdu(packet,settings,vhtPartialAid=0,vhtGroupId=0)
        else:
            packet=genMac80211UdpMPDU(payload)
            if bad_fcs:packet=packet[:-1]+bytes([packet[-1]^1])
            generator.genFromMpdu(packet,settings)
        wave=generator.genFinalSig(multiplier=1.,cfoHz=0.,num=1,gap=True,gapLen=4000)
    x=np.asarray(wave,dtype=np.complex64)
    if x.ndim==2:x=x[0]
    if short_gi:
        # Pinned PY-TB sets the SGI header bit but always calls procGi with16-sample CP.
        # Correct only test waveform data CP to8; training/header CP remains16.
        start=4000+(720 if fmt=='HT' else 800)
        end=start+80*generator.m.nSym
        data=x[start:end].reshape(-1,80)[:,8:].ravel()
        x=np.concatenate([x[:start],data,x[end:]])
    if cfo:x=(x*np.exp(2j*np.pi*cfo*np.arange(len(x))/20e6)).astype(np.complex64)
    return x


class ModernWifiTests(unittest.TestCase):
    def test_parser_crc_and_non_frame_messages(self):
        packet=b'\x08\x00'+b'test-frame'*4
        body=packet+zlib.crc32(packet).to_bytes(4,'little')
        raw=bytes([1])+len(body).to_bytes(2,'little')+body+b'\x00'
        self.assertEqual(wifi_modern.parse_message(raw)['frame_hex'],packet.hex())
        self.assertIsNone(wifi_modern.parse_message(raw[:-2]+bytes([raw[-2]^1])+raw[-1:]))
        self.assertIsNone(wifi_modern.parse_message(bytes([20])+raw[1:]))
        self.assertIsNone(wifi_modern.parse_message(raw[:-1]))

    def test_legacy_ht_vht_mcs0_through7(self):
        for fmt,expected in (('L','legacy'),('HT','HT'),('VHT','VHT')):
            for mcs in range(8):
                with self.subTest(fmt=fmt,mcs=mcs):
                    print('native case',fmt,mcs,flush=True)
                    x=fixture(fmt,mcs);frames,stages=wifi_modern.receive_iq(x,True)
                    self.assertEqual(len(frames),1)
                    f=frames[0];self.assertEqual(f['phy_format'],expected);self.assertEqual(f['mcs'],mcs)
                    self.assertLessEqual(abs(f['subband_sample_start']-4000),16)
                    self.assertLessEqual(f['subband_sample_stop'],len(x))
                    self.assertEqual(stages['strongest_stage'],'mac_fcs_pass')
                    self.assertGreaterEqual(stages['counts']['lsig_pass'],1)
                    if fmt=='HT':self.assertGreaterEqual(stages['counts']['ht_sig_pass'],1)
                    if fmt=='VHT':self.assertGreaterEqual(stages['counts']['vht_sig_a_pass'],1)

    def test_wrong_fcs_and_noise_do_not_confirm(self):
        for fmt in ('L','HT','VHT'):
            with self.subTest(fmt=fmt):
                frames,stages=wifi_modern.receive_iq(fixture(fmt,bad_fcs=True),True)
                self.assertEqual(frames,[])
                self.assertGreater(stages['counts'].get('fcs_failed',0),0)
        rng=np.random.default_rng(520)
        noise=(rng.normal(size=100000)+1j*rng.normal(size=100000)).astype(np.complex64)
        self.assertEqual(wifi_modern.receive_iq(noise),[])

    def test_offsets_cfo_and_short_gi(self):
        for fmt in ('HT','VHT'):
            x=fixture(fmt,mcs=3,short_gi=True,cfo=30000)
            for pad in (1,79,511):
                with self.subTest(fmt=fmt,pad=pad):
                    frames=wifi_modern.receive_iq(np.concatenate([np.zeros(pad,np.complex64),x]))
                    self.assertEqual(len(frames),1)
                    self.assertLessEqual(abs(frames[0]['subband_sample_start']-4000-pad),16)

    def test_vht_two_mpdu_aggregation(self):
        frames=wifi_modern.receive_iq(fixture('VHT',aggregate_count=2))
        self.assertEqual(len(frames),2)
        self.assertNotEqual(frames[0]['frame_hex'],frames[1]['frame_hex'])
        self.assertEqual(frames[0]['subband_sample_start'],frames[1]['subband_sample_start'])
        self.assertEqual(frames[0]['timing_scope'],'containing_ppdu_not_individual_aggregated_mpdu')

    def test_registered_worker_dispatch_audit_and_cache(self):
        from spectrum_agent.common import Context,dump
        from spectrum_agent.worker import execute
        from spectrum_agent.investigation import read
        ctx=Context('wifi_modern_worker_test_'+uuid.uuid4().hex[:10])
        x=fixture('HT');aid,path=ctx.artifact('fixture','cf32');x.tofile(path)
        mp=path.with_suffix('.json');dump(mp,dict(center_hz=2412e6,sample_rate_hz=20e6,
            sample_count=len(x),duration_s=len(x)/20e6,requested_span_hz=17e6,simulated=True,
            segments=[dict(file_start=0,count=len(x),hardware_start_s=None)]))
        ctx.register(aid,path,kind='subband',metadata_path=str(mp))
        args=dict(artifact_id=aid,protocol='wifi_gr20_siso')
        out=execute(ctx.task_id,'verify_protocol',args)
        self.assertEqual(out['status'],'succeeded')
        self.assertEqual(out['result']['frames'][0]['phy_format'],'HT')
        attempt=read(ctx,out['result']['attempt_id'])
        self.assertIsNone(attempt['arguments']['channel'])
        self.assertEqual(attempt['verification_stages']['strongest_stage'],'mac_fcs_pass')
        again=execute(ctx.task_id,'verify_protocol',args)
        self.assertTrue(again['cached'])
        self.assertEqual(again['result']['evidence_id'],out['result']['evidence_id'])


if __name__=='__main__':unittest.main()
