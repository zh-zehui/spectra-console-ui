"""Analyze a third saved recording through actual DSH without presuming bandwidth."""
import hashlib,json,time,uuid
from collections import Counter
from spectrum_agent.common import ROOT,Context,dump
from spectrum_agent.service import normalize,run_dsh

source='task_e7bb08a92f9440da91c043ab85f5b1b6'
ctx=Context('task_drone_third_'+uuid.uuid4().hex[:12])
original=ROOT/'runs'/source/'report.json'
before=hashlib.sha256(original.read_bytes()).hexdigest()
prompt=(f'复用 {source} 已保存的全部原始IQ，新建无人机图传分析任务，不重新采集，不修改源任务。'
    '用户希望在这第三份记录中找出无人机图传，未给出这份记录的中心信道或图传带宽。'
    '先看实际频谱与时域活动，再查询已安装图传参考配置，选择适用模式并搜索实际中心。'
    '不要假定与前两份记录相同：已安装的各参考模式都只是候选；若均不匹配，应保留未知结构并明确未匹配，不能硬套。'
    '对有证据的中心及模式覆盖完整500ms验证，必要时做其他参考配置的对照。'
    '将结果显示在现有Web时频图，区分样本参考匹配、名义包络、协议CRC与视频解码，保存公开决策、工具调用和阶段耗时。'
    '此次聚焦图传，不追加Wi-Fi解码，保留其他任务已有Wi-Fi证据。分析时间上限15分钟。')
request=normalize(dict(prompt=prompt,provider='deepseek',source='usrp',budgets=dict(coverage_mode='complete',deadline_s=900,max_model_rounds=40)))
request['rf_capture_forbidden']=True
print(json.dumps(dict(task_id=ctx.task_id,source_task_id=source,phase='starting')),flush=True)
began=time.perf_counter();out=run_dsh(ctx.task_id,request)
report=json.loads((ctx.run/'report.json').read_text())
events=[json.loads(line) for line in (ctx.run/'events.jsonl').read_text().splitlines()]
calls=Counter(e['stage'] for e in events if e.get('phase')=='start')
evidence=[{k:v for k,v in d.items() if k in ('profile_id','center_hz','matched_references','estimated_repetition_s','coverage','evidence_id')} for d in report.get('drone_video_evidence',[])]
result=dict(task_id=ctx.task_id,source_task_id=source,status=out['status'],elapsed_s=time.perf_counter()-began,
    source_report_unchanged=before==hashlib.sha256(original.read_bytes()).hexdigest(),evidence=evidence,calls=dict(calls),new_rf_capture=bool(calls['capture_iq']))
dump(ROOT/'docs/testing/drone-third-source-dsh-acceptance.json',result)
print(json.dumps(result),flush=True)
assert result['source_report_unchanged'] and not result['new_rf_capture'],result
assert out['status']=='succeeded',result
