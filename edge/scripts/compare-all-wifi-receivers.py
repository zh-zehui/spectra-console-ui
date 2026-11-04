"""Union saved receiver evidence; no fresh IQ decode, RF or model invocation."""
import json,time,uuid
from collections import Counter
from pathlib import Path
from spectrum_agent.common import ROOT,Context,dump
from spectrum_agent import continuation
from spectrum_agent.worker import report
from spectrum_agent.catalog import associate

old_task='task_6642f2a7797a4dd7acdb81ec243c7009'
modern_task='wifi_modern_accept_0a4539c7a4'
crafter_task='crafter_accept_27a5431df3'
def read_report(task):return json.loads((ROOT/'runs'/task/'report.json').read_text())
old=read_report(old_task);modern=read_report(modern_task);crafter=read_report(crafter_task)
ctx=Context('wifi_union_'+uuid.uuid4().hex[:10])
request=dict(source='usrp',continue_from_task_id=crafter_task,
    prompt='Saved evidence union comparison of legacy,GR-WiFi and libcrafter. No new IQ decoding,RF or model calls.',
    constraints={},budgets=dict(max_candidates=20))
continuation.normalize_request(request);dump(ctx.run/'request.json',request);continuation.attach(ctx,request)
for value in modern['verification_attempts']:
    if value.get('verification_profile')!='wifi_gr20_siso':continue
    value=dict(value);source_id=value.pop('evidence_id')
    value['imported_verification_origin']=dict(task_id=modern_task,evidence_id=source_id,
        note='Immutable saved result copy; no receiver executed in this comparison')
    ctx.json_artifact('wifi_verification',value)
report(ctx,stop_reason='other',stop_detail='Three-receiver evidence union,not a new spectrum survey or model decision. Existing reports remain unchanged.')
dump(ctx.run/'outcome.json',dict(status='partial',elapsed_ms=0,
    note='Saved evidence union,no fresh decode,RF or LLM'))
union=read_report(ctx.task_id)

def phy(frame):return frame.get('phy_format','legacy_ofdm')
def frame_type(frame):
    fc=int.from_bytes(bytes.fromhex(frame['frame_hex'])[:2],'little');kind=(fc>>2)&3;sub=(fc>>4)&15
    return {0:{8:'Beacon'},1:{8:'Block_ACK_request',9:'Block_ACK',11:'RTS',12:'CTS',13:'ACK'},
        2:{0:'Data',8:'QoS_Data'}}.get(kind,{}).get(sub,f'type{kind}_subtype{sub}')
def wifi_verifications(r):return [v for v in r['verified_protocols'] if v['protocol']=='IEEE802.11']
def summarize(r):
    by_id={v['evidence_id']:v for v in wifi_verifications(r)}
    rows=[(link,by_id[link['evidence_id']]['frames'][link['frame_index']])
        for link in r['frame_associations'] if link['protocol']=='IEEE802.11']
    return dict(merged_frame_records=len(rows),phy_counts=dict(Counter(phy(f) for _,f in rows)),
        frame_types=dict(Counter(frame_type(f) for _,f in rows)),
        distinct_mac_byte_strings=len({f['frame_hex'] for _,f in rows})),rows
old_summary,old_rows=summarize(old)
before_summary,before_rows=summarize(modern)
after_summary,after_rows=summarize(union)
old_ids={v['evidence_id'] for v in wifi_verifications(old)}
old_retained=sum(any(ref['evidence_id'] in old_ids for ref in
    [dict(evidence_id=link['evidence_id'])]+link.get('duplicate_evidence',[])) for link,_ in after_rows)
new_records=[(link,f) for link,f in after_rows if not any(ref['evidence_id'] in old_ids for ref in
    [dict(evidence_id=link['evidence_id'])]+link.get('duplicate_evidence',[]))]
raw={}
for label,values in [('legacy',wifi_verifications(old)),
    ('gr_wifi',[v for v in wifi_verifications(modern) if v.get('verification_profile')=='wifi_gr20_siso']),
    ('libcrafter',[v for v in wifi_verifications(crafter) if str(v.get('verification_profile','')).startswith('wifi_crafter_')])]:
    fs=[f for v in values for f in v['frames']]
    raw[label]=dict(raw_records=len(fs),phy_counts=dict(Counter(phy(f) for f in fs)))
result=dict(task_id=ctx.task_id,source_tasks=[old_task,modern_task,crafter_task],
    same_capture_id=old['captures'][0]['artifact_id'],new_rf_capture=False,new_decode=False,model_invoked=False,
    original_legacy=old_summary,pre_libcrafter_union=before_summary,three_receiver_union=after_summary,
    original_legacy_records_retained=old_retained,raw_receivers=raw,
    additions_relative_to_original=dict(merged_frame_records=len(new_records),
        phy_counts=dict(Counter(phy(f) for _,f in new_records)),
        frame_types=dict(Counter(frame_type(f) for _,f in new_records))),
    merge_policy='Existing catalog: identical MAC bytes,overlapping frequency windows and source-time uncertainty. No transmitter identity or calibrated physical-packet completeness claim.')
dump(ROOT/'docs/testing/wifi-three-receiver-comparison.json',result)
print(json.dumps(result,indent=2))
