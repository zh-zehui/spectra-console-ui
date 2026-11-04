"""2.4GHz saved-IQ compatibility regression across existing protocol backends."""
import json,time,uuid
from collections import Counter
from spectrum_agent.common import ROOT,Context,dump
from spectrum_agent.service import normalize
from spectrum_agent.task_intent import resolve
from spectrum_agent.protocol_batch import plan,run,wifi_centers
from spectrum_agent.knowledge import query
from spectrum_agent.worker import report
source='task_6642f2a7797a4dd7acdb81ec243c7009';ctx=Context('task_batch24_'+uuid.uuid4().hex[:12]);started=time.perf_counter()
prompt=source+'，复用已有2.4GHz IQ，全时间覆盖Wi-Fi、BLE和IEEE802.15.4候选，不重新采集。'
dump(ctx.run/'request.json',normalize(dict(prompt=prompt,source='usrp',budgets=dict(coverage_mode='complete',deadline_s=1800))))
intent=resolve(ctx,evidence_quotes=[source],reason='Offline multi-protocol compatibility regression over complete saved2.4GHz IQ.',source_task_id=source,acquisition_requested=False)
request=json.loads((ctx.run/'request.json').read_text());c=request['constraints'];low=c['center_hz']-c['span_hz']/2;high=c['center_hz']+c['span_hz']/2
prior=query(ctx,c['center_hz'],c['span_hz']);checks=[]
for center in wifi_centers(low,high):
    for profile in ('wifi_legacy_ofdm','wifi_gr20_siso','wifi_crafter_all'):
        checks.append(dict(profile=profile,center_hz=center,bandwidth_hz=18e6,output_rate_hz=20e6,reason='Technical2.4GHz channel; compare complementary installed Wi-Fi backends'))
for channel,center in ((37,2402e6),(38,2426e6),(39,2480e6)):
    if low<=center-1e6 and center+1e6<=high:checks.append(dict(profile='ble_1m_advertising',center_hz=center,bandwidth_hz=2e6,output_rate_hz=4e6,channel=channel,reason='In-band primary BLE advertising candidate'))
for channel in range(11,27):
    center=(2405+5*(channel-11))*1e6
    if low<=center-1e6 and center+1e6<=high:checks.append(dict(profile='ieee802154_oqpsk',center_hz=center,bandwidth_hz=2e6,output_rate_hz=4e6,channel=channel,reason='In-band IEEE802.15.4 O-QPSK candidate; no identity inference'))
p=plan(ctx,intent['recording_capture_id'],checks,'Deterministic2.4GHz tool-compatibility regression; explicit technical channels,not a fixed model routing policy.',[prior['prior_snapshot_id']])
while not p['complete']:
    with ctx.span('run_protocol_batch'):p=run(ctx,p['plan_id'],max_units=16)
    print(json.dumps(p),flush=True)
    if p['failed_units']:raise RuntimeError(json.dumps(p))
report(ctx,stop_reason='other',stop_detail='Completed2.4GHz fixed-grid multi-backend tool compatibility regression; generic unknown-activity analysis not exhausted.')
r=json.loads((ctx.run/'report.json').read_text());counts=Counter(link['protocol'] for link in r['frame_associations'])
result=dict(task_id=ctx.task_id,source_task_id=source,elapsed_s=time.perf_counter()-started,protocol_coverage=r['protocol_coverage'],
    valid_frame_observations=dict(counts),new_rf_capture=False,
    note='Fixed-grid integration test,not a model-authored exhaustive identification. Multiple PHY backends retained and packet evidence deduplicated by bytes/timing where possible.')
dump(ROOT/'docs/testing/protocol-batch-24-acceptance.json',result)
dump(ctx.run/'outcome.json',dict(status='partial',elapsed_ms=result['elapsed_s']*1000,task_id=ctx.task_id))
print('RESULT '+json.dumps(result),flush=True)
assert counts['IEEE802.11']>=47 and counts['Bluetooth LE']>=71,result
