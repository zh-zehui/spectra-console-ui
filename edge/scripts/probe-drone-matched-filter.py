"""Validate a learned reference on held-out recording time and time the DSP core."""
import json,time
from pathlib import Path
import numpy as np
from scipy.signal import resample_poly,fftconvolve,find_peaks

root=Path('/home/sribd/spectrum_agent');task='task_9668c13363904f8092ca082554958600'
rec=next(json.loads(p.read_text()) for p in (root/'runs'/task/'index').glob('*.json') if json.loads(p.read_text()).get('kind')=='capture')
m=json.loads(Path(rec['metadata_path']).read_text());iq=np.memmap(rec['path'],dtype='<c8',mode='r');fs=m['sample_rate_hz'];center=5803.5e6;rate=15.36e6
N=512;L=293;u=123;cp=36;n=np.arange(L);z=np.exp(-1j*np.pi*u*n*(n+1)/L);bins=np.zeros(N,complex);bins[N//2-L//2:N//2+L//2+1]=z
t=np.fft.ifft(np.fft.ifftshift(bins));h=np.r_[t[-cp:],t].astype(np.complex64);h/=np.sqrt(np.mean(abs(h)**2));eh=np.sum(abs(h)**2)
def detect(x):
    cc=fftconvolve(x,np.conj(h[::-1]),mode='valid');power=np.r_[0,np.cumsum(abs(x).astype(float)**2)];energy=power[len(h):]-power[:-len(h)]
    rho=np.abs(cc)/np.sqrt(np.maximum(energy*eh,1e-30));p,_=find_peaks(rho,height=.60,distance=round(.0001*rate))
    return p,rho
times=[];rows=[]
for start_ms in range(0,500,10):
    a=round(start_ms/1000*fs);b=a+round(.01*fs);start=time.perf_counter()
    x=resample_poly(np.asarray(iq[a:b])*np.exp(-2j*np.pi*(center-m['center_hz'])/fs*np.arange(b-a)),48,625).astype(np.complex64)
    front_ms=(time.perf_counter()-start)*1000;start=time.perf_counter();p,rho=detect(x);core_ms=(time.perf_counter()-start)*1000
    times.append(dict(frontend_ms=front_ms,core_ms=core_ms));rows.extend(dict(time_ms=float(start_ms+i/rate*1e3),rho=float(rho[i])) for i in p)
result=dict(task_id=task,signature=dict(zc_length=L,root=u,subcarrier_spacing_hz=30000,center_hz=center),detected_references=len(rows),
    held_out_reference_count=sum(r['time_ms']>=30 for r in rows),timing_ms={k:dict(p50=float(np.percentile([r[k] for r in times],50)),p95=float(np.percentile([r[k] for r in times],95)),p99=float(np.percentile([r[k] for r in times],99))) for k in times[0]},
    references=rows,training_interval_ms=[0,30],note='All50 windows measured. Warm Python diagnostic frontend includes exp+polyphase allocation; core excludes200MS/s ingest. No protocol CRC or video decoding.')
(root/'docs/testing/drone-matched-filter-heldout.json').write_text(json.dumps(result,indent=2))
print(json.dumps({k:v for k,v in result.items() if k!='references'}));print(json.dumps(dict(first=rows[:5],last=rows[-5:])))
