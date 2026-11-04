"""Model-planned,checkpointed protocol coverage. No hardware or hidden planner."""
import json,math,hashlib,time,subprocess,sys,os
from collections import Counter
from pathlib import Path
import numpy as np
from .common import DomainError,finite_number,dump
from .dsp import load_iq,extract

PROFILES={'wifi_gr20_siso','wifi_legacy_ofdm','wifi_crafter_all','wifi_crafter_dsss',
    'ble_1m_advertising','ble_aux_1m','ble_aux_2m','ieee802154_oqpsk','lte_pbch','nr_pbch'}


def wifi_centers(low,high,passband=18e6):
    channels=list(range(36,65,4))+list(range(100,145,4))+list(range(149,178,4))
    values=[(2407+5*c)*1e6 for c in range(1,14)]+[(5000+5*c)*1e6 for c in channels]
    return [v for v in values if v-passband/2>=low and v+passband/2<=high]


def read(ctx,plan_id):
    rec=ctx.resolve(plan_id)
    if rec['kind']!='protocol_plan':raise DomainError('INVALID_ARTIFACT','Expected protocol plan')
    return json.loads(Path(rec['path']).read_text())


def state_path(ctx,plan_id):return ctx.run/('protocol-progress-'+plan_id+'.json')


def obligations(ctx,low,high):
    request=json.loads((ctx.run/'request.json').read_text());intent=request.get('task_intent',{})
    if not intent.get('comprehensive_protocol_survey'):return []
    wide=intent.get('wide_wifi_headers_requested',False)
    result=[dict(family='Wi-Fi header' if wide else 'Wi-Fi OFDM',center_hz=c,profiles=['wifi_gr20_siso'] if wide else ['wifi_gr20_siso','wifi_legacy_ofdm','wifi_crafter_all'])
        for c in wifi_centers(low,high)]
    # The installed OFDM receivers have complementary acquisition behavior.
    # Real 2.4GHz replay found legacy-valid packets missed by the other two.
    # Keep that validated receiver and also cover DSSS/CCK,which OFDM cannot test.
    for center in wifi_centers(low,high):
        if 2400e6<=center<2500e6:
            result.append(dict(family='Wi-Fi legacy compatibility',center_hz=center,profiles=['wifi_legacy_ofdm']))
            result.append(dict(family='Wi-Fi DSSS/CCK',center_hz=center,profiles=['wifi_crafter_all','wifi_crafter_dsss']))
    for channel,center in ((37,2402e6),(38,2426e6),(39,2480e6)):
        if low<=center-1e6 and center+1e6<=high:result.append(dict(family='BLE primary',center_hz=center,channel=channel,profiles=['ble_1m_advertising']))
    for channel in range(11,27):
        center=(2405+5*(channel-11))*1e6
        if low<=center-1e6 and center+1e6<=high:result.append(dict(family='IEEE802.15.4',center_hz=center,channel=channel,profiles=['ieee802154_oqpsk']))
    return result


