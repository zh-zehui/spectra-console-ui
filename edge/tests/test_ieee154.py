import ast
import json
import unittest
import uuid
from pathlib import Path
import numpy as np
import yaml
from spectrum_agent.common import ROOT,Context,dump,DomainError
from spectrum_agent import ieee154


def fixture(bad_crc=False,preamble=True):
    # Reproduce the pinned upstream baseband TX chip mapper and half-sine shaping.
    # Pure arrays: no radio hardware or transmit sink.
    graph=yaml.safe_load((ROOT/'vendor/gr-ieee802-15-4/examples/ieee802_15_4_OQPSK_PHY.grc').read_text())
    table=next(b['parameters']['symbol_table'] for b in graph['blocks'] if b['name']=='digital_chunks_to_symbols_xx_0')
    table=np.array(ast.literal_eval(table),dtype=np.complex64).reshape(16,16)
    payload=bytes.fromhex('418801cdab34127856')+b'known-802154-test'
    fcs=ieee154.crc16(payload).to_bytes(2,'little')
    if bad_crc: fcs=bytes([fcs[0]^1])+fcs[1:]
    raw=payload+fcs
    ppdu=(b'\x00'*4+b'\xa7' if preamble else b'\xff'*5)+bytes([len(raw)])+raw
    # GNU Radio GR_LSB_FIRST unpacks bit order within each4-bit chunk as well.
    nibbles=[int(f'{n:04b}'[::-1],2) for b in ppdu for n in (b&15,b>>4)]
    pairs=table[nibbles].ravel()
    shaped=np.repeat(pairs,4)*np.tile(np.sin(np.arange(4)*np.pi/4),len(pairs))
    iq=np.r_[shaped.real,[0.,0.]]+1j*np.r_[[0.,0.],shaped.imag]
    return np.r_[np.zeros(2000),iq,np.zeros(4000)].astype(np.complex64),raw


class IEEE154Tests(unittest.TestCase):
    def test_recovered_clock_positions_survive_offsets_and_repeated_packets(self):
        iq,raw=fixture()
        for pad in (0,127,8193,100000):
            data=np.r_[np.zeros(pad,dtype=np.complex64),iq]
            frames,_=ieee154.receive_iq(data)
            frame=next(f for f in frames if f['frame_hex']==raw.hex())
            self.assertLessEqual(abs(frame['subband_sample_start']-(pad+2000)),frame['timing_uncertainty_samples'])
            self.assertEqual(frame['subband_sample_stop']-frame['subband_sample_start'],(6+len(raw))*128)
            self.assertFalse(frame['timing_uncertainty_calibrated'])
        frames,_=ieee154.receive_iq(np.r_[iq,np.zeros(4096),iq].astype(np.complex64))
        self.assertEqual(len(frames),2)
        self.assertGreater(frames[1]['subband_sample_start'],frames[0]['subband_sample_stop'])

    def test_legacy_timing_upgrade_preserves_original_frame_counts(self):
        from spectrum_agent.frame_localization import localize,apply
        ctx=Context('timing154_test_'+uuid.uuid4().hex[:10]);iq,raw=fixture()
        aid,path=ctx.artifact('subband','cf32');iq.tofile(path)
        dump(path.with_suffix('.json'),dict(center_hz=2425e6,sample_rate_hz=4e6,simulated=True,
            segments=[dict(file_start=0,count=len(iq),hardware_start_s=None)]))
        ctx.register(aid,path,kind='subband',metadata_path=str(path.with_suffix('.json')))
        original=dict(artifact_id=aid,protocol='IEEE802.15.4',valid_frames=1,
            frames=[dict(frame_hex=raw.hex(),fcs_pass=True)])
        vid=ctx.json_artifact('ieee154_verification',original)
        result=localize(ctx,vid)
        self.assertEqual(result['matched_frames'],1);self.assertEqual(result['new_protocol_frames'],0)
        upgraded=apply(ctx,[dict(original,evidence_id=vid)])[0]
        self.assertEqual(upgraded['valid_frames'],1)
        self.assertIn('subband_sample_start',upgraded['frames'][0])
        self.assertNotIn('subband_sample_start',original['frames'][0])
    def test_standard_crc_vector(self):
        self.assertEqual(ieee154.crc16(b'123456789'),0x2189)

    def test_phy_crc_and_negative_controls(self):
        iq,raw=fixture();frames,stats=ieee154.receive_iq(iq)
        self.assertTrue(any(f['frame_hex']==raw.hex() for f in frames),stats)
        self.assertTrue(all(f['upper_layer_protocol']=='unknown' for f in frames))
        rng=np.random.default_rng(63)
        impaired=(iq*np.exp(2j*np.pi*5000*np.arange(len(iq))/4e6)+.005*(rng.normal(size=len(iq))+1j*rng.normal(size=len(iq)))).astype(np.complex64)
        self.assertTrue(any(f['frame_hex']==raw.hex() for f in ieee154.receive_iq(impaired)[0]))
        broken,_=fixture(bad_crc=True);frames,stats=ieee154.receive_iq(broken)
        self.assertEqual(frames,[]);self.assertGreater(stats['crc_rejected'],0)
        broken,_=fixture(preamble=False);self.assertEqual(ieee154.receive_iq(broken)[0],[])
        rng=np.random.default_rng(65)
        noise=(rng.normal(size=100000)+1j*rng.normal(size=100000)).astype(np.complex64)
        self.assertEqual(ieee154.receive_iq(noise)[0],[])

    def test_tool_profile_constraints(self):
        ctx=Context('ieee154_test_'+uuid.uuid4().hex[:10]);iq,raw=fixture()
        aid,path=ctx.artifact('fixture','cf32');iq.tofile(path)
        dump(path.with_suffix('.json'),dict(center_hz=2405e6,sample_rate_hz=4e6,simulated=True,
            segments=[dict(file_start=0,count=len(iq),hardware_start_s=None)]))
        ctx.register(aid,path,kind='subband',metadata_path=str(path.with_suffix('.json')))
        value=ieee154.verify(ctx,aid,11)
        self.assertGreater(value['valid_frames'],0)
        self.assertEqual(value['upper_layer_protocol'],'unknown')
        with self.assertRaises(DomainError): ieee154.verify(ctx,aid,12)
