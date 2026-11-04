"""Real dsh2.4GHz multi-protocol planning,not a fixed hand-written check list."""
import json,time,uuid
from collections import Counter
from spectrum_agent.common import ROOT,Context,dump
from spectrum_agent.service import normalize,run_dsh
source='task_6642f2a7797a4dd7acdb81ec243c7009'
prompt=(f'复用 {source} 的原始IQ，不重新采集。全面分析2400–2450MHz内全部已记录时间的信号，'
    '按10ms主窗口并保留2ms上下文。检查适用的Wi-Fi（包括802.11b DSSS/CCK和OFDM）、BLE及IEEE802.15.4候选，'
    '由你选择合适的已注册工具，批量覆盖全时间，不因信号弱而跳过候选。未知结构保留并说明依据。'
    '画出时频图，区分MAC校验与报头证据，保存公开决策与阶段耗时。时间上限15分钟。')
ctx=Context('task_batch24_dsh_'+uuid.uuid4().hex[:12]);start=time.perf_counter()
request=normalize(dict(prompt=prompt,source='usrp',provider='deepseek',budgets=dict(coverage_mode='complete',deadline_s=900,max_model_rounds=48)))
request['rf_capture_forbidden']=True;out=run_dsh(ctx.task_id,request)
r=json.loads((ctx.run/'report.json').read_text());counts=Counter(f['protocol'] for f in r['frame_associations'])
events=[json.loads(v) for v in (ctx.run/'events.jsonl').read_text().splitlines()];calls=Counter(e['stage'] for e in events if e.get('phase')=='start')
result=dict(task_id=ctx.task_id,source_task_id=source,status=out['status'],elapsed_s=time.perf_counter()-start,
    valid_frame_observations=dict(counts),protocol_coverage=r['protocol_coverage'],calls=dict(calls),new_rf_capture=False)
dump(ROOT/'docs/testing/protocol-batch-24-dsh-acceptance.json',result);print(json.dumps(result),flush=True)
assert out['status']=='succeeded' and r['protocol_coverage']['complete']
assert counts['IEEE802.11']>=47 and counts['Bluetooth LE']>=71,result
assert not calls['capture_iq'] and not calls['inspect_capabilities']
