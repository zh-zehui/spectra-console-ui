"""Bounded official DeepSeek Harness acceptance against one inherited subband."""
import json,time,uuid,urllib.request
from spectrum_agent.common import ROOT,dump
from spectrum_agent.service import credentials
token=credentials()['api_token']
def api(path,body=None):
    req=urllib.request.Request('http://127.0.0.1:8765'+path,
        data=json.dumps(body).encode() if body is not None else None,
        headers={'Authorization':'Bearer '+token,'Content-Type':'application/json'})
    with urllib.request.urlopen(req,timeout=15) as response:return json.load(response)
body=dict(source='usrp',provider='deepseek',continue_from_task_id='task_6642f2a7797a4dd7acdb81ec243c7009',
    idempotency_key='crafter-harness-'+uuid.uuid4().hex,
    budgets=dict(coverage_mode='sampled',deadline_s=180,max_model_rounds=10,max_candidates=2,max_local_refinements=1),
    prompt='本次只验收新增libcrafter接收器，不是重新调查整个频段。复用已继承的子带subband_5fa6ffeac7474dc1，'
    '不要采集、不要检查硬件、不要重新分割或批量复查。针对旧OFDM接收未解释的DSSS/CCK或HT聚合，'
    '自行选择wifi_crafter_dsss或wifi_crafter_all，实际调用verify_protocol验证该子带。'
    '区分MAC FCS通过、仅头部检查通过、未知或不支持；保留全部已有证据，不把重复记录当成新设备。'
    '完成后build_report，stop_reason=other，说明这是单子带工具验收，并给出新增有效格式与公开调用、耗时摘要。')
started=time.perf_counter();created=api('/v1/tasks',body);task=created['task_id']
dump(ROOT/'docs/testing/libcrafter-harness-progress.json',dict(task_id=task,status=created['status']))
print(json.dumps(dict(task_id=task,status=created['status'])),flush=True)
while True:
    status=api('/v1/tasks/'+task)
    if status['status'] in ('succeeded','partial','failed','cancelled'):break
    time.sleep(2)
analysis=api('/v1/tasks/'+task+'/result')['analysis']
events=[json.loads(line) for line in (ROOT/'runs'/task/'events.jsonl').read_text().splitlines()]
seq=[e['stage'] for e in events if e.get('phase')=='start']
checks=[v for v in analysis.get('verification_attempts',[]) if str(v.get('verification_profile','')).startswith('wifi_crafter_')]
result=dict(task_id=task,status=status['status'],elapsed_s=time.perf_counter()-started,
    new_rf_capture=False,provider='deepseek',official_harness=True,public_call_sequence=seq,
    checks=[dict(profile=v['verification_profile'],valid_frames=v['valid_frames'],
        formats={fmt:sum(f['phy_format']==fmt for f in v['frames']) for fmt in ('DSSS','CCK','HT20','legacy_ofdm')}) for v in checks])
dump(ROOT/'docs/testing/libcrafter-harness.json',result);print(json.dumps(result),flush=True)
assert not set(seq)&{'capture_iq','inspect_capabilities','make_synthetic_capture'}
assert checks,'Official Harness did not exercise libcrafter'
