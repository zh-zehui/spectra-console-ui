"""New task inherits immutable Wi-Fi evidence; append only drone signature checks."""
import json,time,uuid
from spectrum_agent.common import ROOT,Context,dump
from spectrum_agent.continuation import normalize_request,attach
from spectrum_agent.drone_video import analyze

source='task_9668c13363904f8092ca082554958600';ctx=Context('task_drone_video_'+uuid.uuid4().hex[:12])
request=normalize_request(dict(prompt='Offline acceptance: retain source Wi-Fi evidence and add whole-time drone recording-signature analysis',
    source='usrp',continue_from_task_id=source,budgets=dict(coverage_mode='complete',deadline_s=900,max_model_rounds=32)))
request['task_intent']=dict(status='resolved',interpreter='offline_acceptance_runner_not_dsh',analysis_requested=True,acquisition_requested=False,
    protocol_workflow_version=2,protocol_analysis_requested=False,comprehensive_protocol_survey=False,drone_video_analysis_requested=True,
    full_local_review_requested=False,analysis_start_s=0,analysis_stop_s=.5,visualization_requested=False,
    targets=[dict(label='User-confirmed DJI Mavic4 Pro fixed10MHz video link',source='user_reported',protocol_confirmed=False)])
dump(ctx.run/'request.json',request);attached=attach(ctx,request)
prior=next(json.loads(p.read_text())['artifact_id'] for p in (ctx.run/'index').glob('*.json') if json.loads(p.read_text()).get('kind')=='priors')
start=time.perf_counter();value=analyze(ctx,attached['capture_id'],5803.5e6,
    'User confirms own Mavic4 Pro in this recording. Test observed293/root123/30k signature over all saved time; preserve inherited Wi-Fi evidence',[prior])
dump(ROOT/'docs/testing/drone-video-offline-acceptance.json',dict(task_id=ctx.task_id,source_task_id=source,elapsed_s=time.perf_counter()-start,analysis=value))
print(json.dumps(dict(task_id=ctx.task_id,matched_references=value['matched_references'],coverage=value['coverage'],timing_ms=value['timing_ms'],
    estimated_repetition_s=value['estimated_repetition_s'],elapsed_s=time.perf_counter()-start)),flush=True)
