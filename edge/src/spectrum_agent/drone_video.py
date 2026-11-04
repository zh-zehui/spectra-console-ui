"""Additive recording-signature analysis. Never modifies Wi-Fi or claims payload CRC."""
import ctypes,hashlib,json,math,time
from fractions import Fraction
from pathlib import Path
import numpy as np
from scipy import signal,fft
from .common import ROOT,DomainError,dump,finite_number
from .dsp import load_iq

DEFAULT_PROFILE='zc293_u123_30k_observed_v1'
_NATIVE=None
_TAPS={}
_LIBRARY=None


def _library():
    global _LIBRARY
    path=ROOT/'runtime/drone-video/libspectrum_drone_resample.so'
    if _LIBRARY is None and path.exists():_LIBRARY=ctypes.CDLL(str(path))
    return _LIBRARY


def _resample(mixed,up,down):
    global _NATIVE
    key=(up,down)
    if key not in _TAPS:_TAPS[key]=signal.firwin(20*max(up,down)+1,1/max(up,down),window=('kaiser',5.)).astype(np.float32)
    taps=_TAPS[key];libpath=ROOT/'runtime/drone-video/libspectrum_drone_resample.so'
    if _NATIVE is None and libpath.exists():
        lib=_library();function=lib.spectrum_resample_cf32
        function.argtypes=[ctypes.c_void_p,ctypes.c_int64,ctypes.c_int,ctypes.c_int,ctypes.c_void_p,ctypes.c_int,ctypes.c_void_p,ctypes.c_int64]
        function.restype=ctypes.c_int64;_NATIVE=function
    if _NATIVE is not None:
        mixed=np.ascontiguousarray(mixed,dtype=np.complex64);out=np.empty((len(mixed)*up+down-1)//down,dtype=np.complex64)
        count=_NATIVE(mixed.ctypes.data,len(mixed),up,down,taps.ctypes.data,len(taps),out.ctypes.data,len(out))
        if count!=len(out):raise DomainError('RESAMPLER_ERROR','Native resampler output length mismatch')
        return out
    return signal.resample_poly(mixed,up,down,window=taps).astype(np.complex64)


def profile(name=DEFAULT_PROFILE):
    catalog=json.loads((ROOT/'configs/drone_video_signatures.json').read_text())
    if name not in catalog['profiles']:raise DomainError('UNSUPPORTED_PROFILE','Unknown drone recording signature')
    return dict(catalog['profiles'][name],profile_id=name)


def list_profiles(ctx):
    catalog=json.loads((ROOT/'configs/drone_video_signatures.json').read_text())
    return dict(profiles=[dict(v,profile_id=k) for k,v in catalog['profiles'].items()],
        note='Installed recording-derived references,not proof of presence in the current IQ. Select a profile from bandwidth/structure evidence; no hardware or acquisition.')


class SignatureDetector:
    """Persistent canonical-baseband DSP core,without model or radio dependencies."""
    def __init__(self,p=None,acquisition_threshold=.55,signature_threshold=.72):
        self.p=p or profile();self.rate=self.p['sample_rate_hz'];self.nfft=self.p['fft_size'];self.cp=self.p['cyclic_prefix_samples']
        finite_number(acquisition_threshold,'acquisition_threshold',.4,.9)
        finite_number(signature_threshold,'signature_threshold',.6,.99)
        self.acquisition_threshold=acquisition_threshold;self.signature_threshold=signature_threshold
        length=self.p['zc_length'];n=np.arange(length)
        self.zc=np.exp(-1j*np.pi*self.p['zc_root']*n*(n+1)/length)
        bins=np.zeros(self.nfft,dtype=np.complex64);self.first=self.nfft//2-length//2
        bins[self.first:self.first+length]=self.zc
        symbol=np.fft.ifft(np.fft.ifftshift(bins)).astype(np.complex64)
        self.reference=symbol[:self.p['acquisition_reference_samples']]
        self.kernel=np.conj(self.reference[::-1]);self.energy=float(np.sum(abs(self.reference)**2))
        self.native_handle=None;lib=_library()
        if lib is not None and hasattr(lib,'spectrum_zc_acquire_create'):
            create=lib.spectrum_zc_acquire_create;create.argtypes=[ctypes.c_void_p,ctypes.c_int];create.restype=ctypes.c_void_p
            run=lib.spectrum_zc_acquire_run;run.argtypes=[ctypes.c_void_p,ctypes.c_void_p,ctypes.c_int,ctypes.c_float,ctypes.c_int,ctypes.c_void_p,ctypes.c_void_p,ctypes.c_int];run.restype=ctypes.c_int
            destroy=lib.spectrum_zc_acquire_destroy;destroy.argtypes=[ctypes.c_void_p];destroy.restype=None
            self.native_handle=create(self.reference.ctypes.data,len(self.reference));self.native_run=run;self.native_destroy=destroy
        self.tail=np.empty(0,dtype=np.complex64);self.next_sample=0;self.last_reference=-10**12

    def __del__(self):
        if getattr(self,'native_handle',None):self.native_destroy(self.native_handle)

    @staticmethod
    def _rolling(x,n):
        sums=np.r_[0,np.cumsum(x)];return sums[n:]-sums[:-n]

    def detect(self,x):
        x=np.asarray(x,dtype=np.complex64)
        if x.ndim!=1 or len(x)>2_000_000 or not np.isfinite(x).all():raise DomainError('INVALID_IQ','Finite one-dimensional baseband,<=2M samples required')
        length=len(self.reference)
        if len(x)<self.nfft*3:return []
        power=abs(x).astype(np.float64)**2
        if self.native_handle:
            x=np.ascontiguousarray(x);indices=np.empty(2048,dtype=np.int64);scores=np.empty(2048,dtype=np.float32)
            count=self.native_run(self.native_handle,x.ctypes.data,len(x),self.acquisition_threshold,round(.00015*self.rate),indices.ctypes.data,scores.ctypes.data,len(indices))
            if count<0:raise DomainError('NATIVE_ACQUISITION_ERROR','Reference acquisition capacity/error; no silent truncation')
            candidates=indices[:count];rho={int(i):float(v) for i,v in zip(candidates,scores[:count])}
        else:
            with fft.set_workers(1):cc=signal.oaconvolve(x,self.kernel,mode='valid')
            energy=self._rolling(power,length);rho=abs(cc)/np.sqrt(np.maximum(energy*self.energy,1e-30))
            candidates,_=signal.find_peaks(rho,height=self.acquisition_threshold,distance=round(.00015*self.rate))
        result=[]
        for pos in candidates:
            lo=int(max(0,pos-self.nfft*2));hi=int(min(len(x),pos+self.nfft*2))
            local=x[lo:hi]
            if len(local)<=self.nfft+self.cp:continue
            a=local[:-self.nfft];b=local[self.nfft:]
            num=self._rolling(a*np.conj(b),self.cp)
            den=self._rolling(abs(a).astype(float)**2,self.cp)*self._rolling(abs(b).astype(float)**2,self.cp)
            cp_score=abs(num)**2/np.maximum(den,1e-30)
            possible,_=signal.find_peaks(cp_score,height=.25,distance=max(1,self.cp//2))
            possible=possible[np.argsort(cp_score[possible])[-6:]] if len(possible) else []
            best=None
            offsets=np.asarray(possible,dtype=np.int64)
            offsets=offsets[lo+offsets+self.cp+self.nfft<=len(x)]
            if len(offsets):
                begins=lo+offsets;frequencies=-np.angle(num[offsets])*self.rate/(2*np.pi*self.nfft)
                samples=np.arange(self.nfft)
                useful=x[begins[:,None]+self.cp+samples[None,:]]
                useful*=np.exp(-2j*np.pi*frequencies[:,None]/self.rate*samples[None,:]).astype(np.complex64)
                # Evaluate every candidate as one batch,without dropping checks.
                spectra=fft.fftshift(fft.fft(useful,axis=1,workers=1),axes=1)
                actives=spectra[:,self.first:self.first+len(self.zc)]
                responses=fft.ifft(actives*np.conj(self.zc).astype(np.complex64)[None,:],2048,axis=1,workers=1)*2048
                energies=np.sum(abs(actives).astype(np.float64)**2,axis=1)
                scores=np.max(abs(responses),axis=1)/np.sqrt(np.maximum(energies*len(self.zc),1e-30))
                chosen=int(np.argmax(scores));score=float(scores[chosen]);begin=int(begins[chosen]);offset=int(offsets[chosen]);cfo=float(frequencies[chosen])
                reference_power=float(energies[chosen]/self.nfft**2)
                best=dict(reference_sample_start=begin,reference_sample_stop=begin+self.cp+self.nfft,
                    acquisition_score=float(rho[pos]),signature_score=score,cp_correlation=float(np.sqrt(cp_score[offset])),
                    carrier_offset_hz=cfo,reference_band_power_dbfs=10*math.log10(max(reference_power,1e-30)),
                    coherent_reference_power_dbfs=10*math.log10(max(reference_power*score**2,1e-30)),
                    coherent_power_scope='Dominant coherent signature component,not total multipath power or calibrated RSSI',
                    spectral_inband_fraction=float(energies[chosen]/max(np.sum(abs(spectra[chosen]).astype(np.float64)**2),1e-30)),
                    channel_window_power_dbfs=10*math.log10(max(float(power[begin:begin+self.cp+self.nfft].mean()),1e-30)),
                    reference_power_scope='Active reference-subcarrier power; includes interference,uncalibrated input-scale dB',
                    channel_window_bandwidth_hz=self.rate,
                    timing_scope='CP-supported reference-symbol estimate; not decoded packet start')
            if best and best['signature_score']>=self.signature_threshold:result.append(best)
        # Acquisition sidelobes cannot become separate signal observations.
        unique=[]
        for row in sorted(result,key=lambda r:-r['signature_score']):
            if not any(abs(row['reference_sample_start']-old['reference_sample_start'])<self.nfft*2 for old in unique):unique.append(row)
        return sorted(unique,key=lambda r:r['reference_sample_start'])

    def process(self,iq,start_sample=None):
        """Streaming baseband. Retain boundary context; one event per reference."""
        iq=np.asarray(iq,dtype=np.complex64)
        start=self.next_sample if start_sample is None else start_sample
        if start!=self.next_sample:
            self.tail=np.empty(0,dtype=np.complex64);self.last_reference=-10**12
        combined=np.concatenate((self.tail,iq));origin=start-len(self.tail);rows=[]
        # Defer only references too close to the right edge for full refinement.
        cutoff=start+len(iq)-self.nfft*2
        for row in self.detect(combined):
            begin=origin+row['reference_sample_start'];end=origin+row['reference_sample_stop']
            if begin>cutoff or begin<=self.last_reference+self.nfft*2:continue
            rows.append(dict(row,reference_sample_start=begin,reference_sample_stop=end));self.last_reference=begin
        self.tail=combined[-self.nfft*5:].copy();self.next_sample=start+len(iq)
        return rows

    def flush(self):
        """Only finalize references contained in real buffered samples; no zero-IQ injection."""
        origin=self.next_sample-len(self.tail);rows=[]
        for row in self.detect(self.tail):
            begin=origin+row['reference_sample_start'];end=origin+row['reference_sample_stop']
            if begin<=self.last_reference+self.nfft*2 or end>self.next_sample:continue
            rows.append(dict(row,reference_sample_start=begin,reference_sample_stop=end));self.last_reference=begin
        return rows


class StreamingFrontend:
    """Continuous phase and finite FIR context; no per-block filter discontinuities."""
    def __init__(self,input_rate_hz,input_center_hz,target_center_hz,output_rate_hz=15360000,channel_bandwidth_hz=10000000):
        finite_number(input_rate_hz,'input_rate_hz',output_rate_hz,200e6)
        if abs(target_center_hz-input_center_hz)+channel_bandwidth_hz/2>input_rate_hz/2:raise DomainError('OUT_OF_BAND','Target nominal channel exceeds input Nyquist band')
        self.input_rate=input_rate_hz;self.offset=target_center_hz-input_center_hz;self.rate=output_rate_hz
        ratio=Fraction(output_rate_hz/input_rate_hz).limit_denominator(10000);self.up=ratio.numerator;self.down=ratio.denominator
        if abs(input_rate_hz*self.up/self.down-output_rate_hz)>output_rate_hz*1e-9:raise DomainError('UNSUPPORTED_RATE','Input/output ratio needs an exact supported rational rate')
        self.history=0 if self.up==self.down else max(self.down,math.ceil((20*max(self.up,self.down)+1)/self.up/self.down)*self.down)
        self.buffer=np.empty(0,dtype=np.complex64);self.buffer_start=0;self.received=0;self.next_input=0;self.next_output=0;self.oscillators={}

    def process(self,iq,final=False):
        iq=np.asarray(iq,dtype=np.complex64)
        if iq.ndim!=1 or not np.isfinite(iq).all():raise DomainError('INVALID_IQ','Finite complex stream required')
        self.buffer=np.concatenate((self.buffer,iq));self.received+=len(iq)
        stop=self.received if final else max(0,(self.received-self.history)//self.down*self.down)
        origin=self.next_output
        if stop<=self.next_input:return np.empty(0,dtype=np.complex64),origin
        lo=max(0,self.next_input-self.history);hi=min(self.received,stop+self.history)
        source=self.buffer[lo-self.buffer_start:hi-self.buffer_start]
        x=_frontend(source,self.input_rate,self.offset,self.rate,self.oscillators)
        x*=np.complex64(np.exp(-2j*np.pi*self.offset*lo/self.input_rate))
        first=round((self.next_input-lo)*self.up/self.down)
        last=math.ceil((stop-lo)*self.up/self.down) if final else round((stop-lo)*self.up/self.down)
        out=x[first:last];self.next_input=stop;self.next_output+=len(out)
        keep=max(0,stop-self.history);self.buffer=self.buffer[keep-self.buffer_start:].copy();self.buffer_start=keep
        return out,origin


def _frontend(x,input_rate,offset_hz,rate,oscillators):
    key=(len(x),input_rate,offset_hz)
    if key not in oscillators:
        if len(oscillators)>4:oscillators.clear()
        oscillators[key]=np.exp(-2j*np.pi*offset_hz/input_rate*np.arange(len(x))).astype(np.complex64)
    mixed=np.asarray(x,dtype=np.complex64)*oscillators[key]
    ratio=Fraction(rate/input_rate).limit_denominator(10000)
    if ratio.numerator==ratio.denominator:return mixed
    # Single-precision taps avoid promoting a wideband input to complex128.
    return _resample(mixed,ratio.numerator,ratio.denominator)


def analyze(ctx,capture_id,center_hz,reason,evidence_ids,profile_id=DEFAULT_PROFILE,max_blocks=64,acquisition_threshold=.55,signature_threshold=.72,search_span_hz=0):
    if not isinstance(reason,str) or not 16<=len(reason)<=2000:raise DomainError('INVALID_ARGUMENT','Explain observed target and selected reference profile')
    if not isinstance(evidence_ids,list) or not 1<=len(evidence_ids)<=12:raise DomainError('INVALID_ARGUMENT','Reference1..12 task-owned prior/observation artifacts')
    for aid in evidence_ids:ctx.resolve(aid)
    if isinstance(max_blocks,bool) or not isinstance(max_blocks,int) or not 1<=max_blocks<=64:raise DomainError('INVALID_ARGUMENT','max_blocks1..64')
    p=profile(profile_id);iq,m=load_iq(ctx,capture_id);fs=m['sample_rate_hz'];rate=p['sample_rate_hz']
    if fs<rate:raise DomainError('INSUFFICIENT_BANDWIDTH','Recording rate below the signature observation rate')
    from .frequency_scope import bounds
    low,high=bounds(m);width=p['nominal_channel_bandwidth_hz']
    finite_number(center_hz,'center_hz',low+width/2,high-width/2)
    finite_number(search_span_hz,'search_span_hz',0,40e6)
    request=json.loads((ctx.run/'request.json').read_text());intent=request.get('task_intent',{})
    first=round(intent.get('analysis_start_s',0)*fs);last=min(len(iq),round(intent.get('analysis_stop_s',len(iq)/fs)*fs))
    if not 0<=first<last<=len(iq):raise DomainError('INVALID_ARGUMENT','Analysis scope outside recording')
    params=dict(capture_id=capture_id,center_hz=center_hz,profile=p,sample_start=first,sample_stop=last,
        acquisition_threshold=acquisition_threshold,signature_threshold=signature_threshold,search_span_hz=search_span_hz)
    key=hashlib.sha256(json.dumps(params,sort_keys=True).encode()).hexdigest()[:16]
    checkpoint=ctx.run/('drone-video-progress-'+key+'.json')
    state=json.loads(checkpoint.read_text()) if checkpoint.exists() else dict(parameters=params,completed=[],references=[],timings=[])
    units=[];step=round(.01*fs)
    for seg in m['segments']:
        a=max(first,seg['file_start']);b=min(last,seg['file_start']+seg['count'])
        for start in range(a,b,step):units.append((start,min(b,start+step),seg))
    done={row['unit_index'] for row in state['completed']};detector=SignatureDetector(p,acquisition_threshold,signature_threshold);oscillators={}
    if search_span_hz and not state.get('acquisition') and units:
        candidates=sorted(set(center_hz+v for v in np.arange(-search_span_hz/2,search_span_hz/2+1,500000))|{center_hz})
        candidates=[c for c in candidates if low+width/2<=c<=high-width/2]
        scores=[];a,b,seg=units[0]
        with ctx.span('drone_signature_acquisition'):
            for candidate in candidates:
                ctx.check();x=_frontend(iq[a:b],fs,candidate-m['center_hz'],rate,oscillators);found=detector.detect(x)
                score=float(np.mean([v['signature_score']*v['spectral_inband_fraction'] for v in found])) if found else 0.
                scores.append(dict(center_hz=float(candidate),score=score,matched_references=len(found)))
        winner=max(scores,key=lambda v:v['score'])
        state['acquisition']=dict(requested_center_hz=center_hz,selected_center_hz=winner['center_hz'] if winner['score'] else center_hz,
            grid_step_hz=500000,tested_centers=scores,scope='First continuous10ms block only; not a whole-record absence test',
            frequency_estimate_note='Coarse center search plus modulo-SCS CP CFO; absolute carrier uncertainty is not calibrated')
        dump(checkpoint,state)
    center_hz=state.get('acquisition',{}).get('selected_center_hz',center_hz)
    for index in [i for i in range(len(units)) if i not in done][:max_blocks]:
        ctx.check();a,b,seg=units[index];pad=round(.001*fs);lo=max(seg['file_start'],a-pad);hi=min(seg['file_start']+seg['count'],b+pad)
        began=time.perf_counter();x=_frontend(iq[lo:hi],fs,center_hz-m['center_hz'],rate,oscillators);front=(time.perf_counter()-began)*1000
        began=time.perf_counter();found=detector.detect(x);core=(time.perf_counter()-began)*1000
        for row in found:
            ref_start=round(lo+row['reference_sample_start']/rate*fs);ref_stop=round(lo+row['reference_sample_stop']/rate*fs)
            if not a<=ref_start<b:continue
            # Energy envelope is a hypothesis attached to the matched reference.
            bin_size=max(8,round(rate*.00002));powers=np.mean(abs(x[:len(x)//bin_size*bin_size].reshape(-1,bin_size))**2,axis=1)
            ref_bin=min(len(powers)-1,max(0,int(row['reference_sample_start']//bin_size)))
            floor=float(np.percentile(powers,15));threshold=max(floor*6.3,float(powers[ref_bin])*.12)
            left=right=ref_bin;limit=round(.002*rate/bin_size)
            while left>0 and ref_bin-left<limit and powers[left-1]>threshold:left-=1
            while right+1<len(powers) and right-ref_bin<limit and powers[right+1]>threshold:right+=1
            envelope_start=max(seg['file_start'],round(lo+left*bin_size/rate*fs));envelope_stop=min(seg['file_start']+seg['count'],round(lo+(right+1)*bin_size/rate*fs))
            state['references'].append(dict(row,reference_sample_start=ref_start,reference_sample_stop=ref_stop,
                sample_start=min(envelope_start,ref_start),sample_stop=max(envelope_stop,ref_stop),
                freq_low_hz=center_hz-width/2,freq_high_hz=center_hz+width/2,center_hz=center_hz,
                estimated_carrier_hz=center_hz+row['carrier_offset_hz'],nominal_channel_bandwidth_hz=width,
                reference_active_bandwidth_hz=p['zc_length']*p['subcarrier_spacing_hz'],
                boundary_kind='associated_energy_envelope_not_decoded_packet',frequency_boundary_kind='signature_profile_nominal_channel',
                hardware_reference_time_s=None if seg.get('hardware_start_s') is None else seg['hardware_start_s']+(ref_start-seg['file_start'])/fs,
                hardware_time_reference=m.get('hardware_time_reference'),gain_db=m.get('gain_db'),power_calibrated=False,
                acquisition_center_hz=m['center_hz'],input_sample_rate_hz=fs))
        state['completed'].append(dict(unit_index=index,sample_start=a,sample_stop=b));state['timings'].append(dict(frontend_ms=front,core_ms=core,input_duration_ms=(b-a)/fs*1000))
        dump(checkpoint,state)
        ctx.event('drone_video_progress',phase='checkpoint',analysis_key=key,total_units=len(units),completed_units=len(state['completed']),matched_references=len(state['references']))
    references=sorted(state['references'],key=lambda r:r['reference_sample_start'])
    starts=np.array([r['reference_sample_start'] for r in references],dtype=float)/fs
    repetition=float(np.median(np.diff(starts))) if len(starts)>1 else None
    completed=len(state['completed'])==len(units)
    value=dict(capture_id=capture_id,analysis_key=key,profile_id=profile_id,profile=p,center_hz=center_hz,
        acquisition=state.get('acquisition'),
        backend=dict(frontend='native_cpp_polyphase' if _NATIVE else 'scipy_polyphase',
            acquisition='native_fftw_overlap_save' if detector.native_handle else 'scipy_overlap_add',refinement='batched_scipy_fft'),
        reason=reason,feedback_evidence_ids=evidence_ids,protocol='unknown',evidence_level='recording_signature_match' if references else 'not_detected_in_checked_scope',
        candidate_family='DJI video-link recording signature' if references else None,
        emitter_identity_confirmed=False,
        attribution=p['attribution'],references=references,matched_references=len(references),estimated_repetition_s=repetition,
        coverage=dict(complete=completed,total_units=len(units),completed_units=len(state['completed']),sample_start=first,sample_stop=last,
            processed_samples=sum(r['sample_stop']-r['sample_start'] for r in state['completed']),gaps=m.get('gaps',[]),all_hardware_time_continuous=not m.get('gaps')),
        timing_ms={k:{q:float(np.percentile([t[k] for t in state['timings']],percentile)) for q,percentile in (('p50',50),('p95',95),('p99',99))} for k in ('frontend_ms','core_ms')} if state['timings'] else {},
        limitations=p['limitations']+['Energy-envelope association is uncertain under cochannel interference; the nominal profile channel is not an independently measured whole-burst bandwidth',
            'Power values retain input IQ scale; not calibrated dBm and not directly comparable across receivers without calibration'])
    aid=ctx.json_artifact('drone_video_analysis',value)
    return dict(evidence_id=aid,**{k:v for k,v in value.items() if k!='references'},references=references[:8],summary_truncated=len(references)>8,
        next_action='Coverage complete; report signature evidence separately from CRC-validated Wi-Fi and retain unresolved activity' if completed else 'Call again with identical parameters to finish full saved time')


def project(ctx):
    """Latest checkpoint snapshot per analysis key,not a sum of partial snapshots."""
    latest={}
    for f in sorted((ctx.run/'index').glob('*.json'),key=lambda p:p.stat().st_mtime_ns):
        rec=json.loads(f.read_text())
        if rec.get('kind')=='drone_video_analysis':
            value=json.loads(Path(rec['path']).read_text());latest[value['analysis_key']]=dict(value,evidence_id=rec['artifact_id'])
    return list(latest.values())
