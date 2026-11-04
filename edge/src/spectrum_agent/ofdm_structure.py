"""Bounded CP-based OFDM structure measurements; never a protocol decoder."""
import numpy as np
from scipy import signal
from .common import DomainError
from .dsp import load_iq


def measure(x,fs,useful_symbols_s=(3.2e-6,12.8e-6,66.6666667e-6)):
    x=np.asarray(x,dtype=np.complex128);candidates=[]
    if len(x)<1024:return []
    x=x-x.mean();power=np.abs(x)**2
    for useful in useful_symbols_s:
        lag=round(useful*fs)
        if lag<8 or len(x)<lag*12:continue
        products=x[:-lag]*np.conj(x[lag:]);left=power[:-lag];right=power[lag:]
        global_correlation=abs(products.mean())/max(np.sqrt(left.mean()*right.mean()),1e-20)
        # A tone correlates everywhere and cannot establish a cyclic prefix.
        if global_correlation>.8:continue
        for ratio in (.25,.125,.0625,9/128,5/64):
            guard=max(4,round(lag*ratio));period=lag+guard
            sums=np.r_[0,np.cumsum(products)];ls=np.r_[0,np.cumsum(left)];rs=np.r_[0,np.cumsum(right)]
            offsets=np.arange(0,period,max(1,guard//4))
            cycles=min(128,(len(products)-period-guard)//period)
            if cycles<8:continue
            starts=offsets[:,None]+np.arange(cycles)[None,:]*period
            correlation=np.abs(sums[starts+guard]-sums[starts])**2/np.maximum((ls[starts+guard]-ls[starts])*(rs[starts+guard]-rs[starts]),1e-20)
            scores=np.mean(correlation,axis=1);best=int(np.argmax(scores));baseline=float(np.median(scores))
            score=float(scores[best]);contrast=score-baseline
            if score>=.35 and contrast>=.15:
                candidates.append(dict(useful_symbol_s=lag/fs,subcarrier_spacing_hz=fs/lag,cyclic_prefix_s=guard/fs,
                    symbol_period_s=period/fs,normalized_cp_correlation=score,phase_contrast=contrast,
                    observed_symbols=cycles,phase_sample=int(offsets[best]),global_lag_correlation=float(global_correlation)))
    return sorted(candidates,key=lambda v:v['phase_contrast'],reverse=True)[:8]


def analyze(ctx,artifact_id,sample_start=0,sample_stop=None):
    iq,m=load_iq(ctx,artifact_id);fs=m['sample_rate_hz'];sample_stop=len(iq) if sample_stop is None else sample_stop
    if any(isinstance(v,bool) or not isinstance(v,int) for v in (sample_start,sample_stop)) or not 0<=sample_start<sample_stop<=len(iq):
        raise DomainError('INVALID_ARGUMENT','Valid original sample interval required')
    if fs>40e6:raise DomainError('RESOURCE_LIMIT','Extract an evidence-selected subband at <=40MS/s first; do not infer OFDM from the entire mixed200MS/s band')
    intervals=[];candidates=[];remaining=2_000_000
    for seg in m['segments']:
        a=max(sample_start,seg['file_start'],m.get('valid_output_start',0));b=min(sample_stop,seg['file_start']+seg['count'])
        while b-a>=1024 and remaining>=1024:
            ctx.check();stop=min(b,a+remaining,a+round(fs*.01))
            x=np.asarray(iq[a:stop]);remaining-=len(x)
            if not np.isfinite(x).all():raise DomainError('INVALID_IQ','Nonfinite IQ')
            found=measure(x,fs)
            candidates.extend(dict(c,sample_start=a,sample_stop=stop) for c in found)
            intervals.append(dict(sample_start=a,sample_stop=stop));a=stop
    value=dict(artifact_id=artifact_id,protocol='unknown',evidence_level='structure_candidate',
        waveform='OFDM-like candidate' if candidates else 'unresolved',cp_candidates=candidates,
        coverage=dict(intervals=intervals,evaluated_samples=2_000_000-remaining,requested_samples=sample_stop-sample_start,
            complete=2_000_000-remaining==sample_stop-sample_start),
        limitations=['Tested useful durations3.2,12.8,66.67us and selected CP ratios only; not exhaustive',
            'CP correlation and spacing are structural evidence,not LTE,Wi-Fi or proprietary-link confirmation',
            'Overlapping signals,CFO variation,short bursts and clock error can cause missed or ambiguous structure'])
    aid=ctx.json_artifact('ofdm_structure',value)
    return dict(evidence_id=aid,**value)
