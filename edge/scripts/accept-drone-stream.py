"""Real-recording narrowband replay into the standalone NDJSON DSP worker."""
import json,subprocess,time,sys
from pathlib import Path
import numpy as np
from spectrum_agent.common import ROOT
from spectrum_agent.drone_video import StreamingFrontend

raw=np.memmap(ROOT/'artifacts/task_9668c13363904f8092ca082554958600/capture_bae61ab9ed89459e.cf32',dtype='<c8',mode='r')
front=StreamingFrontend(200e6,5800e6,5803.5e6,20e6);pieces=[]
for a in range(0,len(raw),4000000):
    x,origin=front.process(raw[a:a+4000000]);pieces.append(x)
x,origin=front.process(np.empty(0,dtype=np.complex64),final=True);pieces.append(x)
narrow=np.concatenate(pieces);assert len(narrow)==10000000
repeat=int(sys.argv[1]) if len(sys.argv)>1 else 1
assert 1<=repeat<=20
results=[]
for block_ms in (10,20):
    args=[sys.executable,'-m','spectrum_agent.drone_stream','--sample-rate-hz','20000000','--input-center-hz','5803500000',
        '--target-center-hz','5803500000','--block-ms',str(block_ms),'--node-id','jetson-recording-replay','--gain-db','20']
    path=ROOT/'docs/testing'/f"drone-stream-{block_ms}ms{'-sustained' if repeat>1 else ''}-events.jsonl"
    # Write a prepared recording in large blocks. communicate(input=...) uses
    # 4KiB pipe writes and can benchmark the feeder instead of the DSP worker.
    prepared=narrow.tobytes();began=time.perf_counter()
    with path.open('wb') as log:
        child=subprocess.Popen(args,stdin=subprocess.PIPE,stdout=log,stderr=subprocess.PIPE,cwd=ROOT)
        try:
            for repetition in range(repeat):child.stdin.write(prepared)
            child.stdin.close();child.stdin=None
            unused,stderr=child.communicate(timeout=120)
        except BaseException:
            child.kill();child.wait();raise
        if child.returncode:raise RuntimeError(stderr.decode(errors='replace'))
    wall_s=time.perf_counter()-began
    events=[json.loads(line) for line in path.read_bytes().splitlines()];summary=next(v for v in events if v['type']=='summary')
    detections=[v for v in events if v['type']=='signature_detection']
    assert summary['matched_references']==len(detections) and len(detections)>180*repeat
    assert summary['canonical_samples']==7680000*repeat
    assert all(not e['protocol_confirmed'] and not e['power_calibrated'] for e in detections)
    results.append(dict(block_ms=block_ms,wall_s=wall_s,summary=summary,
        matching_reference_score_range=[min(e['signature_score'] for e in detections),max(e['signature_score'] for e in detections)],
        median_reference_interval_ms=float(np.median(np.diff([v['reference_time_s'] for v in detections]))*1000)))
result=dict(source_task_id='task_9668c13363904f8092ca082554958600',source_kind='real_recording_replay_not_live_RF',
    narrowband_input_sample_rate_hz=20e6,full_channel_bandwidth_hz=10e6,looped_replay_count=repeat,replayed_sample_time_s=.5*repeat,results=results,
    note='20MS/s continuous target channel generated once from real200MS/s recording,then fed to persistent standalone DSP. No model calls or new RF. Narrowband timings exclude original200MS/s channelization.')
destination='drone-stream-sustained-acceptance.json' if repeat>1 else 'drone-stream-acceptance.json'
(ROOT/'docs/testing'/destination).write_text(json.dumps(result,indent=2));print(json.dumps(result))
