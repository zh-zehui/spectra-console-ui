"""Real dsh/DeepSeek additive target investigation,immutable source IQ,Wi-Fi retained."""
import hashlib,json,time,uuid
from collections import Counter
from spectrum_agent.common import ROOT,Context,dump
from spectrum_agent.service import normalize,run_dsh

source='task_9668c13363904f8092ca082554958600';ctx=Context('task_drone_dsh_'+uuid.uuid4().hex[:12])
prompt=(f'继续 {source} 的分析，完整继承并保留已有Wi-Fi有效MAC帧和报头证据，不重新采集，不重复运行Wi-Fi接收器。'
    '我现场确认其中存在DJI Mavic4 Pro的定频10MHz图传，位置在约5800MHz附近，请在附近搜索真实载频，'
    '新增独立的无人机图传样本特征验证，对全部已记录时间进行检查，不能仅因为Wi-Fi检查完成就停止。'
    '使用已注册工具区分参考序列匹配、用户现场机型信息与协议CRC，不将类似NR的OFDM结构当作NR确认，不声称已解出视频。'
    '将图传特征框与原有Wi-Fi帧和报头一起显示到现有Web时频图，保存调用与阶段耗时。分析时间上限15分钟。')
request=normalize(dict(prompt=prompt,provider='deepseek',source='usrp',budgets=dict(coverage_mode='complete',deadline_s=900,max_model_rounds=32)))
request['rf_capture_forbidden']=True
began=time.perf_counter();out=run_dsh(ctx.task_id,request)
r=json.loads((ctx.run/'report.json').read_text());old=json.loads((ROOT/'runs'/source/'report.json').read_text())
events=[json.loads(line) for line in (ctx.run/'events.jsonl').read_text().splitlines()];calls=Counter(v['stage'] for v in events if v.get('phase')=='start')
def fingerprint(rows):return hashlib.sha256(json.dumps(rows,sort_keys=True).encode()).hexdigest()
wifi_unchanged=fingerprint(r['frame_associations'])==fingerprint(old['frame_associations'])
headers_unchanged=fingerprint(r['wifi_header_evidence'])==fingerprint(old['wifi_header_evidence'])
drone=r['drone_video_evidence'];latest=max(drone,key=lambda v:v['matched_references']) if drone else {}
result=dict(task_id=ctx.task_id,source_task_id=source,status=out['status'],elapsed_s=time.perf_counter()-began,
    original_wifi_observations=len(old['frame_associations']),wifi_observations=len(r['frame_associations']),wifi_exact_evidence_unchanged=wifi_unchanged,
    wifi_header_evidence_unchanged=headers_unchanged,vht80_groups=sum(v.get('phy_format')=='VHT' and v.get('channel_width_hz')==80e6 for v in r['wifi_header_groups']),
    drone_matched_references=latest.get('matched_references',0),drone_center_hz=latest.get('center_hz'),drone_coverage=latest.get('coverage'),
    drone_timing_ms=latest.get('timing_ms'),calls=dict(calls),new_rf_capture=bool(calls['capture_iq']),
    note='Real model tool loop; inheritedWi-Fi evidence compared exactly. Drone recording signature is not universal identity,payload CRC or video decode.')
dump(ROOT/'docs/testing/drone-video-dsh-acceptance.json',result);print(json.dumps(result),flush=True)
assert out['status']=='succeeded' and wifi_unchanged and headers_unchanged,result
assert latest.get('coverage',{}).get('complete') and latest.get('matched_references',0)>180,result
assert abs(latest['center_hz']-5803.5e6)<200e3,result
assert calls['analyze_drone_video_link'] and not any(calls[n] for n in ('capture_iq','inspect_capabilities','verify_protocol','inspect_wifi_headers','run_protocol_batch')),result
