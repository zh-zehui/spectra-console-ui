"""Whole-record STFT -> power tiles -> coarse activity proposals.

FFT batches are a memory detail. Statistics and components span a complete
continuous capture segment; the raw recording remains the fine-analysis source.
"""
import math
import numpy as np
from scipy import ndimage
from .common import DomainError,finite_number


def tile_mask(mean_power, peak_power, threshold_db):
    mean_db=10*np.log10(np.maximum(mean_power,1e-20))
    peak_db=10*np.log10(np.maximum(peak_power,1e-20))
    # Separate average-power background and extreme-value background. Using the
    # latter as a short-burst branch does not compare noise maxima to noise means.
    mean_floor=np.percentile(mean_db,20,axis=0)
    peak_floor=np.percentile(peak_db,20,axis=0)
    mean_floor=np.minimum(mean_floor,np.percentile(mean_floor,25)+4)
    peak_floor=np.minimum(peak_floor,np.percentile(peak_floor,25)+4)
    average=mean_db-mean_floor[None,:]
    transient=peak_db-peak_floor[None,:]
    seed=(average>=threshold_db)|(transient>=threshold_db+2)
    support=(average>=max(2,threshold_db-3))|(transient>=max(3,threshold_db-1))
    active=ndimage.binary_propagation(seed,mask=support)
    # Closing joins a coarse envelope; never drops original positive tiles,
    # including signal at a recording/band edge. No duration-based rejection.
    padded=np.pad(active,((2,2),(2,2)))
    closed=ndimage.binary_closing(padded,structure=np.ones((3,3)))[2:-2,2:-2]
    mask=active|closed
    labels,_=ndimage.label(mask,structure=np.ones((3,3)))
    regions=[]
    for i,box in enumerate(ndimage.find_objects(labels),1):
        if box is None:continue
        selected=labels[box]==i
        regions.append(dict(t0=box[0].start,t1=box[0].stop,f0=box[1].start,f1=box[1].stop,
            active_tiles=int(selected.sum()),occupancy=float(selected.mean()),
            snr_db=float(np.maximum(average[box],transient[box])[selected].max()),
            peak_db=float(peak_db[box][selected].max())))
    return regions,dict(mean_db=mean_db,peak_db=peak_db,active=active,mask=mask)


