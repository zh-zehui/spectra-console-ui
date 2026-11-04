"""Final evidence reconciliation; existing IQ only, no new protocol attempts."""
import json,urllib.request,uuid
from spectrum_agent.common import ROOT,dump
from spectrum_agent.service import credentials
body=dict(prompt='分析时间上限10分钟。只做最终证据对账，不再采集，不再增加分割、提取或协议验证。继承任务已经完成215/215区域画像、全部适用BLE广告和802.15.4名义信道、完整可用Wi-Fi名义信道与4条IEEE802.15.4帧位置估计。先刷新plan_unknown_investigations的queue_id，再给G170/G171更新证据决定，最后build_report。必须修正上轮模型解释把硬件时钟时间当作文件相对时间的错误：原始IQ只有100ms，G170文件样本17428976–17471025、200MS/s，对应87.14488–87.355125ms；G171样本17468976–17501024对应87.34488–87.50512ms。不能写422ms/423ms，那是另一时间原点。query/list工具现在有file_time_start_s/file_time_stop_s，record决定会保存authoritative_scope。4条15.4帧约6.85/10.90/12.72/29.53ms，均不是G171的87.34ms；BLE窗口内虽有36条有效帧，其实际包也未关联G171。G170保留已分析但协议未确认，G171保留窗口帧已定位但未关联本区。用queue目标返回的relevant_evidence_ids，不引用其他窗口证据。全区域画像通过review_activity_batch的进度检查即可，不重跑已完成部分。保留粗工作边界215个；最终报告明确完成的检查及能力限制，不声称协议全识别。',
    continue_from_task_id='task_bc7b8162d8df45b9b43aa86d4e2fa316',source='usrp',provider='deepseek',
    budgets=dict(coverage_mode='complete',max_model_rounds=48,max_candidates=4096,max_local_refinements=8192),
    idempotency_key='final-v18-'+uuid.uuid4().hex)
req=urllib.request.Request('http://127.0.0.1:8765/v1/tasks',data=json.dumps(body).encode(),headers={'Content-Type':'application/json','Authorization':'Bearer '+credentials()['api_token']})
with urllib.request.urlopen(req,timeout=15) as response:result=json.load(response)
dump(ROOT/'docs/testing/complete-v18-final-task.json',result)
print(json.dumps(dict(task_id=result['task_id'],status=result['status'],deadline_s=result['request']['budgets']['deadline_s'])))
