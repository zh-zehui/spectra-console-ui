"""Upgrade retained IEEE802.15.4 frame timing without adding duplicate protocol evidence."""
import json,hashlib
from pathlib import Path
from .common import ROOT,DomainError
from .scene import read

TIMING=('subband_sample_start','subband_sample_stop','recovered_chip_stop','timing_reference','timing_status',
        'timing_uncertainty_samples','timing_uncertainty_calibrated','occupied_bandwidth_hz','center_hz')


def localize(ctx,verification_id):
    from .ieee154 import receive_iq
    from .dsp import load_iq
    rec=ctx.resolve(verification_id)
    if rec['kind']!='ieee154_verification':
        raise DomainError('UNSUPPORTED_PROFILE','Timing upgrade currently supports saved IEEE802.15.4 frames only')
    original=read(ctx,verification_id);iq,meta=load_iq(ctx,original['artifact_id']);ctx.check()
    if abs(meta['sample_rate_hz']-4e6)>1 or len(iq)>4_000_000:
        raise DomainError('UNSUPPORTED_PROFILE','Timing upgrade requires original4MS/s subband of at most4M samples')
    frames,stats=receive_iq(iq);unused=list(sorted(frames,key=lambda f:f.get('subband_sample_start',0)));matches=[];unmatched=[]
    for index,frame in enumerate(original['frames']):
        match=next((f for f in unused if f['frame_hex']==frame['frame_hex'] and 'subband_sample_start' in f),None)
        if match is None:unmatched.append(index);continue
        unused.remove(match)
        timing={k:match[k] for k in TIMING if k in match};timing['center_hz']=meta['center_hz']
        matches.append(dict(frame_index=index,frame_hex=frame['frame_hex'],timing=timing))
    library=ROOT/'runtime/radio-plugins/lib/libgnuradio-ieee802_15_4.so'
    value=dict(verification_id=verification_id,verification_sha256=rec['sha256'],artifact_id=original['artifact_id'],
        backend='Pinned gr-ieee802-15-4 with project-local recovered-chip index instrumentation',
        library_sha256=hashlib.sha256(library.read_bytes()).hexdigest(),
        matches=matches,matched_frames=len(matches),unmatched_frame_indices=unmatched,
        extra_decoded_frames_not_added=len(unused),new_protocol_frames=0,
        warning='Frame bytes and CRC must match retained evidence. Positions are clock-derived estimates with uncalibrated drift uncertainty; this does not identify an entire activity region.')
    aid=ctx.json_artifact('frame_localization',value)
    return dict(localization_id=aid,**value)


def apply(ctx,verifications):
    upgrades={}
    for p in sorted((ctx.run/'index').glob('*.json'),key=lambda p:p.stat().st_mtime_ns):
        rec=json.loads(p.read_text())
        if rec['kind']=='frame_localization':
            value=read(ctx,rec['artifact_id']);upgrades[value['verification_id']]=value
    result=[]
    for v in verifications:
        value=upgrades.get(v.get('evidence_id'))
        if not value or ctx.resolve(v['evidence_id'])['sha256']!=value['verification_sha256']:
            result.append(v);continue
        frames=[dict(f) for f in v['frames']]
        for m in value['matches']:
            i=m['frame_index']
            if 0<=i<len(frames) and frames[i].get('frame_hex')==m['frame_hex']:frames[i].update(m['timing'])
        result.append(dict(v,frames=frames,timing_upgrade_applied=True))
    return result
