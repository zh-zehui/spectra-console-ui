"""Read-only discovery on the first30ms of a separate saved recording."""
import json,time
from pathlib import Path
import numpy as np
from scipy import signal,fft
from spectrum_agent.common import ROOT
from spectrum_agent.drone_video import _frontend

task='task_e7bb08a92f9440da91c043ab85f5b1b6'
rec=next(json.loads(p.read_text()) for p in (ROOT/'runs'/task/'index').glob('*.json') if json.loads(p.read_text()).get('kind')=='capture')
m=json.loads(Path(rec['metadata_path']).read_text());raw=np.memmap(rec['path'],dtype='<c8',mode='r');fs=m['sample_rate_hz'];started=time.perf_counter()
center=5798.5e6;rate=61.44e6;nfft=2048;cp=144
x=_frontend(raw[:round(.03*fs)],fs,center-m['center_hz'],rate,{})
def rolling(v,n):
 s=np.r_[0,np.cumsum(v)];return s[n:]-s[:-n]
a=x[:-nfft];b=x[nfft:];num=rolling(a*np.conj(b),cp)
metric=abs(num)**2/np.maximum(rolling(abs(a).astype(float)**2,cp)*rolling(abs(b).astype(float)**2,cp),1e-30)
peaks,_=signal.find_peaks(metric,height=.6,distance=round(.9*nfft));rows=[]
for pos in peaks:
 cfo=-np.angle(num[pos])*rate/(2*np.pi*nfft)
 useful=x[pos+cp:pos+cp+nfft]*np.exp(-2j*np.pi*cfo/rate*np.arange(nfft));y=fft.fftshift(fft.fft(useful))
 for active in (293,601,1201):
  act=y[nfft//2-active//2:nfft//2+active//2+1]
  d=act[2:]*act[:-2]*np.conj(act[1:-1])**2;z=np.mean(d/np.maximum(abs(d),1e-30))
  if abs(z)>.65:rows.append(dict(coherence=float(abs(z)),time_ms=float(pos/rate*1000),active_length=active,quadratic_cycles=float(-np.angle(z)/(2*np.pi)),spectrum=y))
results=[]
for row in sorted(rows,key=lambda r:r['coherence'],reverse=True)[:5]:
 candidates=[]
 for active in (293,601,1201):
  y=row['spectrum'][nfft//2-active//2:nfft//2+active//2+1];n=np.arange(active)
  for length in (293,301,601,1201,2399,2401):
   estimate=round(row['quadratic_cycles']*length)%length
   for u in sorted({(estimate+d)%length for d in range(-5,6)}-{0}):
    zc=np.exp(-1j*np.pi*u*n*(n+1)/length);response=fft.ifft(y*np.conj(zc),8192)*8192
    score=float(max(abs(response))/max(np.sqrt(np.sum(abs(y)**2)*len(y)),1e-30))
    candidates.append(dict(active_length=active,zc_length=length,root=u,score=score))
 results.append(dict(**{k:v for k,v in row.items() if k!='spectrum'},best=sorted(candidates,key=lambda r:r['score'],reverse=True)[:5]))
out=dict(task_id=task,center_hz=center,rate=rate,cp_peaks=len(peaks),quadratic_reference_candidates=len(rows),results=results,elapsed_s=time.perf_counter()-started)
(ROOT/'docs/testing/drone-third-source-root-probe.json').write_text(json.dumps(out,indent=2));print(json.dumps(out),flush=True)
