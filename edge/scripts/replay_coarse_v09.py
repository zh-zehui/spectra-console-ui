"""Official harness acceptance replay for the coarse-to-fine workflow."""
import json,time,uuid
from pathlib import Path
from spectrum_agent.common import ROOT,Context,dump
from spectrum_agent.service import normalize,run_dsh

source=Context('task_activity_f913d09da8e8');ctx=Context('task_coarse_'+uuid.uuid4().hex[:12])
previous=json.loads((source.run/'report.json').read_text());cap=previous['captures'][0]['artifact_id'];baseline=previous['selected_segmentation_id']
for path in (source.run/'index').glob('*.json'):
    record=json.loads(path.read_text())
    if record['kind'] not in ('capture','subband','wifi_verification','ble_verification','ieee154_verification','segmentation'):continue
    rec=dict(record,task_id=ctx.task_id)
    for key in ('path','metadata_path'):
        if key not in rec:continue
        original=Path(rec[key]);target=ROOT/'artifacts'/ctx.task_id/original.name;target.parent.mkdir(parents=True,exist_ok=True)
        target.hardlink_to(original);rec[key]=str(target)
    dump(ctx.run/'index'/(rec['artifact_id']+'.json'),rec)
request=normalize(dict(source='usrp',provider='deepseek',recording_capture_id=cap,
    constraints=dict(center_hz=2400e6,span_hz=100e6,observation_s=.1),
    budgets=dict(deadline_s=240,max_model_rounds=24,max_candidates=4),
    prompt=f'使用附加实测2.4GHz/100MHz/100ms IQ，验收新的从粗到细主流程。旧1312区域工作分割 {baseline} 只作历史对照，不能继续当初始粗分割。'
    '先对全部IQ调用coarse_segment_spectrum形成粗活动包络。FFT和粗网格参数由你选择，不能先跑整段细分割。'
    '已有22条校验帧是参考，不是粗区域边界真值；保留短窄活动和未知，不要求最少框。'
    '本次至少选择一个有依据的粗区域做assess_signal_scene和refine_signal_region，目标、offset_s、duration_s、细检测策略由你决定，尤其注意粗区域可跨很长时间。'
    '必要时再滤波验证；不要用控制帧28us时长替代整个活动包络。粗/细框与帧分层记录，不能直接比较框数。'
    '比较已有帧关联覆盖、完整样本覆盖和尺度取舍后明确选择coarse方案为工作父层，保留局部细查作为子层，最后报告未覆盖的验证范围。不要重新采集。'))
dump(ctx.run/'provenance.json',dict(source_task=source.task_id,source_capture_id=cap,new_rf_capture=False))
print(json.dumps(dict(task_id=ctx.task_id,status='starting')),flush=True)
start=time.perf_counter();out=run_dsh(ctx.task_id,request);report=json.loads((ctx.run/'report.json').read_text())
events=[json.loads(l) for l in (ctx.run/'events.jsonl').read_text().splitlines()]
sequence=[e['stage'] for e in events if e.get('phase')=='start' and e['stage'] not in ('harness','model_stream')]
summary=dict(task_id=ctx.task_id,status=out['status'],elapsed_s=time.perf_counter()-start,new_rf_capture=False,
    tool_sequence=sequence,selected_segmentation_id=report['selected_segmentation_id'],
    initial_segmentation_id=report['initial_segmentation_id'],segmentation_level=report['segmentation_level'],
    group_count=len(report['signal_groups']),hierarchy=report['coarse_to_fine'],
    decisions=report['segmentation_selections'],comparisons=report['segmentation_comparisons'],
    refinement_windows=[dict(parent=r['parent_group_id'],requested=r.get('requested_parent_interval'),
        padded=r['reviewed_parent_interval'],fine_events=len(r['mapped_events']),strategy=r['strategy']) for r in report['scene_refinements']])
dump(ROOT/'docs/testing/coarse-harness-v09.json',summary);print(json.dumps(summary,ensure_ascii=False,indent=2),flush=True)
assert out['status']=='succeeded'
assert report['segmentation_level']=='coarse_activity'
assert 'coarse_segment_spectrum' in sequence and 'refine_signal_region' in sequence
assert sequence.index('coarse_segment_spectrum')<sequence.index('refine_signal_region')
assert not {'capture_iq','make_synthetic_capture'}&set(sequence)
