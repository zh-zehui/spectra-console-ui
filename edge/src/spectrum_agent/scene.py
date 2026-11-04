"""Bounded segmentation/recognition feedback tools for the official harness."""
import json
from pathlib import Path
import numpy as np
from scipy import ndimage
from .common import DomainError,finite_number


class AdaptiveDetector:
    def __init__(self,high_db): self.high_db=high_db

    def detect(self,request):
        db=request.window_db
        # Frequency-dependent background plus time-dependent common-mode noise.
        floor=np.percentile(db,30,axis=0)
        drift=np.percentile(db-floor,30,axis=1)
        residual=db-floor[None,:]-drift[:,None]
        seeds=residual>=self.high_db
        mask=ndimage.binary_propagation(seeds,mask=residual>=self.high_db-3)
        labels,_=ndimage.label(mask)
        result=[]
        for i,box in enumerate(ndimage.find_objects(labels),1):
            if box is None: continue
            selected=labels[box]==i
            if selected.sum()<8 or box[0].stop-box[0].start<3: continue
            values=residual[box][selected]
            result.append(dict(box=dict(x1=box[1].start,x2=box[1].stop-1,
                 y1=box[0].start,y2=box[0].stop-1),snr_db=float(values.max()),
                 peak_db=float(db[box][selected].max()),occupancy=float(selected.mean()),label='adaptive_energy'))
        return result


def read(ctx,aid,kind=None):
    rec=ctx.resolve(aid)
    if kind and rec['kind']!=kind: raise DomainError('INVALID_ARTIFACT','Unexpected evidence type')
    return json.loads(Path(rec['path']).read_text())


