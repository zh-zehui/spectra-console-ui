import json
import unittest
import uuid
from pathlib import Path
import numpy as np
from spectrum_agent import coarse
from spectrum_agent.common import Context,DomainError,dump


class CoarseTests(unittest.TestCase):
    def fixture(self,gapped=False):
        ctx=Context('coarse_test_'+uuid.uuid4().hex[:10]);rng=np.random.default_rng(17);n=40003;fs=4e6
        x=(rng.normal(size=n)+1j*rng.normal(size=n))*.002
        t=np.arange(n)/fs
        x+=.035*np.exp(2j*np.pi*400000*t)
        x[8000:16000]+=.04*(rng.normal(size=8000)+1j*rng.normal(size=8000))
        # Short tone-like activity; mean pooling alone would dilute it.
        x[24000:24240]+=.08*np.exp(2j*np.pi*-900000*t[24000:24240])
        aid,path=ctx.artifact('fixture','cf32');x.astype('<c8').tofile(path)
        segments=([dict(file_start=0,count=20000,hardware_start_s=0),dict(file_start=20000,count=n-20000,hardware_start_s=.1)]
                  if gapped else [dict(file_start=0,count=n,hardware_start_s=0)])
        meta=dict(center_hz=2400e6,sample_rate_hz=fs,requested_span_hz=3.2e6,sample_count=n,duration_s=n/fs,
            simulated=True,segments=segments,gaps=[dict(after=20000)] if gapped else [],overflow_count=0)
        dump(path.with_suffix('.json'),meta);ctx.register(aid,path,kind='capture',metadata_path=str(path.with_suffix('.json')))
        return ctx,aid,n,fs

    def read(self,ctx,id):return json.loads(Path(ctx.resolve(id)['path']).read_text())

    def test_full_sample_coverage_and_batch_invariance(self):
        ctx,cap,n,fs=self.fixture()
        args=dict(capture_id=cap,fft_size=512,time_bin_s=.0002,frequency_bin_hz=.5e6)
        a=coarse.segment(ctx,**args,batch_frames=17);b=coarse.segment(ctx,**args,batch_frames=256)
        self.assertTrue(a['coverage']['all_continuous_samples_covered'])
        self.assertEqual(a['coverage']['stft_covered_samples'],n)
        def boxes(value):return [(e['sample_start'],e['sample_stop'],e['freq_low_hz'],e['freq_high_hz']) for e in self.read(ctx,value['segmentation_id'])['signal_groups']]
        self.assertEqual(boxes(a),boxes(b))
        regions=self.read(ctx,a['segmentation_id'])['signal_groups']
        self.assertLess(len(regions),20)
        self.assertTrue(any(e['sample_start']<=24120<=e['sample_stop'] and e['freq_low_hz']<=2399.1e6<=e['freq_high_hz'] for e in regions))
        grid=self.read(ctx,a['coarse_stft_id'])
        self.assertTrue(all(s['all_positive_tiles_retained'] for s in grid['segments']))
        self.assertFalse(grid['batch_boundaries_are_detection_boundaries'])

    def test_no_region_crosses_real_capture_gap(self):
        ctx,cap,n,fs=self.fixture(gapped=True)
        result=coarse.segment(ctx,cap,fft_size=512,time_bin_s=.0002,frequency_bin_hz=.5e6)
        regions=self.read(ctx,result['segmentation_id'])['signal_groups']
        self.assertTrue(any(e['segment_file_start']==0 for e in regions))
        self.assertTrue(any(e['segment_file_start']==20000 for e in regions))
        self.assertTrue(all(e['sample_stop']<=20000 or e['sample_start']>=20000 for e in regions))

    def test_averaged_and_extreme_noise_not_promoted_to_many_regions(self):
        rng=np.random.default_rng(18)
        mean=1+rng.normal(0,.025,(1000,100));peak=10+rng.normal(0,.7,(1000,100))
        found,_=coarse.tile_mask(mean,peak,6)
        self.assertEqual(found,[])

    def test_invalid_sub_hop_coarse_scale_rejected(self):
        ctx,cap,_,_=self.fixture()
        with self.assertRaises(DomainError):coarse.segment(ctx,cap,fft_size=4096,time_bin_s=.00002)

    def test_guard_and_parent_child_report_with_later_fine_window(self):
        from spectrum_agent.worker import execute,report
        from spectrum_agent import scene
        ctx,cap,_,fs=self.fixture()
        dump(ctx.run/'request.json',dict(segmentation_workflow='coarse_to_fine'))
        with self.assertRaisesRegex(DomainError,'coarse_segment_spectrum'):
            execute(ctx.task_id,'segment_spectrum',dict(capture_id=cap))
        result=execute(ctx.task_id,'coarse_segment_spectrum',dict(capture_id=cap,fft_size=512,
            time_bin_s=.0002,frequency_bin_hz=.5e6))['result']
        seg=self.read(ctx,result['segmentation_id']);parent=max(seg['signal_groups'],key=lambda g:g['duration_s'])
        self.assertGreater(parent['duration_s'],.008)
        review=scene.assess(ctx,result['segmentation_id'])
        fine=scene.refine(ctx,result['segmentation_id'],parent['group_id'],
            'Inspect a later parent window rather than only the first samples.',[review['review_id']],
            fft_size=512,offset_s=.006,duration_s=.001,strategy='traditional')
        self.assertGreaterEqual(fine['requested_parent_interval'][0],int(fs*.006))
        self.assertTrue(fine['quality_comparison']['coarse_and_fine_counts_are_not_comparable'])
        report(ctx);value=json.loads((ctx.run/'report.json').read_text())
        self.assertEqual(value['initial_segmentation_id'],result['segmentation_id'])
        self.assertEqual(value['segmentation_level'],'coarse_activity')
        self.assertEqual(value['coarse_to_fine']['locally_reviewed_regions'],1)


if __name__=='__main__':unittest.main()
