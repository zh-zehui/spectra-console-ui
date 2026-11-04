"""Explicit, provenance-preserving continuation of a completed recorded-IQ task."""
import hashlib
import json
import os
from pathlib import Path
from .common import ROOT, Context, DomainError, dump, safe_id, utc


KINDS={'coarse_stft','window_segmentation','ofdm_structure','decoder_input','ble_aux_followup','capture','subband','segmentation','local_segmentation','scene_refinement','scene_review',
       'ble_verification','wifi_verification','ieee154_verification','lte_verification','nr_verification','verification_attempt',
       'features','iq_quality','burst_analysis','sweep_analysis','activity_analysis','priors','hypotheses',
       'segmentation_comparison','segmentation_selection','local_refinement_comparison',
       'investigation_decision','investigation_plan','bluetooth_classic_analysis','region_analysis','frame_localization',
       'drone_video_analysis','drone_hopping_analysis'}


def digest(path):
    h=hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda:stream.read(4*1024*1024),b''):h.update(block)
    return h.hexdigest()


def inspect_source(task_id):
    task_id=safe_id(task_id);run=ROOT/'runs'/task_id
    if not (run/'outcome.json').exists() or not (run/'report.json').exists():
        raise DomainError('INVALID_CONTINUATION','Continue only a finished task with a saved report and outcome')
    outcome=json.loads((run/'outcome.json').read_text())
    if outcome.get('status') not in ('succeeded','partial','cancelled','failed'):
        raise DomainError('INVALID_CONTINUATION','Source task is not terminal')
    report=json.loads((run/'report.json').read_text())
    base=report.get('initial_segmentation_id')
    if not base:raise DomainError('INVALID_CONTINUATION','Source has no initial segmentation to continue')
    segmentation=json.loads((run/'index'/(safe_id(base)+'.json')).read_text())
    base_path=(ROOT/'artifacts'/task_id).resolve()
    if not Path(segmentation['path']).resolve().is_relative_to(base_path):
        raise DomainError('OUT_OF_SCOPE','Source segmentation path is outside its task')
    seg=json.loads(Path(segmentation['path']).read_text())
    if seg.get('segmentation_level')!='coarse_activity' or seg.get('fused_from'):
        raise DomainError('INVALID_CONTINUATION','Continuation requires an original coarse segmentation')
    cap=seg['capture_id'];record=json.loads((run/'index'/(safe_id(cap)+'.json')).read_text())
    for key in ('path','metadata_path'):
        if not Path(record[key]).resolve().is_relative_to(base_path):
            raise DomainError('OUT_OF_SCOPE','Source capture path is outside its task')
    metadata=json.loads(Path(record['metadata_path']).read_text())
    prior=json.loads((run/'continuation.json').read_text()) if (run/'continuation.json').exists() else {}
    ancestors=prior.get('ancestors',[])+[task_id]
    if len(ancestors)>128:raise DomainError('RESOURCE_LIMIT','Continuation chain limited to128 generations; retain saved evidence')
    return dict(task_id=task_id,run=run,base_id=base,capture_id=cap,record=record,metadata=metadata,ancestors=ancestors)


def normalize_request(request):
    source=inspect_source(request['continue_from_task_id'])
    m=source['metadata'];c=request.setdefault('constraints',{})
    previous=json.loads((source['run']/'request.json').read_text()).get('constraints',{}) if (source['run']/'request.json').exists() else {}
    expected=dict(center_hz=previous.get('center_hz',m['center_hz']),span_hz=previous.get('span_hz',m['requested_span_hz']),observation_s=m['duration_s'])
    for key,value in expected.items():
        if key in c and (isinstance(c[key],bool) or not isinstance(c[key],(int,float)) or abs(c[key]-value)>max(abs(value)*1e-9,1e-9)):
            raise DomainError('CONTINUATION_SCOPE','Continuation retains analysis scope; use fresh saved-IQ replay to change analysis range')
        c.setdefault(key,value)
    source_kind='synthetic' if m.get('simulated') else 'usrp'
    if request.get('source',source_kind)!=source_kind:raise DomainError('CONTINUATION_SCOPE','Source origin cannot change')
    request['source']=source_kind
    request['recording_capture_id']=source['capture_id']
    request['continuation_base_id']=source['base_id']
    return request


