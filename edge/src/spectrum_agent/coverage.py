"""Auditable whole-catalog local review. Completion is separate from protocol recognition."""
import json,time
from pathlib import Path
from .common import DomainError
from .scene import read


def progress(ctx,segmentation_id):
    seg=read(ctx,segmentation_id,'segmentation');latest={}
    for p in sorted((ctx.run/'index').glob('*.json'),key=lambda p:p.stat().st_mtime_ns):
        rec=json.loads(p.read_text())
        if rec['kind']!='region_analysis':continue
        value=read(ctx,rec['artifact_id'])
        if value['segmentation_id']==segmentation_id:
            latest[value['group_id']]=dict(value,evidence_id=rec['artifact_id'])
    completed={k for k,v in latest.items() if v.get('full_parent_local_review') and v.get('features')}
    missing=[g['group_id'] for g in seg['signal_groups'] if g['group_id'] not in completed]
    return dict(segmentation_id=segmentation_id,total_regions=len(seg['signal_groups']),
        reviewed_regions=len(seg['signal_groups'])-len(missing),remaining_regions=len(missing),
        remaining_group_ids=missing,all_regions_reviewed=not missing,
        protocol_recognition_complete=False,
        note='Local segmentation and generic characterization of every parent, not exhaustive protocol recognition or calibrated detection recall.')


def review_batch(ctx,segmentation_id,review_id,strategy='local_power_tiles',fft_size=2048,threshold_db=6,max_regions=8):
    from . import scene,dsp,analysis_budget
    if isinstance(max_regions,bool) or not isinstance(max_regions,int) or not 1<=max_regions<=8:
        raise DomainError('INVALID_ARGUMENT','max_regions must be integer1..8')
    seg=read(ctx,segmentation_id,'segmentation')
    pending=set(progress(ctx,segmentation_id)['remaining_group_ids']);results=[];windows=0
    for group in seg['signal_groups']:
        if group['group_id'] not in pending:continue
        if len(results)>=max_regions or windows>=8:break
        ctx.check();started=time.perf_counter()
        cap=ctx.resolve(seg['capture_id']);meta=json.loads(Path(cap['metadata_path']).read_text());fs=meta['sample_rate_hz']
        existing=[];features=[]
        for p in (ctx.run/'index').glob('*.json'):
            rec=json.loads(p.read_text())
            if rec['kind']=='scene_refinement':
                r=read(ctx,rec['artifact_id'])
                if r['parent_segmentation_id']==segmentation_id and r['parent_group_id']==group['group_id']:
                    freq=r.get('fine_target_frequency_hz',[1,0])
                    if freq[0]<=group['freq_low_hz']+1 and freq[1]>=group['freq_high_hz']-1:
                        existing.append(dict(r,evidence_id=rec['artifact_id']))
        position=group['sample_start']
        while position<group['sample_stop'] and windows<8:
            # Walk the union of already reviewed full-frequency intervals, including inherited work.
            covering=[r for r in existing if r['requested_parent_interval'][0]<=position+1 and r['requested_parent_interval'][1]>position]
            if covering:
                r=max(covering,key=lambda r:r['requested_parent_interval'][1]);position=r['requested_parent_interval'][1]
                if not any(f.get('artifact_id')==r['subband_id'] for f in features):
                    with ctx.span('coverage_profile_signal',group_id=group['group_id']):
                        features.append(dsp.features(ctx,r['subband_id']))
                continue
            analysis_budget.enforce(ctx,dict(segmentation_id=segmentation_id,group_id=group['group_id']))
            with ctx.span('refine_signal_region',group_id=group['group_id'],owner='harness_batch_tool'):
                r=scene.refine(ctx,segmentation_id,group['group_id'],
                    'Complete-catalog review requested by user; inspect all parent time/frequency support, retaining unknowns.',
                    [review_id],fft_size,threshold_db,offset_s=(position-group['sample_start']+.25)/fs,
                    duration_s=.02,strategy=strategy,
                    center_hz=(group['freq_low_hz']+group['freq_high_hz'])/2,
                    bandwidth_hz=group['freq_high_hz']-group['freq_low_hz'])
            # Resolve the persisted full refinement rather than a shortened tool projection.
            rec=ctx.resolve(r['refinement_id']);r=dict(read(ctx,rec['artifact_id']),evidence_id=rec['artifact_id'])
            existing.append(r);windows+=1
        done=position>=group['sample_stop']
        value=dict(segmentation_id=segmentation_id,group_id=group['group_id'],capture_id=seg['capture_id'],
            full_parent_local_review=done,features=features,refinement_ids=[r['evidence_id'] for r in existing],
            elapsed_s=time.perf_counter()-started,protocol='unknown',
            note='Profile measurements can include filter context and have their own sample limits. Local review is not protocol confirmation.')
        aid=ctx.json_artifact('region_analysis',value)
        results.append(dict(group_id=group['group_id'],analysis_id=aid,full_parent_local_review=done,
            feature_ids=[f['feature_artifact_id'] for f in features],
            hypotheses=[h for f in features for h in f.get('waveform_hypotheses',[])],
            subband_ids=[r['subband_id'] for r in existing]))
        if not done:break
    state=progress(ctx,segmentation_id)
    ctx.event('catalog_review_progress',phase='progress',reviewed_regions=state['reviewed_regions'],
        total_regions=state['total_regions'],remaining_regions=state['remaining_regions'])
    return dict(progress=state,regions=results,
        next_action='Continue review_activity_batch until remaining_regions=0; use observed profiles and band priors to select protocol tools, then assess frame associations. Do not equate completed review with all signals recognized.')
