"""Checkpointed time-window detection in ORIGINAL sample coordinates, with context."""
import hashlib,json,math
from pathlib import Path
from .common import DomainError,finite_number,dump
from .dsp import load_iq


def stitch(windows,segments,fs):
    result=[]
    for number,window in enumerate(windows):
        for source in window['events']:
            if min(source['sample_stop'],window['core_stop'])<=max(source['sample_start'],window['core_start']):continue
            e=dict(source)
            e['segment_file_start']=next(s['file_start'] for s in segments if s['file_start']<=e['sample_start']<s['file_start']+s['count'])
            ref=dict(window_index=number,segmentation_id=window['segmentation_id'],group_id=e['group_id'])
            matches=[]
            for old in reversed(result):
                if old['last_window']!=number-1 or old['segment_file_start']!=e['segment_file_start']:continue
                overlap=min(old['sample_stop'],e['sample_stop'])-max(old['sample_start'],e['sample_start'])
                intersection=max(0,min(old['freq_high_hz'],e['freq_high_hz'])-max(old['freq_low_hz'],e['freq_low_hz']))
                union=max(old['freq_high_hz'],e['freq_high_hz'])-min(old['freq_low_hz'],e['freq_low_hz'])
                if overlap>0 and intersection/max(union,1)>=.8:matches.append(old)
            if len(matches)==1:
                old=matches[0];old.update(sample_start=min(old['sample_start'],e['sample_start']),sample_stop=max(old['sample_stop'],e['sample_stop']),
                    freq_low_hz=min(old['freq_low_hz'],e['freq_low_hz']),freq_high_hz=max(old['freq_high_hz'],e['freq_high_hz']),last_window=number)
                old['window_members'].append(ref)
            else:
                e.update(group_id='g'+str(len(result)),event_id='e'+str(len(result)),last_window=number,window_members=[ref])
                result.append(e)
    for e in result:
        e.pop('last_window');e.update(duration_s=(e['sample_stop']-e['sample_start'])/fs,bandwidth_hz=e['freq_high_hz']-e['freq_low_hz'],
            event_ids=[e['event_id']],boundary_source='windowed_energy_envelope',boundary_uncertainty_calibrated=False,
            cross_window_association_is_hypothesis=len(e['window_members'])>1)
    return result


def analyze(ctx,capture_id,window_s=.01,context_s=.002,max_windows=64,fft_size=2048,
            time_bin_s=.00002,frequency_bin_hz=1e6,threshold_db=6,sample_start=0,sample_stop=None):
    finite_number(window_s,'window_s',.001,.1);finite_number(context_s,'context_s',0,.01)
    if isinstance(max_windows,bool) or not isinstance(max_windows,int) or not 1<=max_windows<=64:
        raise DomainError('INVALID_ARGUMENT','max_windows must be1..64')
    iq,m=load_iq(ctx,capture_id);fs=m['sample_rate_hz'];n=len(iq);sample_stop=n if sample_stop is None else sample_stop
    if any(isinstance(v,bool) or not isinstance(v,int) for v in (sample_start,sample_stop)) or not 0<=sample_start<sample_stop<=n:
        raise DomainError('INVALID_ARGUMENT','Source sample bounds required')
    params=dict(capture_id=capture_id,window_s=window_s,context_s=context_s,fft_size=fft_size,time_bin_s=time_bin_s,
        frequency_bin_hz=frequency_bin_hz,threshold_db=threshold_db,sample_start=sample_start,sample_stop=sample_stop)
    key=hashlib.sha256(json.dumps(params,sort_keys=True).encode()).hexdigest()[:16]
    checkpoint=ctx.run/('window-analysis-'+key+'.json')
    state=json.loads(checkpoint.read_text()) if checkpoint.exists() else dict(parameters=params,windows=[])
    step=max(1,round(window_s*fs));pad=round(context_s*fs);count=math.ceil((sample_stop-sample_start)/step)
    from .coarse import segment
    for i in range(len(state['windows']),min(count,len(state['windows'])+max_windows)):
        ctx.check();a=sample_start+i*step;b=min(sample_stop,a+step)
        local=segment(ctx,capture_id,fft_size,time_bin_s,frequency_bin_hz,threshold_db,
            sample_start=max(sample_start,a-pad),sample_stop=min(sample_stop,b+pad),artifact_kind='window_segmentation',
            decision_reason='Model-selected fixed main window with overlapping IQ context; original sample coordinates retained.')
        value=json.loads(Path(ctx.resolve(local['segmentation_id'])['path']).read_text())
        state['windows'].append(dict(index=i,core_start=a,core_stop=b,segmentation_id=local['segmentation_id'],coverage=value['coverage'],events=value['events']))
        dump(checkpoint,state)
    complete=len(state['windows'])==count
    if complete and not state.get('segmentation_id'):
        events=stitch(state['windows'],m['segments'],fs)
        first=json.loads(Path(ctx.resolve(state['windows'][0]['segmentation_id'])['path']).read_text())
        first.update(events=events,signal_groups=events,strategy='windowed_coarse_to_fine',algorithm='overlapping-time-window-stft-v1',
            window_analysis=dict(window_s=window_s,context_s=context_s,total_windows=count,
                windows=[{k:v for k,v in w.items() if k!='events'} for w in state['windows']],
                note='Overlapping energy envelopes associated conservatively; not proof of identical transmitters or packet boundaries.'),
            coverage=dict(observed_samples=sample_stop-sample_start,source_sample_start=sample_start,source_sample_stop=sample_stop,
                requested_range_hz=first['coverage']['requested_range_hz'],all_continuous_samples_covered=all(w['coverage']['all_continuous_samples_covered'] for w in state['windows']),
                continuous_segments=[dict(sample_start=max(sample_start,s['file_start']),sample_stop=min(sample_stop,s['file_start']+s['count']))
                    for s in m['segments'] if min(sample_stop,s['file_start']+s['count'])>max(sample_start,s['file_start'])],
                gaps=m.get('gaps',[]),overflow_count=m.get('overflow_count',0)))
        state['segmentation_id']=ctx.json_artifact('segmentation',first);dump(checkpoint,state)
    return dict(complete=complete,processed_windows=len(state['windows']),total_windows=count,
        remaining_windows=count-len(state['windows']),segmentation_id=state.get('segmentation_id'),parameters=params,
        next_action='Select protocol tools on original capture with per-window sample bounds; retain context, deduplicate by packet bytes and original timing.' if complete else 'Call analyze_spectrum_windows again with identical parameters; completed windows are checkpointed.')
