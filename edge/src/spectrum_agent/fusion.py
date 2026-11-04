"""Conservative coarse/fine spatial partition; residual uncertainty is never erased."""
import copy
import json
import math
from pathlib import Path
from .common import DomainError
from .scene import read

MAX_FINE=200
MAX_PARENT_PIECES=256
MAX_WORKING=20000


def bounds(g):
    return (g['sample_start'],g['sample_stop'],g['freq_low_hz'],g['freq_high_hz'])


def intersection(a,b):
    q=(max(a[0],b[0]),min(a[1],b[1]),max(a[2],b[2]),min(a[3],b[3]))
    return q if q[0]<q[1] and q[2]<q[3] else None


def subtract(a,b):
    """Disjoint rectangles exactly covering a minus b, including unreviewed bands/time."""
    q=intersection(a,b)
    if q is None:return [a]
    pieces=[(a[0],q[0],a[2],a[3]),(q[1],a[1],a[2],a[3]),
            (q[0],q[1],a[2],q[2]),(q[0],q[1],q[3],a[3])]
    return [p for p in pieces if p[0]<p[1] and p[2]<p[3]]


def partition(parent,events,window,fs,refinement_id):
    """Pure bounded geometry. No protocol assignment or accuracy claim."""
    if not events:return None,'empty_fine_candidates'
    if len(events)>MAX_FINE:return None,'dense_fine_candidates'
    cuts=[]
    for event in events:
        b=bounds(event)
        if not all(isinstance(x,(int,float)) and math.isfinite(x) for x in b) or b[0]>=b[1] or b[2]>=b[3]:
            return None,'invalid_fine_geometry'
        clipped=intersection(b,window)
        if clipped and clipped not in cuts:cuts.append(clipped)
    if not cuts:return None,'no_fine_candidates_inside_parent_review'
    residual=[bounds(parent)]
    for cut in cuts:
        residual=[piece for p in residual for piece in subtract(p,cut)]
        if len(residual)+len(cuts)>MAX_PARENT_PIECES:return None,'partition_piece_budget'
    result=[]
    for role,rects in [('adopted_fine',cuts),('unresolved_residual',residual)]:
        for i,b in enumerate(rects):
            g=copy.deepcopy(parent)
            g.update(group_id=f"{parent['group_id']}_{refinement_id}_{role}_{i}",
                event_id=f"{parent['group_id']}_{refinement_id}_{role}_{i}",
                sample_start=b[0],sample_stop=b[1],freq_low_hz=b[2],freq_high_hz=b[3],
                duration_s=(b[1]-b[0])/fs,bandwidth_hz=b[3]-b[2],
                time_start_s=parent.get('time_start_s',parent['sample_start']/fs)+(b[0]-parent['sample_start'])/fs,
                parent_group_id=parent['group_id'],refinement_id=refinement_id,
                boundary_role=role,protocol='unknown',evidence_level='fine_geometry' if role=='adopted_fine' else 'unresolved_residual',
                boundary_only_hypothesis=True,power_metrics_scope='inherited_parent_not_remeasured',
                requires_fine_analysis=role!='adopted_fine',event_count=1)
            g['event_ids']=[g['event_id']]
            g['source_local_event_ids']=[e.get('event_id') for e in events if role=='adopted_fine' and intersection(bounds(e),window)==b]
            result.append(g)
    return result,'geometry_adopted_with_unresolved_residual'


