"""Real recorded IQ checks of the newly registered access-code analysis tool."""
import json,time,uuid
from spectrum_agent.common import Context,ROOT,dump
from spectrum_agent.service import normalize
from spectrum_agent.continuation import attach
from spectrum_agent.worker import execute

ctx=Context('classic_replay_v14_'+uuid.uuid4().hex[:8])
req=normalize(dict(prompt='Recorded-IQ classic access-code regression, no acquisition',continue_from_task_id='task_budget_v13_expanded_cap6_69fdbd0f'))
dump(ctx.run/'request.json',req);manifest=attach(ctx,req)
base=json.loads(open(ctx.resolve(req['continuation_base_id'])['path']).read());groups={g['group_id']:g for g in base['signal_groups']}
rows=[]
for group_id,center in [('g4',2402e6),('g115',2449e6)]:
    g=groups[group_id];started=time.perf_counter()
    sub=execute(ctx.task_id,'extract_subband',dict(capture_id=req['recording_capture_id'],center_hz=center,bandwidth_hz=1e6,
        output_rate_hz=4e6,sample_start=g['sample_start'],sample_stop=min(g['sample_stop'],g['sample_start']+400000),margin_s=.001))['result']
    result=execute(ctx.task_id,'analyze_bluetooth_classic',dict(artifact_id=sub['artifact_id']))['result']
    rows.append(dict(group_id=group_id,elapsed_s=time.perf_counter()-started,**result))
summary=dict(task_id=ctx.task_id,source_task=manifest['source_task_id'],new_rf_capture=False,model_used=False,cases=rows,
    note='Zero sync hits is not absence. Access-code hits are candidates, never validated frames or whole-region confirmation.')
dump(ROOT/'docs/testing/classic-replay-v14.json',summary)
print(json.dumps(summary,ensure_ascii=False,indent=2))
