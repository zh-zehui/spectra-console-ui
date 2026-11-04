"""Real DeepSeek acceptance on existing IQ only; never RF acquisition."""
import json,time,uuid
from spectrum_agent.common import ROOT,Context,dump
from spectrum_agent.service import normalize,run_dsh
from spectrum_agent.ui_gateway import visual

source='task_c5cc43805b45495fb69ed13285592986'
prompt=source+' ，根据这个task采集的数据，不分析，只画出时频图'
ctx=Context('task_plot_accept_'+uuid.uuid4().hex[:12])
request=normalize(dict(prompt=prompt,source='usrp',provider='deepseek',
    budgets=dict(coverage_mode='sampled',deadline_s=180,max_model_rounds=12)))
# Acceptance guarantees no RF access even if the model chooses an incorrect tool.
request['rf_capture_forbidden']=True
start=time.perf_counter();out=run_dsh(ctx.task_id,request)
result=visual(ctx.task_id)
events=[json.loads(line) for line in (ctx.run/'events.jsonl').read_text().splitlines()]
calls=[e['stage'] for e in events if e.get('phase')=='start']
assert out['status']=='succeeded',out
assert result['report']['recording_replay']['source_task_id']==source
assert not result['signals'] and not result['report']['segmentations']
assert result['visual']['rows']==192 and len(result['visual']['db'][0])==512
assert 'capture_iq' not in calls and 'inspect_capabilities' not in calls
assert 'plot_spectrogram' in calls
value=dict(task_id=ctx.task_id,status=out['status'],prompt=prompt,elapsed_s=time.perf_counter()-start,
    source_task_id=source,new_rf_capture=False,public_call_sequence=calls,
    visual_rows=result['visual']['rows'],visual_columns=result['visual']['columns'],
    detected_regions=len(result['signals']),web_visual_url='/edge/tasks/'+ctx.task_id+'/visual')
dump(ROOT/'docs/testing/plot-replay-acceptance.json',value)
print(json.dumps(value,ensure_ascii=False),flush=True)
