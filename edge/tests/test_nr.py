import json,subprocess,unittest,uuid
from pathlib import Path
import numpy as np
from spectrum_agent.common import ROOT,Context,DomainError,dump
from spectrum_agent import nr,investigation
from spectrum_agent.worker import report


class NRRasterTests(unittest.TestCase):
    def test_standard_raster_endpoints_and_input_guard(self):
        self.assertEqual(nr.gscn_frequency(5762),2305250000)
        self.assertEqual(nr.gscn_frequency(5989),2395450000)
        entries=nr.n40_raster(2350e6,2450e6)
        self.assertEqual(len(entries),105)
        self.assertEqual(entries[0]['center_hz'],2354450000)
        self.assertTrue(all(2350e6<=e['center_hz']-4400000 and e['center_hz']+4400000<=2450e6 for e in entries))


@unittest.skipUnless((ROOT/'runtime/lte/bin/nr_fixture').exists(),'Project-local NR receiver/fixture required')
class NRTests(unittest.TestCase):
    def setUp(self):self.ctx=Context('nr_test_'+uuid.uuid4().hex[:10])

    def fixture(self,scs=30,pattern='C',mode='positive'):
        aid,path=self.ctx.artifact('subband','cf32');rate=nr.settings(scs,pattern)['rate_hz']
        if mode=='noise':
            rng=np.random.default_rng(1616);(rng.normal(size=int(rate*.04))+1j*rng.normal(size=int(rate*.04))).astype('<c8').tofile(path)
        else:
            args=[str(ROOT/'runtime/lte/bin/nr_fixture'),str(path),str(scs),pattern]
            if mode=='no_pbch':args.append('no_pbch')
            subprocess.run(args,check=True,capture_output=True,timeout=20)
        n=path.stat().st_size//8;cap,cp=self.ctx.artifact('capture','cf32');cp.write_bytes(path.read_bytes())
        m=dict(sample_rate_hz=rate,center_hz=2370e6,requested_span_hz=nr.settings(scs,pattern)['passband_hz'],sample_count=n,duration_s=n/rate,
            simulated=True,complete=True,gaps=[],segments=[dict(file_start=0,count=n,hardware_start_s=0)])
        dump(cp.with_suffix('.json'),m);self.ctx.register(cap,cp,kind='capture',metadata_path=str(cp.with_suffix('.json')))
        dump(path.with_suffix('.json'),dict(m,parent_artifact_id=cap,parent_sample_start=0,actual_parent_sample_stop=n,filter_group_delay_s=0))
        self.ctx.register(aid,path,kind='subband',metadata_path=str(path.with_suffix('.json')))
        return aid

    def test_case_c_crc_mib_and_broadcast_not_packet_boundaries(self):
        aid=self.fixture();result=investigation.verify(self.ctx,aid,'nr_pbch',ssb_scs_khz=30,ssb_pattern='C')
        self.assertEqual(result['valid_frames'],2)
        self.assertEqual(result['distinct_decoded_pci'],[123])
        self.assertEqual(result['frames'][0]['sfn'],513)
        self.assertEqual(result['frames'][0]['ssb_offset'],2)
        self.assertEqual(result['coverage']['evaluated_samples'],result['coverage']['provided_samples'])
        report(self.ctx);r=json.loads((self.ctx.run/'report.json').read_text())
        self.assertEqual(r['analysis_coverage']['validated_broadcast_blocks'],2)
        self.assertEqual(r['analysis_coverage']['validated_packet_frames'],0)
        self.assertTrue(all(l['association_level']=='extraction_window_only' for l in r['frame_associations']))
        with self.assertRaisesRegex(DomainError,'already attempted'):
            investigation.verify(self.ctx,aid,'nr_pbch',ssb_scs_khz=30,ssb_pattern='C',duplex_mode='tdd')

    def test_other_supported_numerologies(self):
        for scs,pattern in ((15,'A'),(30,'B')):
            r=nr.verify(self.ctx,self.fixture(scs,pattern),scs,pattern)
            self.assertEqual(r['valid_frames'],2)
            self.assertEqual(r['frames'][0]['scs_common_khz'],scs)

    def test_wideband_frequency_translation_and_resampling_preserve_nr(self):
        from scipy.signal import resample_poly
        from spectrum_agent import dsp
        aid=self.fixture();iq=np.fromfile(self.ctx.resolve(aid)['path'],dtype='<c8')
        wide=resample_poly(iq,625,48).astype(np.complex64) #15.36MS/s ->200MS/s
        phase=np.arange(len(wide),dtype=np.float64)*(2*np.pi*1e6/200e6)
        wide*=np.exp(1j*phase).astype(np.complex64)
        cap,path=self.ctx.artifact('capture','cf32');wide.tofile(path)
        meta=dict(sample_rate_hz=200e6,center_hz=2369e6,requested_span_hz=40e6,sample_count=len(wide),duration_s=len(wide)/200e6,
            simulated=True,complete=True,gaps=[],segments=[dict(file_start=0,count=len(wide),hardware_start_s=0)])
        dump(path.with_suffix('.json'),meta);self.ctx.register(cap,path,kind='capture',metadata_path=str(path.with_suffix('.json')))
        sub=dsp.extract(self.ctx,cap,2370e6,8800000,output_rate_hz=15360000,margin_s=.001)
        result=nr.verify(self.ctx,sub['artifact_id'])
        self.assertGreater(result['valid_frames'],0)
        self.assertEqual(result['distinct_decoded_pci'],[123])

    def test_noise_and_pss_only_are_not_confirmation(self):
        for mode in ('noise','no_pbch'):
            r=nr.verify(self.ctx,self.fixture(mode=mode))
            self.assertEqual(r['valid_frames'],0)

    def test_rate_and_profile_constraints(self):
        with self.assertRaises(DomainError):nr.settings(60,'D')
        with self.assertRaises(DomainError):nr.settings(15,'C')
        with self.assertRaises(DomainError):nr.settings(30,'C','fdd')
        aid=self.fixture();rec=self.ctx.resolve(aid);m=json.loads(Path(rec['metadata_path']).read_text())
        dump(rec['metadata_path'],dict(m,sample_rate_hz=1920000))
        with self.assertRaisesRegex(DomainError,'For NR30'):nr.verify(self.ctx,aid)
