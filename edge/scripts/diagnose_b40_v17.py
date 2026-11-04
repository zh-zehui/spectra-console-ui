"""Finite, receive-only B40 diagnostic matrix. This is an operator test, not a Harness decision."""
import json,time,uuid,subprocess
from pathlib import Path
import numpy as np
from spectrum_agent.common import Context,ROOT,dump,utc
from spectrum_agent import acquisition,dsp,lte

batch='b40_v17_'+uuid.uuid4().hex[:8]
out=ROOT/'docs/testing'/f'{batch}.json'
summary=dict(batch_id=batch,started_utc=utc(),mode='operator-directed RF diagnostic, not Harness-selected',
    target_center_hz=2373900000,rows=[],warnings=[
    'Sequential observations are not simultaneous; propagation and traffic can change.',
    'Gain/LO/rate tests cannot conclusively exclude receiver artifacts without external RF controls.',
    'No SIB1 decoder: public/private/vendor cannot be determined by this test.'])
cases=[('baseline',2373.9e6,40e6,20),('gain10',2373.9e6,40e6,10),
       ('gain0',2373.9e6,40e6,0),('lo_plus',2376.4e6,40e6,20),
       ('lo_minus',2371.4e6,40e6,20),('rate50',2373.9e6,50e6,20),
       ('baseline_repeat',2373.9e6,40e6,20)]
print(json.dumps(dict(batch_id=batch,result_path=str(out))),flush=True)
for name,fc,rate,gain in cases:
    ctx=Context(batch+'_'+name)
    row=dict(case=name,task_id=ctx.task_id,requested_center_hz=fc,requested_rate_hz=rate,requested_gain_db=gain)
    summary['rows'].append(row);dump(out,summary)
    dump(ctx.run/'request.json',dict(prompt='Operator-directed finite B40 diagnostic',
        constraints=dict(center_hz=fc,span_hz=30e6,observation_s=.5)))
    started=time.perf_counter()
    try:
        with ctx.span('diagnostic_capture',case=name):
            row['capture']=acquisition.capture(ctx,fc,30e6,.5,gain,rate)
        row['capture_elapsed_s']=time.perf_counter()-started
        row['status']='captured'
    except Exception as exc:
        row.update(status='capture_failed',error_code=getattr(exc,'code',type(exc).__name__),error=str(exc))
    dump(out,summary);print(json.dumps(dict(case=name,status=row['status'])),flush=True)

# Analyze only complete, gap-free captures. Five independent windows cover each recording.
for row in summary['rows']:
    cap=row.get('capture',{})
    if not cap.get('complete') or cap.get('overflow_count') or cap.get('gaps'):
        row['analysis_status']='skipped_incomplete';dump(out,summary);continue
    ctx=Context(row['task_id']);row['windows']=[]
    for index in range(5):
        started=time.perf_counter()
        try:
            fs=cap['sample_rate_hz'];start=round(index*.1*fs);stop=round((index+1)*.1*fs)
            with ctx.span('diagnostic_extract',window=index):
                sub=dsp.extract(ctx,cap['capture_id'],2373.9e6,1.4e6,start,stop,1920000,.001)
            with ctx.span('diagnostic_lte',window=index):
                result=lte.verify(ctx,sub['artifact_id'],'tdd')
            iq,meta=dsp.load_iq(ctx,sub['artifact_id'])
            valid=iq[meta.get('valid_output_start',0):]
            power=float(np.mean(np.abs(valid)**2))
            row['windows'].append(dict(index=index,parent_sample_start=start,parent_sample_stop=stop,
                relative_power_dbfs=10*float(np.log10(max(power,1e-30))),result=result,elapsed_s=time.perf_counter()-started))
        except Exception as exc:
            row['windows'].append(dict(index=index,error_code=getattr(exc,'code',type(exc).__name__),error=str(exc)))
        dump(out,summary)
    row['analysis_status']='completed'
    row['valid_broadcast_blocks']=sum(w.get('result',{}).get('valid_frames',0) for w in row['windows'])
    dump(out,summary)
    print(json.dumps(dict(case=row['case'],valid_broadcast_blocks=row['valid_broadcast_blocks'])),flush=True)
summary['finished_utc']=utc();dump(out,summary)
print(json.dumps(dict(status='finished',result_path=str(out))),flush=True)
