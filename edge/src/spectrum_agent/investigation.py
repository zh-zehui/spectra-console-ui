"""Evidence-bound unknown-region queue. No agent loop and no protocol guessing."""
import hashlib
import json
import math
import time
from collections import Counter,defaultdict
from pathlib import Path
from .common import DomainError
from .scene import read

KINDS={'verification_attempt','ble_verification','wifi_verification','ieee154_verification','lte_verification','nr_verification',
       'features','iq_quality','burst_analysis','sweep_analysis','scene_refinement','bluetooth_classic_analysis','frame_localization'}
VERIFIED={'ble_verification','wifi_verification','ieee154_verification','lte_verification','nr_verification'}


def records(ctx):
    return [json.loads(p.read_text()) for p in sorted((ctx.run/'index').glob('*.json'),key=lambda p:p.stat().st_mtime_ns)]


def fingerprint(items):
    return hashlib.sha256(json.dumps(items,sort_keys=True).encode()).hexdigest()


def scope(ctx,artifact_id):
    rec=ctx.resolve(artifact_id)
    if not rec.get('metadata_path'):return None
    m=json.loads(Path(rec['metadata_path']).read_text())
    if rec['kind']=='capture':
        from .frequency_scope import effective_metadata,bounds
        low,high=bounds(effective_metadata(ctx,m))
        return dict(capture_id=artifact_id,sample_start=0,sample_stop=m['sample_count'],
                    freq_low_hz=low,freq_high_hz=high)
    parent=m.get('parent_artifact_id')
    if not parent or ctx.resolve(parent)['kind']!='capture':return None
    return dict(capture_id=parent,sample_start=m['parent_sample_start'],sample_stop=m['actual_parent_sample_stop'],
                freq_low_hz=m['center_hz']-m['requested_span_hz']/2,freq_high_hz=m['center_hz']+m['requested_span_hz']/2)


def overlap(a,b):
    return min(a['sample_stop'],b['sample_stop'])>max(a['sample_start'],b['sample_start']) and min(a['freq_high_hz'],b['freq_high_hz'])>max(a['freq_low_hz'],b['freq_low_hz'])


