import json
import unittest
import uuid
from pathlib import Path
import numpy as np
from spectrum_agent.common import ROOT,Context,DomainError,dump
from spectrum_agent import bluetooth_classic as bt


@unittest.skipUnless((ROOT/'runtime/libbtbb/lib/libbtbb.so').exists(),'Project-local libbtbb is required')
class ClassicBluetoothTests(unittest.TestCase):
    def setUp(self):
        self.ctx=Context('classic_test_'+uuid.uuid4().hex[:10])

    def artifact(self,iq,rate=4e6,center=2449e6):
        aid,path=self.ctx.artifact('subband','cf32');np.asarray(iq,dtype='<c8').tofile(path)
        metadata=path.with_suffix('.json');dump(metadata,dict(sample_rate_hz=rate,center_hz=center,requested_span_hz=1e6,
            sample_count=len(iq),segments=[dict(file_start=0,count=len(iq))],simulated=True))
        self.ctx.register(aid,path,kind='subband',metadata_path=str(metadata));return aid

    def fixture(self,corrupt=False):
        rng=np.random.default_rng(491)
        # GIAC syncword, not a full packet or independently checked payload.
        word=0x4e7a2cce331a3ae2
        sync=np.array([(word>>i)&1 for i in range(64)],dtype=np.uint8)
        if corrupt:sync[10:22]^=1
        bits=np.concatenate([rng.integers(0,2,150),[0,1,0,1],sync,rng.integers(0,2,150)])
        instantaneous=np.repeat(bits.astype(float)*2-1,4)*250000
        phase=np.cumsum(2*np.pi*instantaneous/4e6)
        return np.exp(1j*phase).astype(np.complex64)

    def test_known_access_code_and_corruption_in_native_library(self):
        lib=bt.library();word=0x4e7a2cce331a3ae2
        bits=np.array([0]*100+[(word>>i)&1 for i in range(64)]+[0]*100,dtype=np.uint8)
        hits=bt.scan_symbols(lib,bits)
        self.assertTrue(any(h['symbol_start']==100 and h['lap']==0x9e8b33 for h in hits))
        bits[110:122]^=1
        self.assertEqual(bt.scan_symbols(lib,bits),[])

    def test_iq_access_code_is_candidate_never_validated_frame(self):
        out=bt.analyze(self.ctx,self.artifact(self.fixture()))
        self.assertGreater(out['access_code_hit_count'],0)
        self.assertEqual(out['valid_frames'],0)
        self.assertEqual(out['protocol'],'unknown')
        self.assertFalse(out['protocol_confirmed'])
        self.assertTrue(any(h['alternating_preamble'] for h in out['hits']))
        self.assertEqual(out['coverage']['evaluated_samples'],len(self.fixture()))

    def test_noise_and_corrupt_sync_are_not_confirmed(self):
        rng=np.random.default_rng(54)
        noise=(rng.normal(size=20000)+1j*rng.normal(size=20000)).astype(np.complex64)
        for iq in (noise,self.fixture(True)):
            result=bt.analyze(self.ctx,self.artifact(iq))
            self.assertEqual(result['access_code_hit_count'],0)
            self.assertFalse(result['protocol_confirmed'])

    def test_input_rate_and_nominal_channel_are_checked(self):
        with self.assertRaisesRegex(DomainError,'requires4MS/s'):bt.analyze(self.ctx,self.artifact(self.fixture(),rate=2e6))
        with self.assertRaisesRegex(DomainError,'nominal classic'):bt.analyze(self.ctx,self.artifact(self.fixture(),center=2449.3e6))
