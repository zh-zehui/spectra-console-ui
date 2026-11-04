"""Bounded receive-only libcrafter adapter for saved20MS/s IQ."""
import json
import os
from pathlib import Path
import subprocess
import time
import zlib
import numpy as np
from .common import ROOT,DomainError
from .dsp import load_iq

PIN='f9ef000bc402eebc676f6b4708395aa8ac169450'
PROFILES={'wifi_crafter_dsss':'dsss','wifi_crafter_all':'all'}


def quantize(iq):
    components=np.asarray(iq,dtype=np.complex64).view(np.float32)
    if not np.isfinite(components).all():raise DomainError('INVALID_IQ','IQ contains NaN or infinity')
    peak=float(np.max(np.abs(components))) if len(components) else 0.
    scale=120./peak if peak>0 else 1.
    scaled=np.rint(components*scale)
    clipping=int(np.count_nonzero((scaled<-127)|(scaled>127)))
    data=np.clip(scaled,-127,127).astype(np.int8)
    return data,dict(input_format='cf32',output_format='cs8',linear_scale=scale,
        component_peak=peak,clipped_components=clipping,
        policy='One fixed scale for the entire input; no per-chunk AGC;8-bit quantization can lose weak signals')


def run_cs8(path,center_hz,mode,timeout_s=120):
    binary=ROOT/'runtime/libcrafter/target/release/spectra-crafter-rx'
    if not binary.exists():raise DomainError('DEPENDENCY_MISSING','Project-local libcrafter receiver is not built')
    parent=os.getpid()
    def parent_death_signal():
        import ctypes,signal
        if ctypes.CDLL(None).prctl(1,signal.SIGTERM)!=0:os._exit(125)
        if os.getppid()!=parent:os._exit(125)
    try:
        completed=subprocess.run([str(binary),str(path),str(round(center_hz)),mode],
            capture_output=True,text=True,timeout=timeout_s,
            preexec_fn=parent_death_signal if os.name=='posix' else None)
    except subprocess.TimeoutExpired as exc:
        raise DomainError('DECODER_TIMEOUT','libcrafter exceeded its receive deadline; not a zero-frame result') from exc
    if completed.returncode:
        raise DomainError('DECODER_ERROR','libcrafter failed: '+completed.stderr[-1000:])
    if len(completed.stdout)>32_000_000:raise DomainError('RESOURCE_LIMIT','Decoder output capacity exceeded')
    try:result=json.loads(completed.stdout)
    except ValueError as exc:raise DomainError('DECODER_ERROR','Invalid libcrafter JSON') from exc
    if result.get('schema')!='spectra.crafter.rx/v1':raise DomainError('DECODER_ERROR','Unexpected receiver schema')
    return result


