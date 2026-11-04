"""Submit continuation through the authenticated task API; no key leaves Jetson."""
import json,time,uuid,urllib.request
from pathlib import Path
from spectrum_agent.common import ROOT,dump
from spectrum_agent.service import credentials

source='task_budget_v13_expanded_cap6_69fdbd0f'
token=credentials()['api_token']
def request(path,body=None):
    r=urllib.request.Request('http://127.0.0.1:8765'+path,
        data=json.dumps(body).encode() if body is not None else None,
        headers={'Authorization':'Bearer '+token,'Content-Type':'application/json'})
    with urllib.request.urlopen(r,timeout=15) as response:return json.load(response)

body=dict(continue_from_task_id=source,source='usrp',provider='deepseek',
    idempotency_key='continue-v14-'+uuid.uuid4().hex,
    budgets=dict(deadline_s=240,max_model_rounds=28,max_candidates=3,max_local_refinements=4),
    prompt='继续上次真实录音调查，不重新采集。先刷新调查队列，优先检查尚未局部检查的父区域和窗口，不要重复g10、g113、g126已经检查过的相同范围。'
    '根据新证据自主选择代表性目标、局部细分和信号画像，至少尝试一个尚未局部检查的父区域；不能把窗口重叠当成识别成功。'
    '对具有窄带/GFSK线索、且完整通带位于录音范围的候选，可自主选择analyze_bluetooth_classic；没有依据不要盲目调用。'
    '比较局部候选，若融合则保留此前已采用的局部证据及全部未知残余，并与原始及既有工作分割比较后再明确选择。'
    '刷新队列，记录暂缓目标的缺失证据及具体下一步。最后build_report，区分继承和本轮新增检查，说明未检查范围和停止原因，不宣称准确率提高。')
started=time.perf_counter();created=request('/v1/tasks',body);task=created['task_id']
dump(ROOT/'docs/testing/continuation-v14-progress.json',dict(task_id=task,source_task=source,status=created['status']))
print(json.dumps(dict(task_id=task,status=created['status'])),flush=True)
while True:
    status=request('/v1/tasks/'+task)
    if status['status'] in ('succeeded','partial','failed','cancelled'):break
    time.sleep(1)
report=request('/v1/tasks/'+task+'/result')['analysis']
events=[json.loads(x) for x in (ROOT/'runs'/task/'events.jsonl').read_text().splitlines()]
stages=[e['stage'] for e in events if e.get('phase')=='start']
manifest=report['continuation'];inherited=set(manifest['imported_artifact_ids'])
new_refs=[r for r in report['scene_refinements'] if r['evidence_id'] not in inherited]
old_refs=[r for r in report['scene_refinements'] if r['evidence_id'] in inherited]
before={r['parent_group_id'] for r in old_refs};after={r['parent_group_id'] for r in new_refs}
summary=dict(task_id=task,source_task=source,status=status['status'],elapsed_s=time.perf_counter()-started,
    new_rf_capture=False,initial_segmentation_id=report['initial_segmentation_id'],
    inherited_evidence_count=manifest['imported_count'],inherited_local_refinements=len(old_refs),
    new_local_refinements=len(new_refs),previously_checked_parents=sorted(before),newly_checked_parents=sorted(after-before),
    current_budget=report['analysis_budget'],cumulative_coverage=report['unknown_investigation']['local_check_coverage'],
    frame_count=report['analysis_coverage']['verified_frame_count'],working_regions=len(report['signal_groups']),
    sequence=stages,harness_called_classic='analyze_bluetooth_classic' in stages,
    failures=[dict(tool=e['stage'],error_code=e.get('error_code')) for e in events if e.get('phase')=='end' and e.get('status')=='failed'],
    accuracy_improvement='not_measured')
dump(ROOT/'docs/testing/continuation-v14.json',summary)
print(json.dumps(summary,ensure_ascii=False,indent=2),flush=True)
assert status['status']=='succeeded'
assert not set(stages)&{'capture_iq','make_synthetic_capture','inspect_capabilities'},'Continuation touched hardware'
assert report['analysis_coverage']['verified_frame_count']==84
assert after-before,'Continuation did not examine a previously unchecked parent'
assert len(old_refs)==4,'Historical local evidence lost'
