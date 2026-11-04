"""Actual official dsh/DeepSeek test on existing IQ; no RF tools permitted."""
import json,time,uuid
from spectrum_agent.common import ROOT,Context,dump
from spectrum_agent.service import normalize,run_dsh
source='task_4d7af074d5bd406e9d9dd781f242cd17'
prompt=(source+'，复用这个任务的IQ。仅分析最前20ms，按10ms主窗口分割，两侧保留2ms上下文。'
    '在5805MHz的20MHz观测子带检查HT/VHT报头，特别留意VHT80；区分SIG CRC通过与MAC FCS通过。'
    '只需这些检查，不做全记录或其它协议遍历，画出时频图，不重新采集。')
ctx=Context('task_window_harness_'+uuid.uuid4().hex[:12])
request=normalize(dict(prompt=prompt,provider='deepseek',source='usrp',
    budgets=dict(coverage_mode='sampled',deadline_s=240,max_model_rounds=24)))
request['rf_capture_forbidden']=True
started=time.perf_counter();out=run_dsh(ctx.task_id,request)
r=json.loads((ctx.run/'report.json').read_text());req=json.loads((ctx.run/'request.json').read_text())
calls=[e['stage'] for e in map(json.loads,(ctx.run/'events.jsonl').read_text().splitlines()) if e.get('phase')=='start']
checks=dict(model_window_s=req['task_intent'].get('analysis_window_s'),source_task_id=req.get('replay_from_task_id'),
    windowed=any('window_analysis' in s for s in r['segmentations']),plotted=bool(r.get('spectrograms')),
    header_observations=len(r.get('wifi_header_evidence',[])),no_rf='capture_iq' not in calls and 'inspect_capabilities' not in calls)
value=dict(task_id=ctx.task_id,status=out['status'],elapsed_s=time.perf_counter()-started,prompt=prompt,
    checks=checks,public_call_sequence=calls)
dump(ROOT/'docs/testing/window-header-harness.json',value);print(json.dumps(value,ensure_ascii=False),flush=True)
assert checks['model_window_s']==.01 and checks['source_task_id']==source and checks['windowed'] and checks['plotted'] and checks['no_rf']
assert 'inspect_wifi_headers' in calls
