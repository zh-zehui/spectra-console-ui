"""Model-selected numerical analysis bundles; no automatic protocol inference."""
import json
from .common import DomainError
from .dsp import load_iq,extract,features
from .analysis_tools import quality,bursts,sweeps
from .ofdm_structure import analyze as ofdm


def analyze(ctx,capture_id,selections,reason,evidence_ids):
    if not isinstance(selections,list) or not 1<=len(selections)<=4:raise DomainError('INVALID_ARGUMENT','Select1..4 explicit signal windows')
    if not isinstance(reason,str) or len(reason)<16:raise DomainError('INVALID_ARGUMENT','Explain observed residual structure and chosen measurements')
    if not isinstance(evidence_ids,list) or not 1<=len(evidence_ids)<=12:raise DomainError('INVALID_ARGUMENT','Reference numerical/protocol evidence')
    for aid in evidence_ids:ctx.resolve(aid)
    iq,m=load_iq(ctx,capture_id);fs=m['sample_rate_hz']
    request=json.loads((ctx.run/'request.json').read_text());intent=request.get('task_intent',{})
    first=round(intent.get('analysis_start_s',0)*fs);last=round(intent.get('analysis_stop_s',len(iq)/fs)*fs)
    functions=dict(features=features,quality=quality,bursts=bursts,sweeps=sweeps,ofdm=ofdm)
    results=[]
    for s in selections:
        if not isinstance(s,dict) or not {'center_hz','bandwidth_hz','output_rate_hz','metrics'}<=set(s):
            raise DomainError('INVALID_ARGUMENT','Each selection requires center,bandwidth,output rate and metrics')
        if set(s)-{'center_hz','bandwidth_hz','output_rate_hz','sample_start','sample_stop','metrics'}:
            raise DomainError('INVALID_ARGUMENT','Unknown signal-window field')
        metrics=s.get('metrics',[])
        if not metrics or len(metrics)>5 or any(v not in functions for v in metrics):raise DomainError('INVALID_ARGUMENT','Choose features/quality/bursts/sweeps/ofdm explicitly')
        start=s.get('sample_start',first);stop=s.get('sample_stop',last)
        if any(isinstance(v,bool) or not isinstance(v,int) for v in (start,stop)):raise DomainError('INVALID_ARGUMENT','Sample bounds must be integers')
        if not first<=start<stop<=last:raise DomainError('INVALID_ARGUMENT','Window must lie within intent time scope')
        ctx.check()
        with ctx.span('batch_residual_extract'):
            sub=extract(ctx,capture_id,s['center_hz'],s['bandwidth_hz'],start,stop,s['output_rate_hz'],margin_s=.002)
        values={}
        for metric in dict.fromkeys(metrics):
            with ctx.span('batch_residual_'+metric):values[metric]=functions[metric](ctx,sub['artifact_id'])
        results.append(dict(selection=s,subband_id=sub['artifact_id'],measurements=values))
    value=dict(capture_id=capture_id,reason=reason,feedback_evidence_ids=evidence_ids,results=results,
        protocol='unknown',note='Numerical structure candidates only. Each measurement retains its sample limits; this bundle does not confirm a protocol or classify the whole recording.')
    aid=ctx.json_artifact('signal_window_analysis',value)
    return dict(evidence_id=aid,**value)