def assess(ctx,segmentation_id):
    seg=read(ctx,segmentation_id,'segmentation')
    groups=seg['signal_groups'];attempts=[];refinements=[];profiles=[];generic=[];verified=[]
    for p in (ctx.run/'index').glob('*.json'):
        rec=json.loads(p.read_text())
        if rec['kind'] in ('ofdm_structure','iq_quality','burst_analysis','sweep_analysis','activity_analysis','bluetooth_classic_analysis'):
            value=read(ctx,rec['artifact_id'])
            source=value.get('artifact_id',value.get('capture_id'))
            root=source
            if source and source!=seg['capture_id']:
                source_rec=ctx.resolve(source)
                if source_rec.get('metadata_path'):
                    root=json.loads(Path(source_rec['metadata_path']).read_text()).get('parent_artifact_id',source)
            if root==seg['capture_id']:
                generic.append(dict(evidence_id=rec['artifact_id'],kind=rec['kind'],
                    artifact_id=source,flags=value.get('flags',[]),
                    period_candidates=value.get('period_candidates',[])[:5],
                    cp_candidates=value.get('cp_candidates',[])[:8],
                    sweep_track_count=len(value.get('tracks',[])),coverage=value.get('coverage')))
        if rec['kind']=='scene_refinement':
            value=read(ctx,rec['artifact_id'])
            if value['parent_segmentation_id']==segmentation_id:
                refinements.append(dict(refinement_id=rec['artifact_id'],group_id=value['parent_group_id'],
                    local_event_count=len(value['mapped_events']),subband_id=value['subband_id'],quality_comparison=value.get('quality_comparison',{})))
        if rec['kind']=='features':
            profiles.append(dict(evidence_id=rec['artifact_id'],features=read(ctx,rec['artifact_id'])))
        if rec['kind'] not in ('ble_verification','wifi_verification','ieee154_verification','lte_verification','nr_verification'): continue
        v=read(ctx,rec['artifact_id'])
        sub=ctx.resolve(v['artifact_id'])
        m=json.loads(Path(sub['metadata_path']).read_text())
        if m.get('parent_artifact_id')!=seg['capture_id']: continue
        verified.append(dict(v,evidence_id=rec['artifact_id']))
        attempts.append(dict(evidence_id=rec['artifact_id'],protocol=v['protocol'],valid_frames=v['valid_frames'],
            sample_start=m['parent_sample_start'],sample_stop=m['actual_parent_sample_stop'],
            center_hz=m['center_hz'],bandwidth_hz=m['requested_span_hz']))
    from .catalog import associate
    packet_links=associate(ctx,[dict(seg,evidence_id=segmentation_id)],verified)
    from .header_evidence import project as project_headers,associate as associate_headers
    from .dsp import load_iq
    _,capture_meta=load_iq(ctx,seg['capture_id'])
    header_links=associate_headers(project_headers(ctx,verified),capture_meta['sample_rate_hz'])
    targets=[]
    for g in groups:
        related=[v for v in attempts if min(v['sample_stop'],g['sample_stop'])>max(v['sample_start'],g['sample_start']) and
            min(v['center_hz']+v['bandwidth_hz']/2,g['freq_high_hz'])>max(v['center_hz']-v['bandwidth_hz']/2,g['freq_low_hz'])]
        flags=[]
        if g.get('boundary_truncated'): flags.append('requested_band_edge')
        if g.get('event_count',1)>1: flags.append('multi_fragment_track')
        if g['bandwidth_hz']<=3*seg['frequency_resolution_hz']: flags.append('frequency_resolution_limited')
        header_related=[h for h in header_links if min(h['sample_stop'],g['sample_stop'])>max(h['sample_start'],g['sample_start']) and min(h['freq_high_hz'],g['freq_high_hz'])>max(h['freq_low_hz'],g['freq_low_hz'])]
        if any(v['valid_frames']==0 for v in related) and not header_related: flags.append('verification_failed_in_overlapping_window')
        if header_related:flags.append('SIG_valid_MAC_not_established_by_header')
        if any(h['sample_start']<g['sample_start']-seg['fft_size'] or h['sample_stop']>g['sample_stop']+seg['fft_size'] for h in header_related):
            flags.append('SIG_duration_extends_energy_boundary')
        packet_related=[l for l in packet_links if l['association_level']=='packet_time_frequency_overlap' and
                        any(c['group_id']==g['group_id'] for c in l['candidate_groups'])]
        if any(l['sample_start']<g['sample_start']-seg['fft_size'] or l['sample_stop']>g['sample_stop']+seg['fft_size'] for l in packet_related):
            flags.append('verified_packet_extends_candidate_group')
        if flags: targets.append(dict(group_id=g['group_id'],flags=flags,already_refined=any(r['group_id']==g['group_id'] for r in refinements),verification_evidence=[v['evidence_id'] for v in related],
            priority=3*int(any(v['valid_frames']==0 for v in related))+int('frequency_resolution_limited' in flags),
            recommended_action='local_adaptive_resegment_then_reverify'))
    targets.sort(key=lambda x:(-x['priority'],x['group_id']))
    result=dict(segmentation_id=segmentation_id,group_count=len(groups),review_target_count=len(targets),
        packet_associations=packet_links,header_associations=header_links,unmatched_packet_count=sum(not l['candidate_groups'] for l in packet_links if l['association_level']=='packet_time_frequency_overlap'),
        review_targets=targets,verification_attempts=attempts,refinements=refinements,generic_evidence=generic,profile_evidence_ids=[p['evidence_id'] for p in profiles],
        warning='Heuristic quality flags, not measured segmentation accuracy. Failed decoding can also mean unsupported PHY, interference or low SNR. Keep full-band unknowns.')
    capture_record=ctx.resolve(seg['capture_id'])
    if capture_record.get('metadata_path'):
        metadata=json.loads(Path(capture_record['metadata_path']).read_text())
        from .frequency_scope import effective_metadata,bounds
        low,high=bounds(effective_metadata(ctx,metadata))
        result['local_extraction_constraints']=dict(
            analysis_low_hz=low,analysis_high_hz=high,
            input_rate_hz=metadata['sample_rate_hz'],minimum_output_rate_to_passband_ratio=1.1,
            rules='Keep center_hz +/- bandwidth_hz/2 inside analysis range. Explicit output_rate_hz must be >=1.1*bandwidth_hz and <=input rate. Omit optional center/bandwidth/rate to use safe defaults when uncertain. Never infer out-of-band activity.')
    aid=ctx.json_artifact('scene_review',result)
    return dict(review_id=aid,**{k:v for k,v in result.items() if k not in ('review_targets','packet_associations')},
                review_targets=targets[:12],packet_associations=packet_links[:20],packet_summary_truncated=len(packet_links)>20,summary_truncated=len(targets)>12)