def attach(ctx,request):
    source=inspect_source(request['continue_from_task_id'])
    if source['task_id']==ctx.task_id or ctx.task_id in source['ancestors']:
        raise DomainError('INVALID_CONTINUATION','Continuation cannot reference itself or form a cycle')
    if digest(source['record']['path'])!=source['record']['sha256']:
        raise DomainError('ARTIFACT_CHANGED','Source IQ checksum no longer matches its registered artifact')
    rows=[json.loads(p.read_text()) for p in sorted((source['run']/'index').glob('*.json'),key=lambda p:p.stat().st_mtime_ns)]
    rows=[r for r in rows if r.get('kind') in KINDS]
    if len(rows)>5000:raise DomainError('RESOURCE_LIMIT','At most5000 inherited evidence artifacts per continuation')
    source_base=(ROOT/'artifacts'/source['task_id']).resolve()
    target_base=ROOT/'artifacts'/ctx.task_id;target_base.mkdir(parents=True,exist_ok=True)
    imported=[]
    for row in rows:
        ctx.check();safe_id(row['artifact_id'])
        original=Path(row['path']).resolve()
        if not original.is_relative_to(source_base) or digest(original)!=row['sha256']:
            raise DomainError('ARTIFACT_CHANGED','Source evidence path or checksum changed: '+row['artifact_id'])
        copied=dict(row,task_id=ctx.task_id,origin_task_id=row.get('origin_task_id',source['task_id']),inherited_from_task=source['task_id'])
        for key in ('path','metadata_path'):
            if key not in row:continue
            original=Path(row[key]).resolve()
            if not original.is_relative_to(source_base):raise DomainError('OUT_OF_SCOPE','Source evidence escaped task')
            target=target_base/original.name
            if target.exists():
                if digest(target)!=digest(original):raise DomainError('ARTIFACT_CHANGED','Conflicting destination artifact')
            else:
                # Tools only read inherited artifacts. New tool outputs always get fresh IDs.
                os.link(original,target)
            copied[key]=str(target)
        dump(ctx.run/'index'/(row['artifact_id']+'.json'),copied)
        imported.append(row['artifact_id'])
    protocol_receipts=inherit_protocol_coverage(ctx,source['task_id'])
    imported.extend(v['plan_id'] for v in protocol_receipts if v['plan_id'] not in imported)
    value=dict(source_task_id=source['task_id'],ancestors=source['ancestors'],capture_id=source['capture_id'],
        capture_sha256=source['record']['sha256'],initial_segmentation_id=source['base_id'],
        imported_artifact_ids=imported,imported_count=len(imported),inherited_protocol_coverage=protocol_receipts,utc=utc(),new_rf_capture=False,
        policy='Historical evidence retained with original IDs and provenance. Current-task call and target budgets reset; old deferrals reopen. Prior checks do not prove whole-region recognition.')
    dump(ctx.run/'continuation.json',value)
    return value


