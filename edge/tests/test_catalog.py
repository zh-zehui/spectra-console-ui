import unittest
from spectrum_agent.catalog import group_events,associate
from spectrum_agent.common import Context,dump
import uuid


class CatalogTests(unittest.TestCase):
    def event(self,id,start,stop,segment=0):
        return dict(event_id=id,segment_file_start=segment,sample_start=start,sample_stop=stop,
                    freq_low_hz=100,freq_high_hz=200,bandwidth_hz=100,duration_s=(stop-start)/1000,
                    snr_db=10,protocol='unknown')

    def test_order_gap_membership(self):
        events=[self.event('late',1000,1100),self.event('early',0,100),
                self.event('continuation',90,200),self.event('hardware_gap',90,200,1)]
        groups=group_events(events,1000,10)
        self.assertEqual(len(groups),3)
        self.assertEqual(groups[0]['event_ids'],['early','continuation'])
        self.assertEqual(groups[0]['sample_start'],0)
        self.assertEqual(groups[0]['sample_stop'],200)
        self.assertEqual(group_events(list(reversed(events)),1000,10),groups)

    def test_precise_and_window_links_no_relabel(self):
        ctx=Context('test_'+uuid.uuid4().hex[:12])
        cap,path=ctx.artifact('capture','cf32');path.write_bytes(b'')
        dump(path.with_suffix('.json'),dict(sample_rate_hz=200e6))
        ctx.register(cap,path,kind='capture',metadata_path=str(path.with_suffix('.json')))
        sub,path=ctx.artifact('subband','cf32');path.write_bytes(b'')
        dump(path.with_suffix('.json'),dict(parent_artifact_id=cap,parent_sample_start=100000,
             actual_parent_sample_stop=2100000,sample_rate_hz=4e6,center_hz=2402e6,
             requested_span_hz=2e6,filter_group_delay_s=1e-6))
        ctx.register(sub,path,kind='subband',metadata_path=str(path.with_suffix('.json')))
        group=dict(group_id='g0',sample_start=290000,sample_stop=400000,
                   freq_low_hz=2401e6,freq_high_hz=2403e6,protocol='unknown')
        seg=dict(capture_id=cap,evidence_id='s0',fft_size=1024,signal_groups=[group])
        ver=dict(artifact_id=sub,evidence_id='v0',protocol='Bluetooth LE',
                 frames=[dict(subband_sample_start=4000,center_hz=2402e6,payload_length=6)])
        result=associate(ctx,[seg],[ver])[0]
        self.assertEqual(result['sample_start'],298200)
        self.assertEqual(result['association_level'],'packet_time_frequency_overlap')
        self.assertEqual(len(result['candidate_groups']),1)
        self.assertEqual(group['protocol'],'unknown')
        ver['frames'][0]['pdu_hex']='01020304'
        duplicate=dict(ver,evidence_id='v1',frames=[dict(ver['frames'][0],subband_sample_start=4001)])
        merged=associate(ctx,[seg],[ver,duplicate])
        self.assertEqual(len(merged),1)
        self.assertEqual(merged[0]['duplicate_evidence'],[dict(evidence_id='v1',frame_index=0)])
        duplicate['frames'][0]['subband_sample_start']=8000
        self.assertEqual(len(associate(ctx,[seg],[ver,duplicate])),2)
        ver['frames']=[{}]
        self.assertEqual(associate(ctx,[seg],[ver])[0]['association_level'],'extraction_window_only')