def segment(ctx,capture_id,fft_size=1024,time_bin_s=.0001,frequency_bin_hz=1e6,threshold_db=6,
            decision_reason=None,batch_frames=256,sample_start=0,sample_stop=None,artifact_kind='segmentation'):
    from .dsp import load_iq
    if ctx.resolve(capture_id).get('kind')!='capture':raise DomainError('INVALID_ARTIFACT','Whole-IQ coarse stage requires the original task-owned capture, not a filtered subband')
    if fft_size not in (512,1024,2048,4096):raise DomainError('INVALID_ARGUMENT','Unsupported FFT size')
    finite_number(time_bin_s,'time_bin_s',.00002,.002)
    finite_number(frequency_bin_hz,'frequency_bin_hz',1e5,5e6)
    finite_number(threshold_db,'threshold_db',3,15)
    if not isinstance(batch_frames,int) or not 1<=batch_frames<=1024:raise DomainError('INVALID_ARGUMENT','Invalid FFT batch size')
    iq,meta=load_iq(ctx,capture_id);fs=meta['sample_rate_hz'];fc=meta['center_hz']
    sample_stop=len(iq) if sample_stop is None else sample_stop
    if any(isinstance(v,bool) or not isinstance(v,int) for v in (sample_start,sample_stop)) or not 0<=sample_start<sample_stop<=len(iq):
        raise DomainError('INVALID_ARGUMENT','Valid integer source sample interval required')
    from .frequency_scope import bounds
    low,high=bounds(meta);span=high-low;hop=fft_size//2;win=np.hanning(fft_size)
    if time_bin_s<hop/fs:raise DomainError('INVALID_ARGUMENT','Coarse time cell must be at least one STFT hop')
    freq=fc+np.fft.fftshift(np.fft.fftfreq(fft_size,1/fs));valid=np.flatnonzero((freq>=low)&(freq<=high))
    if not len(valid):raise DomainError('INSUFFICIENT_EVIDENCE','No STFT frequency bins in requested band')
    if frequency_bin_hz<fs/fft_size:raise DomainError('INVALID_ARGUMENT','Coarse frequency cell must be at least one FFT bin')
    nf=int(math.ceil(span/frequency_bin_hz));ids=np.minimum(nf-1,((freq[valid]-low)/frequency_bin_hz).astype(int))
    starts=np.r_[0,np.flatnonzero(np.diff(ids))+1];unique=ids[starts]
    counts=np.diff(np.r_[starts,len(ids)])
    events=[];grids=[];frame_count=0;covered_samples=0;tail_only_samples=0
    for seg in meta['segments']:
        begin=max(seg['file_start'],meta.get('valid_output_start',0),sample_start);end=min(seg['file_start']+seg['count'],sample_stop)
        if begin>=end:continue
        if end-begin<fft_size:tail_only_samples+=end-begin;continue
        nt=int(math.ceil((end-begin)/fs/time_bin_s))
        if nt*nf>2_000_000:raise DomainError('RESOURCE_LIMIT','Coarse grid limited to2M cells per continuous segment; increase coarse cell size')
        total=np.zeros((nt,nf));peak=np.zeros_like(total);hits=np.zeros(nt,dtype=np.int64)
        offsets=np.arange(begin,end-fft_size+1,hop,dtype=np.int64)
        if offsets[-1]+fft_size<end:offsets=np.r_[offsets,end-fft_size]
        for pos in range(0,len(offsets),batch_frames):
            ctx.check();batch=offsets[pos:pos+batch_frames]
            frames=np.array([np.asarray(iq[int(a):int(a)+fft_size])*win for a in batch])
            power=abs(np.fft.fftshift(np.fft.fft(frames,axis=1),axes=1))**2/np.sum(win**2)
            power=power[:,valid]
            band_mean=np.zeros((len(batch),nf));band_peak=np.zeros_like(band_mean)
            band_mean[:,unique]=np.add.reduceat(power,starts,axis=1)/counts[None,:]
            band_peak[:,unique]=np.maximum.reduceat(power,starts,axis=1)
            rows=np.minimum(nt-1,((batch-begin+fft_size/2)/fs/time_bin_s).astype(int))
            np.add.at(total,rows,band_mean);np.maximum.at(peak,rows,band_peak);np.add.at(hits,rows,1)
            frame_count+=len(batch)
        mean=total/np.maximum(hits[:,None],1)
        boxes,grid=tile_mask(mean,peak,threshold_db)
        for box in boxes:
            # Include the actual FFT support at tile boundaries. This is a coarse
            # proposal, not a precise packet onset/offset or occupied bandwidth.
            start=max(begin,int(begin+box['t0']*time_bin_s*fs)-fft_size//2)
            stop=min(end,int(math.ceil(begin+box['t1']*time_bin_s*fs))+fft_size//2)
            lo=max(low,low+box['f0']*frequency_bin_hz-fs/fft_size/2)
            hi=min(high,low+box['f1']*frequency_bin_hz+fs/fft_size/2)
            events.append(dict(event_id='e'+str(len(events)),group_id='g'+str(len(events)),
                capture_id=capture_id,segment_file_start=begin,sample_start=start,sample_stop=stop,
                time_start_s=(start-seg['file_start'])/fs+(seg.get('hardware_start_s') if seg.get('hardware_start_s') is not None else seg['file_start']/fs),duration_s=(stop-start)/fs,
                freq_low_hz=lo,freq_high_hz=hi,bandwidth_hz=hi-lo,snr_db=box['snr_db'],peak_db_relative=box['peak_db'],
                observed_bin_occupancy=box['occupancy'],active_coarse_tiles=box['active_tiles'],
                event_count=1,event_ids=['e'+str(len(events))],protocol='unknown',evidence_level='coarse_activity',
                label='coarse_activity_region',boundary_truncated=lo<=low or hi>=high,
                requires_fine_analysis=True,may_contain_multiple_or_overlapping_signals=True))
        grids.append(dict(segment_file_start=begin,segment_file_stop=end,rows=nt,columns=nf,
            mean_db=np.round(grid['mean_db'],2).tolist(),peak_db=np.round(grid['peak_db'],2).tolist(),
            positive_tiles=int(grid['active'].sum()),envelope_tiles=int(grid['mask'].sum()),
            sampled_fft_frames=len(offsets),all_positive_tiles_retained=bool(np.all(grid['mask'][grid['active']]))))
        covered_samples+=end-begin
    if not frame_count:raise DomainError('INSUFFICIENT_EVIDENCE','No continuous segment is long enough for STFT')
    if len(events)>20000:raise DomainError('RESOURCE_LIMIT','Coarse proposal count exceeds20000; no silent truncation')
    grid_id=ctx.json_artifact('coarse_stft',dict(capture_id=capture_id,fft_size=fft_size,hop=hop,
        time_bin_s=time_bin_s,frequency_bin_hz=frequency_bin_hz,low_hz=low,high_hz=high,segments=grids,
        aggregation='mean linear power plus independently background-normalized peak power',
        batch_boundaries_are_detection_boundaries=False))
    value=dict(capture_id=capture_id,strategy='coarse_to_fine',algorithm='whole-record-stft-coarse-power-v1',
        segmentation_level='coarse_activity',coarse_stft_id=grid_id,decision_reason=decision_reason,
        fft_size=fft_size,hop=hop,threshold_db=threshold_db,time_resolution_s=hop/fs,frequency_resolution_hz=fs/fft_size,
        proposal_time_cell_s=time_bin_s,proposal_frequency_cell_hz=frequency_bin_hz,
        events=events,signal_groups=events,simulated=meta.get('simulated',False),
        coverage=dict(observed_samples=sample_stop-sample_start,stft_covered_samples=covered_samples,short_segment_unanalyzed_samples=tail_only_samples,
            source_sample_start=sample_start,source_sample_stop=sample_stop,
            observed_duration_s=(sample_stop-sample_start)/fs,requested_range_hz=[low,high],all_continuous_samples_covered=covered_samples==sample_stop-sample_start,
            fft_frames=frame_count,overflow_count=meta.get('overflow_count',0),gaps=meta.get('gaps',[])),
        warnings=['Coarse activity envelopes, NOT packet boundaries, protocols or transmitter counts',
            'Overlapping/weak signals can share a coarse parent; raw IQ and peak-power tiles remain available for fine analysis',
            'Background and detection accuracy are not calibrated; persistent full-band energy can bias noise estimation'])
    aid=ctx.json_artifact(artifact_kind,value)
    return dict(segmentation_id=aid,coarse_stft_id=grid_id,segmentation_level='coarse_activity',
        strategy=value['strategy'],total_groups=len(events),total_events=len(events),signal_groups=events[:30],
        summary_truncated=len(events)>30,coverage=value['coverage'],
        proposal_time_cell_s=time_bin_s,proposal_frequency_cell_hz=frequency_bin_hz,warnings=value['warnings'])
