"""Recorded real-IQ integration: the official Harness chooses unknown-region work."""
import json,time,uuid
from pathlib import Path
from spectrum_agent.common import Context,ROOT,dump
from spectrum_agent.service import normalize,run_dsh

source=Context('task_b73949a5c41c428198c00908b18c8d35');ctx=Context('task_investigate_'+uuid.uuid4().hex[:12])
baseline=json.loads((source.run/'report.json').read_text());base=baseline['initial_segmentation_id']
for p in (source.run/'index').glob('*.json'):
    rec=json.loads(p.read_text())
    if rec['kind'] in ('segmentation_selection','segmentation_comparison','investigation_plan','investigation_decision'):continue
    rec=dict(rec,task_id=ctx.task_id)
    for k in ('path','metadata_path'):
        if k not in rec:continue
        original=Path(rec[k]);target=ROOT/'artifacts'/ctx.task_id/original.name
        target.parent.mkdir(parents=True,exist_ok=True);target.hardlink_to(original);rec[k]=str(target)
    dump(ctx.run/'index'/(rec['artifact_id']+'.json'),rec)
request=normalize(dict(source='usrp',provider='deepseek',recording_capture_id=baseline['captures'][0]['artifact_id'],
    constraints=dict(center_hz=2400e6,span_hz=100e6,observation_s=.1),
    budgets=dict(deadline_s=240,max_model_rounds=24,max_candidates=4),
    prompt=f'验收未知区域自动复查闭环。已有真机IQ和原始粗分割 {base}，215个粗区域和84条既有帧证据。'
    '不重新采集、不重跑粗分割或细分割；保留原粗分割作为工作父层，不做融合验收。'
    '必须用 plan_unknown_investigations 获取4个不同代表目标；不要只看最强、也不要只挑已校验帧。'
    '至少选择两个不同类型目标，按你观察到的证据选择子带窗口和参数做画像/结构分析。'
    '若代表队列未包含合理的已支持协议目标，可从原粗区域补选一个在ISM信道内、有依据的残余区域，'
    '用精确且未重复的局部提取窗口尝试一次verify_protocol，记录窗口范围；不允许把未检出说成该协议不存在。'
    '每轮新分析后刷新队列，再用record_investigation_decision为实际复查的至少两个目标记录continue或defer、相关证据、下一步缺什么。'
    '在最后完成报告前，把本轮已分析但不能确认的目标暂缓并写明确停止理由，不要循环重复同样参数。'
    '可以用现有先验和解码证据指导选择，但不能把帧重叠当整组已确认。预算保留报告轮次，最后build_report。'))
dump(ctx.run/'provenance.json',dict(source_task=source.task_id,new_rf_capture=False))
print(json.dumps(dict(task_id=ctx.task_id,status='starting')),flush=True)
start=time.perf_counter();out=run_dsh(ctx.task_id,request)
report=json.loads((ctx.run/'report.json').read_text());queue=report['unknown_investigation']
events=[json.loads(x) for x in (ctx.run/'events.jsonl').read_text().splitlines()]
summary=dict(task_id=ctx.task_id,status=out['status'],elapsed_s=time.perf_counter()-start,new_rf_capture=False,
    sequence=[e['stage'] for e in events if e.get('phase')=='start'],region_count=len(report['signal_groups']),
    coverage=report['analysis_coverage'],states=queue['states'],deferred_count=queue['deferred_count'],
    decisions=[dict(group_id=r['group_id'],state=r['investigation_state'],stale=r['decision_stale'],decision=r['decision']) for r in queue['regions'] if r['decision']],
    new_attempts=[json.loads(Path(json.loads(p.read_text())['path']).read_text()) for p in (ctx.run/'index').glob('*.json') if json.loads(p.read_text())['kind']=='verification_attempt'])
dump(ROOT/'docs/testing/investigation-harness-v11.json',summary)
print(json.dumps(summary,ensure_ascii=False,indent=2),flush=True)
assert out['status']=='succeeded'
assert len(report['signal_groups'])==215 and queue['total_regions']==215
assert len(summary['decisions'])>=2
assert 'plan_unknown_investigations' in summary['sequence'] and 'record_investigation_decision' in summary['sequence']
assert summary['new_attempts']
assert queue['deferred_count']>=2
