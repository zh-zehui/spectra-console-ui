import json,unittest,uuid
from unittest.mock import patch
from spectrum_agent.common import Context,DomainError,dump
from spectrum_agent.service import normalize,dsh_prompt
from spectrum_agent.task_intent import resolve
from spectrum_agent.worker import execute

PROMPT='分析中心频率 5.8 GHz、160 MHz 带宽内的所有信号活动。识别已知协议，描述未知信号，根据证据复查分割结果。我开了一台无人机，他的图传链路正常，图传频率范围为5798.5-5808.5，带宽10MHz。'

class IntentTests(unittest.TestCase):
    def test_combined_wifi_and_agile_drone_intent_keeps_both_workflows(self):
        prompt='真机采集中心5.8GHz、带宽160MHz、200MS/s、500ms，分析Wi-Fi和跳频变带宽无人机信号。'
        ctx=Context('intent_hopping_'+uuid.uuid4().hex[:10]);request=normalize(dict(prompt=prompt,source='usrp'))
        dump(ctx.run/'request.json',request)
        value=resolve(ctx,5800e6,160e6,[prompt],'联合解码Wi-Fi并独立检查变频变带宽图传，保留两种证据层级。',
            sample_rate_hz=200e6,observation_s=.5,protocol_analysis_requested=True,drone_hopping_analysis_requested=True,
            comprehensive_protocol_survey=True,wide_wifi_headers_requested=True)
        self.assertTrue(value['task_intent']['drone_video_analysis_requested'])
        self.assertTrue(value['task_intent']['drone_hopping_analysis_requested'])
        self.assertTrue(value['task_intent']['protocol_analysis_requested'])
        for tool in ('analyze_drone_hopping','plan_protocol_checks','run_protocol_batch'):
            self.assertIn(tool,value['execution_instructions'])

    def setUp(self):
        self.ctx=Context('intent_test_'+uuid.uuid4().hex[:10])
        self.req=normalize(dict(prompt=PROMPT,constraints=dict(center_hz=5800e6,span_hz=10e6,sample_rate_hz=200e6)))
        dump(self.ctx.run/'request.json',self.req)

    def test_no_regex_override_and_full_prompt_reaches_dsh(self):
        with patch('spectrum_agent.service.prompt_signal_parameters',side_effect=AssertionError('regex must not run')):
            request=normalize(dict(prompt=PROMPT,constraints=dict(center_hz=5800e6,span_hz=100e6)))
        self.assertEqual(request['constraints']['span_hz'],100e6)
        self.assertEqual(request['task_intent']['status'],'pending_model')
        self.assertIn(PROMPT,dsh_prompt(request))

    def test_model_scope_and_target_are_separate_and_immutable(self):
        result=resolve(self.ctx,5800e6,160e6,[PROMPT],
            '全局分析160MHz，图传10MHz是范围内的待验证目标。',
            targets=[dict(label='无人机图传',frequency_low_hz=5798.5e6,frequency_high_hz=5808.5e6,bandwidth_hz=10e6)])
        value=json.loads((self.ctx.run/'request.json').read_text())
        self.assertEqual(value['constraints']['span_hz'],160e6)
        self.assertEqual(value['task_intent']['targets'][0]['bandwidth_hz'],10e6)
        self.assertFalse(value['task_intent']['targets'][0]['protocol_confirmed'])
        self.assertEqual(value['task_intent']['original_prompt'],PROMPT)
        self.assertIn('Use capture_iq',result['execution_instructions'])
        with self.assertRaises(DomainError):resolve(self.ctx,5800e6,10e6,[PROMPT],'不能在解释之后缩小任务范围')

    def test_no_hardware_or_dsp_before_resolution(self):
        with patch('spectrum_agent.acquisition.capture') as capture:
            with self.assertRaisesRegex(DomainError,'resolve_task_intent'):
                execute(self.ctx.task_id,'capture_iq',dict(center_hz=5800e6,span_hz=10e6))
            capture.assert_not_called()
        with self.assertRaises(DomainError):execute(self.ctx.task_id,'query_band_priors',dict(center_hz=5800e6,span_hz=10e6))

    def test_bad_evidence_or_hardware_request_cannot_resolve(self):
        with self.assertRaises(DomainError):resolve(self.ctx,5800e6,160e6,['invented quote'],'无法伪造证据而执行任务')
        with self.assertRaises(DomainError):resolve(self.ctx,5800e6,200e6,[PROMPT],'要求超过当前硬件允许的范围')
        self.assertEqual(json.loads((self.ctx.run/'request.json').read_text())['task_intent']['status'],'pending_model')

    def test_analysis_budget_is_model_resolved(self):
        request=normalize(dict(prompt='分析30分钟',source='synthetic',constraints=dict(center_hz=2400e6,span_hz=3.2e6)))
        dump(self.ctx.run/'request.json',request)
        resolve(self.ctx,2400e6,3.2e6,['分析30分钟'],'分析时限与采集时长分别处理',deadline_s=1800)
        result=json.loads((self.ctx.run/'request.json').read_text())
        self.assertEqual(result['budgets']['deadline_s'],1800)
        self.assertEqual(result['budget_resolution']['source'],'model_prompt')

    def test_api_budget_stays_hard_and_intent_only_blocks_rf(self):
        request=normalize(dict(prompt=PROMPT,constraints=dict(center_hz=5800e6,span_hz=10e6),budgets=dict(deadline_s=60)))
        request['intent_only']=True;dump(self.ctx.run/'request.json',request)
        with self.assertRaises(DomainError):resolve(self.ctx,5800e6,160e6,[PROMPT],'显式API时间预算不能覆盖',deadline_s=90)
        resolve(self.ctx,5800e6,160e6,[PROMPT],'完整160MHz范围，保留显式预算')
        with self.assertRaisesRegex(DomainError,'Interpretation acceptance'):
            execute(self.ctx.task_id,'inspect_capabilities',{})

    def test_recording_only_meaning_is_model_metadata_not_fixed_task_enum(self):
        out=resolve(self.ctx,5800e6,160e6,[PROMPT],'模型可自由描述任务类型，无程序枚举分类',
            task_kind='record_iq_for_later_research',analysis_requested=False)
        value=out['task_intent']
        self.assertFalse(value['analysis_requested'])
        self.assertEqual(value['task_kind'],'record_iq_for_later_research')
        self.assertNotIn('COMPLETE CATALOG',out['execution_instructions'])