def clip_mapped_events(events,start,stop,low,high):
    result=[]
    for e in events:
        a=max(start,e['sample_start']);b=min(stop,e['sample_stop'])
        lo=max(low,e['freq_low_hz']);hi=min(high,e['freq_high_hz'])
        if a<b and lo<hi:result.append(dict(e,sample_start=a,sample_stop=b,freq_low_hz=lo,freq_high_hz=hi))
    return result


def refine(ctx,segmentation_id,group_id,reason,evidence_ids,fft_size=2048,threshold_db=12,
           offset_s=0,duration_s=.02,strategy='adaptive_hysteresis',center_hz=None,bandwidth_hz=None,
           output_rate_hz=None,margin_s=.001,local_time_bin_s=20e-6,local_frequency_bin_hz=200e3):
    from . import dsp
    if fft_size not in (512,1024,2048,4096): raise DomainError('INVALID_ARGUMENT','Unsupported FFT size')
    finite_number(threshold_db,'threshold_db',3,30)
    finite_number(offset_s,'offset_s',0,10);finite_number(duration_s,'duration_s',.0001,.02)
    if strategy not in ('traditional','adaptive_hysteresis','multiscale_energy','local_power_tiles'):
        raise DomainError('INVALID_ARGUMENT','Unsupported local fine detector')
    if not isinstance(reason,str) or not 8<=len(reason)<=1200:
        raise DomainError('INVALID_ARGUMENT','Provide a concise observation-based review reason')
    if not isinstance(evidence_ids,list) or not 1<=len(evidence_ids)<=6:
        raise DomainError('INVALID_ARGUMENT','Provide 1..6 task-owned review/feature/verification evidence IDs')
    has_review=False
    for aid in evidence_ids:
        rec=ctx.resolve(aid)
        if rec['kind'] not in ('ofdm_structure','scene_review','features','ble_verification','wifi_verification','ieee154_verification','lte_verification','nr_verification','iq_quality','burst_analysis','sweep_analysis','activity_analysis','bluetooth_classic_analysis'):
            raise DomainError('INVALID_ARTIFACT','Feedback must be review, features or protocol evidence')
        if rec['kind']=='scene_review':
            if read(ctx,aid)['segmentation_id']!=segmentation_id:
                raise DomainError('INVALID_ARTIFACT','Review must refer to the selected segmentation')
            has_review=True
    if not has_review: raise DomainError('INVALID_ARGUMENT','Include an assess_signal_scene review for this segmentation')
    seg=read(ctx,segmentation_id,'segmentation')
    group=next((g for g in seg['signal_groups'] if g['group_id']==group_id),None)
    if group is None: raise DomainError('NOT_FOUND','Unknown group in segmentation')
    _,meta=dsp.load_iq(ctx,seg['capture_id']);fs=meta['sample_rate_hz']
    from .frequency_scope import bounds
    low,high=bounds(meta)
    requested_width=min(high-low,max(group['bandwidth_hz']*1.5,8*seg['frequency_resolution_hz']))
    width=min(requested_width,fs*.8)
    center=(group['freq_low_hz']+group['freq_high_hz'])/2
    center_low=max(low+width/2,meta['center_hz']-fs/2+.5*width+1)
    center_high=min(high-width/2,meta['center_hz']+fs/2-.5*width-1)
    center=max(center_low,min(center_high,center))
    if bandwidth_hz is not None:
        finite_number(bandwidth_hz,'bandwidth_hz',1000,high-low);width=bandwidth_hz;requested_width=width
    if center_hz is not None:
        finite_number(center_hz,'center_hz',low,high);center=center_hz
    target_low=max(group['freq_low_hz'],center-width/2);target_high=min(group['freq_high_hz'],center+width/2)
    if target_low>=target_high:raise DomainError('INVALID_ARGUMENT','Requested fine subband must overlap the parent frequency range')
    start=group['sample_start']+int(offset_s*fs);stop=min(group['sample_stop'],start+int(fs*duration_s))
    if start>=stop:raise DomainError('INVALID_ARGUMENT','Fine-analysis offset must lie inside the selected parent region')
    # Preserve enough time context for the selected FFT, even for very short/narrow groups.
    sub=dsp.extract(ctx,seg['capture_id'],center,width,start,stop,margin_s=margin_s,output_rate_hz=output_rate_hz)
    actual_fft=fft_size
    if sub['sample_count']<2*actual_fft:
        available=[n for n in (512,1024,2048,4096) if 2*n<=sub['sample_count']]
        if not available:
            raise DomainError('INSUFFICIENT_EVIDENCE','Local window too short for supported FFT; retain original and use a wider observed group')
        actual_fft=max(available)
    local=dsp.segment(ctx,sub['artifact_id'],actual_fft,threshold_db,strategy=strategy,artifact_kind='local_segmentation',
        local_time_bin_s=local_time_bin_s,local_frequency_bin_hz=local_frequency_bin_hz)
    refined=read(ctx,local['segmentation_id'])
    mapped=[]
    for event in refined['events']:
        mapped.append(dict(event_id=event['event_id'],
            sample_start=max(sub['parent_sample_start'],int(sub['parent_sample_start']+(event['sample_start']/sub['sample_rate_hz']-sub['filter_group_delay_s'])*fs)),
            sample_stop=min(sub['actual_parent_sample_stop'],int(sub['parent_sample_start']+(event['sample_stop']/sub['sample_rate_hz']-sub['filter_group_delay_s'])*fs)),
            freq_low_hz=event['freq_low_hz'],freq_high_hz=event['freq_high_hz'],protocol='unknown'))
    context_count=len(mapped)
    mapped=clip_mapped_events(mapped,start,stop,target_low,target_high)
    baseline_count=sum(min(stop,e['sample_stop'])>max(start,e['sample_start']) and min(target_high,e['freq_high_hz'])>max(target_low,e['freq_low_hz']) for e in seg['events'])
    from .local_review import packet_quality
    packet_diagnostics=packet_quality(ctx,seg['capture_id'],start,stop,center-width/2,center+width/2,mapped)
    is_coarse=seg.get('segmentation_level')=='coarse_activity'
    fragmented=not is_coarse and len(mapped)>max(20,3*baseline_count)
    value=dict(parent_segmentation_id=segmentation_id,parent_group_id=group_id,capture_id=seg['capture_id'],
        reason=reason,reason_is_model_authored=True,observed_parent_group=group,
        feedback_evidence_ids=evidence_ids,subband_id=sub['artifact_id'],local_segmentation_id=local['segmentation_id'],
        strategy=strategy,fft_size=actual_fft,requested_fft_size=fft_size,threshold_db=threshold_db,
        parent_segmentation_level=seg.get('segmentation_level','fine_detection'),
        requested_review_bandwidth_hz=requested_width,actual_review_bandwidth_hz=width,
        review_passband_clamped=width<requested_width,
        requested_subband=dict(center_hz=center,bandwidth_hz=width,output_rate_hz=output_rate_hz,margin_s=margin_s),
        fine_target_frequency_hz=[target_low,target_high],
        context_event_count=context_count,context_only_event_count=context_count-len(mapped),
        count_scope='requested parent time interval intersected with parent frequency and extraction passband; context is excluded',
        local_scale=refined.get('local_scale'),
        requested_parent_interval=[start,stop],offset_s=offset_s,duration_s=duration_s,
        mapped_events=mapped,initial_group_preserved=True,
        reviewed_parent_interval=[sub['parent_sample_start'],sub['actual_parent_sample_stop']],
        group_interval_truncated=stop<group['sample_stop'],
        quality_comparison=dict(initial_events_overlapping_review_window=baseline_count,local_events=len(mapped),
            fragmentation_warning=fragmented,accuracy_improvement='not_measured',
            **packet_diagnostics,
            target_area_ms_mhz=(stop-start)/fs*1000*(target_high-target_low)/1e6,
            event_density_per_ms_mhz=len(mapped)/max((stop-start)/fs*1000*(target_high-target_low)/1e6,1e-12),
            coarse_and_fine_counts_are_not_comparable=is_coarse,
            dense_fine_candidates=len(mapped)>200,
            recommendation='do_not_adopt_without_further_evidence' if fragmented else 'alternative_candidate_requires_validation'),
        warning='Alternative local hypothesis, not automatically better or a protocol label; inspect and reverify. Continuous carriers can contaminate temporal noise estimates.')
    aid=ctx.json_artifact('scene_refinement',value)
    return dict(refinement_id=aid,**{k:v for k,v in value.items() if k!='mapped_events'},
                mapped_events=mapped[:30],total_local_events=len(mapped),event_summary_truncated=len(mapped)>30)
