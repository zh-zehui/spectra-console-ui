"""Export public measurement/tool decisions and timing,without credentials or IQ."""
import json,sys
from collections import Counter,defaultdict
from spectrum_agent.common import ROOT,dump

task_ids=sys.argv[1:]
summaries=[]
for task_id in task_ids:
    folder=ROOT/'runs'/task_id
    report=json.loads((folder/'report.json').read_text())
    events=[json.loads(line) for line in (folder/'events.jsonl').read_text().splitlines()]
    totals=defaultdict(float);counts=Counter();errors=Counter()
    public=[]
    for event in events:
        if event.get('phase')=='start':counts[event['stage']]+=1
        if event.get('phase')=='end':
            totals[event['stage']]+=event.get('elapsed_ms',0)/1000
            if event.get('status')=='failed':errors[(event['stage'],event.get('error_code'))]+=1
        if event.get('phase') in ('start','end','checkpoint'):
            public.append({k:event[k] for k in ('utc','stage','phase','elapsed_ms','status','error_code','plan_id','total_units','completed_units','failed_units') if k in event})
    counts_by_protocol=Counter(link['protocol'] for link in report['frame_associations'])
    summary=dict(task_id=task_id,status=json.loads((folder/'outcome.json').read_text())['status'],
        frame_observations=dict(counts_by_protocol),vht80_header_groups=sum(h.get('phy_format')=='VHT' and h.get('channel_width_hz')==80e6 for h in report.get('wifi_header_groups',[])),
        protocol_coverage=report['protocol_coverage'],calls=dict(counts),stage_elapsed_s={k:round(v,3) for k,v in totals.items()},
        failed_calls=[dict(stage=k[0],error_code=k[1],count=v) for k,v in errors.items()],
        new_rf_capture=bool(counts['capture_iq']),
        trace_note='Public tool/phase trace; nested stage durations overlap and must not be summed as wall time. A completed plan is not exhaustive signal identification.')
    summaries.append(summary)
    dump(ROOT/'docs/testing'/(task_id+'-public-timing.json'),dict(summary=summary,public_events=public))
dump(ROOT/'docs/testing/protocol-batch-final-validation.json',summaries)
for s in summaries:
    print(json.dumps({k:v for k,v in s.items() if k not in ('protocol_coverage','stage_elapsed_s')}))
    print(json.dumps(dict(task_id=s['task_id'],stage_elapsed_s=s['stage_elapsed_s'])))
