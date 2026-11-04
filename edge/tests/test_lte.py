import json,subprocess,unittest,uuid
from pathlib import Path
import numpy as np
from spectrum_agent.common import ROOT,Context,DomainError,dump
from spectrum_agent import lte,investigation
from spectrum_agent.worker import report


@unittest.skipUnless((ROOT/'runtime/lte/bin/lte_fixture').exists(),'Project-local LTE receiver and offline fixture generator required')
class LTETests(unittest.TestCase):
    def setUp(self):self.ctx=Context('lte_test_'+uuid.uuid4().hex[:10])

    def fixture(self,mode='tdd',no_pbch=False,noise=False):
        aid,path=self.ctx.artifact('subband','cf32')
        if noise:
            rng=np.random.default_rng(1515);(rng.normal(size=230400)+1j*rng.normal(size=230400)).astype('<c8').tofile(path)
        else:
            args=[str(ROOT/'runtime/lte/bin/lte_fixture'),str(path),mode]
            if no_pbch:args.append('no_pbch')
            subprocess.run(args,check=True,capture_output=True,timeout=20)
        n=path.stat().st_size//8
        cap,cp=self.ctx.artifact('capture','cf32');cp.write_bytes(path.read_bytes())
        m=dict(sample_rate_hz=1920000,center_hz=2370e6,requested_span_hz=1.4e6,sample_count=n,duration_s=n/1920000,
            simulated=True,complete=True,gaps=[],segments=[dict(file_start=0,count=n,hardware_start_s=0)])
        dump(cp.with_suffix('.json'),m);self.ctx.register(cap,cp,kind='capture',metadata_path=str(cp.with_suffix('.json')))
        dump(path.with_suffix('.json'),dict(m,parent_artifact_id=cap,parent_sample_start=0,actual_parent_sample_stop=n,filter_group_delay_s=0))
        self.ctx.register(aid,path,kind='subband',metadata_path=str(path.with_suffix('.json')))
        return aid

    def test_tdd_pss_sss_pbch_crc_and_mib_fields(self):
        result=investigation.verify(self.ctx,self.fixture(),'lte_pbch',duplex_mode='tdd')
        self.assertEqual(result['valid_frames'],1)
        frame=result['frames'][0]
        self.assertEqual((frame['cell_id'],frame['nof_prb'],frame['antenna_ports']),(42,6,1))
        self.assertTrue(frame['pbch_crc_pass']);self.assertEqual(frame['duplex_mode'],'tdd')
        self.assertEqual(frame['matching_cell_detections'],frame['sync_detections'])
        self.assertEqual(frame['synchronization_consistency'],'repeated_same_pci_candidate')
        self.assertAlmostEqual(frame['sync_mode_fraction'],1.0)
        self.assertEqual(frame['integrity_unit'],'broadcast_block')
        self.assertEqual(len(result['coverage']['synchronization_trials']),3)
        self.assertGreater(result['coverage']['evaluated_samples'],0)
        self.assertLessEqual(result['coverage']['evaluated_samples'],result['coverage']['provided_samples'])
        report(self.ctx);r=json.loads((self.ctx.run/'report.json').read_text())
        self.assertEqual(r['analysis_coverage']['validated_broadcast_blocks'],1)
        self.assertEqual(r['analysis_coverage']['validated_packet_frames'],0)
        self.assertEqual(r['frame_associations'][0]['association_level'],'extraction_window_only')
        with self.assertRaisesRegex(DomainError,'already attempted'):
            investigation.verify(self.ctx,result['artifact_id'],'lte_pbch',duplex_mode='tdd')

    def test_fdd_broadcast_positive(self):
        result=lte.verify(self.ctx,self.fixture('fdd'),'fdd')
        self.assertEqual(result['valid_frames'],1);self.assertEqual(result['frames'][0]['cell_id'],42)

    def test_auto_mode_accepts_both_duplex_types(self):
        for mode in ('tdd','fdd'):
            result=lte.verify(self.ctx,self.fixture(mode),'auto')
            self.assertEqual(result['valid_frames'],1)
            self.assertEqual(result['frames'][0]['duplex_mode'],mode)

    def test_sync_without_pbch_is_not_confirmed(self):
        result=lte.verify(self.ctx,self.fixture(no_pbch=True),'tdd')
        self.assertGreater(result['synchronization_candidate_count'],0)
        self.assertEqual(result['valid_frames'],0)
        self.assertEqual(result['evidence_level'],'sync_candidate')

    def test_noise_is_not_broadcast_confirmation(self):
        result=lte.verify(self.ctx,self.fixture(noise=True),'tdd')
        self.assertEqual(result['valid_frames'],0)

    def test_invalid_rate_and_short_time_are_rejected(self):
        aid=self.fixture();record=self.ctx.resolve(aid);m=json.loads(Path(record['metadata_path']).read_text())
        dump(record['metadata_path'],dict(m,sample_rate_hz=4e6))
        with self.assertRaisesRegex(DomainError,'requires1.92'):lte.verify(self.ctx,aid)
        dump(record['metadata_path'],m);Path(record['path']).write_bytes(b'\0'*8000)
        with self.assertRaisesRegex(DomainError,'requires40ms'):lte.verify(self.ctx,aid)
