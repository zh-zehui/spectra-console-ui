"""Compare new receiver against recorded subbands, no RF/no model invocation."""
import json
import os
import uuid
from pathlib import Path
from spectrum_agent.common import ROOT,Context,dump
from spectrum_agent.wifi_modern import verify
source='task_6642f2a7797a4dd7acdb81ec243c7009'
ctx=Context('wifi_modern_replay_'+uuid.uuid4().hex[:10]);rows=[]
for index in sorted((ROOT/'runs'/source/'index').glob('*.json')):
    rec=json.loads(index.read_text())
    if rec['kind']!='wifi_verification':continue
    prior=json.loads(Path(rec['path']).read_text())
    sub=json.loads((ROOT/'runs'/source/'index'/(prior['artifact_id']+'.json')).read_text())
    aid,path=ctx.artifact('saved_wifi_subband','cf32');os.link(sub['path'],path)
    mp=path.with_suffix('.json');meta=json.loads(Path(sub['metadata_path']).read_text());dump(mp,meta)
    ctx.register(aid,path,kind='subband',metadata_path=str(mp),origin_task_id=source)
    checked=verify(ctx,aid)
    out=json.loads(Path(ctx.resolve(checked['evidence_id'])['path']).read_text())
    old={f['frame_hex'] for f in prior['frames']};new={f['frame_hex'] for f in out['frames']}
    row=dict(source_verification=rec['artifact_id'],center_hz=meta['center_hz'],
        old_frames=prior['valid_frames'],new_frames=out['valid_frames'],
        lost_unique_mac_frames=len(old-new),new_unique_mac_frames=len(new-old),
        formats={fmt:sum(f['phy_format']==fmt for f in out['frames']) for fmt in ('legacy','HT','VHT')},
        positioned_frames=sum('subband_sample_start' in f for f in out['frames']),
        verification_stages=out['verification_stages'])
    rows.append(row);print(json.dumps(row),flush=True)
result=dict(task_id=ctx.task_id,source_task=source,new_rf_capture=False,
    test_kind='direct_decoder_comparison_not_harness_run',checks=rows)
dump(ROOT/'docs/testing/wifi-modern-saved-iq.json',result)
assert rows
