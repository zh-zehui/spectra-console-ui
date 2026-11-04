import json
from pathlib import Path
import unittest
from unittest.mock import patch
import numpy as np
from scipy.signal import resample_poly
from spectrum_agent.common import dump
from spectrum_agent import protocols
from spectrum_agent.ble_followup import follow
from tests.test_ble_extended import ExtendedAdvertisingTests


class FollowupTests(unittest.TestCase):
    def setUp(self):
        self.fixture=ExtendedAdvertisingTests();self.fixture.setUp();self.ctx=self.fixture.ctx

    def source(self,payload=bytes.fromhex('0410006420')):
        aid=self.fixture.fixture(payload)
        out=protocols.decode_ble(self.ctx,aid,37)
        return aid,out['evidence_id']

    def test_coded_and_zero_offset_never_call_extraction(self):
        for payload,status in ((bytes.fromhex('0410006440'),'unsupported_phy'),
                (bytes.fromhex('0410000020'),'incomplete_advertising_data')):
            _,vid=self.source(payload)
            with patch('spectrum_agent.dsp.extract') as extract:
                out=follow(self.ctx,vid,0)
            self.assertEqual(out['status'],status);extract.assert_not_called()
            self.assertFalse(out['new_rf_capture']);self.assertFalse(out['chain_confirmed'])

    def test_invalid_evidence_or_frame_index(self):
        from spectrum_agent.common import DomainError
        _,vid=self.source()
        with self.assertRaises(DomainError):follow(self.ctx,vid,True)
        with self.assertRaises(DomainError):follow(self.ctx,vid,99)

    def test_saved_iq_gnuradio_extraction_to_auxiliary_crc(self):
        # Source primary AuxPtr: auxiliary channel0,3ms offset,LE2M.
        primary,vid=self.source()
        cap,path=self.ctx.artifact('capture','cf32')
        aux=self.fixture.fixture(b'\x00'+bytes(range(80)),channel=0,rate=2e6)
        ax=np.fromfile(self.ctx.resolve(aux)['path'],dtype=np.complex64)
        raw=np.zeros(400000,np.complex64) #20ms at20MS/s.
        # Primary subband begins2ms into recording; packet starts128us later.
        target=.002+512/4e6+.003
        insert=round((target-512/8e6)*20e6)
        up=resample_poly(ax,5,2).astype(np.complex64)
        raw[insert:insert+len(up)]=up
        raw.tofile(path);mp=path.with_suffix('.json')
        dump(mp,dict(center_hz=2404e6,sample_rate_hz=20e6,requested_span_hz=16e6,
            sample_count=len(raw),duration_s=.02,simulated=True,
            segments=[dict(file_start=0,count=len(raw),hardware_start_s=None)]))
        self.ctx.register(cap,path,kind='capture',metadata_path=str(mp))
        pm=Path(self.ctx.resolve(primary)['metadata_path']);meta=json.loads(pm.read_text())
        meta.update(parent_artifact_id=cap,parent_sample_start=40000,
            actual_parent_sample_stop=100000,filter_group_delay_s=0)
        dump(pm,meta)
        out=follow(self.ctx,vid,0)
        self.assertEqual(out['status'],'crc_valid_timing_candidates')
        self.assertEqual(out['verification_profile'],'ble_aux_2m')
        self.assertEqual(out['valid_frames'],1)
        self.assertEqual(out['timing_candidate_frame_indices'],[0])
        self.assertFalse(out['chain_confirmed'])


if __name__=='__main__':unittest.main()
