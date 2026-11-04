"""Same ORIGINAL prompt as the slow task; real dsh planning,existing IQ only."""
import json,time,uuid
from collections import Counter
from spectrum_agent.common import ROOT,Context,dump
from spectrum_agent.service import normalize,run_dsh
source_request=json.loads((ROOT/'runs/task_f1273acd57fe449f93227f459391b6ec/request.json').read_text())
ctx=Context('task_batch_dsh_'+uuid.uuid4().hex[:12])
request=normalize(dict(prompt=source_request['prompt'],provider='deepseek',source='usrp',
    budgets=dict(coverage_mode='complete',deadline_s=1800,max_model_rounds=64,max_candidates=4096,max_local_refinements=8192)))
request['rf_capture_forbidden']=True
started=time.perf_counter();out=run_dsh(ctx.task_id,request)
r=json.loads((ctx.run/'report.json').read_text());events=[json.loads(line) for line in (ctx.run/'events.jsonl').read_text().splitlines()]
effective=json.loads((ctx.run/'request.json').read_text())
calls=Counter(e['stage'] for e in events if e.get('phase')=='start')
result=dict(task_id=ctx.task_id,old_task_id='task_f1273acd57fe449f93227f459391b6ec',source_task_id='task_4d7af074d5bd406e9d9dd781f242cd17',
    status=out['status'],elapsed_s=time.perf_counter()-started,mac_frame_observations=len(r['frame_associations']),
    vht80_header_groups=sum(h['phy_format']=='VHT' and h['channel_width_hz']==80e6 for h in r.get('wifi_header_groups',[])),
    protocol_coverage=r.get('protocol_coverage'),calls=dict(calls),new_rf_capture=False,intent=effective.get('task_intent'))
dump(ROOT/'docs/testing/protocol-batch-dsh-acceptance.json',result);print(json.dumps(result),flush=True)
assert result['status']=='succeeded' and result['protocol_coverage']['complete'],result
assert result['mac_frame_observations']>=1007 and result['vht80_header_groups']>=80,result
assert effective['task_intent']['analysis_window_s']==.01 and effective['task_intent']['visualization_requested'] and effective['task_intent']['wide_wifi_headers_requested'],result
assert not calls['capture_iq'] and not calls['inspect_capabilities']
assert result['elapsed_s']<300,'Coverage achieved but runtime target not achieved'
