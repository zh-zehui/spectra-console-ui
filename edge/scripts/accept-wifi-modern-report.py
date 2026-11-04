"""Recorded-IQ diagnostic report; no model invocation and no RF operations."""
import json
import time
import uuid
from pathlib import Path
from spectrum_agent.common import ROOT,Context,dump
from spectrum_agent import continuation
from spectrum_agent.worker import execute

source='task_6642f2a7797a4dd7acdb81ec243c7009'
ctx=Context('wifi_modern_accept_'+uuid.uuid4().hex[:10])
request=dict(source='usrp',continue_from_task_id=source,
    prompt='Recorded-IQ decoder regression: retain all prior evidence and compare complementary GR-WiFi. Direct diagnostic, not a DeepSeek model decision.',
    constraints={},budgets=dict(max_candidates=20))
continuation.normalize_request(request);dump(ctx.run/'request.json',request)
continuation.attach(ctx,request)
start=time.perf_counter();results=[]
prior=[]
for p in (ctx.run/'index').glob('*.json'):
    rec=json.loads(p.read_text())
    if rec['kind']=='wifi_verification':prior.append(json.loads(Path(rec['path']).read_text()))
for v in prior:
    checked=execute(ctx.task_id,'verify_protocol',dict(artifact_id=v['artifact_id'],protocol='wifi_gr20_siso'))
    assert checked['status']=='succeeded',checked
    results.append(dict(source_subband_id=v['artifact_id'],verification_id=checked['result']['evidence_id'],
        old_frames=v['valid_frames'],new_frames=checked['result']['valid_frames']))
out=execute(ctx.task_id,'build_report',dict(stop_reason='other',
    stop_detail='Direct recorded-IQ receiver comparison, not a complete new protocol survey or a Harness model run. Prior evidence retained.'))
assert out['status']=='succeeded',out
dump(ctx.run/'outcome.json',dict(status='partial',elapsed_ms=(time.perf_counter()-start)*1000,
    note='Recorded-IQ direct tool acceptance, not a DeepSeek reasoning run; no new RF'))
result=dict(task_id=ctx.task_id,source_task=source,new_rf_capture=False,model_invoked=False,comparisons=results)
dump(ROOT/'docs/testing/wifi-modern-report-acceptance.json',result)
print(json.dumps(result))
