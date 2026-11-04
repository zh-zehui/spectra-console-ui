"""Bounded direct decoder regression on immutable saved subbands; no RF or LLM."""
import json
import os
import uuid
from pathlib import Path
from spectrum_agent.common import ROOT,Context,dump
from spectrum_agent import protocols

source='task_6642f2a7797a4dd7acdb81ec243c7009'
ctx=Context('ble_extended_replay_'+uuid.uuid4().hex[:10])
rows=[]
for index in sorted((ROOT/'runs'/source/'index').glob('*.json')):
    rec=json.loads(index.read_text())
    if rec['kind']!='ble_verification':continue
    prior=json.loads(Path(rec['path']).read_text())
    sub=json.loads((ROOT/'runs'/source/'index'/(prior['artifact_id']+'.json')).read_text())
    aid,path=ctx.artifact('saved_ble_subband','cf32')
    os.link(sub['path'],path)
    meta=json.loads(Path(sub['metadata_path']).read_text())
    mp=path.with_suffix('.json');dump(mp,meta)
    ctx.register(aid,path,kind='subband',metadata_path=str(mp),origin_task_id=source)
    out=protocols.decode_ble(ctx,aid,prior['channel'])
    out=json.loads(Path(ctx.resolve(out['evidence_id'])['path']).read_text())
    old={f['pdu_hex'] for f in prior['frames']}
    new={f['pdu_hex'] for f in out['frames']}
    rows.append(dict(source_verification=rec['artifact_id'],channel=prior['channel'],
        old_frames=prior['valid_frames'],new_frames=out['valid_frames'],
        lost_unique_pdus=len(old-new),extended_frames=sum(f['pdu_type']==7 for f in out['frames']),
        verification_stages=out['verification_stages'],analysis_coverage=out['analysis_coverage']))
    if len(rows)>=6:break
result=dict(task_id=ctx.task_id,source_task=source,new_rf_capture=False,
    test_kind='direct_decoder_regression_not_harness_run',checks=rows)
dump(ROOT/'docs'/'testing'/'ble-extended-saved-iq.json',result)
print(json.dumps(result))
assert rows and not any(r['lost_unique_pdus'] for r in rows)
