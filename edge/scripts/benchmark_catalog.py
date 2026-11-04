"""Replay recorded events with identical detection settings; no RF or model calls."""
import hashlib
import json
from pathlib import Path
import statistics
import time
from spectrum_agent.common import ROOT,Context,dump
from spectrum_agent.catalog import group_events
from spectrum_agent.worker import report

task='cli_8c213eab929d4e18'
ctx=Context(task)
records=[json.loads(p.read_text()) for p in (ctx.run/'index').glob('*.json')]
rec=next(r for r in records if r['kind']=='segmentation')
path=Path(rec['path']);seg=json.loads(path.read_text())
capture=ctx.resolve(seg['capture_id'])
meta=json.loads(Path(capture['metadata_path']).read_text())
times=[]
for _ in range(10):
    start=time.perf_counter()
    groups=group_events(seg['events'],meta['sample_rate_hz'],seg['hop'])
    times.append(1000*(time.perf_counter()-start))
members=[e for g in groups for e in g['event_ids']]
assert len(members)==len(set(members))==len(seg['events'])
assert set(members)=={e['event_id'] for e in seg['events']}
value=dict(source_task=task,segmentation_id=rec['artifact_id'],
    source_sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
    detection_settings_unchanged=True,event_count=len(seg['events']),
    old_group_count=len(seg['signal_groups']),new_group_count=len(groups),
    every_event_retained_exactly_once=True,grouping_runs_ms=times,
    grouping_median_ms=statistics.median(times),
    note='Counts are spectral groups, not emitters. Group-count changes do not prove improved recall or precision.')
# Keep original reports/artifacts immutable; all replay output belongs to a new task.
replay=Context('catalog_replay_v02')
for r in records:
    r=dict(r)
    r['task_id']=replay.task_id
    for key in ('path','metadata_path'):
        if key not in r: continue
        original=Path(r[key]);target=ROOT/'artifacts'/replay.task_id/original.name
        target.parent.mkdir(parents=True,exist_ok=True)
        if not target.exists(): target.hardlink_to(original)
        r[key]=str(target)
    dump(replay.run/'index'/(r['artifact_id']+'.json'),r)
seg['signal_groups']=groups;seg['algorithm']+=' + time-ordered-group-replay-v2'
newpath=ROOT/'artifacts'/replay.task_id/'segmentation-replay.json';dump(newpath,seg)
rec=dict(rec,path=str(newpath))
rec.update(task_id=replay.task_id,size_bytes=newpath.stat().st_size,
           sha256=hashlib.sha256(newpath.read_bytes()).hexdigest())
dump(replay.run/'index'/(rec['artifact_id']+'.json'),rec)
report(replay)
result=json.loads((replay.run/'report.json').read_text())
value['frame_associations']=dict(total=len(result['frame_associations']),
    packet_time_frequency_overlap=sum(x['association_level']=='packet_time_frequency_overlap' for x in result['frame_associations']),
    extraction_window_only=sum(x['association_level']=='extraction_window_only' for x in result['frame_associations']))
dump(ROOT/'docs/testing/catalog-benchmark-v02.json',value)
print(json.dumps(value,indent=2))