def plan(ctx,capture_id,checks,reason,evidence_ids,window_s=.01,context_s=.002):
    if not isinstance(reason,str) or not 16<=len(reason)<=2000:raise DomainError('INVALID_ARGUMENT','Explain the model-selected channels and profiles')
    if not isinstance(evidence_ids,list) or not 1<=len(evidence_ids)<=12:raise DomainError('INVALID_ARGUMENT','Reference1..12 prior/observation artifacts')
    for aid in evidence_ids:ctx.resolve(aid)
    if not isinstance(checks,list) or not 1<=len(checks)<=64:raise DomainError('INVALID_ARGUMENT','Plan1..64 explicit checks')
    finite_number(window_s,'window_s',.001,.1);finite_number(context_s,'context_s',.001,.02)
    iq,m=load_iq(ctx,capture_id);fs=m['sample_rate_hz'];n=len(iq)
    from .frequency_scope import bounds
    low,high=bounds(m);normalized=[];seen=set()
    request=json.loads((ctx.run/'request.json').read_text());intent=request.get('task_intent',{})
    a=round(intent.get('analysis_start_s',0)*fs);b=min(n,round(intent.get('analysis_stop_s',n/fs)*fs))
    if not 0<=a<b<=n:raise DomainError('INVALID_ARGUMENT','Intent analysis interval must lie in saved IQ')
    for entry in checks:
        if not isinstance(entry,dict) or set(entry)-{'profile','center_hz','bandwidth_hz','output_rate_hz','channel','duplex_mode','ssb_scs_khz','ssb_pattern','reason','nonstandard_center_reason'}:
            raise DomainError('INVALID_ARGUMENT','Unexpected protocol check fields')
        profile=entry.get('profile')
        if profile not in PROFILES:raise DomainError('UNSUPPORTED_PROFILE','Use an installed profile; unsupported protocols remain explicit unknowns')
        center=finite_number(entry.get('center_hz'),'center_hz',low,high)
        width=finite_number(entry.get('bandwidth_hz',18e6 if profile.startswith('wifi_') else None),'bandwidth_hz',1e3,min(high-low,fs*.8))
        rate=finite_number(entry.get('output_rate_hz',20e6 if profile.startswith('wifi_') else None),'output_rate_hz',1.1*width,fs)
        if center-width/2<low or center+width/2>high:raise DomainError('OUT_OF_BAND','Planned extraction exceeds analysis range')
        if not isinstance(entry.get('reason'),str) or len(entry['reason'])<8:raise DomainError('INVALID_ARGUMENT','Each check needs an evidence-based reason')
        if profile.startswith('wifi_'):
            if abs(rate-20e6)>1 or not 17e6<=width<=18.18e6:raise DomainError('INVALID_ARGUMENT','Wi-Fi20 observation requires20MS/s and17..18.18MHz passband')
            if intent.get('comprehensive_protocol_survey') and abs(width-18e6)>1:
                raise DomainError('BASELINE_RECEIVER_SETTINGS','Comprehensive Wi-Fi baseline uses validated18MHz extraction at20MS/s. Omit bandwidth_hz/output_rate_hz for profile defaults. A17/17.5MHz occupied-width estimate is not the extraction filter passband; narrower diagnostics belong after the baseline sweep.')
            technical=wifi_centers(low,high,width)
            if technical and min(abs(center-c) for c in technical)>1000 and len(entry.get('nonstandard_center_reason',''))<16:
                raise DomainError('CHANNEL_ALIGNMENT',json.dumps(dict(message='A wide-envelope center is not necessarily a20MHz Wi-Fi channel center. Use a technical candidate or supply observed nonstandard-center evidence.',candidate_centers_hz=technical)))
        signature=json.dumps(dict({k:v for k,v in entry.items() if k not in ('reason','nonstandard_center_reason')},bandwidth_hz=width,output_rate_hz=rate),sort_keys=True)
        if signature in seen:raise DomainError('INVALID_ARGUMENT','Duplicate check')
        seen.add(signature);normalized.append(dict(entry,bandwidth_hz=width,output_rate_hz=rate))
    required=obligations(ctx,low,high)
    missing=[o for o in required if not any(c['profile'] in o['profiles'] and abs(c['center_hz']-o['center_hz'])<1000
        and ('channel' not in o or c.get('channel')==o['channel']) for c in normalized)]
    if missing:raise DomainError('SURVEY_PLAN_INCOMPLETE',json.dumps(dict(message='A comprehensive survey must cover applicable installed technical candidates,including weak ones. Choose the decoder/order yourself. This is not proof of presence or regional allocation.',missing_candidates=missing)))
    units=[]
    for i,entry in enumerate(normalized):
        block_s=min(.1,36e6/fs,3.5e6/entry['output_rate_hz'])
        step=max(round(window_s*fs),math.floor(block_s/window_s)*round(window_s*fs))
        for segment in m['segments']:
            start=max(a,segment['file_start']);stop=min(b,segment['file_start']+segment['count'])
            for begin in range(start,stop,step):units.append(dict(check_index=i,sample_start=begin,sample_stop=min(stop,begin+step)))
    if not units:raise DomainError('INSUFFICIENT_IQ','No continuous samples in requested interval')
    params=dict(capture_id=capture_id,checks=normalized,reason=reason,evidence_ids=evidence_ids,window_s=window_s,context_s=context_s,
        sample_start=a,sample_stop=b,sample_rate_hz=fs,units=units,gaps=m.get('gaps',[]))
    # An exact retry returns the original plan and checkpoint,not duplicate jobs.
    identity=dict(params);identity.pop('reason');identity.pop('evidence_ids')
    identity['checks']=[{k:v for k,v in c.items() if k not in ('reason','nonstandard_center_reason')} for c in normalized]
    digest=hashlib.sha256(json.dumps(identity,sort_keys=True).encode()).hexdigest()
    for p in (ctx.run/'index').glob('*.json'):
        rec=json.loads(p.read_text())
        if rec.get('kind')=='protocol_plan' and json.loads(Path(rec['path']).read_text()).get('fingerprint')==digest:
            return progress(ctx,rec['artifact_id'])
    params['fingerprint']=digest;aid=ctx.json_artifact('protocol_plan',params)
    dump(state_path(ctx,aid),dict(completed=[],errors=[],plan_id=aid))
    return progress(ctx,aid)


