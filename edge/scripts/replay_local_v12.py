"""Official Harness acceptance for focused local comparison, using copied real IQ."""
import json,time,uuid
from pathlib import Path
from spectrum_agent.common import Context,ROOT,dump
from spectrum_agent.service import normalize,run_dsh
source=Context('task_b73949a5c41c428198c00908b18c8d35');ctx=Context('task_local_'+uuid.uuid4().hex[:12])
previous=json.loads((source.run/'report.json').read_text());base=previous['initial_segmentation_id'];cap=previous['captures'][0]['artifact_id']
bench=json.loads((ROOT/'docs/testing/local-benchmark-v12.json').read_text());target=next(c['case'] for c in bench['cases'] if c['case']['name']=='narrow_ble_packet')
for p in (source.run/'index').glob('*.json'):
    rec=json.loads(p.read_text())
    if rec['kind'] not in ('capture','subband','wifi_verification','ble_verification','ieee154_verification','segmentation'):continue
    rec=dict(rec,task_id=ctx.task_id)
    for k in ('path','metadata_path'):
        if k not in rec:continue
        original=Path(rec[k]);path=ROOT/'artifacts'/ctx.task_id/original.name;path.parent.mkdir(parents=True,exist_ok=True);path.hardlink_to(original);rec[k]=str(path)
    dump(ctx.run/'index'/(rec['artifact_id']+'.json'),rec)
request=normalize(dict(source='usrp',provider='deepseek',recording_capture_id=cap,
    constraints=dict(center_hz=2400e6,span_hz=100e6,observation_s=.1),budgets=dict(deadline_s=240,max_model_rounds=24,max_candidates=4),
    prompt=f'验收聚焦子带局部细分改进。原粗层 {base}，真实IQ和84条旧帧证据保持。不要重新采集、全局分割或解码。'
    f'使用相同目标范围做两次局部细查：父区域 {target["group_id"]}，center_hz={target["center_hz"]}，bandwidth_hz={target["bandwidth_hz"]}，'
    f'output_rate_hz={target["output_rate_hz"]}，offset_s={target["offset_s"]}，duration_s={target["duration_s"]}，margin_s=0.001。'
    '先assess_signal_scene。一次选择传统或adaptive_hysteresis，另一次必须试local_power_tiles，FFT/门限/时间频率瓦片由你根据采样率和短包证据选择。'
    '两次都必须显式传上述聚焦参数，不能跑整个宽带父区域；上下文只用于处理，候选只算目标范围。'
    '用compare_local_refinements对比；没有人工边界标签，不得声称准确率提高。'
    '重新assess_signal_scene后，以一个有依据且不漏已知包参考的局部候选提出fuse_refined_regions，若保护拒绝则保留粗层。'
    'compare_segmentations对比原粗层与融合候选，再明确select_segmentation；只选择一个同父局部方案用于融合。'
    '保留全部unknown残余及84条帧证据，说明范围、边界假设、未覆盖风险和停止原因。最后build_report。'))
dump(ctx.run/'provenance.json',dict(source_task=source.task_id,new_rf_capture=False,prior_local_details_retained_in_source_task=True))
print(json.dumps(dict(task_id=ctx.task_id,status='starting')),flush=True)
t=time.perf_counter();out=run_dsh(ctx.task_id,request);report=json.loads((ctx.run/'report.json').read_text())
events=[json.loads(x) for x in (ctx.run/'events.jsonl').read_text().splitlines()]
summary=dict(task_id=ctx.task_id,status=out['status'],elapsed_s=time.perf_counter()-t,new_rf_capture=False,
    sequence=[e['stage'] for e in events if e.get('phase')=='start'],
    local_comparisons=report.get('local_refinement_comparisons'),fusion=report.get('boundary_fusion'),
    selected=report['selected_segmentation_id'],regions=len(report['signal_groups']),coverage=report['analysis_coverage'],
    initial_regions=report['coarse_to_fine']['initial_coarse_regions'],accuracy_improvement='not_measured')
dump(ROOT/'docs/testing/local-harness-v12.json',summary);print(json.dumps(summary,ensure_ascii=False,indent=2),flush=True)
assert out['status']=='succeeded'
assert len(report['scene_refinements'])==2 and report['local_refinement_comparisons']
assert any(r['strategy']=='local_power_tiles' for r in report['scene_refinements'])
assert report['analysis_coverage']['verified_frame_count']==84
