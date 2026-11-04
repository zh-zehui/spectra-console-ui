"""Blind quadratic-phase reference investigation; standard independent DSP math."""
import json,time
from pathlib import Path
from collections import Counter
import numpy as np
from scipy.signal import resample_poly,find_peaks,welch

root=Path('/home/sribd/spectrum_agent');task='task_9668c13363904f8092ca082554958600'
rec=next(json.loads(p.read_text()) for p in (root/'runs'/task/'index').glob('*.json') if json.loads(p.read_text()).get('kind')=='capture')
m=json.loads(Path(rec['metadata_path']).read_text());iq=np.memmap(rec['path'],dtype='<c8',mode='r');fs=m['sample_rate_hz'];started=time.perf_counter();rows=[]
center=5800.5e6;count=round(.03*fs)
x=resample_poly(np.asarray(iq[:count])*np.exp(-2j*np.pi*(center-m['center_hz'])/fs*np.arange(count)),96,625).astype(np.complex64)
nfft=1024;cp=72
v=x[:-nfft]*np.conj(x[nfft:]);a=np.abs(x[:-nfft]).astype(np.float64)**2;b=np.abs(x[nfft:]).astype(np.float64)**2
def rolling(z,n):s=np.r_[0,np.cumsum(z)];return s[n:]-s[:-n]
num=rolling(v,cp);den=np.maximum(rolling(a,cp)*rolling(b,cp),1e-30);metric=np.abs(num)**2/den
peaks,_=find_peaks(metric,height=.5,distance=round(nfft*.9))
for p in peaks:
    if p+cp+nfft>len(x):continue
    cfo=-np.angle(num[p])*30.72e6/(2*np.pi*nfft)
    useful=x[p+cp:p+cp+nfft]*np.exp(-2j*np.pi*cfo/30.72e6*np.arange(nfft));Y=np.fft.fftshift(np.fft.fft(useful))
    for length in (293,301,601):
        for offset in range(-6,7):
            first=nfft//2-length//2+offset;y=Y[first:first+length]
            d=y[2:]*y[:-2]*np.conj(y[1:-1])**2
            weights=np.minimum.reduce([np.abs(y[2:]),np.abs(y[:-2]),np.abs(y[1:-1])]);weights/=max(float(np.percentile(weights,80)),1e-30);weights=np.minimum(weights,1)
            z=np.sum(d/np.maximum(np.abs(d),1e-30)*weights)/max(np.sum(weights),1e-30);score=float(abs(z));u=round(-np.angle(z)*length/(2*np.pi))%length
            if score>.35:rows.append(dict(time_ms=float(p/30.72e3),cp_score=float(metric[p]),length=length,root=u,offset=offset,quadratic_phase_score=score,cfo_hz=float(cfo)))
result=dict(task_id=task,center_hz=center,cp_peaks=len(peaks),elapsed_s=time.perf_counter()-started,quadratic_references=sorted(rows,key=lambda r:r['quadratic_phase_score'],reverse=True))
(root/'docs/testing/drone-root-initial-probe.json').write_text(json.dumps(result,indent=2))
print(json.dumps(dict(cp_peaks=len(peaks),elapsed_s=result['elapsed_s'],reference_count=len(rows),best=result['quadratic_references'][:12])))
# Compare early active burst and its adjacent gap without touching the source.
freq,power=welch(iq[round(.0002*fs):round(.0011*fs)],fs,nperseg=8192,return_onesided=False)
freq=np.fft.fftshift(freq)+m['center_hz'];power=np.fft.fftshift(power)
sel=(freq>5770e6)&(freq<5825e6);f=freq[sel];db=10*np.log10(np.maximum(power[sel],1e-30));active=f[db>db.max()-10]
print(json.dumps(dict(early_burst_minus10db_low_mhz=float(active.min()/1e6),early_burst_minus10db_high_mhz=float(active.max()/1e6),note='Threshold width of an observed mixture,not isolated drone occupied bandwidth')))
