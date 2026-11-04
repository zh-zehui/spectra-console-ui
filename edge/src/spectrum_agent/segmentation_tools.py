"""Numerical observations and explicit model decisions; no hidden agent/router."""
import json
from pathlib import Path
import numpy as np
from .common import DomainError
from .scene import read


def merge_regions(groups, sample_rate_hz, max_gap_s=.00002):
    """Conservative same-band/time adjacency, with no removal or protocol labels."""
    merged=[];active=[];gap=max_gap_s*sample_rate_hz
    for source in sorted(groups,key=lambda g:(g['segment_file_start'],g['sample_start'],g['freq_low_hz'])):
        e=dict(source);e['source_group_ids']=[source['group_id']]
        active=[g for g in active if g['segment_file_start']==e['segment_file_start'] and g['sample_stop']+gap>=e['sample_start']]
        matches=[]
        for g in active:
            wide=max(g['bandwidth_hz'],e['bandwidth_hz']);small=min(g['bandwidth_hz'],e['bandwidth_hz'])
            overlap=min(g['freq_high_hz'],e['freq_high_hz'])-max(g['freq_low_hz'],e['freq_low_hz'])
            # Keep narrow activity embedded inside a wide signal independent.
            if small/max(wide,1)<.5 or overlap/max(wide,1)<.5:continue
            low=min(g['freq_low_hz'],e['freq_low_hz']);high=max(g['freq_high_hz'],e['freq_high_hz'])
            start=min(g['sample_start'],e['sample_start']);stop=max(g['sample_stop'],e['sample_stop'])
            if high-low>1.5*max(g['_anchor_width'],e['bandwidth_hz']):continue
            if stop-start>max(.002*sample_rate_hz,1.5*max(g['_anchor_duration'],e['sample_stop']-e['sample_start'])):continue
            matches.append((overlap/max(wide,1),g,start,stop,low,high))
        if matches:
            _,g,start,stop,low,high=max(matches,key=lambda x:x[0])
            g['time_start_s']=g.get('time_start_s',g['sample_start']/sample_rate_hz)+(start-g['sample_start'])/sample_rate_hz
            g.update(sample_start=start,sample_stop=stop,freq_low_hz=low,freq_high_hz=high,
                bandwidth_hz=high-low,duration_s=(stop-start)/sample_rate_hz,snr_db=max(g['snr_db'],e['snr_db']))
            g['source_group_ids'].append(source['group_id'])
            g['event_ids']=sorted(set(g.get('event_ids',[])+source.get('event_ids',[])))
            g['event_count']=len(g['event_ids'])
        else:
            e.update(group_id='m'+str(len(merged)),_anchor_width=e['bandwidth_hz'],
                _anchor_duration=e['sample_stop']-e['sample_start'])
            merged.append(e);active.append(e)
    for g in merged:
        g.pop('_anchor_width');g.pop('_anchor_duration')
        g['protocol']='unknown';g['aggregation_is_hypothesis']=True
    return merged


def consolidate(ctx, segmentation_id, reason, max_gap_s=.00002):
    from .common import finite_number
    finite_number(max_gap_s,'max_gap_s',0,.0001)
    if not isinstance(reason,str) or not 16<=len(reason)<=2000:
        raise DomainError('INVALID_ARGUMENT','Record an evidence-based consolidation reason')
    parent=read(ctx,segmentation_id,'segmentation')
    if parent.get('consolidated_from'):
        raise DomainError('INVALID_ARGUMENT','Consolidate the original detector output, not an already consolidated result')
    rec=ctx.resolve(parent['capture_id']);meta=json.loads(Path(rec['metadata_path']).read_text())
    groups=merge_regions(parent['signal_groups'],meta['sample_rate_hz'],max_gap_s)
    value=dict(parent,signal_groups=groups,strategy='conservative_region_consolidation',
        algorithm='same-band-time-adjacency-v1',consolidated_from=segmentation_id,
        decision_reason=reason,max_gap_s=max_gap_s,
        all_source_groups_retained=sum(len(g['source_group_ids']) for g in groups)==len(parent['signal_groups']),
        warnings=parent.get('warnings',[])+['Merged bounds are hypotheses; member regions remain authoritative. Nearby independent transmissions can still be grouped. No protocol identity is inferred.'])
    aid=ctx.json_artifact('segmentation',value)
    return dict(segmentation_id=aid,strategy=value['strategy'],source_segmentation_id=segmentation_id,
        source_count=len(parent['signal_groups']),total_groups=len(groups),
        all_source_groups_retained=value['all_source_groups_retained'],signal_groups=groups[:20],
        summary_truncated=len(groups)>20,warning=value['warnings'][-1])


