"""Independent, checkpointed frequency-agile recording-reference investigation.

Discovery is energy-guided over every requested time block. This is not exhaustive
RF detection: weak/overlapped or unregistered references can remain unresolved.
No radio access and no modification of fixed-frequency or Wi-Fi evidence.
"""
import hashlib,json,math,time
from fractions import Fraction
from pathlib import Path
import numpy as np
from scipy import fft,ndimage
from .common import DomainError,dump,finite_number
from .dsp import load_iq
from .drone_video import SignatureDetector,_frontend,profile
from .frequency_scope import bounds


class FFTChannelBank:
    """One wideband FFT per contextual block; independent complex subbands.

    The surrounding context absorbs the periodic FFT filter edge. Only core
    references are accepted. Scale preserves input IQ amplitude; not dBm.
    """
    def __init__(self,iq,input_rate,profiles):
        self.fs=input_rate;self.ratios={k:Fraction(p['sample_rate_hz']/input_rate).limit_denominator(100000) for k,p in profiles.items()}
        multiple=math.lcm(*(r.denominator for r in self.ratios.values()),2)
        self.n=multiple*fft.next_fast_len(math.ceil(len(iq)/multiple))
        self.spectrum=fft.fft(np.asarray(iq,dtype=np.complex64),self.n,workers=1)
        self.bins={};self.tapers={}
        for name,p in profiles.items():
            ratio=self.ratios[name];count=self.n*ratio.numerator//ratio.denominator
            bins=np.arange(-(count//2),count-count//2,dtype=np.int64)
            # Flat through92% of the output Nyquist interval; taper the edge.
            coordinate=abs(bins.astype(np.float64))/max(count/2,1)
            taper=np.ones(count,np.float32);edge=coordinate>.92
            taper[edge]=.5*(1+np.cos(np.pi*np.clip((coordinate[edge]-.92)/.08,0,1)))
            self.bins[name]=bins;self.tapers[name]=taper

    def extract(self,name,offset_hz):
        indices=self.bins[name];center_bin=round(offset_hz*self.n/self.fs)
        cropped=self.spectrum[(indices+center_bin)%self.n]*self.tapers[name]
        x=fft.ifft(fft.ifftshift(cropped),workers=1)*(len(indices)/self.n)
        return x.astype(np.complex64,copy=False),center_bin*self.fs/self.n


def proposals(iq,fs,fc,low,high,profiles,threshold_db=6.,tile_s=.0001):
    """Observe all samples using contiguous STFT frames; propose centers per tile."""
    nfft=2048;tile=max(nfft,round(tile_s*fs));hop=nfft//2
    frequencies=fc+np.fft.fftshift(np.fft.fftfreq(nfft,1/fs))
    keep=(frequencies>=low)&(frequencies<=high);valid=np.flatnonzero(keep)
    if len(valid)<8:raise DomainError('INSUFFICIENT_BANDWIDTH','Analysis band too narrow for discovery')
    df=fs/nfft;window=np.hanning(nfft).astype(np.float32)
    jobs={};activity=[];count=0
    for start in range(0,len(iq),tile):
        # Context includes the preceding FFT; no unobserved gap between tiles.
        lo=max(0,start-nfft);hi=min(len(iq),start+tile+nfft)
        x=np.asarray(iq[lo:hi]);
        if len(x)<nfft:continue
        frames=np.lib.stride_tricks.sliding_window_view(x,nfft)[::hop]
        spectrum=fft.fftshift(fft.fft(frames*window,axis=1,workers=1),axes=1)
        power=np.mean(abs(spectrum).astype(np.float64)**2,axis=0)[keep]
        power=ndimage.uniform_filter1d(power,max(1,round(.4e6/df)),mode='nearest')
        floor=max(float(np.percentile(power,20)),1e-30)
        mask=power>floor*10**(threshold_db/10)
        mask=ndimage.binary_closing(mask,structure=np.ones(max(1,round(.4e6/df))),border_value=0)
        edges=np.diff(np.r_[False,mask,False].astype(np.int8));count+=1
        for a,b in zip(np.flatnonzero(edges==1),np.flatnonzero(edges==-1)):
            left=max(low,float(frequencies[valid[a]]-df/2));right=min(high,float(frequencies[valid[b-1]]+df/2))
            if right-left<4e6:continue
            activity.append(dict(sample_start=start,sample_stop=min(len(iq),start+tile),freq_low_hz=left,freq_high_hz=right))
            for name,p in profiles.items():
                width=p['nominal_channel_bandwidth_hz']
                if right-left<.6*p['zc_length']*p['subcarrier_spacing_hz']:continue
                # Include edges and middle; broad/overlapping activity also gets
                # interior seeds. No fixed channel raster or training frequency.
                seeds=[(left+right)/2,left+width/2,right-width/2]
                seeds+=list(np.arange(left+width/2,right-width/2+1,width/4))
                for seed in seeds:
                    for offset in (-1e6,-.5e6,0,.5e6,1e6):
                        center=round((seed+offset)/500000)*500000
                        if not low+width/2<=center<=high-width/2:continue
                        key=(name,center)
                        if key not in jobs:jobs[key]=dict(profile_id=name,center_hz=center,sample_start=start,sample_stop=min(len(iq),start+tile))
                        else:
                            jobs[key]['sample_start']=min(jobs[key]['sample_start'],start)
                            jobs[key]['sample_stop']=max(jobs[key]['sample_stop'],min(len(iq),start+tile))
    return list(jobs.values()),dict(time_tiles=count,activity_tiles=len(activity),
        strategy='contiguous-STFT energy proposals with multiple center/width hypotheses',threshold_db=threshold_db,
        fft_size=nfft,frequency_resolution_hz=df,tile_s=tile_s,
        limitation='Proposal-limited reference search; subthreshold,overlapped,edge-truncated and unregistered signals can be missed')


def deduplicate(rows):
    retained=[]
    for row in sorted(rows,key=lambda r:-(r['signature_score']*r['spectral_inband_fraction'])):
        if not any(abs(row['reference_time_s']-v['reference_time_s'])<.00005 and
                   abs(row['center_hz']-v['center_hz'])<min(row['nominal_channel_bandwidth_hz'],v['nominal_channel_bandwidth_hz'])/2
                   for v in retained):retained.append(row)
    return sorted(retained,key=lambda r:(r['reference_sample_start'],r['center_hz']))


def sequence_summary(rows):
    """Temporal compatibility only; never assigns aircraft identity."""
    clusters=[];widths={};transitions=[]
    for row in rows:
        width=row['nominal_channel_bandwidth_hz'];widths[str(width)]=widths.get(str(width),0)+1
        cluster=next((c for c in clusters if abs(c['center_hz']-row['center_hz'])<=2e6),None)
        if cluster is None:clusters.append(dict(center_hz=row['center_hz'],observations=1))
        else:cluster['observations']+=1
    for before,after in zip(rows,rows[1:]):
        gap=after['reference_time_s']-before['reference_time_s']
        if before['segment_file_start']!=after['segment_file_start'] or not .0001<gap<=.02:continue
        frequency_changed=abs(after['center_hz']-before['center_hz'])>2e6
        bandwidth_changed=after['profile_id']!=before['profile_id']
        if frequency_changed or bandwidth_changed:
            transitions.append(dict(from_sample=before['reference_sample_start'],to_sample=after['reference_sample_start'],
                from_center_hz=before['center_hz'],to_center_hz=after['center_hz'],from_bandwidth_hz=before['nominal_channel_bandwidth_hz'],
                to_bandwidth_hz=after['nominal_channel_bandwidth_hz'],gap_s=gap,frequency_changed=frequency_changed,bandwidth_changed=bandwidth_changed,
                association='temporal_compatibility_only_not_emitter_identity'))
    return dict(center_clusters=clusters,bandwidth_observation_counts=widths,
        multiple_frequency_candidates=sum(c['observations']>=2 for c in clusters)>1,
        multiple_bandwidth_candidates=sum(v>=2 for v in widths.values())>1,
        transitions=transitions,aircraft_identity_confirmed=False,
        note='Multiple compatible frequencies/modes may be one agile link or multiple transmitters; not decoded hop scheduling or identity')


def compact_timings(timings):
    """Keep exact per-block sums/counts without rewriting tens of thousands
    of individual probe timings on every checkpoint. Handles old checkpoints.
    """
    result=[];groups={}
    for item in timings:
        if item['stage']!='reference_probe':result.append(item);continue
        index=item['unit_index']
        if index not in groups:
            groups[index]=dict(stage='reference_probe',unit_index=index,frontend_ms=0.,core_ms=0.,probe_count=0)
            result.append(groups[index])
        total=groups[index];total['frontend_ms']+=item['frontend_ms'];total['core_ms']+=item['core_ms'];total['probe_count']+=item.get('probe_count',1)
    return result


def analyze(ctx,capture_id,reason,evidence_ids,profile_ids=None,max_blocks=1,max_probes=128,
            discovery_threshold_db=6.,acquisition_threshold=.55,signature_threshold=.72):
    if not isinstance(reason,str) or not 16<=len(reason)<=2000:raise DomainError('INVALID_ARGUMENT','Explain why frequency-agile analysis is requested')
    if not isinstance(evidence_ids,list) or not 1<=len(evidence_ids)<=12:raise DomainError('INVALID_ARGUMENT','Supply1..12 task-owned evidence IDs')
    for aid in evidence_ids:ctx.resolve(aid)
    for name,value,maximum in [('max_blocks',max_blocks,64),('max_probes',max_probes,4096)]:
        if isinstance(value,bool) or not isinstance(value,int) or not 1<=value<=maximum:raise DomainError('INVALID_ARGUMENT',name+' out of range')
    finite_number(discovery_threshold_db,'discovery_threshold_db',3,15)
    # Threshold validation is identical to the fixed-frequency core.
    from .common import ROOT
    catalog=json.loads((ROOT/'configs/drone_video_signatures.json').read_text())['profiles']
    names=sorted(profile_ids if profile_ids is not None else catalog)
    if not names or len(names)!=len(set(names)) or any(n not in catalog for n in names):raise DomainError('UNSUPPORTED_PROFILE','Select distinct installed reference profiles')
    profiles={name:profile(name) for name in names}
    iq,m=load_iq(ctx,capture_id);fs=m['sample_rate_hz'];low,high=bounds(m)
    if any(p['sample_rate_hz']>fs or p['nominal_channel_bandwidth_hz']>high-low for p in profiles.values()):
        raise DomainError('INSUFFICIENT_BANDWIDTH','Selected profile cannot fit recording rate/analysis scope; select fitting profiles explicitly')
    detectors={name:SignatureDetector(p,acquisition_threshold,signature_threshold) for name,p in profiles.items()}
    request=json.loads((ctx.run/'request.json').read_text());intent=request.get('task_intent',{})
    first=round(intent.get('analysis_start_s',0)*fs);last=min(len(iq),round(intent.get('analysis_stop_s',len(iq)/fs)*fs))
    if not 0<=first<last<=len(iq):raise DomainError('INVALID_ARGUMENT','Analysis time outside recording')
    params=dict(capture_id=capture_id,profiles=profiles,sample_start=first,sample_stop=last,low_hz=low,high_hz=high,
        discovery_threshold_db=discovery_threshold_db,acquisition_threshold=acquisition_threshold,signature_threshold=signature_threshold,version=2)
    key=hashlib.sha256(json.dumps(params,sort_keys=True).encode()).hexdigest()[:16]
    path=ctx.run/('drone-hopping-progress-'+key+'.json')
    state=json.loads(path.read_text()) if path.exists() else dict(parameters=params,completed=[],references=[],current=None,timings=[],checks=0)
    state['timings']=compact_timings(state['timings']);state['timing_storage']='per_unit_probe_sums_and_counts'
    probe_timings={v['unit_index']:v for v in state['timings'] if v['stage']=='reference_probe'}
    units=[]
    for seg in m['segments']:
        a=max(first,seg['file_start']);b=min(last,seg['file_start']+seg['count'])
        for start in range(a,b,round(.01*fs)):units.append((start,min(b,start+round(.01*fs)),seg))
    probes=0;finished=0
    for index,(a,b,seg) in enumerate(units):
        if index<len(state['completed']):continue
        if finished>=max_blocks or probes>=max_probes:break
        ctx.check();lo=max(seg['file_start'],a-round(.001*fs));hi=min(seg['file_start']+seg['count'],b+round(.001*fs))
        if state['current'] is None:
            began=time.perf_counter();jobs,discovery=proposals(iq[lo:hi],fs,m['center_hz'],low,high,profiles,discovery_threshold_db)
            state['current']=dict(unit_index=index,jobs=jobs,next_job=0,rows=[],discovery=discovery)
            state['timings'].append(dict(stage='discovery',elapsed_ms=(time.perf_counter()-began)*1000,unit_index=index))
            dump(path,state)
        current=state['current']
        bank=None
        for job_index in range(current['next_job'],len(current['jobs'])):
            if probes>=max_probes:break
            ctx.check();job=current['jobs'][job_index];name=job['profile_id'];p=profiles[name];center=job['center_hz'];rate=p['sample_rate_hz']
            jl=max(lo,lo+job['sample_start']-round(.00015*fs));jh=min(hi,lo+job['sample_stop']+round(.00015*fs))
            if bank is None:
                began=time.perf_counter();bank=FFTChannelBank(iq[lo:hi],fs,profiles)
                state['timings'].append(dict(stage='shared_wideband_fft',elapsed_ms=(time.perf_counter()-began)*1000,unit_index=index))
            began=time.perf_counter();channel,actual_offset=bank.extract(name,center-m['center_hz'])
            center=m['center_hz']+actual_offset
            begin=round((jl-lo)*rate/fs);end=round((jh-lo)*rate/fs)
            x=channel[begin:end];origin=lo+begin*fs/rate
            front=(time.perf_counter()-began)*1000;began=time.perf_counter();found=detectors[name].detect(x);core=(time.perf_counter()-began)*1000
            for row in found:
                start=round(origin+row['reference_sample_start']/rate*fs);stop=round(origin+row['reference_sample_stop']/rate*fs)
                if not a<=start<b:continue
                # Bound the envelope by the local energy,not a10ms bookkeeping
                # cell; cap at the inspected proposal/context interval.
                n=max(8,round(rate*.00002));power=np.mean(abs(x[:len(x)//n*n].reshape(-1,n))**2,axis=1)
                loc=min(len(power)-1,row['reference_sample_start']//n)
                level=max(float(np.percentile(power,15))*6.3,float(power[loc])*.12);left=right=loc
                while left>0 and loc-left<round(.002*rate/n) and power[left-1]>level:left-=1
                while right+1<len(power) and right-loc<round(.002*rate/n) and power[right+1]>level:right+=1
                current['rows'].append(dict(row,profile_id=name,reference_sample_start=start,reference_sample_stop=stop,reference_time_s=start/fs,
                    sample_start=min(start,max(jl,round(origin+left*n/rate*fs))),sample_stop=max(stop,min(jh,round(origin+(right+1)*n/rate*fs))),
                    center_hz=center,freq_low_hz=center-p['nominal_channel_bandwidth_hz']/2,freq_high_hz=center+p['nominal_channel_bandwidth_hz']/2,
                    nominal_channel_bandwidth_hz=p['nominal_channel_bandwidth_hz'],reference_active_bandwidth_hz=p['zc_length']*p['subcarrier_spacing_hz'],
                    segment_file_start=seg['file_start'],hardware_reference_time_s=(seg['hardware_start_s']+(start-seg['file_start'])/fs) if seg.get('hardware_start_s') is not None else None,
                    gain_db=m.get('gain_db'),power_calibrated=False,boundary_kind='associated_energy_envelope_not_decoded_packet',
                    frequency_boundary_kind='signature_profile_nominal_channel',estimated_carrier_hz=center+row['carrier_offset_hz']))
            current['next_job']=job_index+1;probes+=1;state['checks']+=1
            if index not in probe_timings:
                probe_timings[index]=dict(stage='reference_probe',frontend_ms=0.,core_ms=0.,unit_index=index,probe_count=0)
                state['timings'].append(probe_timings[index])
            totals=probe_timings[index];totals['frontend_ms']+=front;totals['core_ms']+=core;totals['probe_count']+=1
            if probes%128==0:dump(path,state)
        if current['next_job']<len(current['jobs']):dump(path,state);break
        state['references']=deduplicate(state['references']+current['rows'])
        state['completed'].append(dict(unit_index=index,sample_start=a,sample_stop=b,proposed_checks=len(current['jobs']),discovery=current['discovery']))
        state['current']=None;finished+=1;dump(path,state)
        ctx.event('drone_hopping_progress',phase='checkpoint',analysis_key=key,completed_units=len(state['completed']),total_units=len(units),matched_references=len(state['references']))
    done=len(state['completed'])==len(units);rows=state['references'];summary=sequence_summary(rows)
    superseded=list(state.get('supersedes_analysis_keys',[]))
    for old in project(ctx):
        cov=old['coverage']
        if old.get('method_revision',1)<2 and old['capture_id']==capture_id and old['profiles']==profiles and cov['sample_start']==first and cov['sample_stop']==last:
            superseded.append(old['analysis_key'])
    superseded=sorted(set(superseded));state['supersedes_analysis_keys']=superseded
    value=dict(capture_id=capture_id,analysis_key=key,method_revision=2,supersedes_analysis_keys=superseded,
        frontend='shared_wideband_fft_channel_bank',analysis_mode='frequency_agile',profiles=profiles,reason=reason,feedback_evidence_ids=evidence_ids,
        protocol='unknown',evidence_level='recording_signature_match' if rows else 'not_detected_in_checked_scope',references=rows,matched_references=len(rows),
        coverage=dict(complete=done,total_units=len(units),completed_units=len(state['completed']),sample_start=first,sample_stop=last,
            processed_samples=sum(v['sample_stop']-v['sample_start'] for v in state['completed']),gaps=m.get('gaps',[]),all_hardware_time_continuous=not m.get('gaps'),
            search_scope='Full requested time/band STFT; reference testing limited to energy-proposed windows,centers and selected installed profiles',
            reference_checks=state['checks'],pending_current_unit_checks=(len(state['current']['jobs'])-state['current']['next_job']) if state['current'] else 0),
        sequence=summary,attribution=dict(manufacturer='DJI',model='Mavic 4 Pro',source='project_user_context_not_decoded_identity',protocol_confirmed=False),
        limitations=['Experimental frequency-agile reference analysis; live hopping validation pending',
            'Complete means all requested STFT blocks and proposed reference checks finished,not exhaustive RF/packet detection',
            'Unregistered reference modes,weak signals,overlaps,partial band edges or missing reference symbols may remain unknown',
            'Frequency estimates use500kHz candidate grid; nominal profile boxes are not calibrated occupied bandwidth or decoded packet boundaries',
            'Temporal compatibility does not prove one aircraft; simultaneous same-signature emitters are unresolved',
            'No payload CRC,video recovery,absolute power calibration or real-time hardware throughput claim'])
    dump(path,state);aid=ctx.json_artifact('drone_hopping_analysis',value)
    return dict(evidence_id=aid,**{k:v for k,v in value.items() if k!='references'},references=rows[:8],summary_truncated=len(rows)>8,
        next_action='Build report with reference-only limits' if done else 'Repeat identical parameters; max_blocks/max_probes may increase; do not treat partial proposals as completed coverage')


def project(ctx):
    latest={}
    for f in sorted((ctx.run/'index').glob('*.json'),key=lambda p:p.stat().st_mtime_ns):
        rec=json.loads(f.read_text())
        if rec.get('kind')=='drone_hopping_analysis':
            value=json.loads(Path(rec['path']).read_text());latest[value['analysis_key']]=dict(value,evidence_id=rec['artifact_id'])
    superseded={k for v in latest.values() for k in v.get('supersedes_analysis_keys',[])}
    return [v for k,v in latest.items() if k not in superseded]


def coverage_complete(report,intent):
    key='drone_hopping_evidence' if intent.get('drone_hopping_analysis_requested') else 'drone_video_evidence'
    evidence=report.get(key,[])
    return bool(evidence) and all(v['coverage']['complete'] for v in evidence)