def verify(ctx,artifact_id,protocol,channel=37,duplex_mode='auto',ssb_scs_khz=30,ssb_pattern=None):
    """Persist successful, zero-frame, unsupported and decoder-error attempts alike."""
    from .protocols import verify as decoder
    area=scope(ctx,artifact_id)
    channel=None if protocol.startswith('wifi_') or protocol in ('lte_pbch','nr_pbch') else channel
    args=dict(artifact_id=artifact_id,protocol=protocol,channel=channel)
    if protocol=='lte_pbch':args['duplex_mode']=duplex_mode
    if protocol=='nr_pbch':
        from .nr import settings
        p=settings(ssb_scs_khz,ssb_pattern,duplex_mode)
        args.update(duplex_mode=p['duplex_mode'],ssb_scs_khz=p['scs_khz'],ssb_pattern=p['pattern'])
    rec=ctx.resolve(artifact_id);meta=json.loads(Path(rec['metadata_path']).read_text()) if rec.get('metadata_path') else {}
    def signature_for(scope,proto,ch,rate,mode='auto',scs=30,pattern=None):
        values=dict(scope=scope,protocol=proto,channel=ch,sample_rate_hz=rate)
        if proto=='lte_pbch':values['duplex_mode']=mode
        if proto=='nr_pbch':
            from .nr import settings
            p=settings(scs,pattern,mode)
            values.update(duplex_mode=p['duplex_mode'],ssb_scs_khz=p['scs_khz'],ssb_pattern=p['pattern'])
        return fingerprint(values)
    signature=signature_for(area,protocol,channel,meta.get('sample_rate_hz'),duplex_mode,ssb_scs_khz,ssb_pattern)
    for rec in records(ctx):
        if rec['kind'] in VERIFIED:
            prior=read(ctx,rec['artifact_id'])
            kind_protocol={'ble_verification':'ble_1m_advertising','wifi_verification':'wifi_legacy_ofdm','ieee154_verification':'ieee802154_oqpsk','lte_verification':'lte_pbch','nr_verification':'nr_pbch'}[rec['kind']]
            kind_protocol=prior.get('verification_profile',kind_protocol)
            prior_source=prior.get('artifact_id')
            if kind_protocol==protocol and prior_source:
                prior_meta=json.loads(Path(ctx.resolve(prior_source)['metadata_path']).read_text())
                prior_channel=None if protocol.startswith('wifi_') or protocol in ('lte_pbch','nr_pbch') else prior.get('channel')
                prior_signature=signature_for(scope(ctx,prior_source),protocol,prior_channel,prior_meta.get('sample_rate_hz'),prior.get('duplex_mode','auto'),prior.get('ssb_scs_khz',30),prior.get('ssb_pattern'))
                if prior_signature==signature:
                    raise DomainError('REPEATED_VERIFICATION','This protocol/window/rate was already attempted, including inherited legacy results; reuse it or change scope with a reason')
        if rec['kind']=='verification_attempt':
            prior=read(ctx,rec['artifact_id'])
            pa=prior['arguments'];pm=ctx.resolve(pa['artifact_id'])
            prior_meta=json.loads(Path(pm['metadata_path']).read_text()) if pm.get('metadata_path') else {}
            prior_signature=signature_for(prior.get('scope'),pa['protocol'],
                None if pa['protocol'].startswith('wifi_') or pa['protocol'] in ('lte_pbch','nr_pbch') else pa.get('channel'),prior_meta.get('sample_rate_hz'),pa.get('duplex_mode','auto'),pa.get('ssb_scs_khz',30),pa.get('ssb_pattern'))
            if prior_signature==signature:
                raise DomainError('REPEATED_VERIFICATION','This protocol/window/rate was already attempted; reuse its evidence or change parameters with a reason')
    start=time.perf_counter();out=None;error=None
    try:
        out=decoder(ctx,**args)
        return out
    except Exception as exc:
        error=exc
        raise
    finally:
        status=('tool_error' if error else 'unsupported' if out.get('status')=='unsupported' else
                'valid_frames' if out.get('valid_frames',0)>0 else 'no_valid_frames')
        attempt=dict(arguments=args,scope=area,status=status,parameter_fingerprint=signature,valid_frames=out.get('valid_frames',0) if out else 0,
            sample_rate_hz=meta.get('sample_rate_hz'),center_hz=meta.get('center_hz'),bandwidth_hz=meta.get('requested_span_hz'),
            requested_parent_interval=[meta.get('requested_sample_start'),meta.get('requested_sample_stop')],
            verification_id=out.get('evidence_id') if out else None,
            error_code=getattr(error,'code',type(error).__name__) if error else (out.get('error_code') if out else None),
            elapsed_s=time.perf_counter()-start,
            verification_stages=out.get('verification_stages') if out else None,
            verification_profile=out.get('verification_profile') if out else None,
            analysis_coverage=out.get('analysis_coverage') if out else None,
            conclusion='This exact protocol/window attempt only; zero frames or errors do not establish absence. Entire region remains unknown.')
        aid=ctx.json_artifact('verification_attempt',attempt)
        if out is not None:out['attempt_id']=aid


