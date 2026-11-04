"""Bounded real recorded-IQ LTE hypotheses; never starts RF or guesses a decoder result."""
import argparse,json,time,uuid
from pathlib import Path
from spectrum_agent.common import Context,ROOT,dump
from spectrum_agent.service import normalize
from spectrum_agent.continuation import attach
from spectrum_agent import dsp,lte
parser=argparse.ArgumentParser(description=__doc__)
parser.add_argument('--task-id')
parser.add_argument('--fine-raster',action='store_true',help='Test2359..2380MHz in100kHz steps; limited frequency hypotheses, not full-band proof')
args=parser.parse_args()
ctx=Context(args.task_id or 'lte_real_v15_'+uuid.uuid4().hex[:8])
if not (ctx.run/'continuation.json').exists():
    req=normalize(dict(prompt='Recorded-IQ LTE downlink tests, no new acquisition',continue_from_task_id='task_37632c56c97545fea55cfb08683aa7f1'))
    dump(ctx.run/'request.json',req);attach(ctx,req)
req=json.loads((ctx.run/'request.json').read_text())
existing={}
for p in (ctx.run/'index').glob('*.json'):
    rec=json.loads(p.read_text())
    if rec['kind']=='lte_verification':
        value=json.loads(Path(rec['path']).read_text());existing[value['center_hz']]=dict(value,evidence_id=rec['artifact_id'])
rows=[]
print(json.dumps(dict(task_id=ctx.task_id,status='starting')),flush=True)
centers=[2359e6+i*100000 for i in range(211)] if args.fine_raster else [2360e6,2365e6,2369e6,2370e6,2375e6,2380e6]
for center in centers:
    elapsed=None;reused=center in existing
    if reused:result=existing[center]
    else:
        started=time.perf_counter()
        with ctx.span('lte_subband_extract'):
            sub=dsp.extract(ctx,req['recording_capture_id'],center,1.4e6,output_rate_hz=1920000,margin_s=.001)
        with ctx.span('verify_lte_broadcast'):
            result=lte.verify(ctx,sub['artifact_id'],'tdd')
        elapsed=time.perf_counter()-started
    rows.append(dict(result,elapsed_s=elapsed,reused_existing_artifact=reused))
    summary=dict(task_id=ctx.task_id,new_rf_capture=False,centers_tested=[r['center_hz'] for r in rows],results=rows,
        valid_broadcast_blocks=sum(r['valid_frames'] for r in rows),
        note='Selected center hypotheses only, not exhaustive raster coverage. Missing reused timing is unknown, not zero.')
    dump(ROOT/'docs/testing/lte-real-v15.json',summary)
    print(json.dumps(dict(center_hz=center,elapsed_s=elapsed,sync_candidates=result['synchronization_candidate_count'],valid_frames=result['valid_frames'])),flush=True)
