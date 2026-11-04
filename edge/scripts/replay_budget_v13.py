"""Paired official-Harness replay: same IQ/evidence/prompt, local call ceilings2 vs6."""
import hashlib
import json
import time
import uuid
from pathlib import Path
from spectrum_agent.common import ROOT, Context, dump
from spectrum_agent.service import normalize, run_dsh
from spectrum_agent.segmentation_eval import evaluate_report


def attach(source,ctx,base):
    for p in (source.run/'index').glob('*.json'):
        rec=json.loads(p.read_text())
        if rec['kind'] not in ('capture','subband','wifi_verification','ble_verification','ieee154_verification'): 
            if rec['artifact_id']!=base:continue
        rec=dict(rec,task_id=ctx.task_id)
        for key in ('path','metadata_path'):
            if key not in rec:continue
            original=Path(rec[key]);target=ROOT/'artifacts'/ctx.task_id/original.name
            target.parent.mkdir(parents=True,exist_ok=True);target.hardlink_to(original);rec[key]=str(target)
        dump(ctx.run/'index'/(rec['artifact_id']+'.json'),rec)


def main():
    source=Context('task_b73949a5c41c428198c00908b18c8d35')
    previous=json.loads((source.run/'report.json').read_text())
    base=previous['initial_segmentation_id'];cap=previous['captures'][0]['artifact_id']
    source_rec=source.resolve(cap)
    actual_hash=hashlib.sha256(Path(source_rec['path']).read_bytes()).hexdigest()
    if actual_hash!=source_rec['sha256']:raise RuntimeError('Source IQ no longer matches its registered checksum')
    labels=json.loads((ROOT/'docs/testing/local-human-review-v12.json').read_text())
    prompt=(f'Characterize the recorded2350..2450MHz scene using original coarse segmentation {base} and existing valid frames. '
        'Do not recapture, globally resegment, or repeat the existing protocol verifications. '
        'Inspect a representative selection of unexplained regions across weak/strong, narrow/wide and short/long activity. '
        'YOU choose specific parent regions, focused local windows, tools, strategies and parameters from observations. '
        'Refresh the queue as evidence changes; choose whether another target or local alternative has sufficient expected value for its time cost. '
        'Where useful, locally refine at least some unexplained activity, reassess and propose a conservative fusion containing one selected refinement per parent. '
        'Compare and explicitly select the working segmentation. Preserve all unknown residuals and all84 preexisting valid frames. '
        'Do not claim exhaustive identification or measured accuracy improvement. Respect analysis_budget and reserve reporting time. '
        'Finish with build_report(stop_reason=...,stop_detail=...) recording unchecked scope and next steps.')
    rows=[]
    output=ROOT/'docs/testing/budget-replay-v13.json'
    for label,limit in [('baseline_cap2',2),('expanded_cap6',6)]:
        ctx=Context('task_budget_v13_'+label+'_'+uuid.uuid4().hex[:8])
        attach(source,ctx,base)
        request=normalize(dict(prompt=prompt,source='usrp',provider='deepseek',recording_capture_id=cap,
            constraints=dict(center_hz=2400e6,span_hz=100e6,observation_s=.1),
            budgets=dict(deadline_s=300,max_model_rounds=32,max_candidates=4,max_local_refinements=limit)))
        dump(ctx.run/'provenance.json',dict(source_task=source.task_id,capture_id=cap,
            capture_sha256=source_rec['sha256'],new_rf_capture=False,protocol_evidence_reused=True,
            evaluation='Same v13 engine; local call ceiling differs. Not an exact reproduction of the historical v12 engine.'))
        print(json.dumps(dict(task_id=ctx.task_id,arm=label,status='starting')),flush=True)
        started=time.perf_counter();outcome=run_dsh(ctx.task_id,request)
        report=json.loads((ctx.run/'report.json').read_text())
        events=[json.loads(x) for x in (ctx.run/'events.jsonl').read_text().splitlines()]
        stages=[e['stage'] for e in events if e.get('phase')=='start']
        assert not set(stages)&{'capture_iq','make_synthetic_capture','inspect_capabilities'}, 'Replay touched hardware'
        assert report['analysis_coverage']['verified_frame_count']==84,'Reference frame evidence lost'
        row=dict(arm=label,task_id=ctx.task_id,status=outcome['status'],elapsed_s=time.perf_counter()-started,
            model_stream_s=sum(e.get('elapsed_ms',0)/1000 for e in events if e.get('stage')=='model_stream' and e.get('phase')=='end'),
            tool_calls=outcome['tool_result_count'],sequence=stages,analysis_budget=report['analysis_budget'],
            coarse_to_fine=report['coarse_to_fine'],regions=len(report['signal_groups']),
            frame_count=report['analysis_coverage']['verified_frame_count'],
            packet_associations=sum(l['association_level']=='packet_time_frequency_overlap' and bool(l['candidate_groups']) for l in report['frame_associations']),
            fusion=report['boundary_fusion'],scope_evaluation=evaluate_report(report,labels))
        row['failed_domain_stages']=[dict(tool=e['stage'],error_code=e.get('error_code')) for e in events if e.get('phase')=='end' and e.get('status')=='failed']
        row['local_analysis_valid']=row['analysis_budget']['local_succeeded']>0
        rows.append(row)
        summary=dict(source_task=source.task_id,capture_id=cap,capture_sha256=source_rec['sha256'],
            new_rf_capture=False,arms=rows,human_label_status='pending',accuracy_improvement='not_measured',
            limitations=['One model run per arm; stochastic decisions and runtime are not statistically controlled.',
                         'More local windows do not prove improved precision or full-scene identification.'])
        dump(output,summary)
        print(json.dumps(dict(task_id=ctx.task_id,status=outcome['status'],local_attempts=row['analysis_budget']['local_attempts'],
                             distinct_parents=row['analysis_budget']['distinct_locally_reviewed_regions'],elapsed_s=row['elapsed_s'])),flush=True)
    print(json.dumps(dict(output=str(output),done=True)),flush=True)


if __name__=='__main__':main()