def inherit_protocol_coverage(ctx,source_task_id):
    """Carry validated plan checkpoints with inherited decoder artifacts.

    Exact recording,analysis band and time scope only; never rerun a decoder or
    turn missing/failed units into successful checks. Checkpoint origin is saved.
    """
    source=inspect_recording(source_task_id);source_ctx=Context(source_task_id);receipts=[]
    request_path=ctx.run/'request.json';request=json.loads(request_path.read_text()) if request_path.exists() else {}
    source_request=json.loads((source_ctx.run/'request.json').read_text()) if (source_ctx.run/'request.json').exists() else {};constraints=request.get('constraints',{})
    previous=source_request.get('constraints',{});m=source['metadata'];fs=m['sample_rate_hz']
    if any(abs(constraints.get(k,previous.get(k,0))-previous.get(k,constraints.get(k,0)))>1 for k in ('center_hz','span_hz')):return []
    intent=request.get('task_intent',{});first=round(intent.get('analysis_start_s',0)*fs)
    last=round(intent.get('analysis_stop_s',m.get('duration_s',m['sample_count']/fs))*fs)
    target_capture=ctx.resolve(source['capture_id'])
    if target_capture['sha256']!=source['record']['sha256']:raise DomainError('CONTINUATION_SCOPE','Coverage recording checksum differs')
    for index in (source_ctx.run/'index').glob('*.json'):
        row=json.loads(index.read_text())
        if row.get('kind')!='protocol_plan':continue
        record=source_ctx.resolve(row['artifact_id']);plan=json.loads(Path(record['path']).read_text())
        state_path=source_ctx.run/('protocol-progress-'+row['artifact_id']+'.json')
        if not state_path.exists() or plan.get('capture_id')!=source['capture_id'] or plan.get('sample_start')!=first or plan.get('sample_stop')!=last:continue
        if digest(record['path'])!=record['sha256']:raise DomainError('ARTIFACT_CHANGED','Protocol plan checksum differs')
        state=json.loads(state_path.read_text());indices=[v['unit_index'] for v in state['completed']]
        if len(indices)!=len(set(indices)) or any(isinstance(i,bool) or not isinstance(i,int) or not 0<=i<len(plan['units']) for i in indices):
            raise DomainError('INVALID_CONTINUATION','Protocol checkpoint indices are invalid')
        for completed in state['completed']:
            for key in ('verification_id','subband_id'):
                original=source_ctx.resolve(completed[key]);inherited=ctx.resolve(completed[key])
                if original['sha256']!=inherited['sha256']:raise DomainError('ARTIFACT_CHANGED','Inherited protocol evidence differs')
            verification=json.loads(Path(ctx.resolve(completed['verification_id'])['path']).read_text())
            if verification.get('artifact_id')!=completed['subband_id'] or verification.get('valid_frames')!=completed['valid_frames']:
                raise DomainError('INVALID_CONTINUATION','Checkpoint verification does not match decoder evidence')
        target=ROOT/'artifacts'/ctx.task_id/Path(record['path']).name;target.parent.mkdir(parents=True,exist_ok=True)
        if target.exists():
            if digest(target)!=record['sha256']:raise DomainError('ARTIFACT_CHANGED','Conflicting inherited protocol plan')
        else:os.link(record['path'],target)
        copied=dict(record,task_id=ctx.task_id,path=str(target),origin_task_id=record.get('origin_task_id',source_task_id),inherited_from_task=source_task_id)
        dump(ctx.run/'index'/(record['artifact_id']+'.json'),copied)
        checkpoint=dict(state,inherited_from_task=source_task_id,source_checkpoint_sha256=digest(state_path))
        dump(ctx.run/('protocol-progress-'+record['artifact_id']+'.json'),checkpoint)
        receipts.append(dict(plan_id=record['artifact_id'],source_task_id=source_task_id,source_checkpoint_sha256=checkpoint['source_checkpoint_sha256'],
            completed_units=len(indices),total_units=len(plan['units']),failed_units=len(state['errors'])))
    if (ctx.run/'continuation.json').exists():
        continuation=json.loads((ctx.run/'continuation.json').read_text());continuation['inherited_protocol_coverage']=receipts
        continuation['imported_artifact_ids']=sorted(set(continuation['imported_artifact_ids'])|{v['plan_id'] for v in receipts})
        continuation['imported_count']=len(continuation['imported_artifact_ids']);dump(ctx.run/'continuation.json',continuation)
    return receipts


def imported_ids(ctx):
    path=ctx.run/'continuation.json'
    return set(json.loads(path.read_text())['imported_artifact_ids']) if path.exists() else set()


