"""Actual DSH investigation of a separate saved acquisition,not the training capture for10MHz."""
import hashlib,json,time,uuid
from collections import Counter
from spectrum_agent.common import ROOT,Context,dump
from spectrum_agent.service import normalize,run_dsh

source='task_962288f3229a48468017cdd4dca54aab';ctx=Context('task_drone_second_'+uuid.uuid4().hex[:12])
before_report=hashlib.sha256((ROOT/'runs'/source/'report.json').read_bytes()).hexdigest()
prompt=(f'复用 {source} 已保存的原始IQ，新建无人机图传分析，不重新采集。该源任务仅采集，没有已有分割需要继承。'
    '我确认这次记录也有无人机信号，请分析全部已记录时间，找出对应活动。'
    '截图线索位于大约5775–5812MHz附近，不能直接套用另一份数据的10MHz参考配置；'
    '先查询已安装的图传参考配置，再根据本次观测选择并验证，必要时搜索实际中心。'
    '已知存在不能替代实际匹配证据；参考匹配、名义观测包络、协议CRC和视频解码必须区分。'
    '将结果画在现有Web时频图上，保存公开调用和阶段耗时。此次聚焦图传，无需追加Wi-Fi解码；不得改动其他任务的Wi-Fi证据。'
    '分析时间上限15分钟。')
request=normalize(dict(prompt=prompt,provider='deepseek',source='usrp',budgets=dict(coverage_mode='complete',deadline_s=900,max_model_rounds=32)))
request['rf_capture_forbidden']=True
began=time.perf_counter();out=run_dsh(ctx.task_id,request)
r=json.loads((ctx.run/'report.json').read_text());drone=r.get('drone_video_evidence',[]);latest=max(drone,key=lambda v:v['matched_references']) if drone else {}
events=[json.loads(line) for line in (ctx.run/'events.jsonl').read_text().splitlines()];calls=Counter(v['stage'] for v in events if v.get('phase')=='start')
unchanged=before_report==hashlib.sha256((ROOT/'runs'/source/'report.json').read_bytes()).hexdigest()
result=dict(task_id=ctx.task_id,source_task_id=source,status=out['status'],elapsed_s=time.perf_counter()-began,
    source_report_unchanged=unchanged,source_was_capture_only=True,drone_profile_id=latest.get('profile_id'),center_hz=latest.get('center_hz'),
    matched_references=latest.get('matched_references',0),coverage=latest.get('coverage'),estimated_repetition_s=latest.get('estimated_repetition_s'),
    nominal_channel_bandwidth_hz=latest.get('profile',{}).get('nominal_channel_bandwidth_hz'),
    calls=dict(calls),new_rf_capture=bool(calls['capture_iq']),
    note='Separate recording and wider reference profile. Recording-signature evidence,not payload CRC,video decoding or independent aircraft identity.')
dump(ROOT/'docs/testing/drone-second-source-dsh-acceptance.json',result);print(json.dumps(result),flush=True)
assert out['status']=='succeeded' and unchanged,result
assert latest.get('coverage',{}).get('complete') and latest.get('matched_references',0)>100,result
assert latest.get('profile_id')=='zc1201_u504_30k_observed_v1',result
assert abs(latest['center_hz']-5793.5e6)<1e6,result
assert calls['list_drone_video_profiles'] and calls['analyze_drone_video_link'],result
assert not any(calls[n] for n in ('capture_iq','inspect_capabilities','verify_protocol','run_protocol_batch')),result
