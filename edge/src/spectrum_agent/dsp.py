from __future__ import annotations
import json
import math
from pathlib import Path
import sys
import numpy as np
from scipy import ndimage, signal
from .common import ROOT, DomainError, dump, finite_number, request_constraints

def traditional_detector(config):
    # Load copied legacy code only when this detector is requested. Offline IQ
    # loading, full-record STFT and scope validation do not require this plugin.
    sys.path.insert(0,str(ROOT/'vendor'/'usrp_client'))
    from segmentation_traditional import TraditionalSegmentationPlugin
    class FastTraditional(TraditionalSegmentationPlugin):
        def _event_from_points(self, *, points, **kwargs):
            if len(points)<8:return None
            return super()._event_from_points(points=points,**kwargs)
        @staticmethod
        def _connected_components(mask):
            labels,_=ndimage.label(mask);result=[]
            for i,box in enumerate(ndimage.find_objects(labels),1):
                if box is not None:result.append((np.argwhere(labels[box]==i)+[box[0].start,box[1].start]).astype(np.int32))
            return result
    return FastTraditional(config)


def load_iq(ctx, artifact_id):
    record = ctx.resolve(artifact_id)
    if record.get("kind") not in ("capture", "subband"):
        raise DomainError("INVALID_ARTIFACT", "Expected IQ artifact")
    meta = json.loads(Path(record["metadata_path"]).read_text())
    if record['kind']=='capture':
        from .frequency_scope import effective_metadata
        meta=effective_metadata(ctx,meta)
    return np.memmap(record["path"], dtype="<c8", mode="r"), meta


def synthetic(ctx, sample_rate_hz=4e6, center_hz=2.4e9, duration_s=.05):
    finite_number(sample_rate_hz, "sample_rate_hz", 1e5, 20e6)
    finite_number(duration_s, "duration_s", .001, .5)
    n = int(sample_rate_hz*duration_s)
    if n > 4_000_000:
        raise DomainError("RESOURCE_LIMIT", "Synthetic capture too large")
    rng = np.random.default_rng(117)
    t = np.arange(n)/sample_rate_hz
    iq = (.02*(rng.normal(size=n)+1j*rng.normal(size=n))).astype(np.complex64)
    # Independent CW and band-limited noisy burst: no fake Wi-Fi or Bluetooth labels.
    iq += .3*np.exp(2j*np.pi*(-.22*sample_rate_hz)*t).astype(np.complex64)
    burst = signal.lfilter(signal.firwin(65, .06), [1], rng.normal(size=n)+1j*rng.normal(size=n))
    gate = (t > duration_s*.3) & (t < duration_s*.7)
    iq += (1.5*burst*gate*np.exp(2j*np.pi*.19*sample_rate_hz*t)).astype(np.complex64)
    aid, path = ctx.artifact("synthetic", "cf32")
    iq.tofile(path)
    meta = dict(kind="synthetic", simulated=True, center_hz=center_hz,
                sample_rate_hz=sample_rate_hz, sample_count=n, duration_s=n/sample_rate_hz,
                requested_span_hz=sample_rate_hz*.8, dtype="complex64_le",
                segments=[dict(file_start=0, count=n, hardware_start_s=None)],
                gaps=[], overflow_count=0,
                warnings=["SYNTHETIC TEST DATA: not an RF observation", "Amplitude is not calibrated dBm"])
    dump(path.with_suffix(".json"), meta)
    ctx.register(aid, path, kind="capture", metadata_path=str(path.with_suffix(".json")))
    return dict(capture_id=aid, **meta)