def progress(ctx,plan_id):
    p=read(ctx,plan_id);state=json.loads(state_path(ctx,plan_id).read_text())
    done={v['unit_index'] for v in state['completed']};errors={v['unit_index'] for v in state['errors']}
    by_check=[]
    for i,c in enumerate(p['checks']):
        indices={j for j,u in enumerate(p['units']) if u['check_index']==i}
        completed=[v for v in state['completed'] if v['unit_index'] in indices]
        by_check.append(dict(check_index=i,profile=c['profile'],center_hz=c['center_hz'],planned_units=len(indices),
            valid_frame_observations=sum(v['valid_frames'] for v in completed),header_observations=sum(v['header_observations'] for v in completed),
            completed_units=len(indices&done),failed_units=len(indices&errors),complete=indices<=done))
    return dict(plan_id=plan_id,complete=len(done)==len(p['units']),total_units=len(p['units']),completed_units=len(done),
        failed_units=len(errors),remaining_units=len(p['units'])-len(done),checks=by_check,
        requested_interval=dict(sample_start=p['sample_start'],sample_stop=p['sample_stop']),
        valid_frame_observations=sum(v['valid_frames'] for v in state['completed']),
        header_observations=sum(v['header_observations'] for v in state['completed']),
        decoder_and_extraction_s=sum(v.get('elapsed_s',0) for v in state['completed']),
        processed_iq_samples=sum(p['units'][i]['sample_stop']-p['units'][i]['sample_start'] for i in done),
        missing_units=[i for i in range(len(p['units'])) if i not in done][:12],
        errors=state['errors'][-8:],
        next_action='Inspect errors,then retry_errors=true or revise plan; failed units are not complete' if errors else 'Run run_protocol_batch with max_units=64 on this plan_id until complete' if len(done)<len(p['units']) else 'Coverage complete; assess scene and target unresolved evidence,then report',
        note='Completion means every model-planned profile/channel/time interval was attempted successfully,not every protocol recognized. Zero valid frames is not absence. Hardware gaps and unplanned protocols remain explicit.')


