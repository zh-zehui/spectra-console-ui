"""Fit standard quadratic-phase references to a measured symbol,not a brand decoder."""
import json,time
from pathlib import Path
import numpy as np
from scipy.signal import resample_poly,find_peaks
root=Path('/home/sribd/spectrum_agent');task='task_9668c13363904f8092ca082554958600'
rec=next(json.loads(p.read_text()) for p in (root/'runs'/task/'index').glob('*.json') if json.loads(p.read_text()).get('kind')=='capture')
m=json.loads(Path(rec['metadata_path']).read_text());iq=np.memmap(rec['path'],dtype='<c8',mode='r');fs=m['sample_rate_hz'];center=5803.5e6
count=round(.03*fs);x=resample_poly(np.asarray(iq[:count])*np.exp(-2j*np.pi*(center-m['center_hz'])/fs*np.arange(count)),96,625).astype(np.complex64)
nfft=1024;cp=72
def rolling(v,n):s=np.r_[0,np.cumsum(v)];return s[n:]-s[:-n]
v=x[:-nfft]*np.conj(x[nfft:]);num=rolling(v,cp);metric=np.abs(num)**2/np.maximum(rolling(np.abs(x[:-nfft]).astype(float)**2,cp)*rolling(np.abs(x[nfft:]).astype(float)**2,cp),1e-30)
peaks,_=find_peaks(metric,height=.75,distance=round(nfft*.9));rows=[]
for p in peaks:
    cfo=-np.angle(num[p])*30.72e6/(2*np.pi*nfft)
    y=np.fft.fftshift(np.fft.fft(x[p+cp:p+cp+nfft]*np.exp(-2j*np.pi*cfo/30.72e6*np.arange(nfft))))
    active=y[nfft//2-146:nfft//2+147]
    d=active[2:]*active[:-2]*np.conj(active[1:-1])**2;z=np.mean(d/np.maximum(abs(d),1e-30))
    if abs(z)>.75:rows.append((float(abs(z)),p,cfo,y,z))
rows=sorted(rows,key=lambda r:r[0],reverse=True);results=[]
for score,p,cfo,y,z in rows[:4]:
    candidates=[]
    for active_length in (293,301):
        observed=y[nfft//2-active_length//2:nfft//2+active_length//2+1];n=np.arange(active_length)
        for length in (293,301,601,1201):
            for u in range(1,length):
                ref=np.exp(-1j*np.pi*u*n*(n+1)/length)
                channel=np.fft.ifft(observed*np.conj(ref),4096)*4096
                rho=float(np.max(np.abs(channel))/max(np.sqrt(np.sum(abs(observed)**2)*active_length),1e-30))
                candidates.append(dict(active_length=active_length,zc_length=length,root=u,rho=rho))
    results.append(dict(time_ms=p/30.72e3,cfo_hz=float(cfo),quadratic_score=score,quadratic_phase_cycles=float(-np.angle(z)/(2*np.pi)),best=sorted(candidates,key=lambda c:c['rho'],reverse=True)[:5]))
result=dict(task_id=task,center_hz=center,reference_symbols=len(rows),results=results)
(root/'docs/testing/drone-root-fit-probe.json').write_text(json.dumps(result,indent=2))
print(json.dumps(result),flush=True)