def verify(ctx,artifact_id,profile='wifi_crafter_all'):
    if profile not in PROFILES:raise DomainError('UNSUPPORTED_PROFILE','Unsupported libcrafter profile')
    iq,meta=load_iq(ctx,artifact_id)
    if abs(meta['sample_rate_hz']-20e6)>1:raise DomainError('INVALID_ARGUMENT','libcrafter requires20MS/s subband IQ')
    if meta.get('requested_span_hz',0)<17e6:raise DomainError('INVALID_ARGUMENT','Use a17..18MHz extraction passband at20MS/s')
    if not 160<=len(iq)<=4_000_000:raise DomainError('RESOURCE_LIMIT','Use160..4M samples per call')
    segments=meta.get('segments',[])
    if meta.get('gaps') or len(segments)!=1 or segments[0].get('file_start',0)!=0 or segments[0]['count']!=len(iq):
        raise DomainError('CAPTURE_GAP','Decode a single complete contiguous subband')
    ctx.check();started=time.perf_counter()
    data,conversion=quantize(iq)
    aid,path=ctx.artifact('crafter_input','cs8');data.tofile(path)
    ctx.register(aid,path,kind='decoder_input',parent_artifact_id=artifact_id,conversion=conversion)
    with ctx.span('libcrafter_receive',profile=profile,artifact_id=artifact_id):
        out=run_cs8(path,meta['center_hz'],PROFILES[profile])
    frames=[]
    for native in out['frames']:
        try:psdu=bytes.fromhex(native['psdu_hex'])
        except (ValueError,KeyError) as exc:raise DomainError('DECODER_ERROR','Malformed received PSDU') from exc
        if len(psdu)<14 or zlib.crc32(psdu[:-4])!=int.from_bytes(psdu[-4:],'little'):
            raise DomainError('DECODER_INTEGRITY_ERROR','Independent MAC FCS recheck rejected native frame')
        begin=int(native['sample_start']);end=int(native['sample_stop'])
        if not 0<=begin<end<=len(iq):raise DomainError('DECODER_TIMING_ERROR','Receiver frame outside input IQ')
        diagnostics=native.get('diagnostics',[])
        ht=next((d['value'] for d in diagnostics if d['kind']=='ht_sig_pass'),{})
        ampdu=next((d['value'] for d in diagnostics if d['kind']=='ampdu_member'),None)
        frames.append(dict(frame_hex=psdu[:-4].hex(),mac_header_hex=psdu[:-4][:24].hex(),
            frame_length=len(psdu)-4,fcs_hex=psdu[-4:].hex(),fcs_pass=True,
            fcs_verified_by='libcrafter native plus independent Python zlib CRC32',
            evidence_level='decoded',center_hz=meta['center_hz'],phy_format=native['phy_format'],
            rate_bps=native['rate_bps'],mcs=ht.get('mcs'),ht_header=ht or None,ampdu_member=ampdu,
            subband_sample_start=begin,subband_sample_stop=end,
            timing_uncertainty_samples=native['timing_uncertainty_samples'],timing_uncertainty_calibrated=False,
            timing_scope='containing_ppdu_not_individual_aggregated_mpdu' if ampdu is not None else 'packet',
            timing_status='libcrafter_source_sample_estimate',
            occupied_bandwidth_hz=meta['requested_span_hz'],
            bandwidth_kind='decoder_passband_not_measured_occupied_bandwidth',diagnostics=diagnostics))
    counts=dict(out['diagnostic_counts']);counts['fcs_pass']=len(frames)
    for item in out['header_evidence']:
        if item['kind']=='ampdu_errors':
            for key in ('invalid_delimiters','invalid_fcs','truncated_mpdus','oversized_mpdus'):
                counts['ampdu_'+key]=counts.get('ampdu_'+key,0)+item['value'][key]
    stages=dict(counts=counts,unique_valid_frames=len(frames),
        strongest_stage='mac_fcs_pass' if frames else 'phy_header_pass' if counts.get('ht_sig_pass') else 'no_valid_frame',
        count_unit='Emitted libcrafter diagnostics, not an exhaustive count of all synchronization trials',
        header_check='Native HT-SIG/DSSS PLCP checks; independently rechecked MAC FCS')
    result=dict(artifact_id=artifact_id,protocol='IEEE802.11',profile='libcrafter '+PROFILES[profile],
        verification_profile=profile,backend='pellegre/libcrafter '+PIN,
        valid_frames=len(frames),frames=frames,verification_stages=stages,
        analysis_coverage=dict(input_samples=len(iq),processed_samples=out['processed_samples'],
            complete=out['processed_samples']==len(iq)),
        header_evidence=out['header_evidence'],input_conversion=conversion,converted_iq_id=aid,
        elapsed_s=time.perf_counter()-started,native_elapsed_s=out['elapsed_s'],
        simulated=meta.get('simulated',False),evidence_level='decoded' if frames else 'unknown',
        warnings=['20MS/s CS8 receive only; quantization and extraction can reduce sensitivity',
            'DSSS/CCK and one-data-stream HT20 only; no HT40,VHT,HE/EHT or general multi-stream support',
            'HT A-MPDU member boundaries reference containing PPDU; no independent MPDU air-time localization',
            'Zero valid frames does not prove absence; header-only success does not confirm data integrity'])
    evidence=ctx.json_artifact('wifi_verification',result)
    return dict(result,evidence_id=evidence,
        frames=[{k:v for k,v in f.items() if k!='frame_hex'} for f in frames[:10]],
        header_evidence=out['header_evidence'][:20],
        header_summary_truncated=len(out['header_evidence'])>20,frame_summary_truncated=len(frames)>10)
