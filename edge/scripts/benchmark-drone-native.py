"""Check native frontend against SciPy,then measure actual target10ms windows."""
import json,time
from pathlib import Path
import numpy as np
from scipy import signal
from spectrum_agent.drone_video import _resample,_frontend,SignatureDetector,ROOT
rng=np.random.default_rng(823);equivalence=[]
for up,down in ((48,625),(96,125),(624,625),(2,3)):
    x=(rng.normal(size=10003)+1j*rng.normal(size=10003)).astype(np.complex64)
    taps=signal.firwin(20*max(up,down)+1,1/max(up,down),window=('kaiser',5.)).astype(np.float32)
    expected=signal.resample_poly(x,up,down,window=taps).astype(np.complex64);actual=_resample(x,up,down)
    error=float(np.max(abs(expected-actual)));assert actual.shape==expected.shape and error<2e-5,(up,down,error)
    equivalence.append(dict(up=up,down=down,max_absolute_error=error))
raw=np.memmap(ROOT/'artifacts/task_9668c13363904f8092ca082554958600/capture_bae61ab9ed89459e.cf32',dtype='<c8',mode='r')
osc={};detector=SignatureDetector();times=[];counts=[]
for i in range(50):
    start=time.perf_counter();x=_frontend(raw[i*2000000:(i+1)*2000000],200e6,3500000,15.36e6,osc);front=(time.perf_counter()-start)*1000
    start=time.perf_counter();refs=detector.detect(x);core=(time.perf_counter()-start)*1000
    times.append(dict(frontend_ms=front,core_ms=core,total_ms=front+core));counts.append(len(refs))
result=dict(equivalence=equivalence,matched_references=sum(counts),input_window_ms=10,input_sample_rate_hz=200e6,
    timing_ms={name:{q:float(np.percentile([r[name] for r in times[4:]],v)) for q,v in [('p50',50),('p95',95),('p99',99)]} for name in times[0]},
    excluded_warmup_windows=4,note='Measured200MS/s mixed-recording frontend+signature core; excludes radio/network/queue. Not a live USRP reception test.')
(ROOT/'docs/testing/drone-native-latency.json').write_text(json.dumps(result,indent=2));print(json.dumps(result))
