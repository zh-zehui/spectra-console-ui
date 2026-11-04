"""One model-requested AuxPtr follow-up against an existing recording, never RF."""
import json
import math
from pathlib import Path
from .common import DomainError
from .protocols import ble_channel_hz


def follow(ctx,verification_id,frame_index):
    from . import dsp,investigation
    rec=ctx.resolve(verification_id)
    if rec['kind']!='ble_verification':
        raise DomainError('INVALID_ARTIFACT','Expected a BLE verification artifact')
    evidence=json.loads(Path(rec['path']).read_text())
    frames=evidence.get('frames',[])
    if isinstance(frame_index,bool) or not isinstance(frame_index,int) or not 0<=frame_index<len(frames):
        raise DomainError('INVALID_ARGUMENT','frame_index outside saved frames')
    frame=frames[frame_index];ptr=frame.get('aux_ptr')
    if not frame.get('crc_pass') or not ptr:
        raise DomainError('INSUFFICIENT_EVIDENCE','Requires a CRC-valid advertising frame with AuxPtr')
    result=dict(source_verification_id=verification_id,source_frame_index=frame_index,
        aux_ptr=ptr,new_rf_capture=False,chain_confirmed=False)
    def finish(status,**values):
        result.update(status=status,**values)
        aid=ctx.json_artifact('ble_aux_followup',result)
        return dict(result,evidence_id=aid)
    if not ptr['offset_us']:
        return finish('incomplete_advertising_data',reason='Zero AuxOffset: no follow-up packet is scheduled')
    if ptr['phy']=='LE Coded':
        return finish('unsupported_phy',reason='LE Coded decoder is not installed')
    source=ctx.resolve(evidence['artifact_id'])
    meta=json.loads(Path(source['metadata_path']).read_text())
    parent=meta.get('parent_artifact_id')
    if not parent or ctx.resolve(parent)['kind']!='capture':
        raise DomainError('INVALID_ARTIFACT','Follow-up requires a direct subband of a saved capture')
    iq,cap=dsp.load_iq(ctx,parent);fs=cap['sample_rate_hz'];rate=meta['sample_rate_hz']
    start=frame.get('subband_sample_start')
    if start is None:raise DomainError('INSUFFICIENT_EVIDENCE','Source packet lacks timing')
    # Legacy primary timestamp references AA; auxiliary receiver includes preamble.
    reference=(meta['parent_sample_start']/fs+start/rate-meta.get('filter_group_delay_s',0))
    if 'subband_sample_stop' not in frame:reference-=8e-6
    target=reference+ptr['offset_us']*1e-6
    # AuxOffset is a quantized interval. Allow uncalibrated source timing and clock drift.
    guard=20e-6+ptr['offset_us']*1e-6*.0005
    lo=target-guard;hi=target+ptr['offset_unit_us']*1e-6+guard
    profile='ble_aux_2m' if ptr['phy']=='LE2M' else 'ble_aux_1m'
    output_rate=8e6 if profile=='ble_aux_2m' else 4e6
    bandwidth=3e6 if profile=='ble_aux_2m' else 2e6
    # Max PDU plus preamble/AA/CRC; extra tail lets the discriminator finish.
    end=hi+(16+32+16+255*8+24)/(output_rate/4)+30e-6
    result.update(capture_id=parent,expected_packet_start_interval_s=[lo,hi],
        verification_profile=profile,timing_guard_s=guard)
    if lo<0 or end>len(iq)/fs:
        return finish('outside_recording',reason='Complete auxiliary receive window is not available in saved IQ')
    with ctx.span('aux_subband_extraction',source_verification_id=verification_id,frame_index=frame_index):
        sub=dsp.extract(ctx,parent,ble_channel_hz(ptr['channel']),bandwidth,
            sample_start=math.floor(lo*fs),sample_stop=math.ceil(end*fs),
            output_rate_hz=output_rate,margin_s=.001)
    with ctx.span('aux_protocol_verification',source_verification_id=verification_id,frame_index=frame_index):
        checked=investigation.verify(ctx,sub['artifact_id'],profile,channel=ptr['channel'])
    saved=json.loads(Path(ctx.resolve(checked['evidence_id'])['path']).read_text())
    sm=json.loads(Path(ctx.resolve(sub['artifact_id'])['metadata_path']).read_text())
    matches=[]
    adi_conflicts=[]
    for index,f in enumerate(saved['frames']):
        at=sm['parent_sample_start']/fs+f['subband_sample_start']/output_rate-sm.get('filter_group_delay_s',0)
        if lo<=at<=hi:
            source_adi=frame.get('advertising_data_info_hex');target_adi=f.get('advertising_data_info_hex')
            if source_adi and target_adi and source_adi!=target_adi:adi_conflicts.append(index)
            else:matches.append(index)
    # Timing plus CRC is a candidate link; not proof of advertiser identity.
    return finish('crc_valid_timing_candidates' if matches else 'no_matching_valid_frame',
        subband_id=sub['artifact_id'],verification_id=checked['evidence_id'],
        valid_frames=checked['valid_frames'],timing_candidate_frame_indices=matches,
        adi_conflict_frame_indices=adi_conflicts,
        verification_stages=checked.get('verification_stages'),
        conclusion='One-hop timing/PHY/channel candidates only; no identity or complete chain confirmation')
