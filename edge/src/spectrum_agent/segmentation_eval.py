"""Scope-bound boundary evaluation. Pending human labels never become accuracy scores."""
import math


def rectangle(value):
    r = tuple(value[k] for k in ('sample_start','sample_stop','freq_low_hz','freq_high_hz'))
    if not all(isinstance(x,(int,float)) and not isinstance(x,bool) and math.isfinite(x) for x in r) or r[0]>=r[1] or r[2]>=r[3]:
        raise ValueError('Invalid boundary rectangle')
    return r


def clip(a,b):
    r=(max(a[0],b[0]),min(a[1],b[1]),max(a[2],b[2]),min(a[3],b[3]))
    return r if r[0]<r[1] and r[2]<r[3] else None


def area(r):
    return (r[1]-r[0])*(r[3]-r[2]) if r else 0


def union_area(rects):
    """Sweep time slabs and merge frequency intervals; overlapping boxes count once."""
    edges=sorted({x for r in rects for x in r[:2]})
    total=0
    for a,b in zip(edges,edges[1:]):
        intervals=sorted((r[2],r[3]) for r in rects if r[0]<b and r[1]>a)
        width=0;end=None
        for low,high in intervals:
            width+=max(0,high-max(low,end if end is not None else low))
            end=max(high,end if end is not None else high)
        total+=(b-a)*width
    return total


def evaluate(events,case,iou_threshold=.3):
    if not 0<iou_threshold<=1:raise ValueError('IoU threshold must be in (0,1]')
    window=tuple(case['target_samples']+case['target_frequency_hz'])
    rectangle(dict(zip(('sample_start','sample_stop','freq_low_hz','freq_high_hz'),window)))
    if len(events)>2000:raise ValueError('Evaluate local scopes with at most2000 candidates')
    candidates=[r for e in events if (r:=clip(rectangle(e),window))]
    packets=[rectangle(p) for p in case.get('known_packet_estimates',[])]
    packets=[p for p in packets if clip(p,window)==p]
    matched=sum(any(clip(p,e) for e in candidates) for p in packets)
    out=dict(candidate_count=len(candidates),known_packet_reference_count=len(packets),
        known_packet_unmatched_count=len(packets)-matched,
        human_label_status=case.get('human_label_status','pending'),boundary_accuracy=None,
        note='Packet overlap is a partial reference check, not full-burst recall. Fewer candidates alone is not improvement.')
    if case.get('human_label_status')!='complete':return out
    if case.get('label_scope')!='all_observable_activity' or not case.get('reviewer'):
        raise ValueError('Complete labels require reviewer and label_scope=all_observable_activity')
    labels=[rectangle(e) for e in case.get('human_burst_labels',[])]
    if len(labels)>2000:raise ValueError('Too many labels in one local scope')
    if any(clip(r,window)!=r for r in labels):raise ValueError('Human label outside reviewed scope')
    import numpy as np
    from scipy.optimize import linear_sum_assignment
    scores=np.zeros((len(labels),len(candidates)))
    links=np.zeros_like(scores,dtype=bool)
    for i,l in enumerate(labels):
        for j,e in enumerate(candidates):
            intersection=area(clip(l,e));links[i,j]=intersection>0
            scores[i,j]=intersection/(area(l)+area(e)-intersection)
    # Maximize valid one-to-one matches first, IoU only breaks ties.
    rows,cols=linear_sum_assignment((scores>=iou_threshold)*(min(scores.shape,default=0)+1)+scores,maximize=True)
    count=sum(scores[i,j]>=iou_threshold for i,j in zip(rows,cols))
    ua=union_area(candidates);la=union_area(labels);both=union_area(candidates+labels);intersection=max(0,ua+la-both)
    out['boundary_accuracy']=dict(iou_threshold=iou_threshold,one_to_one_matches=int(count),
        missed_labels=len(labels)-int(count),unmatched_candidates=len(candidates)-int(count),
        precision=count/len(candidates) if candidates else None,
        recall=count/len(labels) if labels else None,
        union_area_iou=intersection/both if both else None,
        labels_overlapped_by_multiple_candidates=int(sum(links.sum(axis=1)>1)),
        candidates_overlapping_multiple_labels=int(sum(links.sum(axis=0)>1)),
        scope='Reviewer-complete local scope only; overlap multiplicities are fragmentation/merge diagnostics, not identities.')
    return out


def evaluate_report(report,dataset):
    rows=[]
    for case in dataset['cases']:
        matches=[]
        for r in report.get('scene_refinements',[]):
            if r['capture_id']!=case['capture_id'] or r['parent_group_id']!=case['case']['group_id']:continue
            if r.get('requested_parent_interval')!=case['target_samples']:continue
            f=r.get('fine_target_frequency_hz',[])
            if len(f)!=2 or any(abs(a-b)>.01 for a,b in zip(f,case['target_frequency_hz'])):continue
            if 'context is excluded' not in r.get('count_scope',''):continue
            matches.append(dict(refinement_id=r['evidence_id'],strategy=r['strategy'],fft_size=r['fft_size'],
                threshold_db=r['threshold_db'],evaluation=evaluate(r['mapped_events'],case)))
        rows.append(dict(case=case['case'],capture_id=case['capture_id'],candidates=matches,
                         status='evaluated' if matches else 'no_matching_target_scope'))
    return dict(task_id=report['task_id'],cases=rows,new_rf_capture=False,
                accuracy_improvement='not_measured' if any(c.get('human_label_status')!='complete' for c in dataset['cases']) else 'compare_scope_metrics',
                note='Evaluate only identical capture/parent/time/frequency scope. Unmatched cases are not zero-error detections.')
