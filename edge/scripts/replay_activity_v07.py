"""Official harness evaluates fragmentation and consolidation on recorded RF."""
import json
import time
import uuid
from pathlib import Path
from spectrum_agent.common import ROOT,Context,dump
from spectrum_agent.service import normalize,run_dsh

source=Context('task_segchoice_8d9a2ff1b6d7');ctx=Context('task_activity_'+uuid.uuid4().hex[:12])
previous=json.loads((source.run/'report.json').read_text())
records=[json.loads(p.read_text()) for p in (source.run/'index').glob('*.json')]
for record in records:
    if record['kind'] not in ('capture','subband','ble_verification','wifi_verification','ieee154_verification','segmentation'):continue
    rec=dict(record,task_id=ctx.task_id)
    for key in ('path','metadata_path'):
        if key not in rec:continue
        target=ROOT/'artifacts'/ctx.task_id/Path(rec[key]).name;target.parent.mkdir(parents=True,exist_ok=True)
        target.hardlink_to(Path(rec[key]));rec[key]=str(target)
    dump(ctx.run/'index'/(rec['artifact_id']+'.json'),rec)
cap=previous['captures'][0]['artifact_id'];selected=previous['selected_segmentation_id']
request=normalize(dict(source='usrp',provider='deepseek',recording_capture_id=cap,
    constraints=dict(center_hz=2400e6,span_hz=100e6,observation_s=.1),
    budgets=dict(deadline_s=240,max_model_rounds=20,max_candidates=3),
    prompt=f'附加同一份2.4GHz/100MHz实测IQ及所有已有校验帧。用户认为检测框太多且未知太多。上一工作分割 {selected} 有1579个区域，其他分割工件也已附加。'
    '本次重点评估碎片是否能保守归并，优先考察新注册的consolidate_signal_regions，参数与是否采用由你决定；可拒绝不可靠的归并。'
    '比较原工作方案与归并候选，记录选择、原始成员保留情况和已验证包覆盖。未知不得凭重叠升级为已识别；区分未验证、已验证未通过与有有效帧重叠。'
    '本次不是继续盲目遍历门限。没有必要不得重跑全带检测或协议解码，已有证据足以做归并对照时及时报告。不要新采集。'))
dump(ctx.run/'provenance.json',dict(source_task=source.task_id,source_capture_id=cap,new_rf_capture=False))
print(json.dumps(dict(task_id=ctx.task_id,status='starting')),flush=True)
start=time.perf_counter();out=run_dsh(ctx.task_id,request);report=json.loads((ctx.run/'report.json').read_text())
events=[json.loads(l) for l in (ctx.run/'events.jsonl').read_text().splitlines()]
summary=dict(task_id=ctx.task_id,status=out['status'],elapsed_s=time.perf_counter()-start,new_rf_capture=False,
    tool_sequence=[e['stage'] for e in events if e.get('phase')=='start' and e['stage'] not in ('harness','model_stream')],
    selected_segmentation_id=report['selected_segmentation_id'],group_count=len(report['signal_groups']),
    decisions=report['segmentation_selections'],comparisons=report['segmentation_comparisons'],
    consolidation_candidates=[dict(id=s['evidence_id'],source=s['consolidated_from'],count=len(s['signal_groups']),
        retained=s['all_source_groups_retained']) for s in report['segmentations'] if s.get('consolidated_from')],
    accuracy_improvement='not_measured')
dump(ROOT/'docs/testing/activity-v07.json',summary)
print(json.dumps(summary,ensure_ascii=False,indent=2),flush=True)
assert out['status']=='succeeded'
assert report['segmentation_selection_status']=='explicit_model_selection'
assert not {'capture_iq','make_synthetic_capture'}&set(summary['tool_sequence'])
