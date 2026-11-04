"""Official Harness validation of prompt time limit and remaining frame/channel checks."""
import json,urllib.request,uuid
from spectrum_agent.common import ROOT,dump
from spectrum_agent.service import credentials

body=dict(prompt='分析时间上限30分钟。继续已完成全部215区域局部细查的真实IQ任务，禁止再采集。首先调用localize_verified_frames，verification_id=ieee154_verification_2282b022a7fb4dd3，补齐4条继承IEEE802.15.4有效帧的时钟位置估计，再assess_signal_scene复核G171的实际帧关联。不要把时钟位置估计说成已标定精确边界。使用新的紧凑预算输出刷新plan_unknown_investigations，应能读取queue_id；对G170与G171写入具体证据和未知原因。剩余有完整通带可检查的名义信道：IEEE802.15.4信道12、13、14、17、18与Wi-Fi2422MHz，请按先验和实测结构完成不重复的整段100ms验证；已完成的信道不重复。2442MHz的17MHz通带上界2450.5MHz超出原始请求2450MHz，不得越界尝试。所有粗活动区域都已有完整局部复查，继承并复用region_analysis，不要重新做215次。保留粗工作方案；新增局部细候选单独展示。最后生成报告，区分全区域画像已覆盖、协议解码成功、未知原因、尚不支持的PHY，不要宣称所有信号均已识别。',
    continue_from_task_id='task_4222a86d292f49f286dc5ce41bfce8c1',source='usrp',provider='deepseek',
    budgets=dict(coverage_mode='complete',max_model_rounds=128,max_candidates=4096,max_local_refinements=8192),
    idempotency_key='localization-v18-'+uuid.uuid4().hex)
req=urllib.request.Request('http://127.0.0.1:8765/v1/tasks',data=json.dumps(body).encode(),
    headers={'Content-Type':'application/json','Authorization':'Bearer '+credentials()['api_token']})
with urllib.request.urlopen(req,timeout=15) as response:result=json.load(response)
dump(ROOT/'docs/testing/localization-v18-task.json',result)
print(json.dumps(dict(task_id=result['task_id'],status=result['status'],budget_resolution=result['request']['budget_resolution'])))
