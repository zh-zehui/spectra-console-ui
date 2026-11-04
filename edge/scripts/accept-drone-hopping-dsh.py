"""Actual DSH integration on clearly labelled synthetic agile IQ; no RF access."""
import json,time,uuid
from collections import Counter
from spectrum_agent.common import ROOT,Context,dump
from spectrum_agent.service import normalize,run_dsh
from test_drone_hopping import make_agile_capture

source=Context('synthetic_agile_source_'+uuid.uuid4().hex[:10])
capture,prior,schedule=make_agile_capture(source)
dump(source.run/'outcome.json',dict(status='succeeded',note='Synthetic fixture generated; not an RF capture or live validation'))
ctx=Context('accept_agile_dsh_'+uuid.uuid4().hex[:12])
prompt=(f'复用 {source.task_id} 已保存的人工仿真IQ，新建联合分析，禁止硬件访问和新采集。'
    '这是一份24ms、中心5800MHz、采样率200MS/s、分析范围5720–5880MHz的仿真记录，不是真实无人机数据。'
    '通过已注册工具全面检查适用的Wi-Fi候选（含HT40/VHT80报头），并用独立变频变带宽无人机工具分析全部记录。'
    '仿真中的参考模式与频点会变化，不能用一个锁定中心代替跳频分析；查询已安装模式并自动搜索，不给定真实频点。'
    'Wi-Fi与图传分别保留证据级别，不能把参考匹配当作有效MAC，未知保留。'
    '明确标记仿真，不作为真机跳频验证。画时频图，保存调用与耗时，分析时间上限10分钟。')
request=normalize(dict(prompt=prompt,provider='deepseek',source='synthetic',budgets=dict(coverage_mode='complete',deadline_s=600,max_model_rounds=64)))
request['rf_capture_forbidden']=True
print(json.dumps(dict(task_id=ctx.task_id,fixture_task_id=source.task_id,phase='starting')),flush=True)
began=time.perf_counter();out=run_dsh(ctx.task_id,request)
r=json.loads((ctx.run/'report.json').read_text());evidence=r.get('drone_hopping_evidence',[])
events=[json.loads(line) for line in (ctx.run/'events.jsonl').read_text().splitlines()];calls=Counter(e['stage'] for e in events if e.get('phase')=='start')
result=dict(task_id=ctx.task_id,fixture_task_id=source.task_id,status=out['status'],elapsed_s=time.perf_counter()-began,
    simulated=True,live_hopping_validated=False,intent=r['task_intent'],protocol_coverage=r['protocol_coverage'],
    matched_references=sum(v['matched_references'] for v in evidence),drone_coverage=[v['coverage'] for v in evidence],
    sequence=[v['sequence'] for v in evidence],calls=dict(calls))
dump(ROOT/'docs/testing/drone-hopping-synthetic-dsh-acceptance.json',result)
print(json.dumps({k:v for k,v in result.items() if k not in ('intent','sequence','protocol_coverage')},ensure_ascii=False),flush=True)
assert out['status']=='succeeded',result
assert calls['analyze_drone_hopping'] and calls['plan_protocol_checks'] and calls['run_protocol_batch'],result
assert r['task_intent'].get('drone_hopping_analysis_requested') and r['protocol_coverage']['complete'],result
assert all(v['coverage']['complete'] for v in evidence) and result['matched_references']==6,result
assert not any(calls[n] for n in ('capture_iq','inspect_capabilities','make_synthetic_capture')),result
