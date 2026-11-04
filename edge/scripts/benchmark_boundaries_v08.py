"""Recorded-IQ candidate evaluation; retains original reports and recordings."""
import json,time,uuid
from pathlib import Path
from spectrum_agent.common import ROOT,Context,dump
from spectrum_agent import dsp,segmentation_tools

source=Context('task_activity_f913d09da8e8');ctx=Context('boundary_v08_'+uuid.uuid4().hex[:10])
report=json.loads((source.run/'report.json').read_text())
records=[json.loads(p.read_text()) for p in (source.run/'index').glob('*.json')]
baseline=report['selected_segmentation_id'];cap=report['captures'][0]['artifact_id']
for record in records:
    if record['kind'] not in ('capture','subband','wifi_verification','ble_verification','ieee154_verification') and record['artifact_id']!=baseline:continue
    rec=dict(record,task_id=ctx.task_id)
    for key in ('path','metadata_path'):
        if key not in rec:continue
        original=Path(rec[key]);target=ROOT/'artifacts'/ctx.task_id/original.name;target.parent.mkdir(parents=True,exist_ok=True)
        target.hardlink_to(original);rec[key]=str(target)
    dump(ctx.run/'index'/(rec['artifact_id']+'.json'),rec)
print(json.dumps(dict(task_id=ctx.task_id,capture=cap,baseline=baseline)),flush=True)
ids=[baseline]
for threshold in (10,14):
    start=time.perf_counter()
    with ctx.span('benchmark_multiscale'):
        result=dsp.segment(ctx,cap,fft_size=512,threshold_db=threshold,strategy='multiscale_energy',decision_reason='Controlled same-IQ numerical regression; not a model decision or automatic winner.')
    ids.append(result['segmentation_id'])
    print(json.dumps(dict(threshold=threshold,count=result['total_groups'],elapsed_s=time.perf_counter()-start)),flush=True)
comparison=segmentation_tools.compare(ctx,ids)
dump(ROOT/'docs/testing/boundaries-v08.json',dict(task_id=ctx.task_id,baseline=baseline,**comparison))
print(json.dumps(comparison,ensure_ascii=False,indent=2),flush=True)
