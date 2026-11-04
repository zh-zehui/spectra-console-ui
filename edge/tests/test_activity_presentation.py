import unittest
from spectrum_agent.segmentation_tools import merge_regions
from spectrum_agent.presentation import activity_catalog


class ActivityTests(unittest.TestCase):
    def region(self,id,start,stop,lo=2400e6,hi=2402e6,segment=0):
        return dict(group_id=id,segment_file_start=segment,sample_start=start,sample_stop=stop,
            freq_low_hz=lo,freq_high_hz=hi,bandwidth_hz=hi-lo,duration_s=(stop-start)/1e6,
            snr_db=15,event_ids=[id],event_count=1,protocol='unknown')

    def test_nearby_fragments_merge_with_every_member_preserved(self):
        source=[self.region('a',0,100),self.region('b',110,200),self.region('c',400,500)]
        result=merge_regions(source,1e6,.00002)
        self.assertEqual(len(result),2)
        self.assertEqual(result[0]['source_group_ids'],['a','b'])
        self.assertEqual(sum(len(g['source_group_ids']) for g in result),len(source))
        self.assertEqual(result[0]['sample_stop'],200)
        self.assertEqual(source[0]['sample_stop'],100)

    def test_separates_narrow_overlap_frequency_neighbors_and_capture_gaps(self):
        source=[self.region('wide',0,100,2400e6,2420e6),
            self.region('narrow',10,90,2401e6,2402e6),
            self.region('neighbor',10,90,2421e6,2423e6),
            self.region('gap',110,200,2400e6,2420e6,segment=110)]
        self.assertEqual(len(merge_regions(source,1e6)),4)

    def test_display_groups_are_unknown_and_do_not_cover_interburst_gaps(self):
        def r(id,t0,t1):return dict(id=id,f0=2401,f1=2402,t0=t0,t1=t1,status='unknown',type='unknown')
        regions=[r('a',1,2),r('b',80,81)]
        packet=dict(r('p',1,2),status='verified',name='Bluetooth LE · 有效帧证据',feature='包级候选关联')
        catalog=activity_catalog(regions,[packet])
        self.assertEqual(len(catalog),2)
        unknown=next(x for x in catalog if x['status']=='unknown')
        self.assertEqual(unknown['evidence_state'],'存在有效帧重叠')
        self.assertEqual(len(unknown['members']),2)
        self.assertEqual([(m['t0'],m['t1']) for m in unknown['members']],[(1,2),(80,81)])

    def test_unknown_reasons_remain_distinct(self):
        a=dict(id='a',f0=2401,f1=2402,t0=1,t1=2)
        b=dict(id='b',f0=2420,f1=2421,t0=1,t1=2)
        values=activity_catalog([a,b],[],[a])
        self.assertEqual({v['evidence_state'] for v in values},{'局部验证未通过','尚未验证'})


if __name__=='__main__':unittest.main()
