import unittest
import numpy as np
from spectrum_agent.multiscale import detect


class MultiscaleTests(unittest.TestCase):
    def test_broad_packets_separate_and_narrow_overlap_retained(self):
        db=np.random.default_rng(9).normal(-60,.4,(220,160)).astype('float32')
        db[30:65,30:95]+=18;db[100:140,30:95]+=18
        db[40:80,60:62]+=22
        found=detect(db,10)
        broad=[e for e in found if e['label']=='broad_energy_component']
        narrow=[e for e in found if e['label']=='narrow_energy_component']
        self.assertEqual(len(broad),2)
        self.assertTrue(any(e['box']['x1']<=60<=e['box']['x2'] and e['box']['y1']<=45<=e['box']['y2'] for e in narrow))
        self.assertFalse(any(e['box']['y1']<70 and e['box']['y2']>90 for e in broad))

    def test_continuous_narrow_and_noise(self):
        noise=np.random.default_rng(10).normal(-60,.4,(200,100)).astype('float32')
        self.assertEqual(detect(noise,10),[])
        noise[:,45:47]+=20
        self.assertTrue(any(e['box']['y1']==0 and e['box']['y2']==199 for e in detect(noise,10)))

    def test_thermal_power_speckle_does_not_become_hundreds_of_regions(self):
        power=np.random.default_rng(12).exponential(size=(1000,256))
        db=(10*np.log10(np.maximum(power,1e-12))-60).astype('float32')
        self.assertLess(len(detect(db,14)),5)

if __name__=='__main__':unittest.main()
