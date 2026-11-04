"""Independent standard ZC math with publicly reported experimental PHY parameters."""
import json,time
from pathlib import Path
import numpy as np
from scipy.signal import resample_poly,fftconvolve,find_peaks

root=Path('/home/sribd/spectrum_agent');task='task_9668c13363904f8092ca082554958600'
rec=next(json.loads(p.read_text()) for p in (root/'runs'/task/'index').glob('*.json') if json.loads(p.read_text()).get('kind')=='capture')
m=json.loads(Path(rec['metadata_path']).read_text());iq=np.memmap(rec['path'],dtype='<c8',mode='r');fs=m['sample_rate_hz'];started=time.perf_counter();rows=[]
for center in (5800.5e6,5800e6,5799.5e6,5803.5e6,5793.5e6,5796.5e6,5798.5e6):
    a=0;b=round(.01*fs);x=np.asarray(iq[a:b])*np.exp(-2j*np.pi*(center-m['center_hz'])/fs*np.arange(b-a))
    x=resample_poly(x,96,625).astype(np.complex64)
    for profile,nfft,length,zcroot,cp in (('O4-research-601-r330',1024,601,330,72),('O4plus-research-1201-r232',2048,1201,232,144),('legacy-DroneID-r147',2048,601,147,144),('legacy-DroneID-r600',2048,601,600,144)):
        k=np.arange(length);zc=np.exp(-1j*np.pi*zcroot*k*(k+1)/length)
        bins=np.zeros(nfft,dtype=np.complex64);bins[nfft//2-length//2:nfft//2+length//2+1]=zc
        useful=np.fft.ifft(np.fft.ifftshift(bins));template=np.r_[useful[-cp:],useful].astype(np.complex64)
        cc=fftconvolve(x,np.conj(template[::-1]),mode='valid');power=np.r_[0,np.cumsum(np.abs(x).astype(np.float64)**2)]
        energy=power[len(template):]-power[:-len(template)];rho=np.abs(cc)/np.sqrt(np.maximum(energy*np.sum(np.abs(template)**2),1e-30))
        peaks,_=find_peaks(rho,height=.25,distance=round(.0001*30.72e6));order=peaks[np.argsort(rho[peaks])[-8:]] if len(peaks) else []
        row=dict(center_hz=center,profile=profile,max_rho=float(rho.max()),peak_count_above025=len(peaks),strongest=[dict(time_ms=float(i/30.72e3),rho=float(rho[i])) for i in order])
        rows.append(row);print(json.dumps(row),flush=True)
result=dict(task_id=task,source_capture=rec['artifact_id'],elapsed_s=time.perf_counter()-started,rows=rows,
    parameter_source='https://github.com/huangjiansheng321-cloud/dji-drone-ocusync-research',
    note='Experimental reference matching,not payload CRC,video decryption or proven Mavic4 model identity. No RF or original artifacts modified.')
(root/'docs/testing/drone-zc-initial-probe.json').write_text(json.dumps(result,indent=2))
print('elapsed_s='+str(result['elapsed_s']),flush=True)
