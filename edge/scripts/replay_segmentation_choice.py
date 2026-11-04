"""Exercise official dsh tool selection on recorded RF; no new acquisition."""
import json
import time
import uuid
from pathlib import Path
from spectrum_agent.common import ROOT,Context,dump
from spectrum_agent.service import normalize,run_dsh

source=Context('task_dd70322264bc42d198f27322c0804d01')
ctx=Context('task_segchoice_'+uuid.uuid4().hex[:12])
records=[json.loads(p.read_text()) for p in (source.run/'index').glob('*.json')]
allowed={'capture','subband','ble_verification','wifi_verification','ieee154_verification','segmentation'}
for record in records:
    if record['kind'] not in allowed:continue
    rec=dict(record,task_id=ctx.task_id)
    for key in ('path','metadata_path'):
        if key not in rec:continue
        original=Path(rec[key]);target=ROOT/'artifacts'/ctx.task_id/original.name
        target.parent.mkdir(parents=True,exist_ok=True)
        target.hardlink_to(original)
        rec[key]=str(target)
    dump(ctx.run/'index'/(rec['artifact_id']+'.json'),rec)
cap=next(r for r in records if r['kind']=='capture')['artifact_id']
baseline=next(r for r in records if r['kind']=='segmentation')['artifact_id']
request=normalize(dict(source='usrp',provider='deepseek',recording_capture_id=cap,
    constraints=dict(center_hz=2400e6,span_hz=100e6,observation_s=.1),
    budgets=dict(deadline_s=300,max_model_rounds=24,max_candidates=3),
    prompt=f'分析已附加实测IQ中心2.4GHz、100MHz完整带宽。原有基线分割 {baseline} 是traditional、FFT4096、门限12dB，产生2057个区域，疑似过度碎片化。'
    '已有协议校验工件也已附加。由你自主决定调用哪些工具、选择何种策略和参数，检查问题、比较候选，再记录工作方案及理由。'
    '不以框少为目标，不因减少框而丢弃未知、弱信号、连续活动或已验证帧。可以保留基线，说明依据及局限。不要重新采集。'))
dump(ctx.run/'provenance.json',dict(source_task=source.task_id,source_capture_id=cap,
    baseline_segmentation_id=baseline,recorded_rf=True,new_rf_capture=False,
    note='Task-owned hard links to immutable recorded IQ/evidence. Tool choices made by official dsh, not this runner.'))
print(json.dumps(dict(task_id=ctx.task_id,status='starting',baseline=baseline)),flush=True)
start=time.perf_counter();outcome=run_dsh(ctx.task_id,request)
report=json.loads((ctx.run/'report.json').read_text())
events=[json.loads(l) for l in (ctx.run/'events.jsonl').read_text().splitlines()]
tools=[e['stage'] for e in events if e.get('phase')=='start' and e['stage'] not in ('harness','model_stream')]
summary=dict(task_id=ctx.task_id,status=outcome['status'],elapsed_s=time.perf_counter()-start,
    source_task=source.task_id,new_rf_capture=False,tool_sequence=tools,
    selected_segmentation_id=report['selected_segmentation_id'],selection_status=report['segmentation_selection_status'],
    candidates=[dict(id=s['evidence_id'],strategy=s.get('strategy'),fft_size=s['fft_size'],threshold_db=s['threshold_db'],
        count=len(s['signal_groups']),decision_reason=s.get('decision_reason')) for s in report['segmentations']],
    decisions=report['segmentation_selections'],comparisons=report['segmentation_comparisons'],
    accuracy_improvement='not_measured')
dump(ROOT/'docs/testing/harness-segmentation-choice-v06.json',summary)
print(json.dumps(summary,ensure_ascii=False,indent=2),flush=True)
assert not {'capture_iq','make_synthetic_capture'} & set(tools),'Replay must not acquire new IQ'
assert 'inspect_spectrum' in tools,'Model did not inspect numerical spectrum'
assert 'compare_segmentations' in tools and 'select_segmentation' in tools,'Missing explicit model comparison/selection'
assert len(report['signal_groups'])==len(next(s for s in report['segmentations'] if s['evidence_id']==report['selected_segmentation_id'])['signal_groups'])
assert outcome['status']=='succeeded',outcome['status']
