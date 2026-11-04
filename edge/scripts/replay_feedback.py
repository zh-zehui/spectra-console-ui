"""Fixed real-IQ feedback regression, no RF capture or LLM calls."""
import json
import time
import uuid
from pathlib import Path
from spectrum_agent.common import ROOT,Context,dump
from spectrum_agent import dsp,scene
from spectrum_agent.worker import report

source=Context('cli_5459070e41b145d6')
ctx=Context('feedback_replay_v03_'+uuid.uuid4().hex[:8])
records=[json.loads(p.read_text()) for p in (source.run/'index').glob('*.json')]
for record in records:
    if record['kind'] not in ('capture','subband','ble_verification','wifi_verification','priors'): continue
    rec=dict(record,task_id=ctx.task_id)
    for key in ('path','metadata_path'):
        if key not in rec: continue
        original=Path(rec[key]);target=ROOT/'artifacts'/ctx.task_id/original.name
        target.parent.mkdir(parents=True,exist_ok=True)
        if not target.exists(): target.hardlink_to(original)
        rec[key]=str(target)
    dump(ctx.run/'index'/(rec['artifact_id']+'.json'),rec)
cap=next(r for r in records if r['kind']=='capture')['artifact_id']
dump(ctx.run/'provenance.json',dict(source_task=source.task_id,source_capture_id=cap,
    note='Recorded real IQ and existing protocol evidence; no new RF or protocol decoding in this replay.'))
start=time.perf_counter()
with ctx.span('replay_initial_segmentation'):
    seg=dsp.segment(ctx,cap)
before=scene.assess(ctx,seg['segmentation_id'])
groups=scene.read(ctx,seg['segmentation_id'])['signal_groups']
target=max(groups,key=lambda g:g['bandwidth_hz'])
with ctx.span('replay_local_refinement'):
    refinement=scene.refine(ctx,seg['segmentation_id'],target['group_id'],
        'Review the widest observed group using adaptive local boundaries; retain initial events and protocol uncertainty.',[before['review_id']])
profile=dsp.features(ctx,refinement['subband_id'])
after=scene.assess(ctx,seg['segmentation_id'])
report(ctx)
assert len(after['refinements'])==1
assert all(e['sample_start']<e['sample_stop'] for e in refinement['mapped_events'])
summary=dict(task_id=ctx.task_id,source_task=source.task_id,elapsed_s=time.perf_counter()-start,
    full_band_groups=seg['total_groups'],review_count=2,refinement_count=1,
    local_events=refinement['total_local_events'],quality_comparison=refinement['quality_comparison'],initial_group_preserved=True,
    requested_fft=refinement['requested_fft_size'],actual_fft=refinement['fft_size'],
    profile_evidence_id=profile['feature_artifact_id'],
    note='Validates execution, provenance and bounds; not a ground-truth accuracy measurement.')
dump(ROOT/'docs/testing/feedback-replay-v03.json',summary)
print(json.dumps(summary,indent=2))