def modern_windows(ctx,sub,unit,p):
    from .wifi_modern import receive_iq
    x,m=load_iq(ctx,sub['artifact_id']);fs=p['sample_rate_hz'];rate=m['sample_rate_hz'];frames=[];headers=[];counts=Counter()
    step=round(p['window_s']*fs)
    for core in range(unit['sample_start'],unit['sample_stop'],step):
        ctx.check();end=min(unit['sample_stop'],core+step)
        lo=max(m.get('valid_output_start',0),int((core-m['parent_sample_start'])/fs*rate)-round(p['context_s']*rate))
        hi=min(len(x),int((end-m['parent_sample_start'])/fs*rate)+round(p['context_s']*rate))
        with ctx.span('batch_wifi_receive'):found,stages=receive_iq(np.asarray(x[lo:hi]),True)
        counts.update(stages['counts'])
        for collection,values in ((frames,found),(headers,stages['headers'])):
            for item in values:
                if 'subband_sample_start' not in item:continue
                e=dict(item);e['subband_sample_start']+=lo;e['subband_sample_stop']+=lo;e['center_hz']=m['center_hz']
                original=m['parent_sample_start']+(e['subband_sample_start']/rate-m.get('filter_group_delay_s',0))*fs
                if core<=original<end:collection.append(e)
    value=dict(artifact_id=sub['artifact_id'],protocol='IEEE802.11',verification_profile='wifi_gr20_siso',
        profile='GR-WiFi20MHz SISO with independently checked wide HT/VHT headers',valid_frames=len(frames),frames=frames,
        verification_stages=dict(counts=dict(counts),headers=headers),simulated=m.get('simulated',False),
        evidence_level='decoded' if frames else 'phy_header_pass' if headers else 'unknown',
        analysis_coverage=dict(input_samples=len(x),processed_samples=len(x),complete=True,core_interval=unit),
        warnings=['Wide SIG is header evidence,not wideband MAC decoding. Original sample positions retained; context-only duplicates excluded.'])
    value['evidence_id']=ctx.json_artifact('wifi_verification',value)
    return value


def isolated_decode(ctx,artifact_id,check,result_path):
    """Different native protocol libraries must not share one Python address space."""
    from .common import ROOT
    command=[sys.executable,'-m','spectrum_agent.protocol_batch','decode',ctx.task_id,artifact_id,json.dumps(check),str(result_path)]
    env=dict(os.environ,SPECTRUM_ROOT=str(ROOT),SPECTRUM_BATCH_PARENT_PID=str(os.getpid()))
    with result_path.with_suffix('.log').open('wb') as log:
        child=subprocess.Popen(command,stdout=log,stderr=log,env=env,cwd=ROOT)
        try:code=child.wait(timeout=120 if check['profile'] in ('lte_pbch','nr_pbch') else 30)
        except subprocess.TimeoutExpired:
            child.kill();child.wait()
            raise DomainError('DECODER_TIMEOUT','Isolated decoder exceeded its limit; interval remains unverified')
    if code!=0 or not result_path.exists():raise DomainError('DECODER_EXIT_ERROR','Isolated decoder exited '+str(code)+'; no false zero-frame success')
    result=json.loads(result_path.read_text())
    if result.get('error'):raise DomainError(result['error']['code'],result['error']['message'])
    return result['result']


