"""Recorded-IQ n40 SSB raster replay. No RF acquisition, no arbitrary center guesses."""
import json,time,uuid
from pathlib import Path
from spectrum_agent.common import Context,ROOT,dump
from spectrum_agent.service import normalize
from spectrum_agent.continuation import attach
from spectrum_agent import dsp,nr
ctx=Context('nr_real_v16_'+uuid.uuid4().hex[:8])
request=normalize(dict(prompt='Recorded-IQ NR SSB n40 raster verification; no new acquisition',continue_from_task_id='task_f4deea19200a4da28678ae5e8756465c'))
dump(ctx.run/'request.json',request);manifest=attach(ctx,request)
capture=ctx.resolve(request['recording_capture_id']);m=json.loads(Path(capture['metadata_path']).read_text())
raster=nr.n40_raster(m['center_hz']-m['requested_span_hz']/2,m['center_hz']+m['requested_span_hz']/2)
results=[];print(json.dumps(dict(task_id=ctx.task_id,centers=len(raster),status='starting')),flush=True)
for i,entry in enumerate(raster):
    started=time.perf_counter()
    with ctx.span('nr_subband_extract'):
        sub=dsp.extract(ctx,request['recording_capture_id'],entry['center_hz'],8800000,output_rate_hz=15360000,margin_s=.001)
    with ctx.span('verify_nr_broadcast'):
        out=nr.verify(ctx,sub['artifact_id'],30,'C','tdd')
    results.append(dict(out,gscn=entry['gscn'],elapsed_s=time.perf_counter()-started))
    summary=dict(task_id=ctx.task_id,new_rf_capture=False,source_task=manifest['source_task_id'],capture_sha256=manifest['capture_sha256'],
        band_hypothesis='n40',ssb_scs_khz=30,ssb_pattern='C',raster_source=nr.RASTER_SOURCE,
        raster_candidates=len(raster),tested=len(results),results=results,
        validated_broadcast_records=sum(r['valid_frames'] for r in results),
        note='Available100ms only, n40/30kHz/CaseC technical raster subset; not all NR bands, numerologies or signal conditions. Decode records are not unique transmitters.')
    dump(ROOT/'docs/testing/nr-real-v16.json',summary)
    print(json.dumps(dict(index=i+1,total=len(raster),gscn=entry['gscn'],center_hz=entry['center_hz'],valid_blocks=out['valid_frames'],elapsed_s=results[-1]['elapsed_s'])),flush=True)
