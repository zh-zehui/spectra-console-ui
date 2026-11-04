"""Same recorded RF, actual whole-IQ coarse STFT, no RF or model calls."""
import json,time,uuid
from pathlib import Path
from spectrum_agent.common import ROOT,Context,dump
from spectrum_agent import coarse,segmentation_tools

source=Context('task_activity_f913d09da8e8');ctx=Context('coarse_v09_'+uuid.uuid4().hex[:10])
previous=json.loads((source.run/'report.json').read_text());baseline=previous['selected_segmentation_id'];cap=previous['captures'][0]['artifact_id']
for path in (source.run/'index').glob('*.json'):
    record=json.loads(path.read_text())
    if record['kind'] not in ('capture','subband','wifi_verification','ble_verification','ieee154_verification') and record['artifact_id']!=baseline:continue
    rec=dict(record,task_id=ctx.task_id)
    for key in ('path','metadata_path'):
        if key not in rec:continue
        original=Path(rec[key]);target=ROOT/'artifacts'/ctx.task_id/original.name;target.parent.mkdir(parents=True,exist_ok=True)
        target.hardlink_to(original);rec[key]=str(target)
    dump(ctx.run/'index'/(rec['artifact_id']+'.json'),rec)
print(json.dumps(dict(task_id=ctx.task_id,capture_id=cap,baseline=baseline)),flush=True)
ids=[baseline]
for margin in (6,9):
    start=time.perf_counter()
    with ctx.span('coarse_segment_spectrum'):
        result=coarse.segment(ctx,cap,threshold_db=margin,decision_reason='Same-IQ offline candidate qualification; not an automatic model selection.')
    ids.append(result['segmentation_id']);print(json.dumps(dict(threshold=margin,count=result['total_groups'],seconds=time.perf_counter()-start,coverage=result['coverage'])),flush=True)
comparison=segmentation_tools.compare(ctx,ids)
summary=dict(task_id=ctx.task_id,baseline=baseline,**comparison)
dump(ROOT/'docs/testing/coarse-v09.json',summary);print(json.dumps(summary,ensure_ascii=False,indent=2),flush=True)
