"""Bounded, protocol-independent receive analysis using NumPy/SciPy and SigMF."""
import json
import hashlib
import math
import os
from pathlib import Path
import numpy as np
from scipy import signal
from .common import DomainError,dump,finite_number
from .dsp import load_iq


def windows(ctx,artifact_id,limit=2_000_000):
    iq,meta=load_iq(ctx,artifact_id)
    remaining=limit;chunks=[]
    for seg in meta['segments']:
        start=max(seg['file_start'],meta.get('valid_output_start',0))
        stop=min(len(iq),seg['file_start']+seg['count'],start+remaining)
        if stop<=start: continue
        ctx.check()
        chunk=np.asarray(iq[start:stop])
        if not np.isfinite(chunk).all(): raise DomainError('INVALID_IQ','IQ contains nonfinite samples')
        chunks.append((start,chunk))
        remaining-=stop-start
        if not remaining: break
    if not chunks: raise DomainError('INSUFFICIENT_EVIDENCE','No valid IQ samples')
    coverage=dict(evaluated_samples=sum(len(x) for _,x in chunks),total_samples=len(iq),
        intervals=[dict(sample_start=s,sample_stop=s+len(x)) for s,x in chunks],
        prefix_limited=sum(len(x) for _,x in chunks)<len(iq),
        note='At most2M samples, respecting continuous segments; absence in analyzed intervals does not establish absence elsewhere')
    return chunks,meta,coverage


def quality(ctx,artifact_id):
    chunks,meta,coverage=windows(ctx,artifact_id)
    x=np.concatenate([x for _,x in chunks])
    if not np.isfinite(x).all(): raise DomainError('INVALID_IQ','IQ contains nonfinite samples')
    power=float(np.mean(abs(x)**2));vi=float(np.var(x.real));vq=float(np.var(x.imag))
    corr=float(np.mean((x.real-x.real.mean())*(x.imag-x.imag.mean()))/max(math.sqrt(vi*vq),1e-20))
    rail=float(np.mean((abs(x.real)>=.99)|(abs(x.imag)>=.99)))
    dc=float(abs(np.mean(x))**2/max(power,1e-20))
    warnings=[]
    if meta.get('gaps') or meta.get('overflow_count',0): warnings.append('hardware_gaps_or_overflow')
    if rail>.001: warnings.append('possible_normalized_sample_saturation')
    if dc>.1: warnings.append('strong_dc_or_centered_carrier_do_not_automatically_remove')
    value=dict(artifact_id=artifact_id,coverage=coverage,relative_power_db=10*math.log10(max(power,1e-20)),
        dc_power_fraction=dc,iq_variance_ratio_db=10*math.log10(max(vi,1e-20)/max(vq,1e-20)),
        iq_correlation=corr,normalized_rail_fraction=rail,flags=warnings,
        simulated=meta.get('simulated',False),evidence_level='measurement',
        limitations=['IQ imbalance/DC may reflect the signal itself; these are diagnostics, not calibrated receiver faults',
                     'Rail indicator assumes normalized samples; absolute dBm and clipping are not established'])
    aid=ctx.json_artifact('iq_quality',value)
    return dict(evidence_id=aid,**value)


