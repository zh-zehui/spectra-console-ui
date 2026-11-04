"""Official headless Harness integration against a copy of fresh hardware evidence."""
import json,time,uuid
from pathlib import Path
from spectrum_agent.common import Context,ROOT,dump
from spectrum_agent.service import normalize,run_dsh

source=Context('task_b73949a5c41c428198c00908b18c8d35')
ctx=Context('task_fusion_'+uuid.uuid4().hex[:12])
report=json.loads((source.run/'report.json').read_text())
for p in (source.run/'index').glob('*.json'):
    rec=json.loads(p.read_text())
    if rec['kind'] in ('segmentation_selection','segmentation_comparison'):continue
    rec=dict(rec,task_id=ctx.task_id)
    for k in ('path','metadata_path'):
        if k not in rec:continue
        original=Path(rec[k]);target=ROOT/'artifacts'/ctx.task_id/original.name
        target.parent.mkdir(parents=True,exist_ok=True);target.hardlink_to(original);rec[k]=str(target)
    dump(ctx.run/'index'/(rec['artifact_id']+'.json'),rec)
base=report['initial_segmentation_id'];refs=[r['evidence_id'] for r in report['scene_refinements']]
request=normalize(dict(source='usrp',provider='deepseek',recording_capture_id=report['captures'][0]['artifact_id'],
    constraints=dict(center_hz=2400e6,span_hz=100e6,observation_s=.1),
    budgets=dict(deadline_s=180,max_model_rounds=14,max_candidates=4),
    prompt=f'验收局部边界融合工具。沿用附件原始粗分割 {base}、局部细查 {refs} 和84条真实校验帧证据。'
    '不重新采集、不重跑粗或细分割、不重新解码。先重新 assess_signal_scene 获取包含这两次细查的最新review，'
    '调用 fuse_refined_regions 检查这两次细查能否采用。过密应拒绝并保留原父框；不要隐藏拒绝原因。'
    '比较原粗分割与新融合候选，再显式选择融合候选以保留拒绝审计（若细边界被全部拒绝，几何仍应与原始215区域完全一致）。'
    '保留84条校验帧，不能声称准确率改善；最后build_report并说明采用/拒绝和unknown残余。'))
dump(ctx.run/'provenance.json',dict(source_task=source.task_id,new_rf_capture=False))
print(json.dumps(dict(task_id=ctx.task_id,status='starting')),flush=True)
start=time.perf_counter();out=run_dsh(ctx.task_id,request)
result=json.loads((ctx.run/'report.json').read_text())
events=[json.loads(x) for x in (ctx.run/'events.jsonl').read_text().splitlines()]
summary=dict(task_id=ctx.task_id,status=out['status'],elapsed_s=time.perf_counter()-start,new_rf_capture=False,
    tool_sequence=[e['stage'] for e in events if e.get('phase')=='start'],
    boundary_fusion=result.get('boundary_fusion'),proposals=result.get('boundary_fusion_proposals'),
    region_count=len(result['signal_groups']),coverage=result['analysis_coverage'])
dump(ROOT/'docs/testing/fusion-harness-v10.json',summary)
print(json.dumps(summary,ensure_ascii=False,indent=2),flush=True)
assert out['status']=='succeeded'
assert result['boundary_fusion']['rejected_parent_count']==2
assert result['boundary_fusion']['accepted_parent_count']==0
assert len(result['signal_groups'])==215
assert result['analysis_coverage']['verified_frame_count']==84
assert 'fuse_refined_regions' in summary['tool_sequence']
