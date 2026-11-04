"""Map SIG evidence into saved-capture coordinates without promoting it to MAC FCS."""
import json,math
from pathlib import Path


def project(ctx,verifications):
    result=[]
    for v in verifications:
        headers=(v.get('verification_stages') or {}).get('headers',[])
        if not headers:continue
        meta=json.loads(Path(ctx.resolve(v['artifact_id'])['metadata_path']).read_text())
        parent=meta.get('parent_artifact_id')
        if not parent:continue
        pm=json.loads(Path(ctx.resolve(parent)['metadata_path']).read_text());fs=pm['sample_rate_hz'];rate=meta['sample_rate_hz']
        for i,h in enumerate(headers):
            if not h.get('header_crc_pass'):continue
            start=math.floor(meta['parent_sample_start']+(h['subband_sample_start']/rate-meta.get('filter_group_delay_s',0))*fs)
            stop=math.ceil(meta['parent_sample_start']+(h['subband_sample_stop']/rate-meta.get('filter_group_delay_s',0))*fs)
            start=max(meta['parent_sample_start'],start);stop=min(meta['actual_parent_sample_stop'],stop)
            if stop<=start:continue
            e=dict(h,capture_id=parent,sample_start=start,sample_stop=stop,evidence_id=v['evidence_id'],header_index=i,
                observed_center_hz=meta['center_hz'],freq_low_hz=meta['center_hz']-meta['requested_span_hz']/2,
                freq_high_hz=meta['center_hz']+meta['requested_span_hz']/2,
                bandwidth_kind='observed20MHz_slice_not_full_signalled_channel',duplicate_evidence=[],
                channel_center_hz=None,channel_center_status='not_determined_from_single20MHz_observation')
            duplicate=next((old for old in result if old['capture_id']==parent and old['header_bits']==e['header_bits']
                and old['observed_center_hz']==e['observed_center_hz'] and abs(old['sample_start']-start)<=fs*2e-6),None)
            if duplicate:duplicate['duplicate_evidence'].append(dict(evidence_id=v['evidence_id'],header_index=i))
            else:result.append(e)
    return result


def associate(observations,sample_rate_hz):
    """Associate identical SIG bits and timing across duplicated20MHz observations."""
    groups=[]
    for e in sorted(observations,key=lambda h:h['sample_start']):
        match=next((g for g in reversed(groups) if g['capture_id']==e['capture_id'] and g['header_bits']==e['header_bits']
            and abs(g['sample_start']-e['sample_start'])<=sample_rate_hz*2e-6
            and max(g['observed_centers_hz']+[e['observed_center_hz']])-min(g['observed_centers_hz']+[e['observed_center_hz']])<=e['channel_width_hz']-20e6+1),None)
        ref=dict(evidence_id=e['evidence_id'],header_index=e['header_index'],observed_center_hz=e['observed_center_hz'])
        if match:
            match['observations'].append(ref)
            match['observed_centers_hz']=sorted(set(match['observed_centers_hz']+[e['observed_center_hz']]))
            match['freq_low_hz']=min(match['freq_low_hz'],e['freq_low_hz']);match['freq_high_hz']=max(match['freq_high_hz'],e['freq_high_hz'])
        else:groups.append(dict(e,observations=[ref],observed_centers_hz=[e['observed_center_hz']]))
    for g in groups:
        centers=g['observed_centers_hz'];needed=int(g['channel_width_hz']/20e6)
        #160MHz includes80+80 ambiguity; do not infer its geometry here.
        if needed in (2,4) and len(centers)==needed and all(abs(b-a-20e6)<1 for a,b in zip(centers,centers[1:])):
            center=(centers[0]+centers[-1])/2
            g.update(channel_center_hz=center,channel_center_status='corroborated_by_identical_SIG_and_timing_in_all20MHz_slices',
                freq_low_hz=center-g['channel_width_hz']/2,freq_high_hz=center+g['channel_width_hz']/2,
                bandwidth_kind='SIG_signalled_nominal_channel_not_measured_occupied_bandwidth')
    return groups
