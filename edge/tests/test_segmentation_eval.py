import unittest
from spectrum_agent.segmentation_eval import evaluate,union_area


def box(start,stop,low=100,high=200):
    return dict(sample_start=start,sample_stop=stop,freq_low_hz=low,freq_high_hz=high)


class SegmentationEvalTests(unittest.TestCase):
    def case(self,**kwargs):
        return dict(target_samples=[0,100],target_frequency_hz=[100,200],**kwargs)

    def test_pending_empty_labels_cannot_produce_perfect_accuracy(self):
        r=evaluate([box(10,20)],self.case(human_label_status='pending',human_burst_labels=[],known_packet_estimates=[box(12,18)]))
        self.assertIsNone(r['boundary_accuracy'])
        self.assertEqual(r['known_packet_unmatched_count'],0)

    def test_split_and_merge_diagnostics_are_separate(self):
        c=self.case(human_label_status='complete',label_scope='all_observable_activity',reviewer='test',human_burst_labels=[box(10,30),box(50,60)])
        r=evaluate([box(10,20),box(20,30)],c)['boundary_accuracy']
        self.assertEqual(r['labels_overlapped_by_multiple_candidates'],1)
        self.assertEqual((r['missed_labels'],r['unmatched_candidates']),(1,1))
        self.assertAlmostEqual(r['union_area_iou'],2/3)
        r=evaluate([box(10,60)],c)['boundary_accuracy']
        self.assertEqual(r['candidates_overlapping_multiple_labels'],1)
        self.assertEqual(r['missed_labels'],1)

    def test_complete_noise_window_has_false_positives(self):
        c=self.case(human_label_status='complete',label_scope='all_observable_activity',reviewer='test',human_burst_labels=[])
        r=evaluate([box(10,20)],c)['boundary_accuracy']
        self.assertEqual(r['unmatched_candidates'],1)
        self.assertIsNone(r['recall'])
        with self.assertRaises(ValueError): evaluate([],self.case(human_label_status='complete'))

    def test_area_union_does_not_double_count_and_clips_context(self):
        self.assertEqual(union_area([(0,10,0,10),(5,15,5,15)]),175)
        r=evaluate([box(-50,10),box(110,120)],self.case())
        self.assertEqual(r['candidate_count'],1)
        with self.assertRaises(ValueError): evaluate([box(20,10)],self.case())