def run(ctx,plan_id,max_units=64,retry_errors=False):
    if isinstance(max_units,bool) or not isinstance(max_units,int) or not 1<=max_units<=64:raise DomainError('INVALID_ARGUMENT','max_units1..64')
    if not isinstance(retry_errors,bool):raise DomainError('INVALID_ARGUMENT','retry_errors must be boolean')
    p=read(ctx,plan_id);state=json.loads(state_path(ctx,plan_id).read_text())
    finished={v['unit_index'] for v in state['completed']};failed={v['unit_index'] for v in state['errors']}
    pending=[i for i in range(len(p['units'])) if i not in finished and (retry_errors or i not in failed)]
    for index in pending[:max_units]:
        ctx.check();u=p['units'][index];c=p['checks'][u['check_index']];started=time.perf_counter()
        checkpoint=ctx.run/('protocol-unit-'+plan_id+'-'+str(index)+'.json')
        saved=json.loads(checkpoint.read_text()) if checkpoint.exists() else {}
        try:
            if 'subband' not in saved:
                with ctx.span('batch_extract_subband'):
                    saved['subband']=extract(ctx,p['capture_id'],c['center_hz'],c['bandwidth_hz'],u['sample_start'],u['sample_stop'],c['output_rate_hz'],p['context_s'])
                dump(checkpoint,saved)
            if 'verification' not in saved:
                if c['profile']=='wifi_gr20_siso':
                    saved['verification']=modern_windows(ctx,saved['subband'],u,p)
                else:
                    saved['verification']=isolated_decode(ctx,saved['subband']['artifact_id'],c,checkpoint.with_suffix('.decode.json'))
                if saved['verification'].get('status')=='unsupported':raise DomainError('UNSUPPORTED_PROFILE','Decoder unavailable; this unit remains incomplete')
                if saved['verification'].get('analysis_coverage',{}).get('complete') is False:raise DomainError('INCOMPLETE_DECODER','Decoder did not cover the extraction')
                dump(checkpoint,saved)
            v=saved['verification']
            state['completed'].append(dict(unit_index=index,verification_id=v['evidence_id'],subband_id=saved['subband']['artifact_id'],
                valid_frames=v.get('valid_frames',0),header_observations=len(v.get('verification_stages',{}).get('headers',[])),elapsed_s=time.perf_counter()-started))
            state['errors']=[e for e in state['errors'] if e['unit_index']!=index]
        except DomainError as exc:
            if exc.code in ('CANCELLED','DEADLINE'):raise
            state['errors']=[e for e in state['errors'] if e['unit_index']!=index]
            state['errors'].append(dict(unit_index=index,code=exc.code,message=str(exc)))
        dump(state_path(ctx,plan_id),state)
        ctx.event('protocol_coverage_progress',phase='checkpoint',plan_id=plan_id,total_units=len(p['units']),
            completed_units=len(state['completed']),failed_units=len(state['errors']))
    return progress(ctx,plan_id)


def summary(ctx):
    plans=[]
    for f in (ctx.run/'index').glob('*.json'):
        row=json.loads(f.read_text())
        if row.get('kind')=='protocol_plan':plans.append(progress(ctx,row['artifact_id']))
    return dict(plans=plans,planned=bool(plans),complete=bool(plans) and all(p['complete'] for p in plans),
        recognition_exhaustive=False,note='Planned protocol-time coverage,not proof that all signals have been identified.')


if __name__=='__main__':
    if sys.platform.startswith('linux'):
        import ctypes,signal
        expected=int(os.environ.get('SPECTRUM_BATCH_PARENT_PID','0'))
        if not expected or os.getppid()!=expected:raise SystemExit('Batch parent is no longer alive')
        if ctypes.CDLL(None).prctl(1,signal.SIGKILL)!=0:raise SystemExit('Cannot bind decoder lifetime to parent')
        if os.getppid()!=expected:raise SystemExit('Batch parent exited during startup')
    from .common import Context
    if len(sys.argv)!=6 or sys.argv[1]!='decode':raise SystemExit('Internal fixed decoder entry only')
    ctx=Context(sys.argv[2]);artifact_id=sys.argv[3];check=json.loads(sys.argv[4]);destination=Path(sys.argv[5])
    if destination.parent.resolve()!=ctx.run.resolve() or not destination.name.startswith('protocol-unit-'):
        raise SystemExit('Decoder output must stay in its task')
    from .protocols import verify
    try:
        if check['profile'] not in PROFILES:raise DomainError('UNSUPPORTED_PROFILE','Unsupported batch decoder')
        result=verify(ctx,artifact_id,check['profile'],**{k:check[k] for k in ('channel','duplex_mode','ssb_scs_khz','ssb_pattern') if k in check})
        dump(destination,dict(result=result))
    except DomainError as exc:dump(destination,dict(error=dict(code=exc.code,message=str(exc))))
