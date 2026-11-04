"""Submit one official Harness continuation over retained RF IQ, without another capture."""
import json,urllib.request,uuid
from spectrum_agent.common import ROOT,dump
from spectrum_agent.service import credentials

body=dict(prompt='继续全面分析已保存的真实IQ。用户明确要求增加耗时，不得只分析代表性区域。先对原始粗分割的全部215个区域分批完成局部细查和通用画像，再根据证据选择协议工具复查。重点解释G170与G171，所有未确认活动都要有已完成的分析记录及具体未知原因。有效帧只能关联到实际支持的范围；不支持包定位时说明能力限制，不能伪造精确边界。不要再次采集，不要重复继承的完全相同协议尝试。使用review_activity_batch直到remaining_regions为0。保留原始边界，避免把全部细分框直接叠加成工作边界。',
    continue_from_task_id='task_3c974467c0f0438b93bb43ffae5a75e9',source='usrp',provider='deepseek',
    budgets=dict(coverage_mode='complete',deadline_s=3600,max_model_rounds=256,max_candidates=4096,max_local_refinements=8192),
    idempotency_key='complete-v18-'+uuid.uuid4().hex)
req=urllib.request.Request('http://127.0.0.1:8765/v1/tasks',data=json.dumps(body).encode(),
    headers={'Content-Type':'application/json','Authorization':'Bearer '+credentials()['api_token']})
with urllib.request.urlopen(req,timeout=15) as response:result=json.load(response)
dump(ROOT/'docs/testing/complete-v18-task.json',result)
print(json.dumps(dict(task_id=result['task_id'],status=result['status'])))
