"""Conservative event grouping and evidence association; never assigns emitter identity."""
import json
import math
from pathlib import Path


def group_events(events, sample_rate_hz, hop):
    groups=[]
    active=[]
    for e in sorted(events,key=lambda x:(x['segment_file_start'],x['sample_start'],x['freq_low_hz'],x['event_id'])):
        active=[g for g in active if g['segment_file_start']==e['segment_file_start'] and
                g['sample_stop']+2*hop>=e['sample_start']]
        candidates=[]
        for g in active:
            intersection=min(g['freq_high_hz'],e['freq_high_hz'])-max(g['freq_low_hz'],e['freq_low_hz'])
            ratio=intersection/max(g['bandwidth_hz'],e['bandwidth_hz'],1)
            if ratio>.35: candidates.append((ratio,g))
        if candidates:
            g=max(candidates,key=lambda item:item[0])[1]
            g['sample_stop']=max(g['sample_stop'],e['sample_stop'])
            g['freq_low_hz']=min(g['freq_low_hz'],e['freq_low_hz'])
            g['freq_high_hz']=max(g['freq_high_hz'],e['freq_high_hz'])
            g['bandwidth_hz']=g['freq_high_hz']-g['freq_low_hz']
            g['duration_s']=(g['sample_stop']-g['sample_start'])/sample_rate_hz
            g['snr_db']=max(g['snr_db'],e['snr_db'])
            g['event_ids'].append(e['event_id'])
            g['event_count']+=1
        else:
            g=dict(e,group_id='g'+str(len(groups)),event_count=1,event_ids=[e['event_id']])
            groups.append(g);active.append(g)
    return groups


def associate(ctx, segmentations, verifications):
    from .frame_localization import apply
    verifications=apply(ctx,verifications)
    links=[];identities={}
    for v in verifications:
        record=ctx.resolve(v['artifact_id'])
        meta=json.loads(Path(record['metadata_path']).read_text())
        parent=meta.get('parent_artifact_id')
        if not parent: continue
        parent_meta=json.loads(Path(ctx.resolve(parent)['metadata_path']).read_text())
        fs=parent_meta['sample_rate_hz'];rate=meta['sample_rate_hz']
        for i,frame in enumerate(v.get('frames',[])):
            precise='subband_sample_start' in frame
            if precise:
                start=meta['parent_sample_start']+(frame['subband_sample_start']/rate-meta.get('filter_group_delay_s',0))*fs
                if 'subband_sample_stop' in frame:
                    stop=meta['parent_sample_start']+(frame['subband_sample_stop']/rate-meta.get('filter_group_delay_s',0))*fs
                    width=frame.get('occupied_bandwidth_hz',meta['requested_span_hz'])
                    lo=frame['center_hz']-width/2;hi=frame['center_hz']+width/2
                else:
                    # BLE start is access address, after the one-byte preamble.
                    start-=8e-6*fs
                    stop=start+(8+32+16+8*frame['payload_length']+24)*1e-6*fs
                    lo=frame['center_hz']-1e6;hi=frame['center_hz']+1e6
            else:
                start=meta['parent_sample_start'];stop=meta['actual_parent_sample_stop']
                lo=meta['center_hz']-meta['requested_span_hz']/2
                hi=meta['center_hz']+meta['requested_span_hz']/2
            start=max(meta['parent_sample_start'],math.floor(start))
            stop=min(meta['actual_parent_sample_stop'],math.ceil(stop))
            if stop<=start: continue
            matches=[]
            for s in segmentations:
                if s['capture_id']!=parent: continue
                tolerance=s.get('fft_size',0)
                for g in s['signal_groups']:
                    if (min(stop+tolerance,g['sample_stop'])>max(start-tolerance,g['sample_start']) and
                        min(hi,g['freq_high_hz'])>max(lo,g['freq_low_hz'])):
                        matches.append(dict(segmentation_id=s['evidence_id'],group_id=g['group_id']))
            links.append(dict(evidence_id=v['evidence_id'],frame_index=i,protocol=v['protocol'],capture_id=parent,
                sample_start=start,sample_stop=stop,freq_low_hz=lo,freq_high_hz=hi,
                timing_uncertainty_samples=math.ceil(frame.get('timing_uncertainty_samples',4)*fs/rate) if precise else None,
                timing_uncertainty_calibrated=frame.get('timing_uncertainty_calibrated',False),
                bandwidth_kind=frame.get('bandwidth_kind','decoder_passband_not_measured_occupied_bandwidth'),
                association_level='packet_time_frequency_overlap' if precise else 'extraction_window_only',
                candidate_groups=matches,ambiguous=len(matches)!=1,
                warning='Overlap is a candidate association; it does not establish emitter identity or label the whole group'))
            raw=frame.get('frame_hex') or frame.get('pdu_hex')
            if raw and precise:
                # Identical MPDUs may occur twice inside one aggregate and share
                # a PPDU time range. Distinct delimiter offsets are distinct members.
                member=frame.get('ampdu_member')
                identities[(v['evidence_id'],i)]=(v['protocol'],parent,raw,
                    member.get('delimiter_offset') if member else None)
    # Same received bytes can decode in overlapping subbands. Merge only their
    # display/association references, retaining every original verification record.
    unique=[]
    for link in links:
        identity=identities.get((link['evidence_id'],link['frame_index']))
        match=next((u for u in unique if identity and identities.get((u['evidence_id'],u['frame_index']))==identity
            and abs(u['sample_start']-link['sample_start'])<=u['timing_uncertainty_samples']+link['timing_uncertainty_samples']
            and min(u['freq_high_hz'],link['freq_high_hz'])>max(u['freq_low_hz'],link['freq_low_hz'])),None)
        if match is None:unique.append(dict(link,duplicate_evidence=[]))
        else:match['duplicate_evidence'].append(dict(evidence_id=link['evidence_id'],frame_index=link['frame_index']))
    return unique
