"""Direct existing-IQ comparison, immutable inherited evidence, no RF/LLM."""
import json
from pathlib import Path
import time
import uuid
from spectrum_agent.common import ROOT,Context,dump
from spectrum_agent import continuation
from spectrum_agent.worker import execute

source='task_6642f2a7797a4dd7acdb81ec243c7009'
ctx=Context('crafter_accept_'+uuid.uuid4().hex[:10])
request=dict(source='usrp',continue_from_task_id=source,
    prompt='Direct saved-IQ libcrafter acceptance: compare DSSS/CCK and HT20 A-MPDU reception while retaining prior evidence. No model decision or new capture.',
    constraints={},budgets=dict(max_candidates=24))
continuation.normalize_request(request);dump(ctx.run/'request.json',request);continuation.attach(ctx,request)
prior=[]
for p in (ctx.run/'index').glob('*.json'):
    rec=json.loads(p.read_text())
    if rec['kind']=='wifi_verification':prior.append(json.loads(Path(rec['path']).read_text()))
started=time.perf_counter();rows=[]
for v in prior:
    checked=execute(ctx.task_id,'verify_protocol',dict(artifact_id=v['artifact_id'],protocol='wifi_crafter_all'))
    row=dict(source_subband_id=v['artifact_id'],old_frames=v['valid_frames'],status=checked['status'])
    if checked['status']=='succeeded':
        out=checked['result'];rec=ctx.resolve(out['evidence_id']);full=json.loads(Path(rec['path']).read_text())
        old={f['frame_hex'] for f in v['frames']};new={f['frame_hex'] for f in full['frames']}
        meta=json.loads(Path(ctx.resolve(v['artifact_id'])['metadata_path']).read_text())
        row.update(verification_id=out['evidence_id'],center_hz=meta['center_hz'],
            new_frames=out['valid_frames'],formats={fmt:sum(f['phy_format']==fmt for f in full['frames']) for fmt in ('DSSS','CCK','HT20','legacy_ofdm')},
            aggregation_members=sum(f['ampdu_member'] is not None for f in full['frames']),
            new_unique_mac_bytes=len(new-old),lost_unique_mac_bytes=len(old-new),
            elapsed_s=out['elapsed_s'],verification_stages=out['verification_stages'])
    else:row['error']=checked.get('error') or checked.get('error_code')
    rows.append(row);print(json.dumps(row),flush=True)
out=execute(ctx.task_id,'build_report',dict(stop_reason='other',stop_detail='Direct libcrafter saved-IQ comparison, not a full new survey or live model-selected task. Prior results retained.'))
assert out['status']=='succeeded',out
dump(ctx.run/'outcome.json',dict(status='partial',elapsed_ms=(time.perf_counter()-started)*1000,
    note='Direct domain-tool comparison, no model invocation or RF'))
result=dict(task_id=ctx.task_id,source_task=source,new_rf_capture=False,model_invoked=False,
    elapsed_s=time.perf_counter()-started,checks=rows)
dump(ROOT/'docs/testing/libcrafter-saved-iq.json',result);print(json.dumps(dict(task_id=ctx.task_id,completed=True)),flush=True)
