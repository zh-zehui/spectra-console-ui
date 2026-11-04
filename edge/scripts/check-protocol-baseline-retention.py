"""Compare packet bytes,timing and RF observation channel,not just gross counts."""
import json,sys
from collections import defaultdict
from spectrum_agent.common import ROOT,dump

def packet_keys(task):
    r=json.loads((ROOT/'runs'/task/'report.json').read_text());attempts={v['evidence_id']:v for v in r['verification_attempts']};result=[]
    for link in r['frame_associations']:
        if link['protocol']!='IEEE802.11':continue
        frame=attempts[link['evidence_id']]['frames'][link['frame_index']]
        result.append((frame.get('frame_hex'),round((link['freq_low_hz']+link['freq_high_hz'])/2),link['sample_start']))
    return result

baseline=sys.argv[2] if len(sys.argv)>2 else 'task_window_wide_4065cef0c157';current=sys.argv[1]
expected=packet_keys(baseline);actual=packet_keys(current);lookup=defaultdict(list)
for raw,frequency,start in actual:lookup[(raw,frequency)].append(start)
missing=[]
for raw,frequency,start in expected:
    if not any(abs(start-t)<=400 for t in lookup[(raw,frequency)]):missing.append(dict(frequency_hz=frequency,sample_start=start))
result=dict(baseline_task_id=baseline,current_task_id=current,baseline_packet_observations=len(expected),current_packet_observations=len(actual),
    baseline_retained=len(expected)-len(missing),missing_count=len(missing),missing=missing[:20],
    matching='Exact MAC bytes and RF observation center,<=2us original-time tolerance; not a physical-device identity claim')
destination='protocol-batch-24-baseline-retention.json' if len(sys.argv)>2 else 'protocol-batch-baseline-retention.json'
dump(ROOT/'docs/testing'/destination,result);print(json.dumps(result))
assert not missing
