"""Official Harness acceptance of NR profile and SSB-raster selection through API."""
import json,time,uuid,urllib.request
from spectrum_agent.common import ROOT,dump
from spectrum_agent.service import credentials
token=credentials()['api_token']
def api(path,body=None):
    r=urllib.request.Request('http://127.0.0.1:8765'+path,data=json.dumps(body).encode() if body is not None else None,
        headers={'Authorization':'Bearer '+token,'Content-Type':'application/json'})
    with urllib.request.urlopen(r,timeout=15) as response:return json.load(response)
body=dict(source='usrp',provider='deepseek',continue_from_task_id='task_f4deea19200a4da28678ae5e8756465c',
    idempotency_key='nr-v16-'+uuid.uuid4().hex,
    budgets=dict(deadline_s=180,max_model_rounds=18,max_candidates=2,max_local_refinements=2),
    prompt='验收5G NR独立解码链路，继续同一份深圳室内真实录音，不重新采集。先查询频段先验，读取n40_ssb_raster。'
    '结合2350–2400MHz已有宽带活动，自主从标准SSB栅格中选择至多两个中心，不要直接套LTE载波中心或粗框中点。'
    '提取完整连续100ms的8.8MHz子带，output_rate_hz=15360000，调用verify_protocol(protocol=nr_pbch,ssb_scs_khz=30,ssb_pattern=C,duplex_mode=tdd)。'
    '只有NR PBCH CRC及MIB解析通过才能确认；未通过不能排除现场5G。MIB不提供完整载波带宽或运营商身份。'
    '保留全部既有84条包帧，刷新调查队列，验证后重新compare_segmentations并select_segmentation。最后build_report，写明测试中心、未覆盖范围和停止理由，不宣称准确率提高。')
started=time.perf_counter();created=api('/v1/tasks',body);task=created['task_id']
dump(ROOT/'docs/testing/nr-harness-v16-progress.json',dict(task_id=task,status=created['status']))
print(json.dumps(dict(task_id=task,status=created['status'])),flush=True)
while True:
    status=api('/v1/tasks/'+task)
    if status['status'] in ('succeeded','partial','failed','cancelled'):break
    time.sleep(1)
r=api('/v1/tasks/'+task+'/result')['analysis']
ev=[json.loads(x) for x in (ROOT/'runs'/task/'events.jsonl').read_text().splitlines()]
sequence=[e['stage'] for e in ev if e.get('phase')=='start']
attempts=[v for v in r['verification_attempts'] if v['protocol']=='5G NR']
summary=dict(task_id=task,status=status['status'],elapsed_s=time.perf_counter()-started,new_rf_capture=False,
    nr_attempts=[dict(evidence_id=v['evidence_id'],center_hz=v['center_hz'],scs_khz=v['ssb_scs_khz'],pattern=v['ssb_pattern'],valid_blocks=v['valid_frames']) for v in attempts],
    validated_broadcast_blocks=r['analysis_coverage']['validated_broadcast_blocks'],preserved_packet_frames=r['analysis_coverage']['validated_packet_frames'],
    sequence=sequence,segmentation_selection_status=r['segmentation_selection_status'],
    failures=[dict(tool=e['stage'],code=e.get('error_code')) for e in ev if e.get('phase')=='end' and e.get('status')=='failed'])
dump(ROOT/'docs/testing/nr-harness-v16.json',summary);print(json.dumps(summary,ensure_ascii=False,indent=2),flush=True)
assert status['status']=='succeeded'
assert attempts,'Harness did not exercise NR decoder'
assert not set(sequence)&{'capture_iq','make_synthetic_capture','inspect_capabilities'}
assert r['analysis_coverage']['validated_packet_frames']==84
