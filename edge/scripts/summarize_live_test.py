"""Read-only evidence summary for one fresh hardware acceptance task."""
import collections,json,sys
from pathlib import Path

task=sys.argv[1]
root=Path('/home/sribd/spectrum_agent')
run=root/'runs'/task
events=[json.loads(x) for x in (run/'events.jsonl').read_text().splitlines()]
ends=[e for e in events if e.get('phase')=='end']
totals=collections.defaultdict(float)
for e in ends: totals[e['stage']]+=e.get('elapsed_ms',0)/1000
records=[json.loads(p.read_text()) for p in (run/'index').glob('*.json')]
captures=[json.loads(Path(r['metadata_path']).read_text()) for r in records if r['kind']=='capture']
out=dict(task_id=task,last_event=events[-1],stage_seconds=dict(totals),
         failed_tools=[e for e in ends if e.get('status')=='failed'],captures=captures)
if (run/'outcome.json').exists():out['outcome']=json.loads((run/'outcome.json').read_text())
if (run/'report.json').exists():
    r=json.loads((run/'report.json').read_text())
    for k in ('analysis_coverage','coarse_to_fine','segmentation_level','segmentation_quality','feedback_loop','segmentation_selection_status'):
        out[k]=r.get(k)
    out['verifications']=[{k:v.get(k) for k in ('protocol','channel','valid_frames','evidence_id','status','warnings')} for v in r['verification_attempts']]
    out['verification_details']=r['verification_attempts']
    out['coarse_metadata']=[{k:v for k,v in s.items() if k not in ('events','signal_groups')} for s in r['segmentations'] if s.get('segmentation_level')=='coarse_activity']
    out['refinements']=[{k:v for k,v in s.items() if k not in ('mapped_events','signal_groups','events')} for s in r['scene_refinements']]
    out['groups_summary']=[{k:g.get(k) for k in ('group_id','freq_low_hz','freq_high_hz','duration_s','bandwidth_hz','snr_db')} for g in sorted(r['signal_groups'],key=lambda g:g['snr_db'],reverse=True)[:10]]
    out['selections']=r['segmentation_selections']
print(json.dumps(out,ensure_ascii=False,indent=2))
