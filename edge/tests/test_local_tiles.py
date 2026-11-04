import unittest
import numpy as np
from spectrum_agent import local_tiles,scene
from spectrum_agent.common import DomainError


class LocalTilesTests(unittest.TestCase):
    def noise(self,shape=(128,256)):
        return 10*np.log10(np.random.default_rng(14).exponential(1,size=shape))

    def test_noise_does_not_turn_into_dense_candidates(self):
        out=local_tiles.detect(self.noise(),10,10000,2e-6,time_bin_s=10e-6,frequency_bin_hz=100e3)
        self.assertLessEqual(len(out),2)

    def test_short_and_stationary_narrow_activity_retained(self):
        db=self.noise();db[20:23,50:52]+=35;db[:,210:213]+=30
        out=local_tiles.detect(db,10,10000,2e-6,time_bin_s=4e-6,frequency_bin_hz=50e3)
        def covers(t,f):return any(e['box']['y1']<=t<=e['box']['y2'] and e['box']['x1']<=f<=e['box']['x2'] for e in out)
        self.assertTrue(covers(21,50));self.assertTrue(covers(64,211))

    def test_embedded_narrow_hint_is_not_absorbed_into_broad_parent(self):
        db=self.noise();db[30:90,60:180]+=25;db[20:100,100:102]+=35
        out=local_tiles.detect(db,10,10000,2e-6,time_bin_s=4e-6,frequency_bin_hz=50e3)
        self.assertTrue(any(e['label']=='embedded_narrow_hint' and e['box']['x1']<=101<=e['box']['x2'] for e in out))

    def test_target_clipping_preserves_context_artifact_but_excludes_it_from_candidates(self):
        events=[dict(event_id='outside',sample_start=0,sample_stop=10,freq_low_hz=100,freq_high_hz=120),
                dict(event_id='partial',sample_start=15,sample_stop=35,freq_low_hz=90,freq_high_hz=130)]
        out=scene.clip_mapped_events(events,20,30,100,125)
        self.assertEqual(len(out),1);self.assertEqual(out[0]['sample_start'],20);self.assertEqual(out[0]['sample_stop'],30)
        self.assertEqual(out[0]['freq_low_hz'],100);self.assertEqual(out[0]['freq_high_hz'],125)
        self.assertEqual(events[1]['sample_start'],15)

    def test_scale_reports_native_floor_and_rejects_invalid_values(self):
        self.assertEqual(local_tiles.scale(100000,50e-6,1e-6,1000),(1,1))
        with self.assertRaises(DomainError):local_tiles.scale(1,1,float('nan'),1000)


if __name__=='__main__':unittest.main()
