import unittest
import uuid
import numpy as np
from scapy.layers.bluetooth4LE import BTLE
from spectrum_agent.common import Context,dump
from spectrum_agent import protocols


class ExtendedAdvertisingTests(unittest.TestCase):
    def setUp(self):
        self.ctx=Context('ble_extended_test_'+uuid.uuid4().hex[:10])

    def fixture(self,payload,corrupt=False,truncate=False,channel=37,rate=1e6):
        pdu=bytes([7,len(payload)])+payload
        crc=bytearray(BTLE.compute_crc(pdu,init=0x555555))
        if corrupt:crc[0]^=1
        data=np.unpackbits(np.frombuffer(pdu+crc,dtype=np.uint8),bitorder='little')
        aa=np.unpackbits(np.frombuffer(bytes.fromhex('d6be898e'),dtype=np.uint8),bitorder='little')
        bits=np.concatenate([np.array([0,1]*(8 if rate==2e6 else 4)),aa,protocols.whitening(data,channel)])
        wave=np.repeat(bits.astype(float)*2-1,4)*rate/4
        iq=np.exp(1j*np.cumsum(2*np.pi*wave/(4*rate))).astype(np.complex64)
        if truncate:iq=iq[:-120]
        iq=np.concatenate([np.zeros(512,np.complex64),iq])
        if not truncate:iq=np.concatenate([iq,np.zeros(512,np.complex64)])
        aid,path=self.ctx.artifact('ble_fixture','cf32');iq.tofile(path)
        mp=path.with_suffix('.json')
        dump(mp,dict(center_hz=protocols.ble_channel_hz(channel),sample_rate_hz=4*rate,requested_span_hz=3e6 if rate==2e6 else 2e6,
            simulated=True,segments=[dict(file_start=0,count=len(iq),hardware_start_s=None)]))
        self.ctx.register(aid,path,kind='subband',metadata_path=str(mp))
        return aid

    def test_primary_aux_pointer_crc_and_channel_whitening(self):
        # Flags AuxPtr; channel20, 30us units, offset100, LE2M.
        payload=bytes.fromhex('0410146420')
        for channel in (37,38,39):
            out=protocols.decode_ble(self.ctx,self.fixture(payload,channel=channel),channel)
            self.assertEqual(out['valid_frames'],1)
            frame=out['frames'][0]
            self.assertEqual(frame['pdu_type'],7)
            self.assertEqual(frame['aux_ptr']['channel'],20)
            self.assertEqual(frame['aux_ptr']['phy'],'LE2M')
            self.assertEqual(frame['aux_ptr']['offset_us'],3000)
            self.assertEqual(frame['aux_ptr']['followup_status'],'not_attempted')

    def test_crc_error_and_truncation_never_confirm(self):
        payload=bytes.fromhex('0410146420')
        bad=protocols.decode_ble(self.ctx,self.fixture(payload,corrupt=True),37)
        self.assertEqual(bad['valid_frames'],0)
        self.assertGreater(bad['verification_stages']['counts']['crc_failed'],0)
        short=protocols.decode_ble(self.ctx,self.fixture(payload,truncate=True),37)
        self.assertEqual(short['valid_frames'],0)
        self.assertGreater(short['verification_stages']['counts']['truncated'],0)

    def test_malformed_extended_header_with_valid_crc_is_rejected(self):
        out=protocols.decode_ble(self.ctx,self.fixture(bytes.fromhex('0410')),37)
        self.assertEqual(out['valid_frames'],0)
        self.assertGreater(out['verification_stages']['counts']['extended_header_rejected'],0)

    def test_aux_pointer_reserved_and_offset_units(self):
        with self.assertRaises(ValueError):protocols.extended_header(bytes.fromhex('04103f6420'))
        parsed=protocols.extended_header(bytes.fromhex('0410946400'))
        self.assertEqual(parsed['aux_ptr']['offset_us'],30000)

    def test_auxiliary_phys_channel_mapping_and_packet_duration(self):
        self.assertEqual([protocols.ble_channel_hz(ch) for ch in (0,10,11,36)],
            [2404e6,2424e6,2428e6,2478e6])
        # No extended header, 80 bytes data: exercises full8-bit payload length.
        payload=b'\x00'+bytes(range(80))
        for profile,rate in (('ble_aux_1m',1e6),('ble_aux_2m',2e6)):
            for ch in (0,11,36):
                out=protocols.verify(self.ctx,self.fixture(payload,channel=ch,rate=rate),profile,channel=ch)
                self.assertEqual(out['valid_frames'],1)
                frame=out['frames'][0]
                symbols=(16 if rate==2e6 else 8)+32+16+8*len(payload)+24
                self.assertEqual(frame['subband_sample_stop']-frame['subband_sample_start'],4*symbols)
                self.assertEqual(out['verification_profile'],profile)

    def test_auxiliary_report_maps_rate_and_filter_delay(self):
        from pathlib import Path
        import json
        from spectrum_agent.catalog import associate
        cap,path=self.ctx.artifact('capture','cf32');path.write_bytes(b'')
        mp=path.with_suffix('.json');dump(mp,dict(sample_rate_hz=200e6))
        self.ctx.register(cap,path,kind='capture',metadata_path=str(mp))
        aid=self.fixture(b'\x00test',channel=11,rate=2e6)
        rec=self.ctx.resolve(aid);meta=json.loads(Path(rec['metadata_path']).read_text())
        count=Path(rec['path']).stat().st_size//8
        meta.update(parent_artifact_id=cap,parent_sample_start=200000,
            actual_parent_sample_stop=200000+count*25,filter_group_delay_s=2e-6)
        dump(rec['metadata_path'],meta)
        out=protocols.verify(self.ctx,aid,'ble_aux_2m',channel=11)
        saved=json.loads(Path(self.ctx.resolve(out['evidence_id'])['path']).read_text())
        saved['evidence_id']=out['evidence_id']
        link=associate(self.ctx,[],[saved])[0]
        frame=saved['frames'][0]
        self.assertEqual(link['sample_start'],200000+frame['subband_sample_start']*25-400)
        self.assertEqual(link['sample_stop']-link['sample_start'],
            (frame['subband_sample_stop']-frame['subband_sample_start'])*25)
        self.assertEqual(link['freq_high_hz']-link['freq_low_hz'],3e6)
        self.assertIn('not_measured',link['bandwidth_kind'])

    def test_auxiliary_noise_is_not_confirmed(self):
        from pathlib import Path
        for profile,rate in (('ble_aux_1m',1e6),('ble_aux_2m',2e6)):
            aid=self.fixture(b'\x00test',channel=10,rate=rate)
            rng=np.random.default_rng(912)
            noise=(rng.normal(size=40000)+1j*rng.normal(size=40000)).astype(np.complex64)
            noise.tofile(Path(self.ctx.resolve(aid)['path']))
            self.assertEqual(protocols.verify(self.ctx,aid,profile,channel=10)['valid_frames'],0)

    def test_auxiliary_crc_errors_and_wrong_profile_rejected(self):
        from spectrum_agent.common import DomainError
        for profile,rate in (('ble_aux_1m',1e6),('ble_aux_2m',2e6)):
            aid=self.fixture(b'\x00test',channel=10,rate=rate,corrupt=True)
            out=protocols.verify(self.ctx,aid,profile,channel=10)
            self.assertEqual(out['valid_frames'],0)
            self.assertGreater(out['verification_stages']['counts']['crc_failed'],0)
        with self.assertRaises(DomainError):
            protocols.verify(self.ctx,self.fixture(b'\x00'), 'ble_aux_2m',channel=37)


if __name__=='__main__':unittest.main()
