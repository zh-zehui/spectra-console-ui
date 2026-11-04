"""Validate the official Harness model's structured interpretation, never parse prose."""
import json
import time
from .common import DomainError,dump,finite_number


def resolve(ctx,center_hz=None,span_hz=None,evidence_quotes=None,reason=None,targets=None,
            sample_rate_hz=None,observation_s=None,acquisition_center_hz=None,deadline_s=None,task_kind='spectrum_task',analysis_requested=True,
            source_task_id=None,visualization_requested=False,acquisition_requested=True,analysis_window_s=None,inherit_analysis=False,
            protocol_analysis_requested=None,analysis_start_s=0,analysis_stop_s=None,full_local_review_requested=False,
            comprehensive_protocol_survey=None,wide_wifi_headers_requested=False,windowed_analysis_requested=False,
            time_subset_requested=False,analysis_time_evidence_quote=None,drone_video_analysis_requested=False,drone_hopping_analysis_requested=False):
    from .service import normalize,dsh_prompt
    path=ctx.run/'request.json'
    if not path.exists():raise DomainError('TASK_INTENT_REQUIRED','Task request is missing')
    request=json.loads(path.read_text())
    if request.get('task_intent',{}).get('status')!='pending_model':
        raise DomainError('INTENT_ALREADY_RESOLVED','Task intent cannot change after resolution; submit a fresh task')
    prompt=request['prompt']
    if not isinstance(windowed_analysis_requested,bool):raise DomainError('INVALID_ARGUMENT','windowed_analysis_requested must be boolean')
    if windowed_analysis_requested and analysis_window_s is None:raise DomainError('INVALID_ARGUMENT','Requested windowed analysis needs explicit analysis_window_s;10ms=0.01')
    if protocol_analysis_requested is None:protocol_analysis_requested=analysis_requested
    if comprehensive_protocol_survey is None:comprehensive_protocol_survey=protocol_analysis_requested and request.get('budgets',{}).get('coverage_mode')=='complete'
    if not isinstance(comprehensive_protocol_survey,bool) or not isinstance(wide_wifi_headers_requested,bool):raise DomainError('INVALID_ARGUMENT','Survey and wide header intent flags must be boolean')
    if not isinstance(protocol_analysis_requested,bool) or not isinstance(full_local_review_requested,bool):
        raise DomainError('INVALID_ARGUMENT','Protocol and full-local-review intent flags must be boolean')
    if not isinstance(drone_video_analysis_requested,bool):raise DomainError('INVALID_ARGUMENT','Drone-video analysis flag must be boolean')
    if not isinstance(drone_hopping_analysis_requested,bool):raise DomainError('INVALID_ARGUMENT','Drone-hopping flag must be boolean')
    if drone_hopping_analysis_requested:drone_video_analysis_requested=True
    if drone_video_analysis_requested and not analysis_requested:raise DomainError('INVALID_ARGUMENT','Drone waveform checks are signal analysis')
    if protocol_analysis_requested and not analysis_requested:raise DomainError('INVALID_ARGUMENT','Protocol recognition is signal analysis')
    if not isinstance(inherit_analysis,bool):raise DomainError('INVALID_ARGUMENT','inherit_analysis must be boolean')
    if inherit_analysis and not source_task_id:raise DomainError('INVALID_ARGUMENT','Continuing analysis requires an explicit source_task_id')
    if analysis_window_s is not None:finite_number(analysis_window_s,'analysis_window_s',.001,.1)
    if not isinstance(visualization_requested,bool) or not isinstance(acquisition_requested,bool):
        raise DomainError('INVALID_ARGUMENT','Acquisition and visualization flags must be boolean')
    if source_task_id is not None:
        from .common import safe_id
        safe_id(source_task_id)
        if source_task_id not in prompt:
            raise DomainError('INVALID_INTENT_EVIDENCE','Source task ID must occur in the original prompt')
        # Persist RF prohibition before any source lookup, including failed lookups.
        request.update(rf_capture_forbidden=True)
        dump(path,request)
        from .continuation import inspect_recording,attach_replay,normalize_request,attach
        source=inspect_recording(source_task_id);m=source['metadata']
        previous=json.loads((ctx.run.parent/source_task_id/'request.json').read_text()).get('constraints',{})
        if center_hz is None:center_hz=previous.get('center_hz',m['center_hz'])
        if span_hz is None:span_hz=previous.get('span_hz',m['requested_span_hz'])
        request.update(recording_capture_id=source['capture_id'])
        request['constraints'].update(center_hz=center_hz,span_hz=span_hz,sample_rate_hz=m['sample_rate_hz'],observation_s=m['duration_s'],acquisition_center_hz=m['center_hz'])
        sample_rate_hz=m['sample_rate_hz'];observation_s=m['duration_s'];acquisition_requested=False
        if inherit_analysis:
            request.pop('replay_from_task_id',None)
            request['continue_from_task_id']=source_task_id
            request=normalize_request(request)
            attach(ctx,request)
        else:
            request.pop('continue_from_task_id',None)
            request['replay_from_task_id']=source_task_id
            attach_replay(ctx,request)
    if not acquisition_requested:
        request['rf_capture_forbidden']=True
        dump(path,request)
    if not isinstance(task_kind,str) or not 1<=len(task_kind)<=100 or not isinstance(analysis_requested,bool):
        raise DomainError('INVALID_ARGUMENT','Task description must be text and analysis_requested must be boolean')
    if not isinstance(evidence_quotes,list) or not 1<=len(evidence_quotes)<=12 or any(
            not isinstance(q,str) or not q.strip() or len(q)>2000 or q not in prompt for q in evidence_quotes):
        raise DomainError('INVALID_INTENT_EVIDENCE','Provide1..12 exact nonempty quotes from the complete original prompt')
    if not isinstance(reason,str) or not 8<=len(reason)<=1500:
        raise DomainError('INVALID_ARGUMENT','Give a concise public explanation of scope versus target interpretation')
    targets=[] if targets is None else targets
    if not isinstance(targets,list) or len(targets)>32:raise DomainError('INVALID_ARGUMENT','At most32 target hypotheses')
    normalized_targets=[]
    for target in targets:
        if not isinstance(target,dict):raise DomainError('INVALID_ARGUMENT','Target must be an object')
        label=target.get('label');low=target.get('frequency_low_hz');high=target.get('frequency_high_hz')
        if not isinstance(label,str) or not 1<=len(label)<=300:raise DomainError('INVALID_ARGUMENT','Target label required')
        normalized=dict(target,source='user_reported',protocol_confirmed=False)
        if low is not None or high is not None:
            finite_number(low,'target low frequency',1e6,6e9);finite_number(high,'target high frequency',1e6,6e9)
            if high<=low:raise DomainError('INVALID_ARGUMENT','Target end must exceed start')
            width=target.get('bandwidth_hz',high-low)
            finite_number(width,'target bandwidth',1,200e6)
            if abs(width-(high-low))>max(1.,width*1e-6):
                raise DomainError('INVALID_ARGUMENT','Target bandwidth conflicts with its frequency range')
            normalized['bandwidth_hz']=width
        normalized_targets.append(normalized)
    original=dict(request['constraints']);c=request['constraints']
    c.update(center_hz=center_hz,span_hz=span_hz)
    replay=bool(request.get('recording_capture_id'))
    if not replay:c['acquisition_center_hz']=center_hz if acquisition_center_hz is None else acquisition_center_hz
    elif acquisition_center_hz is not None and abs(acquisition_center_hz-c.get('acquisition_center_hz',acquisition_center_hz))>1:
        raise DomainError('REPLAY_SCOPE','Saved acquisition LO cannot change')
    if sample_rate_hz is not None:c['sample_rate_hz']=sample_rate_hz
    if observation_s is not None:c['observation_s']=observation_s
    prior_deadline=request['budgets']['deadline_s'];budget_source=request.get('budget_resolution',{}).get('source')
    duration=c['observation_s']
    analysis_stop_s=duration if analysis_stop_s is None else analysis_stop_s
    if not isinstance(time_subset_requested,bool):raise DomainError('INVALID_ARGUMENT','time_subset_requested must be boolean')
    if time_subset_requested:
        if not isinstance(analysis_time_evidence_quote,str) or not analysis_time_evidence_quote or analysis_time_evidence_quote not in prompt:
            raise DomainError('INVALID_INTENT_EVIDENCE','A requested time subset needs an exact supporting quote from the ORIGINAL prompt')
    elif analysis_start_s!=0 or abs(analysis_stop_s-duration)>1e-9:
        raise DomainError('UNREQUESTED_TIME_CROP',f'No explicit time subset was requested: analyze the ENTIRE actual saved duration0..{duration}s. Omit analysis_stop_s or use that actual duration,not a provisional UI/default duration.')
    finite_number(analysis_start_s,'analysis_start_s',0,duration)
    finite_number(analysis_stop_s,'analysis_stop_s',0,duration)
    if analysis_stop_s<=analysis_start_s:raise DomainError('INVALID_ARGUMENT','Analysis stop must exceed start')
    if deadline_s is not None:
        if budget_source=='explicit_budget' and abs(deadline_s-prior_deadline)>1e-9:
            raise DomainError('TASK_CONSTRAINT','Explicit API execution deadline cannot be changed')
        request['budgets']['deadline_s']=deadline_s
    resolved=normalize(request,model_resolved=True)
    if 'deadline_unix' in request:
        resolved['deadline_unix']=request['deadline_unix']-prior_deadline+resolved['budgets']['deadline_s']
    value=dict(status='resolved',task_kind=task_kind,analysis_requested=analysis_requested,interpreter='official_dsh_model',original_prompt=prompt,
        protocol_workflow_version=2,protocol_analysis_requested=protocol_analysis_requested,
        drone_video_analysis_requested=drone_video_analysis_requested,
        drone_hopping_analysis_requested=drone_hopping_analysis_requested,
        comprehensive_protocol_survey=comprehensive_protocol_survey,wide_wifi_headers_requested=wide_wifi_headers_requested,
        time_subset_requested=time_subset_requested,analysis_time_evidence_quote=analysis_time_evidence_quote,
        analysis_start_s=analysis_start_s,analysis_stop_s=analysis_stop_s,full_local_review_requested=full_local_review_requested,
        analysis_window_s=analysis_window_s,
        inherit_analysis=inherit_analysis,
        acquisition_requested=acquisition_requested,visualization_requested=visualization_requested,source_task_id=source_task_id,
        form_defaults=original,effective_parameters=resolved['constraints'],targets=normalized_targets,
        evidence_quotes=evidence_quotes,reason=reason,resolved_unix=time.time())
    resolved['task_intent']=value
    resolved['budget_resolution']['source']='model_prompt' if deadline_s is not None and budget_source!='explicit_budget' else budget_source
    resolved['analysis_scope_resolution']['source']='official_dsh_model'
    resolved['signal_parameter_resolution']['prompt_parameters']={k:resolved['constraints'].get(k)
        for k in ('center_hz','span_hz','sample_rate_hz','observation_s')}
    aid=ctx.json_artifact('task_intent',value)
    resolved['task_intent']['evidence_id']=aid
    dump(path,resolved)
    ctx.event('task_intent_resolved',phase='result',center_hz=center_hz,span_hz=span_hz,
        target_count=len(normalized_targets),evidence_id=aid)
    instructions=dsh_prompt(resolved)
    if analysis_window_s is not None and analysis_requested and not inherit_analysis:
        instructions+=f'\nUSER WINDOW OVERRIDE: Instead of whole-record coarse_segment_spectrum, call analyze_spectrum_windows(window_s={analysis_window_s},capture_id=<saved capture>) repeatedly with identical parameters until complete=true. Its final segmentation is the original coarse parent for all subsequent review and protocol tools. Core windows cover the recording; context preserves cross-window bursts. Protocol extraction uses original sample coordinates and margins. Never interpret a10ms window boundary as a packet boundary.'
    if resolved['budgets'].get('coverage_mode')=='complete' and analysis_requested and ((not protocol_analysis_requested and not drone_video_analysis_requested) or full_local_review_requested):
        instructions+='\nFor a complete analysis request, every original coarse activity region needs full-time/full-frequency review and generic features. Use review_activity_batch checkpoints until remaining_regions=0 before requested_checks_complete. Choose protocol tools from evidence; keep unsupported or unresolved signals explicit. Do not apply this analysis workflow to recording-only requests.'
    if resolved.get('intent_only'):
        instructions='Interpretation-only acceptance. Do not inspect hardware, acquire RF, look up priors or process IQ. Call build_report(stop_reason="other") and summarize the resolved global range versus target hints.'
    return dict(evidence_id=aid,task_intent=resolved['task_intent'],
        recording_capture_id=resolved.get('recording_capture_id'),
        continuation_base_id=resolved.get('continuation_base_id'),
        analysis_scope=resolved['analysis_scope_resolution'],execution_instructions=instructions)