def normalize_replay(request):
    source=inspect_recording(request['replay_from_task_id']);m=source['metadata'];value=dict(request)
    c=value.setdefault('constraints',{})
    path=ROOT/'runs'/source['task_id']/'request.json'
    previous=json.loads(path.read_text()).get('constraints',{}) if path.exists() else {}
    c.setdefault('center_hz',previous.get('center_hz',m['center_hz']))
    c.setdefault('span_hz',previous.get('span_hz',m['requested_span_hz']))
    for key,expected in dict(observation_s=m['duration_s'],sample_rate_hz=m['sample_rate_hz'],acquisition_center_hz=m['center_hz']).items():
        if key in c and (isinstance(c[key],bool) or not isinstance(c[key],(int,float)) or abs(c[key]-expected)>max(abs(expected)*1e-9,1e-9)):
            raise DomainError('CONTINUATION_SCOPE','Saved-IQ replay cannot change acquisition rate, LO or recorded duration')
        c.setdefault(key,expected)
    from .frequency_scope import describe
    describe(m,c)
    kind='synthetic' if m.get('simulated') else 'usrp'
    if value.get('source',kind)!=kind:raise DomainError('CONTINUATION_SCOPE','Recording origin cannot change')
    value.update(source=kind,recording_capture_id=source['capture_id'])
    return value


def inspect_recording(task_id):
    task_id=safe_id(task_id);run=ROOT/'runs'/task_id
    outcome=run/'outcome.json'
    if not outcome.exists() or json.loads(outcome.read_text()).get('status') not in ('succeeded','partial','failed','cancelled'):
        raise DomainError('INVALID_REPLAY','Replay requires a finished task with saved IQ')
    rows=[json.loads(p.read_text()) for p in (run/'index').glob('*.json')]
    captures=[r for r in rows if r.get('kind')=='capture']
    if len(captures)!=1:raise DomainError('INVALID_REPLAY','Source must contain exactly one saved recording')
    record=captures[0];base=(ROOT/'artifacts'/task_id).resolve()
    for key in ('path','metadata_path'):
        if not Path(record[key]).resolve().is_relative_to(base):raise DomainError('OUT_OF_SCOPE','Recording outside source task')
    return dict(task_id=task_id,record=record,capture_id=record['artifact_id'],
        metadata=json.loads(Path(record['metadata_path']).read_text()))


def attach_replay(ctx,request):
    """Only IQ and acquisition metadata; intentionally do not import old analysis."""
    source=inspect_recording(request['replay_from_task_id'])
    row=source['record']
    if source['task_id']==ctx.task_id:raise DomainError('INVALID_REPLAY','Cannot replay into the source task')
    if digest(row['path'])!=row['sha256']:raise DomainError('ARTIFACT_CHANGED','Original IQ checksum differs')
    original_base=(ROOT/'artifacts'/source['task_id']).resolve()
    destination=ROOT/'artifacts'/ctx.task_id;destination.mkdir(parents=True,exist_ok=True)
    copied=dict(row,task_id=ctx.task_id,origin_task_id=row.get('origin_task_id',source['task_id']))
    for key in ('path','metadata_path'):
        original=Path(row[key]).resolve()
        if not original.is_relative_to(original_base):raise DomainError('OUT_OF_SCOPE','Recording file outside source task')
        target=destination/original.name
        if target.exists():
            if digest(target)!=digest(original):raise DomainError('ARTIFACT_CHANGED','Conflicting replay destination')
        else:os.link(original,target)
        copied[key]=str(target)
    dump(ctx.run/'index'/(row['artifact_id']+'.json'),copied)
    value=dict(source_task_id=source['task_id'],capture_id=row['artifact_id'],capture_sha256=row['sha256'],
        new_rf_capture=False,inherited_analysis=False,utc=utc())
    from .frequency_scope import describe
    value['frequency_scope']=describe(source['metadata'],request.get('constraints'))
    dump(ctx.run/'recording-replay.json',value)
    return value