def overview(ctx, artifact_id, fft_size=1024):
    from .dsp import load_iq
    if fft_size not in (512, 1024, 2048, 4096):
        raise DomainError('INVALID_ARGUMENT', 'Unsupported FFT size')
    iq, meta = load_iq(ctx, artifact_id)
    fs=meta['sample_rate_hz'];fc=meta['center_hz']
    from .frequency_scope import bounds
    low,high=bounds(meta);span=high-low
    intervals=[]
    # Uniform windows across each continuous segment, never FFT across a gap.
    eligible=[s for s in meta['segments'] if s['count']>=fft_size]
    total=sum(s['count'] for s in eligible)
    for seg in eligible:
        ctx.check()
        start=max(seg['file_start'],meta.get('valid_output_start',0))
        last=seg['file_start']+seg['count']-fft_size
        if last<start: continue
        count=max(1,int(256*seg['count']/max(total,1)))
        intervals.extend((int(i),int(i)+fft_size) for i in np.unique(np.linspace(start,last,count,dtype=int)))
    intervals=intervals[:256]
    if not intervals: raise DomainError('INSUFFICIENT_EVIDENCE','Recording shorter than FFT')
    win=np.hanning(fft_size)
    frames=np.array([np.asarray(iq[a:b])*win for a,b in intervals])
    power=abs(np.fft.fftshift(np.fft.fft(frames,axis=1),axes=1))**2/np.sum(win**2)
    db=10*np.log10(np.maximum(power,1e-20))
    freq=fc+np.fft.fftshift(np.fft.fftfreq(fft_size,1/fs))
    valid=(freq>=low)&(freq<=high)
    floor=np.percentile(db[:,valid],30,axis=1)
    bands=[]
    for cols in np.array_split(np.flatnonzero(valid),32):
        if not len(cols): continue
        bands.append(dict(low_hz=float(freq[cols[0]]-fs/fft_size/2),
            high_hz=float(freq[cols[-1]]+fs/fft_size/2),
            mean_power_db=float(10*np.log10(np.maximum(power[:,cols].mean(),1e-20))),
            peak_power_db=float(db[:,cols].max()),
            sampled_above_12db_fraction=float((db[:,cols]>floor[:,None]+12).mean())))
    value=dict(artifact_id=artifact_id,fft_size=fft_size,frequency_resolution_hz=fs/fft_size,
        recording_samples=len(iq),sample_rate_hz=fs,requested_span_hz=span,
        sampled_windows=[dict(sample_start=a,sample_stop=b) for a,b in intervals],
        sampled_window_count=len(intervals),frequency_bands=bands,
        temporal_peak_db=db[:,valid].max(axis=1).tolist(),
        evidence_level='sampled_numerical_overview',
        warning='Sparse sampled windows, not full-band detection coverage; may miss short bursts. Relative power, not calibrated dBm. Use detection tools on full recording.')
    aid=ctx.json_artifact('spectrum_overview',value)
    return dict(overview_id=aid,**value)


