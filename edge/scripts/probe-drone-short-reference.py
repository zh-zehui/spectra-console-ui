"""Short reference acquisition plus CFO/CP refinement feasibility and latency."""
import json,time
from pathlib import Path
import numpy as np
from scipy.signal import resample_poly,firwin,oaconvolve,find_peaks
root=Path('/home/sribd/spectrum_agent');p=root/'artifacts/task_9668c13363904f8092ca082554958600/capture_bae61ab9ed89459e.cf32';raw=np.memmap(p,dtype='<c8',mode='r')
nraw=2000000;rot=np.exp(-2j*np.pi*3500000/200e6*np.arange(nraw)).astype(np.complex64);taps=firwin(12501,1/625,window=('kaiser',5)).astype(np.float32)
N=512;L=293;n=np.arange(L);bins=np.zeros(N,complex);bins[N//2-L//2:N//2+L//2+1]=np.exp(-1j*np.pi*123*n*(n+1)/L);symbol=np.fft.ifft(np.fft.ifftshift(bins)).astype(np.complex64)
rows=[];times=[]
for start_ms in range(0,500,10):
 x=resample_poly(np.asarray(raw[start_ms//10*nraw:(start_ms//10+1)*nraw])*rot,48,625,window=taps).astype(np.complex64)
 for length in (128,192,256):
  h=symbol[:length];start=time.perf_counter();cc=oaconvolve(x,np.conj(h[::-1]),mode='valid');s=np.r_[0,np.cumsum(abs(x).astype(float)**2)];rho=abs(cc)/np.sqrt(np.maximum((s[length:]-s[:-length])*np.sum(abs(h)**2),1e-30));pks,_=find_peaks(rho,height=.65,distance=1536)
  times.append(dict(length=length,core_ms=(time.perf_counter()-start)*1000));rows.extend(dict(length=length,time_ms=float(start_ms+i/15.36e3),rho=float(rho[i])) for i in pks)
result=dict(counts={str(n):sum(r['length']==n for r in rows) for n in (128,192,256)},timing={str(n):dict(p50=float(np.percentile([r['core_ms'] for r in times[3:] if r['length']==n],50)),p95=float(np.percentile([r['core_ms'] for r in times[3:] if r['length']==n],95))) for n in (128,192,256)},rows=rows)
(root/'docs/testing/drone-short-reference-probe.json').write_text(json.dumps(result,indent=2));print(json.dumps({k:v for k,v in result.items() if k!='rows'}));print(json.dumps(rows[:9]))
