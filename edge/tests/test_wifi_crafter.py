import csv
import hashlib
import json
from pathlib import Path
import unittest
import uuid
import numpy as np
from spectrum_agent.common import ROOT,Context,DomainError,dump
from spectrum_agent import wifi_crafter as rx

FIXTURES=ROOT/'vendor/libcrafter/crafter/tests/fixtures/iq'


class CrafterTests(unittest.TestCase):
    def setUp(self):self.ctx=Context('crafter_test_'+uuid.uuid4().hex[:10])

    def artifact(self,name):
        raw=np.fromfile(FIXTURES/(name+'.cs8'),dtype=np.int8).astype(np.float32).reshape(-1,2)
        iq=(raw[:,0]+1j*raw[:,1]).astype(np.complex64)/128
        aid,path=self.ctx.artifact('fixture','cf32');iq.tofile(path)
        mp=path.with_suffix('.json');dump(mp,dict(center_hz=2412e6,sample_rate_hz=20e6,
            sample_count=len(iq),duration_s=len(iq)/20e6,requested_span_hz=18e6,simulated=True,
            segments=[dict(file_start=0,count=len(iq),hardware_start_s=None)]))
        self.ctx.register(aid,path,kind='subband',metadata_path=str(mp))
        return aid

    def test_quantization_is_fixed_finite_and_bounded(self):
        data,meta=rx.quantize(np.array([.5+.25j,-1-2j],np.complex64))
        np.testing.assert_array_equal(data,[30,15,-60,-120])
        self.assertEqual(meta['clipped_components'],0)
        with self.assertRaises(DomainError):rx.quantize(np.array([np.nan+0j],np.complex64))

    def test_independent_dsss_cck_matrix(self):
        manifest=json.loads((FIXTURES/'dsss-manifest.json').read_text())
        results=[]
        for item in manifest['fixtures']:
            with self.subTest(name=item['name']):
                path=FIXTURES/(item['name']+'.cs8')
                self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(),item['sha256'])
                out=rx.run_cs8(path,2412e6,'dsss')
                expected=[item['psdu_hex']] if item['expected']=='frame' else []
                self.assertEqual([f['psdu_hex'] for f in out['frames']],expected)
                self.assertEqual(out['processed_samples'],item['sample_count'])
                results.append(dict(name=item['name'],expected=item['expected'],frames=len(out['frames'])))
        dump(ROOT/'docs/testing/crafter-dsss-fixtures.json',dict(checks=results,new_rf_capture=False))

    def test_ht_ampdu_mcs_gi_coding_and_bad_member_matrix(self):
        with (FIXTURES/'ht-ampdu-index.tsv').open() as stream:
            truth={row['name']:row for row in csv.DictReader(stream,delimiter='\t')}
        results=[]
        for path in sorted(FIXTURES.glob('ht-ampdu-*.cs8')):
            with self.subTest(name=path.stem):
                case=path.stem.split('-',6)[-1];item=truth[path.stem]
                expected=item['mpdu_hex'].split(',') if item['mpdu_hex'] else []
                self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(),item['sha256'])
                out=rx.run_cs8(path,2412e6,'all')
                self.assertEqual([f['psdu_hex'] for f in out['frames']],expected)
                self.assertTrue(all(f['phy_format']=='HT20' for f in out['frames']))
                if case=='duplicate':
                    offsets=[next(d['value']['delimiter_offset'] for d in f['diagnostics'] if d['kind']=='ampdu_member') for f in out['frames']]
                    self.assertEqual(len(offsets),len(set(offsets)))
                results.append(dict(name=path.stem,valid_mpdus=len(out['frames'])))
        self.assertGreaterEqual(len(results),96)
        dump(ROOT/'docs/testing/crafter-ht-ampdu-fixtures.json',dict(checks=results,new_rf_capture=False))

    def test_cf32_adapter_dsss_and_duplicate_ampdu(self):
        for name,profile,count,phy in (('dsss-10-long-clean-48','wifi_crafter_dsss',1,'DSSS'),
                ('ht-ampdu-0-gi400-bcc-duplicate','wifi_crafter_all',2,'HT20')):
            out=rx.verify(self.ctx,self.artifact(name),profile)
            self.assertEqual(out['valid_frames'],count)
            self.assertEqual(out['frames'][0]['phy_format'],phy)
            self.assertTrue(out['analysis_coverage']['complete'])
            self.assertTrue(all(f['fcs_pass'] for f in out['frames']))

    def test_aggregated_identical_members_survive_catalog_dedup(self):
        from spectrum_agent.catalog import associate
        aid=self.artifact('ht-ampdu-0-gi400-bcc-duplicate')
        cap,path=self.ctx.artifact('capture','cf32');path.write_bytes(b'')
        mp=path.with_suffix('.json');dump(mp,dict(sample_rate_hz=200e6))
        self.ctx.register(cap,path,kind='capture',metadata_path=str(mp))
        sm=Path(self.ctx.resolve(aid)['metadata_path']);meta=json.loads(sm.read_text())
        meta.update(parent_artifact_id=cap,parent_sample_start=200000,
            actual_parent_sample_stop=200000+10*meta['sample_count'],filter_group_delay_s=0)
        dump(sm,meta)
        out=rx.verify(self.ctx,aid,'wifi_crafter_all')
        saved=json.loads(Path(self.ctx.resolve(out['evidence_id'])['path']).read_text());saved['evidence_id']=out['evidence_id']
        links=associate(self.ctx,[],[saved])
        self.assertEqual(len(links),2)
        self.assertEqual(links[0]['sample_start'],200000+10*saved['frames'][0]['subband_sample_start'])

    def test_noise_and_invalid_rate(self):
        aid=self.artifact('dsss-10-long-clean-48');rec=self.ctx.resolve(aid)
        rng=np.random.default_rng(319)
        x=(rng.normal(size=100000)+1j*rng.normal(size=100000)).astype(np.complex64);x.tofile(rec['path'])
        mp=Path(rec['metadata_path']);meta=json.loads(mp.read_text());meta.update(sample_count=len(x),segments=[dict(file_start=0,count=len(x),hardware_start_s=None)]);dump(mp,meta)
        self.assertEqual(rx.verify(self.ctx,aid,'wifi_crafter_all')['valid_frames'],0)
        meta['sample_rate_hz']=4e6;dump(mp,meta)
        with self.assertRaises(DomainError):rx.verify(self.ctx,aid,'wifi_crafter_all')

    def test_registered_tool_audit_and_cached_retry(self):
        from spectrum_agent.worker import execute
        from spectrum_agent.investigation import read
        aid=self.artifact('ht-ampdu-0-gi400-bcc-duplicate')
        args=dict(artifact_id=aid,protocol='wifi_crafter_all')
        out=execute(self.ctx.task_id,'verify_protocol',args)
        self.assertEqual(out['status'],'succeeded')
        self.assertEqual(out['result']['valid_frames'],2)
        audit=read(self.ctx,out['result']['attempt_id'])
        self.assertIsNone(audit['arguments']['channel'])
        self.assertEqual(audit['verification_stages']['strongest_stage'],'mac_fcs_pass')
        again=execute(self.ctx.task_id,'verify_protocol',args)
        self.assertTrue(again['cached'])
        self.assertEqual(again['result']['evidence_id'],out['result']['evidence_id'])
