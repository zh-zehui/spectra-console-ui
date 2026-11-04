"""Full existing-IQ replay through actual DSH; inherit Wi-Fi unchanged, no RF."""
import hashlib,json,time,uuid
from collections import Counter
from spectrum_agent.common import ROOT,Context,dump
from spectrum_agent.service import normalize,run_dsh

source='task_49fb52faa41f40f8a492211d85969bad';ctx=Context('task_agile_full_'+uuid.uuid4().hex[:12])
original=ROOT/'runs'/source/'report.json';before=hashlib.sha256(original.read_bytes()).hexdigest();old=json.loads(original.read_text())
prompt=(f'继续任务 {source}，复用原始IQ，完整继承Wi-Fi有效MAC与SIG报头证据，禁止重新采集，不重复Wi-Fi解码。'
    '仅用独立的analyze_drone_hopping新版重新检查原始记录全部1秒、5720–5880MHz，检查10/20/40MHz所有已注册参考。'
    '旧跳频参考仅完成前140ms，这次必须覆盖全部100个10ms时间块，包括高频端及记录后半段；不能把旧部分覆盖当作完成。'
    '新版共享FFT前端替代重复提取子带，检测阈值保持默认不降低。用max_blocks=8、max_probes=4096批量推进；max_probes是每次调用的假设上限，不是每块，降低它不会跳过剩余假设。'
    '保持相同分析参数重复调用直到coverage.complete=true，旧方法的参考产物保留归档但由新方法全段结果替代展示，不能累加重复匹配。'
    '不要因为只匹配到低频端就断言没有高频无人机；未支持参考或能量提议遗漏保持未知。'
    '图上绘制各次参考自己的中心/带宽/时间。结合实际证据说明是否真的观察到频率及带宽变化，不把多个源或接收镜像当作同一架飞机跳频。'
    '保留原有时频图与Wi-Fi叠加，保存公开决策、调用和耗时。分析时长上限30分钟，剩余时间以实际deadline_remaining_s为准，不得凭累计调用次数声称预算耗尽。')
request=normalize(dict(prompt=prompt,provider='deepseek',source='usrp',budgets=dict(coverage_mode='complete',deadline_s=1800,max_model_rounds=64)))
request['rf_capture_forbidden']=True
print(json.dumps(dict(task_id=ctx.task_id,source_task_id=source,phase='starting')),flush=True)
began=time.perf_counter();out=run_dsh(ctx.task_id,request)
r=json.loads((ctx.run/'report.json').read_text());evidence=r.get('drone_hopping_evidence',[])
events=[json.loads(l) for l in (ctx.run/'events.jsonl').read_text().splitlines()];calls=Counter(v['stage'] for v in events if v.get('phase')=='start')
def digest(v):return hashlib.sha256(json.dumps(v,sort_keys=True).encode()).hexdigest()
rows=[row for e in evidence for row in e['references']];previous=[row for e in old.get('drone_hopping_evidence',[]) for row in e['references']]
retained=sum(any(a['profile_id']==b['profile_id'] and abs(a['reference_sample_start']-b['reference_sample_start'])<4000 and abs(a['center_hz']-b['center_hz'])<=2e6 for b in rows) for a in previous)
result=dict(task_id=ctx.task_id,source_task_id=source,status=out['status'],elapsed_s=time.perf_counter()-began,
    original_report_unchanged=before==hashlib.sha256(original.read_bytes()).hexdigest(),
    wifi_exact_unchanged=digest(old['frame_associations'])==digest(r['frame_associations']),
    headers_exact_unchanged=digest(old['wifi_header_evidence'])==digest(r['wifi_header_evidence']),
    wifi_frames=len(r['frame_associations']),headers=len(r['wifi_header_evidence']),
    matched_references=len(rows),old_reference_observations=len(previous),old_references_retained=retained,
    coverage=[v['coverage'] for v in evidence],sequence=[v['sequence'] for v in evidence],calls=dict(calls),new_rf_capture=bool(calls['capture_iq']))
dump(ROOT/'docs/testing/agile-real-full-v2-acceptance.json',result)
print(json.dumps({k:v for k,v in result.items() if k not in ('sequence',)},ensure_ascii=False),flush=True)
assert result['original_report_unchanged'] and result['wifi_exact_unchanged'] and result['headers_exact_unchanged'],result
assert out['status']=='succeeded' and evidence and all(v['coverage']['complete'] for v in evidence),result
assert retained==len(previous) and not any(calls[n] for n in ('capture_iq','inspect_capabilities','run_protocol_batch','verify_protocol')),result