def evidence(ctx):
    rows=records(ctx);audited=set();items=[];verified=[]
    for rec in rows:
        if rec['kind']!='verification_attempt':continue
        v=read(ctx,rec['artifact_id']);audited.add(v.get('verification_id'))
        source=ctx.resolve(v['arguments']['artifact_id'])
        m=json.loads(Path(source['metadata_path']).read_text()) if source.get('metadata_path') else {}
        if v['arguments']['protocol'].startswith('wifi_') or v['arguments']['protocol'] in ('lte_pbch','nr_pbch'):v['arguments']['channel']=None
        for key,meta_key in [('sample_rate_hz','sample_rate_hz'),('center_hz','center_hz'),('bandwidth_hz','requested_span_hz')]:
            v.setdefault(key,m.get(meta_key))
        items.append(dict(evidence_id=rec['artifact_id'],kind=rec['kind'],**v))
    for rec in rows:
        if rec['kind'] not in KINDS or rec['kind']=='verification_attempt':continue
        v=read(ctx,rec['artifact_id']);aid=rec['artifact_id']
        if rec['kind'] in VERIFIED:
            verified.append(dict(v,evidence_id=aid))
            if aid in audited:continue
            area=scope(ctx,v['artifact_id'])
            items.append(dict(evidence_id=aid,kind=rec['kind'],scope=area,status='valid_frames' if v['valid_frames'] else 'no_valid_frames',
                protocol=v['protocol'],valid_frames=v['valid_frames'],verification_id=aid,legacy_import=True,
                conclusion='Legacy verification projected from the recorded extraction window; not full-region validation.'))
        elif rec['kind']=='scene_refinement':
            parent=v.get('observed_parent_group') or next(g for g in read(ctx,v['parent_segmentation_id'])['signal_groups'] if g['group_id']==v['parent_group_id'])
            area=dict(parent,capture_id=v['capture_id'])
            interval=v.get('requested_parent_interval',v.get('reviewed_parent_interval',[parent['sample_start'],parent['sample_stop']]))
            area.update(sample_start=interval[0],sample_stop=interval[1])
            if v.get('fine_target_frequency_hz'):
                area.update(freq_low_hz=v['fine_target_frequency_hz'][0],freq_high_hz=v['fine_target_frequency_hz'][1])
            items.append(dict(evidence_id=aid,kind=rec['kind'],scope=area,status='local_analysis',parent_group_id=v['parent_group_id'],parent_segmentation_id=v['parent_segmentation_id']))
        else:
            source=v.get('artifact_id')
            if not source:continue
            area=scope(ctx,source)
            coverage=v.get('coverage',{})
            if not coverage.get('intervals') and v.get('evaluated_samples'):
                coverage=dict(intervals=[dict(sample_start=0,sample_stop=v['evaluated_samples'])])
            if area and coverage.get('intervals'):
                # Generic analysis often inspects only the prefix: never claim the full extraction window.
                m=json.loads(Path(ctx.resolve(source)['metadata_path']).read_text())
                if ctx.resolve(source)['kind']=='capture':
                    intervals=coverage['intervals']
                    area=dict(area,sample_start=min(i['sample_start'] for i in intervals),sample_stop=max(i['sample_stop'] for i in intervals))
                else:
                    pm=json.loads(Path(ctx.resolve(area['capture_id'])['metadata_path']).read_text());fs=pm['sample_rate_hz']
                    intervals=coverage['intervals'];base=m['parent_sample_start'];delay=m.get('filter_group_delay_s',0)
                    area=dict(area,sample_start=max(area['sample_start'],math.floor(base+(min(i['sample_start'] for i in intervals)/m['sample_rate_hz']-delay)*fs)),
                        sample_stop=min(area['sample_stop'],math.ceil(base+(max(i['sample_stop'] for i in intervals)/m['sample_rate_hz']-delay)*fs)))
            items.append(dict(evidence_id=aid,kind=rec['kind'],scope=area,status='local_analysis'))
    return rows,items,verified


