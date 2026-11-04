import json
import os
import time
import unittest
import uuid
from spectrum_agent.common import Context, DomainError, dump
from spectrum_agent import analysis_budget as budget
from spectrum_agent.service import normalize, dsh_prompt
from spectrum_agent.worker import execute, report


class AnalysisBudgetTests(unittest.TestCase):
    def test_unfinished_hopping_cannot_claim_time_limit_with_budget_remaining(self):
        ctx=Context('budget_hopping_'+uuid.uuid4().hex[:10])
        dump(ctx.run/'request.json',dict(deadline_unix=time.time()+900,task_intent=dict(drone_hopping_analysis_requested=True)))
        with self.assertRaisesRegex(DomainError,'remain before the actual deadline'):
            budget.record_stop(ctx,'time_budget','Unfinished hopping is not permission to invent an exhausted deadline')
        self.assertFalse((ctx.run/'analysis-stop.json').exists())
        dump(ctx.run/'request.json',dict(deadline_unix=time.time()-1,task_intent=dict(drone_hopping_analysis_requested=True)))
        budget.record_stop(ctx,'time_budget','Actual deadline reached with coverage retained as partial')
        value=json.loads((ctx.run/'analysis-stop.json').read_text())
        self.assertTrue(value['deadline_reached']);self.assertEqual(value['observed_deadline_remaining_s'],0)

    def setUp(self):
        self.ctx = Context('budget_test_' + uuid.uuid4().hex[:10])

    def request(self, **values):
        req = dict(budgets=dict(deadline_s=180, max_candidates=4, max_local_refinements=6),
                   deadline_unix=time.time()+180)
        req.update(values)
        dump(self.ctx.run/'request.json', req)
        return req

    def test_default_policy_and_explicit_baseline(self):
        req = dict(prompt='test', constraints=dict(center_hz=2.4e9, span_hz=100e6),budgets=dict(coverage_mode='sampled'))
        normalized = normalize(req)
        self.assertEqual(normalized['budgets']['max_local_refinements'], 8)
        self.assertIn('DYNAMIC budget', dsh_prompt(dict(normalized,task_intent=dict(status='resolved'))))
        self.assertNotIn('全任务最多两次', dsh_prompt(normalized))
        self.assertEqual(normalize(dict(req,budgets=dict(coverage_mode='sampled',max_local_refinements=2)))['budgets']['max_local_refinements'], 2)
        for value in (True, 0, 17, 2.5):
            with self.assertRaises(DomainError): normalize(dict(req,budgets=dict(coverage_mode='sampled',max_local_refinements=value)))

    def test_more_than_two_attempts_allowed_and_failures_count(self):
        self.request()
        for i in range(6):
            budget.enforce(self.ctx, dict(segmentation_id='base',group_id='g1'))
            try:
                with self.ctx.span('refine_signal_region'):
                    if i % 2: raise DomainError('TEST', 'controlled failure')
            except DomainError: pass
        state = budget.snapshot(self.ctx)
        self.assertEqual((state['local_attempts'],state['local_succeeded']), (6,3))
        with self.assertRaisesRegex(DomainError,'local_call_budget'):
            budget.enforce(self.ctx,dict(segmentation_id='base',group_id='g1'))
        self.assertEqual(budget.snapshot(self.ctx)['local_attempts'],6)

    def test_deadline_reserves_reporting_and_uses_measured_cost(self):
        self.request(deadline_unix=1000)
        self.assertTrue(budget.snapshot(self.ctx,now=900)['next_local_allowed'])
        self.ctx.event('refine_signal_region',phase='end',status='succeeded',elapsed_ms=50000)
        state=budget.snapshot(self.ctx,now=930)
        self.assertFalse(state['next_local_allowed'])
        self.assertEqual(state['local_stop_constraint'],'report_time_reserve')
        self.assertEqual(state['report_reserve_s'],27)

    def test_parent_budget_allows_alternatives_but_not_new_parent(self):
        self.request(budgets=dict(max_candidates=1,max_local_refinements=6))
        self.ctx.json_artifact('scene_refinement',dict(parent_segmentation_id='base',parent_group_id='g1'))
        budget.enforce(self.ctx,dict(segmentation_id='base',group_id='g1'))
        with self.assertRaisesRegex(DomainError,'distinct_local_target_budget'):
            budget.enforce(self.ctx,dict(segmentation_id='base',group_id='g2'))

    def test_stop_is_explicit_model_rationale_not_completeness(self):
        self.request()
        report(self.ctx,stop_reason='time_budget',stop_detail='Stop with untested regions retained; resume from their saved IQ windows.')
        report(self.ctx)  # supervisor projection preserves the model's declared reason
        result=json.loads((self.ctx.run/'report.json').read_text())
        self.assertEqual(result['analysis_budget']['declared_stop']['reason'],'time_budget')
        self.assertFalse(result['analysis_budget']['scope_complete'])
        self.assertIn('Local-analysis budget', (self.ctx.run/'report.md').read_text(encoding='utf-8'))

    @unittest.skipIf(os.name=='nt','Worker file locking requires Jetson/Linux')
    def test_replay_cannot_touch_hardware_and_limit_is_enforced_in_worker(self):
        self.request(recording_capture_id='recording',budgets=dict(max_local_refinements=3))
        for tool in ('capture_iq','inspect_capabilities','make_synthetic_capture'):
            with self.assertRaisesRegex(DomainError,'cannot access hardware'): execute(self.ctx.task_id,tool,{})
        for i in range(3):
            with self.assertRaises(DomainError):
                execute(self.ctx.task_id,'refine_signal_region',dict(segmentation_id='missing',group_id='g1',reason=str(i),evidence_ids=[]))
        with self.assertRaisesRegex(DomainError,'local_call_budget'):
            execute(self.ctx.task_id,'refine_signal_region',dict(segmentation_id='missing',group_id='g1',reason='four',evidence_ids=[]))
