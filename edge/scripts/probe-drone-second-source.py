"""Inspect independent saved capture,without assuming previous channel width or root."""
import json,time
from pathlib import Path
import numpy as np
from scipy import signal
from spectrum_agent.common import ROOT
from spectrum_agent.drone_video import _frontend,SignatureDetector
from spectrum_agent.ofdm_structure import measure

task='task_962288f3229a48468017cdd4dca54aab';run=ROOT/'runs'/task
rec=next(json.loads(p.read_text()) for p in (run/'index').glob('*.json') if json.loads(p.read_text()).get('kind')=='capture')
m=json.loads(Path(rec['metadata_path']).read_text());raw=np.memmap(rec['path'],dtype='<c8',mode='r');fs=m['sample_rate_hz'];started=time.perf_counter()
a=round(.0002*fs);b=round(.0011*fs)
f,power=signal.welch(raw[a:b],fs,nperseg=8192,return_onesided=False);f=np.fft.fftshift(f)+m['center_hz'];power=np.fft.fftshift(power)
mask=(f>5760e6)&(f<5830e6);db=10*np.log10(np.maximum(power[mask],1e-30));active=f[mask][db>db.max()-10]
rows=[]
for center in (5793.5e6,5803.5e6,5800.5e6,5783.5e6):
    x=_frontend(raw[a:b],fs,center-m['center_hz'],61.44e6,{})
    found=measure(x,61.44e6,useful_symbols_s=(3.2e-6,12.8e-6,1/15000,1/30000,1/60000))
    rows.append(dict(center_hz=center,cp_candidates=found[:3]))
result=dict(task_id=task,capture_id=rec['artifact_id'],duration_s=m['duration_s'],
    early_burst_minus10db_band_hz=[float(active.min()),float(active.max())],cp_tests=rows,elapsed_s=time.perf_counter()-started)
(ROOT/'docs/testing/drone-second-source-probe.json').write_text(json.dumps(result,indent=2));print(json.dumps(result),flush=True)