def segment(ctx, capture_id, fft_size=1024, threshold_db=12.0, strategy='traditional', artifact_kind='segmentation', decision_reason=None,
            local_time_bin_s=20e-6,local_frequency_bin_hz=200e3):
    if fft_size not in (512,1024,2048,4096):
        raise DomainError("INVALID_ARGUMENT", "Unsupported FFT size")
    finite_number(threshold_db, "threshold_db", 3, 30)
    if strategy not in ('traditional','adaptive_hysteresis','multiscale_energy','local_power_tiles'):
        raise DomainError('INVALID_ARGUMENT','Unsupported segmentation strategy')
    iq, meta = load_iq(ctx, capture_id)
    fs, fc = meta["sample_rate_hz"], meta["center_hz"]
    from .frequency_scope import bounds
    low,high=bounds(meta);span=high-low
    hop = fft_size//2
    win = np.hanning(fft_size).astype(np.float32)
    plugin = traditional_detector(dict(
        noise_percentile=50.,global_noise_percentile=50.,
        cfar_threshold_db=threshold_db, persistent_threshold_db=threshold_db,
        min_event_bins=1, min_event_frames=3, morphology_iterations=0,
        persistence_window_frames=3, persistence_min_hits=2, min_snr_db=threshold_db,
        frequency_smoothing_bins=1, max_box_freq_fraction=.99,
        max_events_per_window=10000, min_box_occupancy=.1,
        fuse_freq_gap_bins=1, fuse_time_gap_frames=1))
    if strategy=='adaptive_hysteresis':
        from .scene import AdaptiveDetector
        plugin=AdaptiveDetector(threshold_db)
    events, overview, psd_total = [], [], np.zeros(fft_size)
    occupied_cells = valid_cells = rows_seen = 0
    max_events = 20000
    block = fft_size+hop*255
    frequencies=fc+np.fft.fftshift(np.fft.fftfreq(fft_size,1/fs))
    valid_cols=(frequencies>=low)&(frequencies<=high)
    sys.path.insert(0,str(ROOT/'vendor'/'usrp_client'))
    from segmentation_contracts import SegmentationRequest
    full_window=strategy in ('multiscale_energy','local_power_tiles')
    if strategy=='local_power_tiles':
        if ctx.resolve(capture_id)['kind']!='subband':
            raise DomainError('INVALID_ARTIFACT','local_power_tiles requires an explicitly extracted subband')
        from .local_tiles import scale
        tile_rt,tile_rf=scale(fs/fft_size,hop/fs,local_time_bin_s,local_frequency_bin_hz)
    if full_window:
        cells=sum(max(0,1+(s['count']-fft_size)//hop) for s in meta['segments'])*int(valid_cols.sum())
        if cells>32_000_000:
            raise DomainError('RESOURCE_LIMIT','Full-segment multiscale detector limited to32M valid-band STFT cells; choose a shorter observation or another strategy')
    col_start=int(np.flatnonzero(valid_cols)[0])
    for seg in meta["segments"]:
        begin, end = seg["file_start"], seg["file_start"]+seg["count"]
        begin=max(begin,meta.get('valid_output_start',0))
        continuous=[]
        for offset in range(begin, end-fft_size+1, block-fft_size+hop):
            ctx.check()
            data = np.asarray(iq[offset:min(offset+block,end)])
            frames = np.lib.stride_tricks.sliding_window_view(data, fft_size)[::hop]
            if not len(frames):
                continue
            power = abs(np.fft.fftshift(np.fft.fft(frames*win,axis=1),axes=1))**2 / np.sum(win**2)
            db = (10*np.log10(np.maximum(power,1e-20))).astype(np.float32)
            psd_total += power.sum(axis=0)
            rows_seen += len(db)
            # Occupancy uses a separate reproducible energy threshold over valid columns.
            floor = np.percentile(db[:,valid_cols],50)
            mask = (db[:,valid_cols] > floor+threshold_db)
            occupied_cells += int(mask.sum()); valid_cells += mask.size
            if len(overview)<1024:
                overview.append(db.max(axis=0).tolist())
            if full_window:
                continuous.append(db[:,valid_cols]);continue
            # Background statistics belong to the requested band, not guard bands.
            raw = plugin.detect(SegmentationRequest(db[:,valid_cols],0,"IQ0",fft_size,len(db)))
            for e in raw:
                b=e["box"]
                lo=fc-fs/2+(col_start+b["x1"]-.5)*fs/fft_size
                hi=fc-fs/2+(col_start+b["x2"]+.5)*fs/fft_size
                if hi<low or lo>high:
                    continue
                start=offset+b["y1"]*hop
                stop=min(end,offset+b["y2"]*hop+fft_size)
                events.append(dict(event_id="e"+str(len(events)),
                    capture_id=capture_id, segment_file_start=begin,
                    sample_start=int(start),sample_stop=int(stop),
                    time_start_s=(start-begin)/fs+(seg["hardware_start_s"] or begin/fs),
                    duration_s=(stop-start)/fs, freq_low_hz=max(low,lo),
                    freq_high_hz=min(high,hi), bandwidth_hz=min(high,hi)-max(low,lo),
                    snr_db=e["snr_db"], peak_db_relative=e["peak_db"],
                    observed_bin_occupancy=e["occupancy"],
                    evidence_level="detected", protocol="unknown",
                    boundary_truncated=lo<=low or hi>=high,
                    label=e["label"]))
                if len(events)>=max_events:
                    raise DomainError("RESOURCE_LIMIT","Event capacity reached; partial output not silently truncated")
        if continuous:
            if strategy=='local_power_tiles':
                from .local_tiles import detect
            else:
                from .multiscale import detect
            ctx.check()
            extra=dict(time_bin_s=local_time_bin_s,frequency_bin_hz=local_frequency_bin_hz) if strategy=='local_power_tiles' else {}
            for e in detect(np.concatenate(continuous,axis=0),threshold_db,fs/fft_size,hop/fs,**extra):
                b=e['box'];start=begin+b['y1']*hop;stop=min(end,begin+b['y2']*hop+fft_size)
                lo=max(low,fc-fs/2+(col_start+b['x1']-.5)*fs/fft_size)
                hi=min(high,fc-fs/2+(col_start+b['x2']+.5)*fs/fft_size)
                events.append(dict(event_id='e'+str(len(events)),capture_id=capture_id,segment_file_start=begin,
                    sample_start=int(start),sample_stop=int(stop),duration_s=(stop-start)/fs,
                    time_start_s=(start-begin)/fs+(seg['hardware_start_s'] or begin/fs),
                    freq_low_hz=lo,freq_high_hz=hi,bandwidth_hz=hi-lo,snr_db=e['snr_db'],
                    peak_db_relative=e['peak_db'],observed_bin_occupancy=e['occupancy'],
                    protocol='unknown',evidence_level='detected',label=e['label'],
                    boundary_truncated=lo<=low or hi>=high))
                if len(events)>=max_events:raise DomainError('RESOURCE_LIMIT','Event capacity reached')
    if not rows_seen:
        raise DomainError("INSUFFICIENT_EVIDENCE","Capture shorter than FFT")
    from .catalog import group_events
    groups=([dict(e,group_id='g'+str(i),event_count=1,event_ids=[e['event_id']]) for i,e in enumerate(events)]
            if full_window else group_events(events,fs,hop))
    result=dict(capture_id=capture_id, algorithm={'traditional':'traditional-requested-band-v1.3','adaptive_hysteresis':'adaptive-requested-band-v1.1','multiscale_energy':'full-segment-multiscale-v1','local_power_tiles':'whole-local-linear-power-tiles-v1'}[strategy],
        strategy=strategy,decision_reason=decision_reason,
        fft_size=fft_size, hop=hop, threshold_db=threshold_db,
        frequency_resolution_hz=fs/fft_size,time_resolution_s=hop/fs,
        events=events,signal_groups=groups,
        coverage=dict(observed_samples=len(iq),observed_duration_s=len(iq)/fs,
                      requested_range_hz=[low,high],
                      occupancy_fraction=occupied_cells/max(valid_cells,1),
                      occupancy_definition="union of above-threshold STFT cells in requested band",
                      overflow_count=meta.get("overflow_count",0),gaps=meta.get("gaps",[])),
        simulated=meta.get("simulated",False),
        warnings=["Detection thresholds can miss weak or overlapping signals",
                  "Groups are spectral tracks, not proven physical transmitters",
                  "PSD preview is max-pooled and limited to first 1024 analysis blocks"])
    if strategy=='local_power_tiles':
        result['local_scale']=dict(requested_time_bin_s=local_time_bin_s,requested_frequency_bin_hz=local_frequency_bin_hz,
            actual_time_bin_s=tile_rt*hop/fs,actual_frequency_bin_hz=tile_rf*fs/fft_size,
            background_scope='whole continuous local segment; never FFT-batch boundaries',
            no_duration_pruning=True,no_top_k_pruning=True)
    aid=ctx.json_artifact(artifact_kind,result)
    preview=ctx.json_artifact("psd",dict(psd_db=(10*np.log10(np.maximum(psd_total/rows_seen,1e-20))).tolist(),
                             center_hz=fc,sample_rate_hz=fs))
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        fig,ax=plt.subplots(figsize=(11,5))
        ax.imshow(np.array(overview),aspect="auto",origin="lower",
                  extent=[(fc-fs/2)/1e6,(fc+fs/2)/1e6,0,len(overview)],
                  cmap="viridis")
        ax.set(xlabel="Frequency (MHz)",ylabel="Analysis block (max pooled)",
               title="Synthetic data" if result["simulated"] else "Received spectrum")
        image_id,image_path=ctx.artifact("waterfall","png")
        fig.savefig(image_path,dpi=140,bbox_inches="tight");plt.close(fig)
        ctx.register(image_id,image_path,kind="waterfall")
    except ImportError:
        image_id=None
    return dict(segmentation_id=aid,psd_id=preview,waterfall_id=image_id,
                signal_groups=groups[:30],total_events=len(events),
                total_groups=len(groups),summary_truncated=len(groups)>30,
                strategy=strategy,fft_size=fft_size,threshold_db=threshold_db,
                coverage=result["coverage"],warnings=result["warnings"])


def extract(ctx,capture_id,center_hz,bandwidth_hz,sample_start=0,sample_stop=None,output_rate_hz=None,margin_s=.005):
    iq,meta=load_iq(ctx,capture_id)
    fs=meta["sample_rate_hz"]
    finite_number(center_hz,"center_hz",meta["center_hz"]-fs/2,meta["center_hz"]+fs/2)
    finite_number(bandwidth_hz,"bandwidth_hz",1e3,fs*.8)
    from .frequency_scope import bounds
    low,high=bounds(meta)
    if center_hz-bandwidth_hz/2<low-1 or center_hz+bandwidth_hz/2>high+1:
        raise DomainError("OUT_OF_BAND",f"Requested subband passband exceeds task analysis range [{low}, {high}] Hz; center_hz must be within [{low+bandwidth_hz/2}, {high-bandwidth_hz/2}]")
    transition_room=fs/2-abs(center_hz-meta['center_hz'])-bandwidth_hz/2
    if transition_room<=0:
        raise DomainError("OUT_OF_BAND","Filter transition band exceeds input range")
    decimation=max(1,int(fs/(bandwidth_hz*1.6)))
    native_rate=fs/decimation
    if output_rate_hz is not None and output_rate_hz!=native_rate:
        finite_number(output_rate_hz,"output_rate_hz",bandwidth_hz*1.1,fs)
    sample_stop=len(iq) if sample_stop is None else sample_stop
    if not (isinstance(sample_start,int) and isinstance(sample_stop,int) and 0<=sample_start<sample_stop<=len(iq)):
        raise DomainError("INVALID_ARGUMENT","Invalid sample interval")
    if sample_stop-sample_start>40_000_000:
        raise DomainError("RESOURCE_LIMIT","Subband extraction limited to 40M input samples per call")
    segment=next((s for s in meta["segments"] if sample_start>=s["file_start"] and sample_stop<=s["file_start"]+s["count"]),None)
    if segment is None:
        raise DomainError("CAPTURE_GAP","Cannot filter across a hardware gap")
    finite_number(margin_s,"margin_s",.001,.02)
    requested_start,requested_stop=sample_start,sample_stop
    sample_start=max(segment["file_start"],sample_start-int(fs*margin_s))
    sample_stop=min(segment["file_start"]+segment["count"],sample_stop+int(fs*margin_s))
    if sample_stop-sample_start>40_000_000:
        raise DomainError("RESOURCE_LIMIT","Padded subband interval exceeds40M input samples")
    # Fixed flowgraph; no model-provided Python code.
    from gnuradio import gr,blocks,filter as grfilter
    transition_hz=min(bandwidth_hz*.15,transition_room*.9)
    taps=grfilter.firdes.low_pass(1.,fs,bandwidth_hz*.5,transition_hz)
    source=blocks.vector_source_c(np.asarray(iq[sample_start:sample_stop]).tolist(),False)
    translated=grfilter.freq_xlating_fir_filter_ccc(decimation,taps,center_hz-meta["center_hz"],fs)
    sink=blocks.vector_sink_c()
    tb=gr.top_block();tb.connect(source,translated,sink);tb.run()
    output=np.asarray(sink.data(),dtype=np.complex64)
    rate=fs/decimation
    if output_rate_hz is not None and output_rate_hz!=rate:
        finite_number(output_rate_hz,"output_rate_hz",bandwidth_hz*1.1,fs)
        from fractions import Fraction
        ratio=Fraction(output_rate_hz/rate).limit_denominator(10000)
        output=signal.resample_poly(output,ratio.numerator,ratio.denominator).astype(np.complex64)
        rate*=ratio.numerator/ratio.denominator
    aid,path=ctx.artifact("subband","cf32");output.tofile(path)
    delay=(len(taps)-1)/2/fs
    submeta=dict(center_hz=center_hz,sample_rate_hz=rate,sample_count=len(output),
        duration_s=len(output)/rate,requested_span_hz=bandwidth_hz,simulated=meta.get("simulated",False),
        parent_artifact_id=capture_id,parent_sample_start=sample_start,
        requested_sample_start=requested_start,requested_sample_stop=requested_stop,
        actual_parent_sample_stop=sample_stop,context_margin_s=margin_s,
        filter_transition_hz=transition_hz,
        filter_group_delay_s=delay,valid_output_start=int(math.ceil(delay*rate*2)),
        segments=[dict(file_start=0,count=len(output),hardware_start_s=None)],gaps=[],
        backend="GNU Radio freq_xlating_fir_filter_ccc + optional scipy polyphase resampling")
    dump(path.with_suffix(".json"),submeta)
    ctx.register(aid,path,kind="subband",metadata_path=str(path.with_suffix(".json")))
    return dict(artifact_id=aid,**submeta)


def features(ctx,artifact_id):
    iq,meta=load_iq(ctx,artifact_id)
    x=np.asarray(iq[:min(len(iq),2_000_000)])
    if len(x)<256:
        raise DomainError("INSUFFICIENT_EVIDENCE","At least 256 samples needed")
    fs=meta["sample_rate_hz"]
    freq,power=signal.welch(x,fs,nperseg=min(4096,len(x)),return_onesided=False)
    order=np.argsort(freq);freq,power=freq[order],power[order]
    cumulative=np.cumsum(power)/max(float(power.sum()),1e-20)
    lo=float(freq[np.searchsorted(cumulative,.005)])
    hi=float(freq[min(len(freq)-1,np.searchsorted(cumulative,.995))])
    amplitude=abs(x)
    phase=np.angle(x[1:]*np.conj(x[:-1]))*fs/(2*np.pi)
    flatness=float(np.exp(np.mean(np.log(np.maximum(power,1e-20))))/max(np.mean(power),1e-20))
    bw=max(hi-lo,fs/len(freq))
    hypotheses=[]
    amplitude_cv=float(np.std(amplitude)/max(np.mean(amplitude),1e-20))
    phase_spread=float(np.std(phase))
    # Observable cyclic-prefix hypothesis tests; no protocol inference from this statistic alone.
    cp_tests=[]
    for useful_s in (3.2e-6,6.4e-6,12.8e-6):
        lag=int(round(useful_s*fs));cp=max(2,lag//4);period=lag+cp
        if lag<8 or len(x)<period*8: continue
        best=0.
        count=min(128,(len(x)-period)//period)
        for offset in range(0,period,max(1,cp//2)):
            starts=offset+np.arange(count)*period
            a=x[starts[:,None]+np.arange(cp)]
            b=x[starts[:,None]+lag+np.arange(cp)]
            corr=abs(np.sum(a*np.conj(b),axis=1))**2
            norm=np.sum(abs(a)**2,axis=1)*np.sum(abs(b)**2,axis=1)
            score=float(np.mean(corr/np.maximum(norm,1e-20)))
            best=max(best,score)
        cp_tests.append(dict(useful_symbol_s=useful_s,lag_samples=lag,guard_samples=cp,
                             normalized_correlation=best))
    # A bimodal discriminator is evidence for FSK-like behavior, not for Bluetooth specifically.
    spread=max(float(np.max(phase)-np.min(phase)),1.)
    hist,edges=np.histogram(phase,bins=48,range=(float(np.min(phase))-spread*.05,float(np.max(phase))+spread*.05))
    peaks,_=signal.find_peaks(hist.astype(float),distance=8,prominence=max(1,float(hist.max())*.12))
    peaks=sorted(peaks,key=lambda p:hist[p],reverse=True)
    bimodal=False
    if len(peaks)>=2:
        left,right=sorted(peaks[:2])
        valley=float(hist[left:right+1].min())
        bimodal=valley<min(hist[left],hist[right])*.65
    if bw<fs*.015:
        hypotheses.append(dict(label="CW-like",reason="Narrow occupied spectrum; phase stability requires further validation"))
    if bw>fs*.15 and amplitude_cv>.2 and any(t["normalized_correlation"]>.5 for t in cp_tests):
        hypotheses.append(dict(label="OFDM-like",reason="Significant cyclic-prefix correlation at a tested useful-symbol duration; not protocol verification"))
    if bimodal and amplitude_cv<.65 and bw>fs*.02 and phase_spread>fs*.001:
        hypotheses.append(dict(label="FSK-like",reason="Bimodal frequency discriminator with relatively stable envelope; not protocol verification"))
    if flatness>.5:
        hypotheses.append(dict(label="noise-like",reason="Relatively flat power spectrum; not proof of interference"))
    if not hypotheses:
        hypotheses.append(dict(label="unknown",reason="Generic spectral features alone do not identify modulation"))
    result=dict(artifact_id=artifact_id,occupied_bandwidth_99_hz=bw,
        relative_power_db=float(10*np.log10(max(np.mean(amplitude**2),1e-20))),
        papr_db=float(10*np.log10(max(np.max(amplitude**2),1e-20)/max(np.mean(amplitude**2),1e-20))),
        phase_increment_median_hz=float(np.median(phase)),spectral_flatness=flatness,
        envelope_coefficient_of_variation=amplitude_cv,frequency_discriminator_std_hz=phase_spread,
        frequency_discriminator_bimodal=bool(bimodal),cyclic_prefix_tests=cp_tests,
        clipping_indicator_fraction=float(np.mean((abs(x.real)>.99)|(abs(x.imag)>.99))),
        clipping_note="Indicator assumes UHD normalized IQ; not an RF calibration",
        waveform_hypotheses=hypotheses,evidence_level="candidate",protocol="unknown",
        evaluated_samples=len(x),sample_rate_hz=fs,simulated=meta.get("simulated",False))
    aid=ctx.json_artifact("features",result)
    return dict(feature_artifact_id=aid,**result)