def compare(ctx, segmentation_ids):
    if not isinstance(segmentation_ids,list) or not 1<=len(segmentation_ids)<=4 or len(set(segmentation_ids))!=len(segmentation_ids):
        raise DomainError('INVALID_ARGUMENT','Supply 1..4 distinct segmentation IDs')
    segs=[read(ctx,aid,'segmentation') for aid in segmentation_ids]
    if len({s['capture_id'] for s in segs})!=1:
        raise DomainError('INVALID_ARGUMENT','Compare only segmentations of the same recording')
    from .catalog import associate
    verified=[]
    for path in (ctx.run/'index').glob('*.json'):
        rec=json.loads(path.read_text())
        if rec['kind'] in ('ble_verification','wifi_verification','ieee154_verification','lte_verification','nr_verification'):
            v=read(ctx,rec['artifact_id']);verified.append(dict(v,evidence_id=rec['artifact_id']))
    candidates=[]
    for aid,seg in zip(segmentation_ids,segs):
        ctx.check();groups=seg['signal_groups']
        links=[l for l in associate(ctx,[dict(seg,evidence_id=aid)],verified)
               if l['capture_id']==seg['capture_id'] and l['association_level']=='packet_time_frequency_overlap']
        expansions=[];temporal_ious=[]
        for link in links:
            matching={m['group_id'] for m in link['candidate_groups']}
            options=[g for g in groups if g['group_id'] in matching]
            if not options:continue
            duration=max(1,link['sample_stop']-link['sample_start'])
            expansions.append(min((g['sample_stop']-g['sample_start'])/duration for g in options))
            temporal_ious.append(max(max(0,min(g['sample_stop'],link['sample_stop'])-max(g['sample_start'],link['sample_start']))/
                max(1,max(g['sample_stop'],link['sample_stop'])-min(g['sample_start'],link['sample_start'])) for g in options))
        candidates.append(dict(segmentation_id=aid,strategy=seg.get('strategy','traditional'),
            segmentation_level=seg.get('segmentation_level','fine_detection'),
            proposal_time_cell_s=seg.get('proposal_time_cell_s'),proposal_frequency_cell_hz=seg.get('proposal_frequency_cell_hz'),
            fft_size=seg['fft_size'],threshold_db=seg['threshold_db'],group_count=len(groups),
            frequency_resolution_hz=seg['frequency_resolution_hz'],time_resolution_s=seg.get('time_resolution_s'),
            short_under_100us_count=sum(g['duration_s']<.0001 for g in groups),
            narrow_under_3bins_count=sum(g['bandwidth_hz']<=3*seg['frequency_resolution_hz'] for g in groups),
            singleton_count=sum(g.get('event_count',1)==1 for g in groups),
            median_duration_s=float(np.median([g['duration_s'] for g in groups])) if groups else 0,
            median_bandwidth_hz=float(np.median([g['bandwidth_hz'] for g in groups])) if groups else 0,
            packet_evidence_count=len(links),packet_overlap_count=sum(bool(l['candidate_groups']) for l in links),
            packet_boundary_diagnostics=dict(median_best_time_expansion=float(np.median(expansions)) if expansions else None,
                median_best_time_iou=float(np.median(temporal_ious)) if temporal_ious else None,
                warning='Relative to decoded control-frame estimates only, not ground-truth burst boundaries; longer bursts may contain multiple frames. Overlap alone never establishes segmentation correctness.'),
            coverage=seg.get('coverage',{}),
            warnings=seg.get('warnings',[])))
    value=dict(capture_id=segs[0]['capture_id'],candidates=candidates,
        verification_evidence_ids=sorted(v['evidence_id'] for v in verified),accuracy_improvement='not_measured',
        warning='No automatic winner. Fewer boxes is not proof of accuracy. Copy resolution facts from numerical fields, not guesses. Coverage occupancy is a separate threshold statistic, NOT the detector mask or recall. Packet overlap is a coverage hint, not ground truth or correct boundaries; retain weak, short, continuous and overlapping activity.')
    aid=ctx.json_artifact('segmentation_comparison',value)
    return dict(comparison_id=aid,**value)


def select(ctx, segmentation_id, comparison_id, reason):
    if not isinstance(reason,str) or not 16<=len(reason)<=2000:
        raise DomainError('INVALID_ARGUMENT','Evidence-based selection reason must be 16..2000 characters')
    seg=read(ctx,segmentation_id,'segmentation')
    comparison=read(ctx,comparison_id,'segmentation_comparison')
    current=sorted(json.loads(p.read_text())['artifact_id'] for p in (ctx.run/'index').glob('*.json')
        if json.loads(p.read_text())['kind'] in ('ble_verification','wifi_verification','ieee154_verification','lte_verification','nr_verification'))
    if current!=comparison.get('verification_evidence_ids',[]):
        raise DomainError('STALE_COMPARISON','Protocol evidence changed; compare again before selecting')
    candidate=next((c for c in comparison['candidates'] if c['segmentation_id']==segmentation_id),None)
    if not candidate or comparison['capture_id']!=seg['capture_id']:
        raise DomainError('INVALID_ARGUMENT','Selection must be a candidate in the supplied comparison')
    # A candidate cannot replace an alternative that retains strictly more known packet evidence.
    best=max(c['packet_overlap_count'] for c in comparison['candidates'])
    if candidate['packet_overlap_count']<best:
        raise DomainError('EVIDENCE_REGRESSION','Selected candidate loses known packet overlap; recheck or keep better-supported candidate')
    value=dict(segmentation_id=segmentation_id,comparison_id=comparison_id,reason=reason,
        decision_owner='deepseek_harness',reason_is_model_authored=True,
        accuracy_improvement='not_measured',originals_preserved=True,
        warning='Explicit working hypothesis, not calibrated accuracy or proof that unknown/weak signals are preserved.')
    aid=ctx.json_artifact('segmentation_selection',value)
    return dict(selection_id=aid,**value)
