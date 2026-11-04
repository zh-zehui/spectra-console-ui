"""Read selected existing-IQ windows; no RF and no original-task mutation."""
import json,time
from pathlib import Path
import numpy as np
from scipy.signal import resample_poly
from spectrum_agent.ofdm_structure import measure

root=Path('/home/sribd/spectrum_agent');task='task_9668c13363904f8092ca082554958600'
rec=next(json.loads(p.read_text()) for p in (root/'runs'/task/'index').glob('*.json') if json.loads(p.read_text()).get('kind')=='capture')
m=json.loads(Path(rec['metadata_path']).read_text());iq=np.memmap(rec['path'],dtype='<c8',mode='r');fs=m['sample_rate_hz'];started=time.perf_counter()
rows=[]
for center in (5800e6,5803.5e6,5793.5e6):
    for start_ms in (.2,2.6,5.1,7.7,10.3,15.3,25.4,100.3,200.3,300.3,400.3,495.3):
        a=round(start_ms/1000*fs);b=min(len(iq),a+round(.0009*fs));x=np.asarray(iq[a:b])
        x=x*np.exp(-2j*np.pi*(center-m['center_hz'])/fs*np.arange(len(x)))
        x=resample_poly(x,48,625).astype(np.complex64)
        found=measure(x,15.36e6,useful_symbols_s=(3.2e-6,12.8e-6,1/15000,1/30000,1/60000))
        rows.append(dict(center_hz=center,start_ms=start_ms,duration_ms=(b-a)/fs*1e3,cp_candidates=found[:3]))
result=dict(task_id=task,source_capture=rec['artifact_id'],elapsed_s=time.perf_counter()-started,rows=rows,
    note='Diagnostic CP matches only; selected subbands/windows,not full coverage or brand/protocol confirmation. No RF or original artifact writes.')
path=root/'docs/testing/drone-ofdm-initial-probe.json';path.write_text(json.dumps(result,indent=2))
for row in rows:
    if row['cp_candidates']:print(json.dumps(row),flush=True)
print('elapsed_s='+str(result['elapsed_s']),flush=True)
