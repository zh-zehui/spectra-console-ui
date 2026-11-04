"""Time cached single-precision frontend and convolution choices on Jetson."""
import json,time
from pathlib import Path
import numpy as np
from scipy import signal,fft
root=Path('/home/sribd/spectrum_agent');p=root/'artifacts/task_9668c13363904f8092ca082554958600/capture_bae61ab9ed89459e.cf32'
raw=np.memmap(p,dtype='<c8',mode='r');nraw=2000000
rotator=np.exp(-2j*np.pi*3503920/200e6*np.arange(nraw)).astype(np.complex64)
taps=signal.firwin(12501,1/625,window=('kaiser',5.0)).astype(np.float32)
N=512;L=293;cp=36;n=np.arange(L);bins=np.zeros(N,complex);bins[N//2-L//2:N//2+L//2+1]=np.exp(-1j*np.pi*123*n*(n+1)/L)
t=np.fft.ifft(np.fft.ifftshift(bins));h=np.r_[t[-cp:],t].astype(np.complex64);h/=np.sqrt(np.mean(abs(h)**2));kernel=np.conj(h[::-1]);eh=np.sum(abs(h)**2)
front=[];methods={name:[] for name in ('fftconvolve','oaconvolve','cached262144','cached163840','cached155520')};counts=[];hcaches={n:fft.fft(kernel,n) for n in (262144,163840,155520)}
for i in range(12):
    start=time.perf_counter();x=signal.resample_poly(np.asarray(raw[i*nraw:(i+1)*nraw])*rotator,48,625,window=taps).astype(np.complex64);front.append((time.perf_counter()-start)*1000)
    for name in methods:
        start=time.perf_counter()
        if name=='fftconvolve':cc=signal.fftconvolve(x,kernel,mode='valid')
        elif name=='oaconvolve':cc=signal.oaconvolve(x,kernel,mode='valid')
        else:
            nf=int(name[6:]);cc=fft.ifft(fft.fft(x,nf)*hcaches[nf])[len(h)-1:len(x)]
        power=np.r_[0,np.cumsum(abs(x).astype(float)**2)];energy=power[len(h):]-power[:-len(h)];rho=abs(cc)/np.sqrt(np.maximum(energy*eh,1e-30));peaks,_=signal.find_peaks(rho,height=.6,distance=1536)
        methods[name].append((time.perf_counter()-start)*1000)
    counts.append(len(peaks))
result=dict(frontend_ms={k:float(np.percentile(front[2:],v)) for k,v in [('p50',50),('p95',95)]},
    core_ms={name:{k:float(np.percentile(times[2:],v)) for k,v in [('p50',50),('p95',95)]} for name,times in methods.items()},counts=counts,
    note='Cached oscillator restarts phase each block; magnitude detection invariant. Real-time stdin state still requires continuous filter history.')
(root/'docs/testing/drone-frontend-benchmark.json').write_text(json.dumps(result,indent=2));print(json.dumps(result))
