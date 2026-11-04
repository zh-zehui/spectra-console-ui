"""Official Harness LTE-tool acceptance through task API, replaying existing IQ."""
import json,time,uuid,urllib.request
from spectrum_agent.common import ROOT,dump
from spectrum_agent.service import credentials
token=credentials()['api_token']
def api(path,body=None):
    req=urllib.request.Request('http://127.0.0.1:8765'+path,data=json.dumps(body).encode() if body is not None else None,
        headers={'Authorization':'Bearer '+token,'Content-Type':'application/json'})
    with urllib.request.urlopen(req,timeout=15) as response:return json.load(response)
body=dict(source='usrp',provider='deepseek',continue_from_task_id='task_37632c56c97545fea55cfb08683aa7f1',
    idempotency_key='lte-v15-'+uuid.uuid4().hex,
    budgets=dict(deadline_s=180,max_model_rounds=18,max_candidates=2,max_local_refinements=2),
    prompt='验收新接入的LTE同步和PBCH/MIB校验工具，继续已有真实室内录音，不重采集。查询先验、观察2350-2400MHz宽带活动，自主选择至多两个LTE候选中心。'
    '通过extract_subband提取中心1.4MHz通带、output_rate_hz=1920000，使用整个连续100ms录音（不是局部短框），调用verify_protocol(protocol=lte_pbch,duplex_mode=tdd)。'
    '载波中心由你根据观测选择，不必等于粗框中点。PSS/SSS单次低一致性命中只能保留为线索，只有PBCH CRC通过才报告广播确认。'
    '如未通过，明确仅测试哪些中心、未覆盖的参数范围及下一步，不得因用户确信有蜂窝信号而编造结论。保留84条既有有效帧。'
    '验证后刷新原始粗分割调查队列，重新compare_segmentations比较已有原始和工作候选，再select_segmentation。最后build_report记录停止原因和覆盖限制。')
start=time.perf_counter();created=api('/v1/tasks',body);tid=created['task_id']
dump(ROOT/'docs/testing/lte-harness-v15-progress.json',dict(task_id=tid,status=created['status']))
print(json.dumps(dict(task_id=tid,status=created['status'])),flush=True)
while True:
    status=api('/v1/tasks/'+tid)
    if status['status'] in ('succeeded','partial','failed','cancelled'):break
    time.sleep(1)
r=api('/v1/tasks/'+tid+'/result')['analysis']
events=[json.loads(x) for x in (ROOT/'runs'/tid/'events.jsonl').read_text().splitlines()]
stages=[e['stage'] for e in events if e.get('phase')=='start']
attempts=[v for v in r['verification_attempts'] if v['protocol']=='LTE']
summary=dict(task_id=tid,status=status['status'],elapsed_s=time.perf_counter()-start,new_rf_capture=False,
    lte_attempts=[dict(evidence_id=v['evidence_id'],center_hz=v['center_hz'],sync_candidates=v['synchronization_candidate_count'],valid_broadcast_blocks=v['validated_broadcast_blocks']) for v in attempts],
    validated_broadcast_blocks=r['analysis_coverage']['validated_broadcast_blocks'],
    preserved_packet_frames=r['analysis_coverage']['validated_packet_frames'],sequence=stages,
    segmentation_selection_status=r['segmentation_selection_status'],
    failures=[dict(stage=e['stage'],code=e.get('error_code')) for e in events if e.get('phase')=='end' and e.get('status')=='failed'])
dump(ROOT/'docs/testing/lte-harness-v15.json',summary);print(json.dumps(summary,ensure_ascii=False,indent=2),flush=True)
assert status['status']=='succeeded'
assert attempts,'Harness did not exercise the LTE decoder'
assert not set(stages)&{'capture_iq','make_synthetic_capture','inspect_capabilities'}
assert r['analysis_coverage']['validated_packet_frames']==84