def fuse(ctx,segmentation_id,refinement_ids,review_id,reason):
    if not isinstance(reason,str) or not 16<=len(reason)<=2000:
        raise DomainError('INVALID_ARGUMENT','Record an evidence-based fusion reason,16..2000 characters')
    if not isinstance(refinement_ids,list) or not 1<=len(refinement_ids)<=8 or len(set(refinement_ids))!=len(refinement_ids):
        raise DomainError('INVALID_ARGUMENT','Supply 1..8 distinct scene refinement IDs')
    base=read(ctx,segmentation_id,'segmentation')
    if base.get('segmentation_level')!='coarse_activity' or base.get('fused_from'):
        raise DomainError('INVALID_ARGUMENT','Fuse the original coarse segmentation, not a previously fused candidate')
    review=read(ctx,review_id,'scene_review')
    reviewed={r['refinement_id'] for r in review.get('refinements',[])}
    if review['segmentation_id']!=segmentation_id or not set(refinement_ids)<=reviewed:
        raise DomainError('STALE_REVIEW','Assess this coarse scene again after all requested refinements')
    rec=ctx.resolve(base['capture_id'])
    meta=json.loads(Path(rec['metadata_path']).read_text());fs=meta['sample_rate_hz']
    parents={g['group_id']:g for g in base['signal_groups']};replacements={};decisions=[];seen=set()
    for aid in refinement_ids:
        ctx.check();r=read(ctx,aid,'scene_refinement')
        if r['parent_segmentation_id']!=segmentation_id or r['capture_id']!=base['capture_id'] or r['parent_group_id'] not in parents:
            raise DomainError('INVALID_ARTIFACT','Refinement must belong to this original parent and recording')
        gid=r['parent_group_id']
        if gid in seen:raise DomainError('INVALID_ARGUMENT','Only one refinement proposal per parent in one fusion')
        seen.add(gid);parent=parents[gid]
        local=read(ctx,r['local_segmentation_id'],'local_segmentation')
        sub=ctx.resolve(r['subband_id']);sm=json.loads(Path(sub['metadata_path']).read_text())
        if sm.get('parent_artifact_id')!=base['capture_id'] or local['capture_id']!=r['subband_id']:
            raise DomainError('INVALID_ARTIFACT','Fine segmentation provenance mismatch')
        start,stop=r['requested_parent_interval']
        window=intersection(bounds(parent),(start,stop,sm['center_hz']-sm['requested_span_hz']/2,sm['center_hz']+sm['requested_span_hz']/2))
        pieces=None;status='review_window_outside_parent'
        if window:pieces,status=partition(parent,r['mapped_events'],window,fs,aid)
        if r.get('quality_comparison',{}).get('fragmentation_warning'):
            pieces=None;status='fine_quality_warning'
        from .local_review import current_quality
        current_packet_quality=current_quality(ctx,r)
        if current_packet_quality['known_packet_unmatched_count']:
            pieces=None;status='known_packet_reference_missed'
        projected=len(base['signal_groups'])+sum(len(p)-1 for p in replacements.values())+(len(pieces)-1 if pieces else 0)
        if projected>MAX_WORKING:pieces=None;status='working_piece_budget'
        if pieces:replacements[gid]=pieces
        decisions.append(dict(refinement_id=aid,parent_group_id=gid,accepted=bool(pieces),
            status=status,fine_candidate_count=len(r['mapped_events']),
            adopted_fine_count=sum(g['boundary_role']=='adopted_fine' for g in pieces or []),
            residual_count=sum(g['boundary_role']=='unresolved_residual' for g in pieces or []),
            parent_retained_whole=not bool(pieces),review_window=window))
        decisions[-1]['current_packet_quality']=current_packet_quality
    groups=[]
    for parent in base['signal_groups']:
        groups.extend(replacements.get(parent['group_id'],[dict(parent,boundary_role='coarse_retained',parent_group_id=parent['group_id'])]))
    value=dict(base,signal_groups=groups,events=[dict(g) for g in groups],
        strategy='conservative_local_boundary_fusion',fused_from=segmentation_id,
        boundary_composition='mixed' if replacements else 'coarse_retained',
        fusion=dict(review_id=review_id,reason=reason,decisions=decisions,
            accepted_parent_count=len(replacements),rejected_parent_count=len(decisions)-len(replacements),
            coarse_support_preserved_by_partition=True,unreviewed_area_preserved=True,
            policy=dict(max_fine_candidates=MAX_FINE,max_parent_pieces=MAX_PARENT_PIECES,max_working_pieces=MAX_WORKING),
            accuracy_improvement='not_measured',
            note='Numerical geometry gate, not validated detection accuracy. Fine rectangles and unresolved residuals partition original support; residuals are uncertainty, not new detected transmitters.'),
        warnings=base.get('warnings',[])+['Mixed boundary hypotheses retain all unresolved coarse area. Inherited power metrics describe the parent; all protocols remain unknown.'])
    aid=ctx.json_artifact('segmentation',value)
    return dict(segmentation_id=aid,source_segmentation_id=segmentation_id,total_groups=len(groups),
        boundary_composition=value['boundary_composition'],fusion=value['fusion'],
        next_action='Compare original and fused candidates, then explicitly select; no automatic replacement')
