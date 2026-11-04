"""Replay previously captured real IQ; no new RF or model calls."""
import json
import uuid
from pathlib import Path
from spectrum_agent.common import ROOT,Context,dump
from spectrum_agent import dsp,wifi,ieee154,scene
from spectrum_agent.worker import report

source=Context('cli_8c213eab929d4e18');ctx=Context('protocol_replay_v05_'+uuid.uuid4().hex[:8])
records=[json.loads(p.read_text()) for p in (source.run/'index').glob('*.json')]
for r in records:
    if r['kind'] not in ('capture','subband','segmentation'): continue
    rec=dict(r,task_id=ctx.task_id)
    for key in ('path','metadata_path'):
        if key not in rec: continue
        original=Path(rec[key]);target=ROOT/'artifacts'/ctx.task_id/original.name
        target.parent.mkdir(parents=True,exist_ok=True);target.hardlink_to(original);rec[key]=str(target)
    dump(ctx.run/'index'/(rec['artifact_id']+'.json'),rec)
cap=next(r['artifact_id'] for r in records if r['kind']=='capture')
dump(ctx.run/'provenance.json',dict(source_task=source.task_id,source_capture_id=cap,note='Historical real IQ replay, not a new RF observation'))
wifi_results=[]
for r in records:
    if r['kind']!='subband': continue
    meta=json.loads(Path(r['metadata_path']).read_text())
    if meta['sample_rate_hz']!=20e6: continue
    with ctx.span('wifi_timestamp_replay'):
        value=wifi.verify(ctx,r['artifact_id'])
    wifi_results.append(dict(evidence_id=value['evidence_id'],valid_frames=value['valid_frames'],
        timestamped_frames=sum('subband_sample_start' in f for f in value['frames'])))
with ctx.span('ieee154_historical_channel15_replay'):
    sub=dsp.extract(ctx,cap,2425e6,2e6,sample_start=0,sample_stop=4_000_000,output_rate_hz=4e6)
    value=ieee154.verify(ctx,sub['artifact_id'],15)
seg_id=next(r['artifact_id'] for r in records if r['kind']=='segmentation')
review=scene.assess(ctx,seg_id)
report(ctx)
r=json.loads((ctx.run/'report.json').read_text())
summary=dict(task_id=ctx.task_id,source_task=source.task_id,wifi_attempts=wifi_results,
    ieee154=dict(channel=15,valid_frames=value['valid_frames'],phy_candidates=value['phy_candidates'],
        analyzed_duration_s=sub['duration_s'],upper_layer_protocol=value['upper_layer_protocol']),
    packet_time_links=sum(l['association_level']=='packet_time_frequency_overlap' for l in r['frame_associations']),
    packet_boundary_review_targets=sum('verified_packet_extends_candidate_group' in g['flags'] for g in review['review_targets']),
    note='Real-IQ execution regression; no controlled802.15.4 RF-positive source, no accuracy claim for real packet boundaries')
dump(ROOT/'docs/testing/protocol-replay-v05.json',summary)
print(json.dumps(summary,indent=2))
