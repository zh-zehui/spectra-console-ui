"""Validate the discovered40MHz reference against all independent saved time."""
import json,time
from pathlib import Path
import numpy as np
from spectrum_agent.common import ROOT
from spectrum_agent.drone_video import _frontend,SignatureDetector,profile
task='task_962288f3229a48468017cdd4dca54aab';run=ROOT/'runs'/task
rec=next(json.loads(p.read_text()) for p in (run/'index').glob('*.json') if json.loads(p.read_text()).get('kind')=='capture')
m=json.loads(Path(rec['metadata_path']).read_text());raw=np.memmap(rec['path'],dtype='<c8',mode='r');fs=m['sample_rate_hz']
p=profile();p.update(sample_rate_hz=61440000,fft_size=2048,cyclic_prefix_samples=144,zc_length=1201,zc_root=504,nominal_channel_bandwidth_hz=40000000)
d=SignatureDetector(p,acquisition_threshold=.4);osc={};rows=[];began=time.perf_counter()
for start in range(0,len(raw),2000000):
    lo=max(0,start-200000);hi=min(len(raw),start+2200000)
    x=_frontend(raw[lo:hi],fs,5793.5e6-m['center_hz'],p['sample_rate_hz'],osc)
    for row in d.detect(x):
        t=lo/fs+row['reference_sample_start']/p['sample_rate_hz']
        if start/fs<=t<min(len(raw),start+2000000)/fs:rows.append(dict(time_s=t,signature_score=row['signature_score'],cp_correlation=row['cp_correlation'],carrier_offset_hz=row['carrier_offset_hz']))
out=dict(source_task_id=task,profile=p,matched_references=len(rows),held_out_matches=sum(r['time_s']>=.03 for r in rows),elapsed_s=time.perf_counter()-began,rows=rows)
(ROOT/'docs/testing/drone-second-source-full-probe.json').write_text(json.dumps(out,indent=2))
print(json.dumps({k:v for k,v in out.items() if k not in ('rows','profile')}));print(json.dumps(dict(first=rows[:3],last=rows[-3:])))