def bursts(ctx,artifact_id,bin_s=.00002):
    finite_number(bin_s,'bin_s',1e-6,.001)
    chunks,meta,coverage=windows(ctx,artifact_id);fs=meta['sample_rate_hz']
    width=max(8,int(fs*bin_s));events=[];periods=[];active=total=0
    for start,x in chunks:
        n=len(x)//width
        if n<16: continue
        power=np.mean(abs(x[:n*width].reshape(n,width))**2,axis=1)
        floor=max(float(np.percentile(power,10)),1e-20)
        db=10*np.log10(np.maximum(power,1e-20)/floor)
        gate=db>=6
        edges=np.diff(np.r_[False,gate,False].astype(int));begins=np.flatnonzero(edges==1);ends=np.flatnonzero(edges==-1)
        for a,b in zip(begins,ends):
            events.append(dict(sample_start=int(start+a*width),sample_stop=int(start+b*width),duration_s=float((b-a)*width/fs)))
        active+=int(gate.sum());total+=n
        # Autocorrelation is confined to a continuous segment; require at least3 observed cycles.
        y=gate.astype(float)-gate.mean()
        if np.dot(y,y)>1e-10:
            ac=signal.fftconvolve(y,y[::-1],mode='full')[n-1:]
            ac=ac/np.arange(n,0,-1);ac/=max(ac[0],1e-20)
            peaks,_=signal.find_peaks(ac[2:n//3],height=.3,prominence=.15)
            for lag in peaks[:10]+2:
                periods.append(dict(period_s=float(lag*width/fs),correlation=float(ac[lag]),observed_cycles=float(n/lag)))
    value=dict(artifact_id=artifact_id,coverage=coverage,envelope_bin_s=width/fs,
        detected_bursts=events,duty_fraction_above_local_threshold=active/max(total,1),period_candidates=periods,
        protocol='unknown',evidence_level='candidate',simulated=meta.get('simulated',False),
        limitations=['Relative6dB envelope gate may miss weak or continuously occupied signals',
                     'Periodicity does not establish a device, protocol or mains-related interference'])
    aid=ctx.json_artifact('burst_analysis',value)
    return dict(evidence_id=aid,**{k:v for k,v in value.items() if k!='detected_bursts'},
                detected_bursts=events[:30],total_bursts=len(events),summary_truncated=len(events)>30)


def sweeps(ctx,artifact_id):
    chunks,meta,coverage=windows(ctx,artifact_id);fs=meta['sample_rate_hz'];nfft=256;hop=128;tracks=[]
    for base,x in chunks:
        if len(x)<nfft*8: continue
        frames=np.lib.stride_tricks.sliding_window_view(x,nfft)[::hop]
        # Work in tiles; a single straight-line ridge is only a waveform hypothesis.
        for offset in range(0,len(frames)-7,64):
            ctx.check();tile=frames[offset:offset+64]
            p=abs(np.fft.fftshift(np.fft.fft(tile*np.hanning(nfft),axis=1),axes=1))**2
            bins=np.argmax(p,axis=1);freq=(bins-nfft/2)*fs/nfft
            concentration=np.partition(p,-3,axis=1)[:,-3:].sum(axis=1)/np.maximum(p.sum(axis=1),1e-20)
            if float(np.median(concentration))<.5 or np.ptp(freq)<3*fs/nfft: continue
            t=np.arange(len(freq))*hop/fs;fit=np.polyfit(t,freq,1);prediction=np.polyval(fit,t)
            r2=1-float(np.sum((freq-prediction)**2))/max(float(np.sum((freq-freq.mean())**2)),1e-20)
            if r2<.9: continue
            tracks.append(dict(sample_start=int(base+offset*hop),sample_stop=int(base+(offset+len(tile)-1)*hop+nfft),
                slope_hz_per_s=float(fit[0]),ridge_fit_r2=r2,frequency_excursion_hz=float(np.ptp(freq)),
                label='linear-sweep-like',protocol='unknown'))
    value=dict(artifact_id=artifact_id,coverage=coverage,tracks=tracks,frequency_resolution_hz=fs/nfft,
        protocol='unknown',evidence_level='candidate',simulated=meta.get('simulated',False),
        limitations=['Only strong locally linear dominant ridges; overlapping, weak, wrapping or nonlinear chirps may be missed',
                     'Does not confirm LoRa, radar, drone identity or intentional jamming'])
    aid=ctx.json_artifact('sweep_analysis',value)
    return dict(evidence_id=aid,**{k:v for k,v in value.items() if k!='tracks'},tracks=tracks[:20],total_tracks=len(tracks),summary_truncated=len(tracks)>20)


def activity(ctx,segmentation_id):
    rec=ctx.resolve(segmentation_id)
    if rec['kind']!='segmentation': raise DomainError('INVALID_ARTIFACT','Use initial full-band segmentation')
    seg=json.loads(Path(rec['path']).read_text());events=seg['events'];_,meta=load_iq(ctx,seg['capture_id'])
    resolution=seg['frequency_resolution_hz'];buckets={}
    # Separate continuous segments; grid associations are descriptive, not emitter tracks.
    for e in events:
        key=(e['segment_file_start'],round((e['freq_low_hz']+e['freq_high_hz'])/(4*resolution)))
        buckets.setdefault(key,[]).append(e)
    families=[]
    for key,items in buckets.items():
        intervals=sorted((e['sample_start'],e['sample_stop']) for e in items);merged=[]
        for a,b in intervals:
            if merged and a<=merged[-1][1]+seg['hop']: merged[-1][1]=max(merged[-1][1],b)
            else: merged.append([a,b])
        families.append(dict(family_id='f'+str(len(families)),event_ids=[e['event_id'] for e in items],
            freq_low_hz=min(e['freq_low_hz'] for e in items),freq_high_hz=max(e['freq_high_hz'] for e in items),
            segment_file_start=key[0],burst_count=len(merged),
            observed_active_duration_s=sum(b-a for a,b in merged)/meta['sample_rate_hz'],
            median_start_interval_s=float(np.median(np.diff([a for a,b in merged]))/meta['sample_rate_hz']) if len(merged)>1 else None,
            protocol='unknown'))
    value=dict(segmentation_id=segmentation_id,capture_id=seg['capture_id'],families=families,
        total_events=len(events),all_events_retained=sum(len(f['event_ids']) for f in families)==len(events),
        evidence_level='candidate',limitations=['Frequency-grid activity families are not device identities or proven hopping networks',
        'Burst counts depend on segmentation and grid width; drifting signals can cross buckets'])
    aid=ctx.json_artifact('activity_analysis',value)
    summary=sorted(families,key=lambda f:-f['observed_active_duration_s'])[:20]
    return dict(evidence_id=aid,**{k:v for k,v in value.items() if k!='families'},families=summary,total_families=len(families),summary_truncated=len(families)>20)


def export_sigmf(ctx,artifact_id,segmentation_id=None):
    from sigmf import validate
    iq,meta=load_iq(ctx,artifact_id);record=ctx.resolve(artifact_id)
    aid,path=ctx.artifact('sigmf','sigmf-meta');data_path=path.with_suffix('.sigmf-data')
    # A project-owned read-only dataset link avoids duplicating large recordings.
    os.link(record['path'],data_path)
    captures=[{'core:sample_start':s['file_start'],'core:frequency':meta['center_hz']} for s in meta['segments']]
    annotations=[]
    for s in meta['segments']:
        if s['count']:
            annotations.append({'core:sample_start':s['file_start'],'core:sample_count':s['count'],
                'core:comment':'Continuous recorded segment; consult source metadata for hardware time and gaps'})
    if segmentation_id:
        rec=ctx.resolve(segmentation_id)
        if rec['kind']!='segmentation': raise DomainError('INVALID_ARTIFACT','Use initial segmentation')
        seg=json.loads(Path(rec['path']).read_text())
        if seg['capture_id']!=artifact_id: raise DomainError('INVALID_ARTIFACT','Segmentation must refer to the exported recording')
        for e in seg['events']:
            annotations.append({'core:sample_start':e['sample_start'],'core:sample_count':e['sample_stop']-e['sample_start'],
                'core:freq_lower_edge':e['freq_low_hz'],'core:freq_upper_edge':e['freq_high_hz'],
                'core:label':'unknown','core:comment':f"Detected event {e['event_id']}; evidence {segmentation_id}; not protocol ground truth"})
    annotations.sort(key=lambda a:a['core:sample_start'])
    digest=hashlib.sha512()
    with Path(record['path']).open('rb') as source:
        for block in iter(lambda:source.read(4*1024*1024),b''):
            ctx.check();digest.update(block)
    value={'global':{'core:datatype':'cf32_le','core:sample_rate':meta['sample_rate_hz'],'core:version':'1.2.6',
        'core:description':f"Task {ctx.task_id}; source {artifact_id}; simulated={meta.get('simulated',False)}; "
        'file sample continuity does not imply uninterrupted hardware capture; no protocol label inferred',
        'core:sha512':digest.hexdigest()},
        'captures':captures,'annotations':annotations}
    validate.validate(value)
    dump(path,value);ctx.register(aid,path,kind='sigmf_export',source_artifact_id=artifact_id)
    data_id=aid+'_data';ctx.register(data_id,data_path,kind='sigmf_dataset')
    return dict(evidence_id=aid,dataset_id=data_id,source_artifact_id=artifact_id,validated=True,
        protocol='unknown',note='Local dataset and validated SigMF metadata; no external upload, no new capture')
