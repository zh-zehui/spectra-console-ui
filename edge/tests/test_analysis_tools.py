import hashlib
import json
from pathlib import Path
import unittest
import uuid
import numpy as np
from spectrum_agent.common import Context,dump
from spectrum_agent import analysis_tools as tools,dsp,knowledge,scene
from spectrum_agent.worker import report


class GenericToolsTests(unittest.TestCase):
    def setUp(self): self.ctx=Context('generic_test_'+uuid.uuid4().hex[:10])

    def iq(self,x,fs=1e6,segments=None):
        aid,path=self.ctx.artifact('fixture','cf32')
        np.asarray(x,dtype='<c8').tofile(path)
        dump(path.with_suffix('.json'),dict(center_hz=5.8e9,sample_rate_hz=fs,requested_span_hz=fs*.8,
             sample_count=len(x),duration_s=len(x)/fs,simulated=True,
             segments=segments or [dict(file_start=0,count=len(x),hardware_start_s=0)],gaps=[]))
        self.ctx.register(aid,path,kind='capture',metadata_path=str(path.with_suffix('.json')))
        return aid

    def test_extended_priors_without_cross_band_bluetooth(self):
        low=knowledge.query(self.ctx,2.44e9,100e6)
        ids={p['id'] for p in low['candidates']}
        self.assertTrue({'bluetooth_classic','ieee802154','proprietary_24','ant_24','lora_flrc_24','microwave_like_24'}<=ids)
        high=knowledge.query(self.ctx,5.8e9,100e6)
        ids={p['id'] for p in high['candidates']}
        self.assertTrue({'wifi_58','digital_video_58','analog_video_58','traffic_dsrc_58','other_58'}<=ids)
        self.assertNotIn('bluetooth_le',ids)
        self.assertNotIn('bluetooth_classic',ids)
        self.assertFalse(high['observed'])

    def test_periodic_bursts_not_protocol(self):
        rng=np.random.default_rng(9);n=100000
        gate=np.arange(n)%10000<2000
        x=(rng.normal(size=n)+1j*rng.normal(size=n))*(.01+.3*gate)
        value=tools.bursts(self.ctx,self.iq(x))
        self.assertEqual(value['total_bursts'],10)
        self.assertTrue(any(abs(p['period_s']-.01)<.0001 for p in value['period_candidates']))
        self.assertEqual(value['protocol'],'unknown')

    def test_linear_sweep_positive_cw_noise_negative(self):
        fs=1e6;t=np.arange(50000)/fs
        chirp=np.exp(2j*np.pi*(-.3e6*t+.5*12e6*t*t))
        result=tools.sweeps(self.ctx,self.iq(chirp))
        self.assertGreater(result['total_tracks'],0)
        self.assertTrue(all(t['protocol']=='unknown' for t in result['tracks']))
        cw=np.exp(2j*np.pi*.1e6*t)
        self.assertEqual(tools.sweeps(self.ctx,self.iq(cw))['total_tracks'],0)
        rng=np.random.default_rng(17)
        self.assertEqual(tools.sweeps(self.ctx,self.iq(rng.normal(size=len(t))+1j*rng.normal(size=len(t))))['total_tracks'],0)

    def test_quality_coverage_and_gaps_are_preserved(self):
        aid=self.iq(np.full(2100000,1+0j))
        result=tools.quality(self.ctx,aid)
        self.assertEqual(result['coverage']['evaluated_samples'],2000000)
        self.assertTrue(result['coverage']['prefix_limited'])
        self.assertGreater(result['dc_power_fraction'],.99)
        self.assertIn('possible_normalized_sample_saturation',result['flags'])
        aid=self.iq(np.ones(10000),segments=[dict(file_start=0,count=5000,hardware_start_s=0),dict(file_start=5000,count=5000,hardware_start_s=1)])
        self.assertEqual(len(tools.bursts(self.ctx,aid)['coverage']['intervals']),2)

    def test_activity_and_sigmf_export_do_not_create_labels(self):
        cap=dsp.synthetic(self.ctx)
        seg=dsp.segment(self.ctx,cap['capture_id'])
        activity=tools.activity(self.ctx,seg['segmentation_id'])
        self.assertTrue(activity['all_events_retained'])
        self.assertTrue(all(f['protocol']=='unknown' for f in activity['families']))
        tools.quality(self.ctx,cap['capture_id'])
        tools.bursts(self.ctx,cap['capture_id'])
        review=scene.assess(self.ctx,seg['segmentation_id'])
        self.assertEqual(len(review['generic_evidence']),3)
        exported=tools.export_sigmf(self.ctx,cap['capture_id'],seg['segmentation_id'])
        self.assertTrue(exported['validated'])
        meta=json.loads(Path(self.ctx.resolve(exported['evidence_id'])['path']).read_text())
        data=Path(self.ctx.resolve(exported['dataset_id'])['path']).read_bytes()
        self.assertEqual(hashlib.sha512(data).hexdigest(),meta['global']['core:sha512'])
        self.assertTrue(all(a.get('core:label','unknown')=='unknown' for a in meta['annotations']))
        report(self.ctx)
        value=json.loads((self.ctx.run/'report.json').read_text())
        self.assertEqual(len(value['generic_signal_analysis']),3)
