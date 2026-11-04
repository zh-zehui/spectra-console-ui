"""Observable, bounded local-analysis scheduling; the official Harness chooses actions."""
import json
import time
from .common import DomainError, dump, utc


def local_limit(request):
    budgets = request.get('budgets', {})
    # Allow alternatives per target, but do not make an unbounded model loop.
    return budgets.get('max_local_refinements', 8192 if budgets.get('coverage_mode')=='complete' else min(8, 2 * budgets.get('max_candidates', 6)))


def snapshot(ctx, now=None, compact=False):
    now = time.time() if now is None else now
    path = ctx.run / 'request.json'
    request = json.loads(path.read_text()) if path.exists() else {}
    path = ctx.run / 'events.jsonl'
    events = [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []
    starts = [e for e in events if e.get('stage') == 'refine_signal_region' and e.get('phase') == 'start']
    completed = [e for e in events if e.get('stage') == 'refine_signal_region' and e.get('phase') == 'end']
    durations = [e['elapsed_ms'] / 1000 for e in completed if e.get('elapsed_ms') is not None]
    models = [e['elapsed_ms'] / 1000 for e in events if e.get('stage') == 'model_stream'
              and e.get('phase') == 'end' and e.get('elapsed_ms') is not None]
    deadline = request.get('deadline_unix')
    remaining = max(0, deadline - now) if deadline is not None else None
    reserve = min(45, max(10, request.get('budgets', {}).get('deadline_s', 180) * .15))
    estimated = max([5] + durations[-4:]) * 1.5 + (sum(models[-4:]) / len(models[-4:]) if models else 5)
    limit = local_limit(request)
    reason = ('local_call_budget' if len(starts) >= limit else
              'report_time_reserve' if remaining is not None and remaining < reserve + estimated else None)
    refinements = []
    from .continuation import imported_ids
    inherited=imported_ids(ctx)
    inherited_count=0
    for p in (ctx.run / 'index').glob('*.json'):
        rec = json.loads(p.read_text())
        if rec.get('kind') == 'scene_refinement':
            if rec['artifact_id'] in inherited:
                inherited_count+=1
                continue
            from pathlib import Path
            r = json.loads(Path(rec['path']).read_text())
            refinements.append(dict(segmentation_id=r['parent_segmentation_id'], group_id=r['parent_group_id'],
                                    refinement_id=rec['artifact_id'],
                                    target_samples=r.get('requested_parent_interval'),
                                    target_frequency_hz=r.get('fine_target_frequency_hz')))
    distinct = {(r['segmentation_id'], r['group_id']) for r in refinements}
    return dict(policy_version='dynamic_local_v13', observed_utc=utc(), max_local_refinements=limit,
                local_attempts=len(starts), local_completed=len(completed),
                local_succeeded=sum(e.get('status') == 'succeeded' for e in completed),
                inherited_local_refinement_count=inherited_count,
                local_calls_remaining=max(0, limit-len(starts)), distinct_locally_reviewed_regions=len(distinct),
                local_targets=refinements[-8:] if compact else refinements,
                local_targets_summary_truncated=compact and len(refinements)>8,
                local_target_record_count=len(refinements), deadline_remaining_s=remaining, report_reserve_s=reserve,
                estimated_next_local_plus_model_s=estimated, next_local_allowed=reason is None,
                local_stop_constraint=reason, scope_complete=False,
                note='Attempts include failed calls; exact cache hits do not spend another call. '
                     'Time prediction is heuristic, not a completion guarantee. Reviewed windows do not establish full-region recognition.')


def enforce(ctx, args):
    state = snapshot(ctx)
    path = ctx.run / 'request.json'
    request = json.loads(path.read_text()) if path.exists() else {}
    targets = {(r['segmentation_id'], r['group_id']) for r in state['local_targets']}
    target = (args.get('segmentation_id'), args.get('group_id'))
    limit = request.get('budgets', {}).get('max_candidates', 6)
    reason = state['local_stop_constraint']
    if reason is None and target not in targets and len(targets) >= limit:
        reason = 'distinct_local_target_budget'
    if reason:
        ctx.event('local_budget_guard', phase='blocked', reason=reason, budget=state)
        raise DomainError('LOCAL_ANALYSIS_BUDGET', reason + ': refresh the queue, retain unknowns and build_report with a stopping reason')
    return state


STOP_REASONS = {'requested_checks_complete', 'tool_capability_limit', 'time_budget', 'call_budget',
                'model_round_budget', 'other'}


def record_stop(ctx, reason, detail):
    if reason not in STOP_REASONS or not isinstance(detail, str) or not 16 <= len(detail) <= 1600:
        raise DomainError('INVALID_ARGUMENT', 'Supply a valid stop_reason and stop_detail of16..1600 characters')
    request_path=ctx.run/'request.json';request=json.loads(request_path.read_text()) if request_path.exists() else {}
    deadline=request.get('deadline_unix');remaining=max(0.,deadline-time.time()) if deadline is not None else None
    intent=request.get('task_intent',{})
    if reason=='time_budget' and intent.get('drone_hopping_analysis_requested') and remaining is not None and remaining>120:
        from .drone_hopping import project
        evidence=project(ctx)
        if not evidence or not all(v['coverage']['complete'] for v in evidence):
            raise DomainError('TIME_BUDGET_NOT_EXHAUSTED',f'{remaining:.1f}s remain before the actual deadline. Continue checkpointed hopping checks; do not claim the time limit was reached. A genuine tool/capability failure needs a different honest stop reason.')
    dump(ctx.run / 'analysis-stop.json', dict(reason=reason, detail=detail, utc=utc(),
         observed_deadline_remaining_s=remaining,deadline_reached=remaining is not None and remaining<=0,
         owner='harness', narrative_validation='Model-authored stopping rationale; observed deadline fields are deterministic,not proof of exhaustive recognition.'))


def summary(ctx):
    state = snapshot(ctx)
    path = ctx.run / 'analysis-stop.json'
    declared = json.loads(path.read_text()) if path.exists() else None
    return dict(**state, declared_stop=declared, stopping_reason_recorded=declared is not None)