def project(ctx,segmentation_id,max_targets=4):
    seg=read(ctx,segmentation_id,'segmentation');all_records,items,verified=evidence(ctx)
    capture_rec=ctx.resolve(seg['capture_id'])
    capture_rate=json.loads(Path(capture_rec['metadata_path']).read_text())['sample_rate_hz']
    from .continuation import imported_ids
    inherited=imported_ids(ctx)
    local_checks={}
    for rec in all_records:
        if rec['kind']!='scene_refinement':continue
        r=read(ctx,rec['artifact_id'])
        if r['parent_segmentation_id']!=segmentation_id:continue
        interval=r.get('requested_parent_interval')
        frequency=r.get('fine_target_frequency_hz')
        if interval and frequency:
            local_checks.setdefault(r['parent_group_id'],[]).append(dict(evidence_id=rec['artifact_id'],
                sample_start=interval[0],sample_stop=interval[1],freq_low_hz=frequency[0],freq_high_hz=frequency[1],
                inherited=rec['artifact_id'] in inherited,origin_task_id=rec.get('origin_task_id',ctx.task_id)))
    groups=seg['signal_groups'];snrs=sorted(g.get('snr_db',0) for g in groups);median=snrs[len(snrs)//2] if snrs else 0
    from .catalog import associate
    links=associate(ctx,[dict(seg,evidence_id=segmentation_id)],verified)
    decisions={};reviewed_catalog=set()
    for rec in all_records:
        if rec['kind']=='region_analysis':
            value=read(ctx,rec['artifact_id'])
            if value['segmentation_id']==segmentation_id and value.get('full_parent_local_review') and value.get('features'):
                reviewed_catalog.add(value['group_id'])
        if rec['kind']=='investigation_decision':
            d=read(ctx,rec['artifact_id'])
            if d['segmentation_id']==segmentation_id:decisions[d['group_id']]=dict(d,evidence_id=rec['artifact_id'])
    result=[];families=defaultdict(list)
    for g in groups:
        relevant=[i for i in items if i.get('scope') and i['scope']['capture_id']==seg['capture_id'] and overlap(g,i['scope'])]
        attempts=[i for i in relevant if i['kind']=='verification_attempt' or i['kind'] in VERIFIED]
        packet=[l for l in links if l['association_level']=='packet_time_frequency_overlap' and any(m['group_id']==g['group_id'] for m in l['candidate_groups'])]
        unlocalized=[l for l in links if l['association_level']=='extraction_window_only' and any(m['group_id']==g['group_id'] for m in l['candidate_groups'])]
        fp=fingerprint(sorted(i['evidence_id'] for i in relevant));decision=decisions.get(g['group_id'])
        prior_decision=bool(decision and decision['evidence_id'] in inherited)
        deferred=bool(decision and not prior_decision and decision['action']=='defer' and decision['target_evidence_fingerprint']==fp)
        from .segmentation_eval import rectangle,clip,union_area,area
        checked=local_checks.get(g['group_id'],[])
        parent=rectangle(g)
        checked_area=union_area([b for c in checked if (b:=clip(rectangle(c),parent))])
        width='narrow' if g['bandwidth_hz']<=3e6 else 'wide' if g['bandwidth_hz']>=10e6 else 'medium'
        duration='short' if g['duration_s']<=.001 else 'long'
        strength='weak' if g.get('snr_db',0)<=median else 'strong'
        family=(width,duration,int((g['freq_low_hz']+g['freq_high_hz'])/2/5e6))
        key=':'.join(map(str,family));families[key].append(g['group_id'])
        status=('valid_frames_overlap' if packet else 'valid_frames_in_extraction_window' if unlocalized else
                'localized_frames_not_associated' if any(a.get('valid_frames',0)>0 for a in attempts) else
                'attempted_no_valid_frame' if any(a['status']=='no_valid_frames' for a in attempts) else
                'decoder_unsupported' if any(a['status']=='unsupported' for a in attempts) else
                'decoder_error' if attempts else 'analyzed_unverified' if relevant else 'unverified')
        row=dict(group_id=g['group_id'],sample_start=g['sample_start'],sample_stop=g['sample_stop'],
            file_time_start_s=g['sample_start']/capture_rate,file_time_stop_s=g['sample_stop']/capture_rate,
            time_reference='file-relative seconds from first saved IQ sample; not hardware-clock seconds',
            freq_low_hz=g['freq_low_hz'],freq_high_hz=g['freq_high_hz'],duration_s=g['duration_s'],bandwidth_hz=g['bandwidth_hz'],snr_db=g.get('snr_db'),
            family_id=key,diversity=[width,duration,strength],evidence_state=status,protocol='unknown',
            catalog_review_completed=g['group_id'] in reviewed_catalog,
            unresolved_reason={'valid_frames_overlap':'frame_overlap_does_not_identify_entire_parent',
                'valid_frames_in_extraction_window':'packet_position_unavailable',
                'localized_frames_not_associated':'localized_window_frames_not_associated_with_this_parent',
                'attempted_no_valid_frame':'tested_protocol_profiles_returned_no_valid_frame',
                'analyzed_unverified':'generic_features_do_not_uniquely_identify_protocol'}.get(status,status),
            investigation_state='deferred' if deferred else 'pending',
            target_evidence_fingerprint=fp,relevant_evidence_ids=sorted({aid for i in relevant for aid in [i['evidence_id'],i.get('verification_id')] if aid}),
            analysis_scope='Overlapping analysis/extraction windows; features may include context or other signals, not an isolated target measurement.',
            verification_attempts=attempts,packet_overlap_count=len(packet),
            decision=decision,decision_stale=bool(decision and decision['target_evidence_fingerprint']!=fp),
            reopened_prior_deferral=bool(prior_decision and decision['action']=='defer'),
            local_checked_windows=checked,local_checked_area_fraction=checked_area/area(parent),
            local_coverage_note='Geometric union of requested local windows inside this coarse envelope; not recognition accuracy or signal-energy coverage.',
            next_action='Inspect unexplained activity; choose a supported decoder from observed evidence, or profile signal features. Do not repeat identical failed parameters; frame overlap never confirms whole region.')
        result.append(row)
    # First choose distinct width/duration/strength strata, then distinct similarity families.
    pending=[r for r in result if r['investigation_state']=='pending'];selected=[];used_strata=set();used_families=set()
    locally_reviewed={i['parent_group_id'] for i in items if i['kind']=='scene_refinement' and i['parent_segmentation_id']==segmentation_id}
    pending.sort(key=lambda r:(r['group_id'] in locally_reviewed,bool(r['verification_attempts']),bool(r['packet_overlap_count']),r['group_id']))
    for reviewed in (False,True):
        for diverse in (True,False):
            for r in pending:
                if len(selected)>=max_targets:break
                if (r['group_id'] in locally_reviewed)!=reviewed:continue
                if r['family_id'] in used_families:continue
                stratum=tuple(r['diversity'])
                if diverse and stratum in used_strata:continue
                selected.append(r);used_strata.add(stratum);used_families.add(r['family_id'])
    return dict(segmentation_id=segmentation_id,capture_id=seg['capture_id'],total_regions=len(groups),
        states=dict(Counter(r['evidence_state'] for r in result)),deferred_count=sum(r['investigation_state']=='deferred' for r in result),
        selected_targets=selected,regions=result,families=[dict(family_id=k,group_ids=v,member_count=len(v)) for k,v in families.items()],
        local_check_coverage=dict(parents_with_local_checks=sum(bool(r['local_checked_windows']) for r in result),
            parents_without_local_checks=sum(not r['local_checked_windows'] for r in result),
            reopened_prior_deferrals=sum(r['reopened_prior_deferral'] for r in result),
            note='Packet-window overlap is not local DSP coverage or whole-region protocol confirmation.'),
        evidence_snapshot=sorted([r['artifact_id'],r['sha256']] for r in all_records if r['kind'] in KINDS),
        note='Similarity groups schedule representative checks, not protocols or transmitters. Attempt/window overlap is not full-region coverage. Deferral retains unknowns; fresh relevant evidence reopens a deferred target.')


def plan(ctx,segmentation_id,max_targets=4):
    if isinstance(max_targets,bool) or not isinstance(max_targets,int) or not 1<=max_targets<=8:
        raise DomainError('INVALID_ARGUMENT','max_targets must be integer1..8')
    path=ctx.run/'request.json';request=json.loads(path.read_text()) if path.exists() else {}
    max_targets=min(max_targets,request.get('budgets',{}).get('max_candidates',8))
    value=project(ctx,segmentation_id,max_targets)
    aid=ctx.json_artifact('investigation_plan',value)
    from .analysis_budget import snapshot
    return dict(queue_id=aid,analysis_budget=snapshot(ctx,compact=True),**{k:v for k,v in value.items() if k not in ('regions','families','evidence_snapshot')},
        family_count=len(value['families']))


def decide(ctx,queue_id,group_id,action,reason,evidence_ids,next_step=None):
    if action not in ('continue','defer'):raise DomainError('INVALID_ARGUMENT','action must be continue or defer; neither confirms a protocol')
    if not isinstance(reason,str) or not 16<=len(reason)<=1600:raise DomainError('INVALID_ARGUMENT','Evidence-based reason must be16..1600 characters')
    if not isinstance(evidence_ids,list) or len(evidence_ids)>8 or not all(isinstance(i,str) for i in evidence_ids):
        raise DomainError('INVALID_ARGUMENT','Supply up to8 task-owned relevant evidence IDs')
    if not isinstance(next_step,str) or not 8<=len(next_step)<=1000:raise DomainError('INVALID_ARGUMENT','Record next step or missing evidence,8..1000 characters')
    queue=read(ctx,queue_id,'investigation_plan')
    request_path=ctx.run/'request.json'
    request=json.loads(request_path.read_text()) if request_path.exists() else {}
    limit=request.get('budgets',{}).get('max_candidates',4096) if request.get('budgets',{}).get('coverage_mode')=='complete' else min(8,request.get('budgets',{}).get('max_candidates',8))
    from .continuation import imported_ids
    inherited=imported_ids(ctx)
    previous={d['group_id'] for rec in records(ctx) if rec['kind']=='investigation_decision' and rec['artifact_id'] not in inherited
        and (d:=read(ctx,rec['artifact_id']))['segmentation_id']==queue['segmentation_id']}
    if group_id not in previous and len(previous)>=limit:
        raise DomainError('RESOURCE_LIMIT','Distinct investigation targets reached the task candidate budget; retain remaining unknowns')
    current=project(ctx,queue['segmentation_id'],1)
    if current['evidence_snapshot']!=queue['evidence_snapshot']:
        raise DomainError('STALE_QUEUE','Analysis evidence changed; refresh plan_unknown_investigations before recording the decision')
    target=next((r for r in current['regions'] if r['group_id']==group_id),None)
    if target is None:raise DomainError('NOT_FOUND','Group not present in this queue')
    if not set(evidence_ids)<=set(target['relevant_evidence_ids']):
        raise DomainError('UNRELATED_EVIDENCE','Evidence must overlap this target in the same original capture')
    if action=='continue' and not evidence_ids:raise DomainError('INSUFFICIENT_EVIDENCE','Continue decisions require observed target evidence')
    value=dict(segmentation_id=queue['segmentation_id'],queue_id=queue_id,group_id=group_id,action=action,
        authoritative_scope={k:target[k] for k in ('sample_start','sample_stop','file_time_start_s','file_time_stop_s','freq_low_hz','freq_high_hz','time_reference')},
        reason=reason,evidence_ids=evidence_ids,next_step=next_step,
        target_evidence_fingerprint=target['target_evidence_fingerprint'],
        protocol='unknown',whole_region_identified=False,decision_owner='deepseek_harness',reason_is_model_authored=True,
        narrative_validation='Model-authored rationale, not independently validated; numerical measurements and exact coverage remain authoritative.')
    aid=ctx.json_artifact('investigation_decision',value)
    return dict(decision_id=aid,**value)
