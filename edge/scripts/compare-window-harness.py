"""Read-only comparison of offline regression and model-directed real tasks."""
import json
from pathlib import Path
from collections import Counter,defaultdict
from spectrum_agent.common import ROOT

tasks=['task_window_wide_9fb2183ea875','task_window_wide_4065cef0c157','task_f1273acd57fe449f93227f459391b6ec']
for task in tasks:
    run=ROOT/'runs'/task;r=json.loads((run/'report.json').read_text());req=json.loads((run/'request.json').read_text());out=json.loads((run/'outcome.json').read_text())
    events=[json.loads(line) for line in (run/'events.jsonl').read_text().splitlines()]
    starts=Counter(e['stage'] for e in events if e.get('phase')=='start');times=defaultdict(float)
    for e in events:
        if e.get('phase')=='end':times[e['stage']]+=e.get('elapsed_ms',0)/1000
    verifications=[]
    for v in r.get('verification_attempts',[]):
        rec=json.loads((run/'index'/(v['artifact_id']+'.json')).read_text());m=json.loads(Path(rec['metadata_path']).read_text())
        cap=json.loads((run/'index'/(m['parent_artifact_id']+'.json')).read_text());pm=json.loads(Path(cap['metadata_path']).read_text());fs=pm['sample_rate_hz']
        verifications.append(dict(center_mhz=m['center_hz']/1e6,bandwidth_mhz=m['requested_span_hz']/1e6,sample_rate_mhz=m['sample_rate_hz']/1e6,
            start_ms=m.get('requested_sample_start',m['parent_sample_start'])/fs*1000,
            stop_ms=m.get('requested_sample_stop',m['actual_parent_sample_stop'])/fs*1000,
            frames=v['valid_frames'],headers=len(v.get('verification_stages',{}).get('headers',[]))))
    selected=next(s for s in r['segmentations'] if s['evidence_id']==r['selected_segmentation_id'])
    print(json.dumps(dict(task=task,source=req.get('replay_from_task_id'),status=out['status'],elapsed_s=out['elapsed_ms']/1000,
        raw_mac_frames=sum(v['valid_frames'] for v in r.get('verification_attempts',[])),associated_mac=len(r.get('frame_associations',[])),
        header_observations=len(r.get('wifi_header_evidence',[])),header_groups=len(r.get('wifi_header_groups',[])),
        vht80_groups=sum(h['phy_format']=='VHT' and h['channel_width_hz']==80e6 for h in r.get('wifi_header_groups',[])),
        segmentation=dict(count=len(r['signal_groups']),fft=selected['fft_size'],time_bin_us=selected.get('proposal_time_cell_s',0)*1e6,
            frequency_bin_mhz=selected.get('proposal_frequency_cell_hz',0)/1e6,threshold_db=selected['threshold_db']),
        tool_counts=dict(starts),times_s=dict(times),protocol_checks=verifications,
        stop_reason=(r.get('analysis_budget',{}).get('declared_stop') or {}).get('reason'),
        remaining_deadline_s=r.get('analysis_budget',{}).get('deadline_remaining_s'),
        catalog_review={k:r.get('catalog_review',{}).get(k) for k in ('total_regions','reviewed_regions','remaining_regions','protocol_recognition_complete')}),ensure_ascii=False))
