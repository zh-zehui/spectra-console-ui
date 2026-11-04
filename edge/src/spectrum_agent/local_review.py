"""Comparable local scopes and current packet-reference diagnostics, never a ground-truth score."""
import json
from statistics import median
from pathlib import Path
from .common import DomainError


def packet_quality(ctx,capture_id,start,stop,low,high,mapped):
    from .catalog import associate
    verified=[]
    for p in (ctx.run/'index').glob('*.json'):
        rec=json.loads(p.read_text())
        if rec['kind'] in ('wifi_verification','ble_verification','ieee154_verification','lte_verification','nr_verification'):
            verified.append(dict(json.loads(Path(rec['path']).read_text()),evidence_id=rec['artifact_id']))
    links=associate(ctx,[],verified)
    eligible=[l for l in links if l['association_level']=='packet_time_frequency_overlap' and l['capture_id']==capture_id
        and start<=l['sample_start'] and l['sample_stop']<=stop and low<=l['freq_low_hz'] and l['freq_high_hz']<=high]
    unmatched=0;ious=[];expansions=[]
    for l in eligible:
        matches=[e for e in mapped if min(l['sample_stop'],e['sample_stop'])>max(l['sample_start'],e['sample_start']) and min(l['freq_high_hz'],e['freq_high_hz'])>max(l['freq_low_hz'],e['freq_low_hz'])]
        if not matches:unmatched+=1;continue
        ious.append(max((min(l['sample_stop'],e['sample_stop'])-max(l['sample_start'],e['sample_start']))/
            max(1,max(l['sample_stop'],e['sample_stop'])-min(l['sample_start'],e['sample_start'])) for e in matches))
        expansions.append(min((e['sample_stop']-e['sample_start'])/max(1,l['sample_stop']-l['sample_start']) for e in matches))
    return dict(known_packet_reference_count=len(eligible),known_packet_unmatched_count=unmatched,
        verification_snapshot=sorted(v['evidence_id'] for v in verified),
        packet_time_diagnostics=dict(median_best_time_iou=median(ious) if ious else None,
            median_best_time_expansion=median(expansions) if expansions else None,
            warning='Partial packet reference only. Wi-Fi control frames do not define a whole activity burst; do not optimize only this IoU.'),
        known_packet_reference_scope='Fully contained packet position estimates and decoder passbands only; not burst ground truth or calibrated recall')


def current_quality(ctx,r):
    from .scene import read
    sm=json.loads(Path(ctx.resolve(r['subband_id'])['metadata_path']).read_text())
    interval=r.get('requested_parent_interval') or r.get('reviewed_parent_interval')
    if not interval:raise DomainError('INVALID_ARTIFACT','Refinement has no requested or reviewed interval')
    start,stop=interval
    return packet_quality(ctx,r['capture_id'],start,stop,sm['center_hz']-sm['requested_span_hz']/2,
        sm['center_hz']+sm['requested_span_hz']/2,r['mapped_events'])


def compare(ctx,refinement_ids):
    from .scene import read
    if not isinstance(refinement_ids,list) or not 1<=len(refinement_ids)<=3 or len(set(refinement_ids))!=len(refinement_ids):
        raise DomainError('INVALID_ARGUMENT','Provide1..3 distinct local refinement IDs')
    refs=[read(ctx,aid,'scene_refinement') for aid in refinement_ids]
    def scope(r):
        sm=json.loads(Path(ctx.resolve(r['subband_id'])['metadata_path']).read_text())
        return (r['capture_id'],r['parent_segmentation_id'],r['parent_group_id'],r.get('requested_parent_interval'),
                r.get('fine_target_frequency_hz'),sm['center_hz'],sm['requested_span_hz'])
    if any(scope(r)!=scope(refs[0]) for r in refs):
        raise DomainError('INCOMPARABLE_SCOPE','Compare identical parent, requested time and frequency scopes; do not count narrower windows as accuracy improvements')
    if any(not r.get('count_scope') for r in refs):
        raise DomainError('INCOMPARABLE_SCOPE','Legacy context-inclusive counts require reanalysis or explicit clipping before comparison')
    rows=[]
    for aid,r in zip(refinement_ids,refs):
        q=current_quality(ctx,r)
        rows.append(dict(refinement_id=aid,strategy=r['strategy'],fft_size=r['fft_size'],threshold_db=r['threshold_db'],
            target_events=len(r['mapped_events']),context_events=r['context_event_count'],
            local_scale=r.get('local_scale'),quality=dict(r['quality_comparison'],**q),
            source_subband_id=r['subband_id']))
    value=dict(parent_segmentation_id=refs[0]['parent_segmentation_id'],parent_group_id=refs[0]['parent_group_id'],
        requested_parent_interval=refs[0]['requested_parent_interval'],target_frequency_hz=refs[0]['fine_target_frequency_hz'],
        candidates=rows,accuracy_improvement='not_measured',human_reference_status='pending',
        warning='No automatic winner. Fewer candidates can mean lost activity; packet overlap is only a partial reference. Keep originals, raw IQ and unresolved residuals.')
    aid=ctx.json_artifact('local_refinement_comparison',value)
    return dict(local_comparison_id=aid,**value)
