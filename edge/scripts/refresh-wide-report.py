import sys,json
from collections import Counter
from spectrum_agent.common import ROOT,Context,dump
from spectrum_agent.worker import report
ctx=Context(sys.argv[1])
r=report(ctx,stop_reason='tool_capability_limit',stop_detail='Offline fixed-grid regression: all recorded time covered by10ms windows; wideband header identification only,not full wide MAC decoding or exhaustive all-protocol verification.')
r=json.loads((ctx.run/'report.json').read_text())
request=json.loads((ctx.run/'request.json').read_text());m=r['captures'][0]['metadata'];out=json.loads((ctx.run/'outcome.json').read_text())
counts=Counter((h['phy_format'],h['channel_width_hz']/1e6) for h in r['wifi_header_evidence'])
selected=next(s for s in r['segmentations'] if s['evidence_id']==r['selected_segmentation_id'])
value=dict(task_id=ctx.task_id,source_task_id=request['replay_from_task_id'],new_rf_capture=False,duration_s=m['duration_s'],window_s=.01,
    total_windows=selected['window_analysis']['total_windows'],coarse_regions=len(r['signal_groups']),valid_mac_frames=len(r['frame_associations']),
    header_observations=len(r['wifi_header_evidence']),header_groups=len(r['wifi_header_groups']),
    wide_headers_corroborated=sum(h['channel_center_hz'] is not None for h in r['wifi_header_groups']),
    header_formats={str(k):v for k,v in counts.items()},elapsed_s=out['elapsed_ms']/1000,
    note='Independent20MHz observations,header groups and MAC FCS records are separate counts. Diagnostic is not exhaustive protocol identification.')
dump(ROOT/'docs/testing'/('window-wide-'+request['replay_from_task_id']+'.json'),value)
print(json.dumps(value),flush=True)
