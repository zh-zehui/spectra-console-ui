"""User-authorized fresh USRP capture and official Harness complete-catalog test."""
import json,urllib.request,uuid
from spectrum_agent.common import ROOT,dump
from spectrum_agent.service import credentials
body=dict(prompt='分析时间上限60分钟。用户要求重新真机采集并进行完整测试。中心2400MHz、分析带宽100MHz，连续采集100ms，增益20dB；请inspect_capabilities后使用capture_iq，sample_rate_hz=200000000，不使用历史IQ替代，不重复已完成采集。对这份新数据先全段STFT粗分割，再assess_signal_scene，反复review_activity_batch直到全部粗区域完成局部细查与通用画像。由你基于频段先验与实测特征选择协议工具，关注所有可观测波形，不只Wi-Fi与BLE。可用完整通带的2.4GHz名义信道包括BLE37/38，Wi-Fi2412/2417/2422/2427/2432/2437MHz、IEEE802.15.4信道11到19；按证据选取并记录实际覆盖，不得越界。IEEE802.15.4现在直接返回恢复时钟位置估计；若有旧式窗口证据可localize_verified_frames补定位。细查候选单独保留，不能把全部局部细候选相加成工作边界。区分文件样本相对时间与硬件时钟，采集记录只有100ms。不要把CRC记录数当物理信号数；若不同频率子带出现相同字节与相同时间的帧，记录频率一致性疑点，不宣称新增发射源。完成后报告完整性、溢出、全部区域覆盖、校验结果、未知原因、实际调用及耗时；尚有未检查区域时不得声明全部完成。',
    constraints=dict(center_hz=2400000000,span_hz=100000000,observation_s=.1),
    source='usrp',provider='deepseek',budgets=dict(coverage_mode='complete',max_model_rounds=256,max_candidates=4096,max_local_refinements=8192),
    idempotency_key='fresh-rf-v18-'+uuid.uuid4().hex)
req=urllib.request.Request('http://127.0.0.1:8765/v1/tasks',data=json.dumps(body).encode(),
    headers={'Content-Type':'application/json','Authorization':'Bearer '+credentials()['api_token']})
with urllib.request.urlopen(req,timeout=15) as response:result=json.load(response)
dump(ROOT/'docs/testing/fresh-rf-v18-task.json',result)
print(json.dumps(dict(task_id=result['task_id'],status=result['status'],deadline_s=result['request']['budgets']['deadline_s'])))
