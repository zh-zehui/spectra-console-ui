"""Export observable Harness actions and current source; omit private reasoning and credentials."""
import argparse
import collections
import csv
import hashlib
import json
import re
from pathlib import Path
from datetime import datetime,timezone
from spectrum_agent.common import ROOT,dump

parser=argparse.ArgumentParser(description=__doc__)
parser.add_argument('--output',default=str(ROOT/'docs/archive/2026-10-23-system-v12'))
args=parser.parse_args();dest=Path(args.output).resolve();dest.mkdir(parents=True,exist_ok=True)
PRIVATE={'thinking','reasoning','reasoning_content','reasoningContent','chain_of_thought'}
SECRET=re.compile(r'(api[_-]?key|authorization|password|access[_-]?token|bearer[_-]?token)',re.I)

def text_clean(s):
    s=re.sub(r'\bsk-[A-Za-z0-9_-]{16,}', '[REDACTED_API_KEY]',s)
    return re.sub(r'(?i)\bBearer\s+[A-Za-z0-9._~-]{16,}', 'Bearer [REDACTED]',s)

def clean(v):
    if isinstance(v,dict):return {k:('[REDACTED]' if SECRET.search(k) else clean(x)) for k,x in v.items() if k not in PRIVATE}
    if isinstance(v,list):return [clean(x) for x in v if not isinstance(x,dict) or x.get('type') not in PRIVATE]
    if isinstance(v,str):return text_clean(v)
    return v

def read_json(path,default=None):
    try:return json.loads(path.read_text())
    except (OSError,json.JSONDecodeError):return default

def lines(path):
    if not path.exists():return []
    result=[]
    for i,line in enumerate(path.read_text().splitlines(),1):
        try:result.append((i,json.loads(line)))
        except json.JSONDecodeError:result.append((i,dict(type='unparsed_line',length=len(line))))
    return result

def write_json(path,data):dump(path,clean(data))

inventory=[]
for run in sorted((ROOT/'runs').iterdir()):
    if not run.is_dir() or not (run/'dsh.jsonl').exists():continue
    out=dest/'tasks'/run.name;out.mkdir(parents=True,exist_ok=True)
    trace=lines(run/'dsh.jsonl');events=[e for _,e in lines(run/'events.jsonl')]
    starts={e.get('call_id'):e for e in events if e.get('phase')=='start' and e.get('call_id')}
    ends={e.get('call_id'):e for e in events if e.get('phase')=='end' and e.get('call_id')}
    results={e.get('callId'):e for _,e in trace if e.get('type')=='tool_result'}
    calls=[];observable=[];turn=None;omitted=0
    for index,e in trace:
        if e.get('type') in PRIVATE:omitted+=1;continue
        if e.get('type')=='status' and e.get('turn') is not None:turn=e['turn']
        observable.append(dict(source_line=index,event=clean(e)))
        if e.get('type')!='tool_call':continue
        cid=e.get('callId');response=results.get(cid,{})
        body=response.get('result')
        if isinstance(body,str):
            try:body=json.loads(body)
            except json.JSONDecodeError:pass
        body=clean(body)
        ordinal=len(calls)+1
        call=dict(ordinal=ordinal,source_line=index,model_turn=turn,call_id=cid,tool=e.get('tool'),
            arguments=clean(e.get('input')),started_utc=starts.get(cid,{}).get('utc'),
            ended_utc=ends.get(cid,{}).get('utc'),elapsed_ms=ends.get(cid,{}).get('elapsed_ms'),
            runtime_status=response.get('status','missing_result'),domain_status=body.get('status') if isinstance(body,dict) else None,
            cached=body.get('cached') if isinstance(body,dict) else None,
            error_code=body.get('error_code') if isinstance(body,dict) else None,
            result_status=body.get('result',{}).get('status') if isinstance(body,dict) and isinstance(body.get('result'),dict) else None,
            result_file=f'outputs/{ordinal:03d}.json')
        write_json(out/call['result_file'],body);calls.append(call)
    write_json(out/'calls.json',calls)
    (out/'observable_trace.jsonl').write_text(''.join(json.dumps(e,ensure_ascii=False)+'\n' for e in observable))
    (out/'events.jsonl').write_text(''.join(json.dumps(clean(e),ensure_ascii=False)+'\n' for e in events))
    with (out/'calls.csv').open('w',encoding='utf-8-sig',newline='') as f:
        writer=csv.DictWriter(f,fieldnames=['ordinal','model_turn','tool','call_id','started_utc','ended_utc','elapsed_ms','runtime_status','domain_status','result_status','cached','error_code','arguments','result_file'])
        writer.writeheader()
        for c in calls:writer.writerow({k:json.dumps(c[k],ensure_ascii=False) if k=='arguments' else c.get(k) for k in writer.fieldnames})
    for name in ('request.json','outcome.json','provenance.json','report.json','timing.json'):
        data=read_json(run/name)
        if data is not None:write_json(out/name,data)
    for name in ('prompt.txt','report.md','timing.md'):
        if (run/name).exists():(out/name).write_text(text_clean((run/name).read_text()))
    metadata=[]
    for p in (run/'index').glob('*.json'):
        rec=read_json(p,{})
        if rec:metadata.append(rec)
    write_json(out/'artifact_manifest.json',metadata)
    outcome=read_json(run/'outcome.json',{});request=read_json(run/'request.json',{});report=read_json(run/'report.json',{})
    method='recorded_IQ_replay' if request.get('recording_capture_id') else 'synthetic' if request.get('source')=='synthetic' else 'fresh_RF_task' if any(c.get('metadata',{}).get('simulated') is False for c in report.get('captures',[])) else 'test_or_incomplete'
    spans=[e for e in events if e.get('phase')=='end'];totals=collections.defaultdict(float)
    for e in spans:totals[e['stage']]+=e.get('elapsed_ms',0)/1000
    write_json(out/'archive_metadata.json',dict(task_id=run.name,private_reasoning_events_omitted=omitted,
        method=method,trace_source_sha256=hashlib.sha256((run/'dsh.jsonl').read_bytes()).hexdigest(),
        note='Original logs remain unchanged on Jetson. Export includes observable actions, public text and explicit decision reasons, not private chain-of-thought. Missing per-call timing is not zero; cache/guards can precede worker span instrumentation.'))
    report_lines=[f'# {run.name} 可观测执行记录',f"状态：{outcome.get('status','incomplete')}；来源：{method}；工具调用 {len(calls)} 次。",
        '这是实际日志提取的行为与显式理由，不是重建的内部思维链。runtime completed 不一定代表领域工具成功，需同时看 domain_status/error_code。',
        '原始参数和完整返回见 calls.json、outputs/；模型对外文字见 observable_trace.jsonl；任务完整提示见 prompt.txt。',
        '|序号|模型轮次|工具|领域状态|耗时ms|缓存|','|---|---|---|---|---:|---|']
    for c in calls:
        report_lines.append(f"|{c['ordinal']}|{c['model_turn']}|{c['tool']}|{c['domain_status'] or c['runtime_status']}|{c['elapsed_ms'] if c['elapsed_ms'] is not None else '未记录'}|{c['cached']}|")
    for c in calls:
        report_lines.extend([f"## {c['ordinal']}. {c['tool']}",f"call_id：{c['call_id']}；开始：{c['started_utc']}；结束：{c['ended_utc']}",
            '```json',json.dumps(c['arguments'],ensure_ascii=False,indent=2),'```',f"[完整工具返回]({c['result_file']})"])
    (out/'sequence.md').write_text('\n\n'.join(report_lines)+'\n')
    inventory.append(dict(task_id=run.name,status=outcome.get('status','incomplete'),method=method,tool_calls=len(calls),
        elapsed_s=outcome.get('elapsed_ms',0)/1000 if outcome.get('elapsed_ms') is not None else None,
        completed_utc=outcome.get('completed_utc'),tools=dict(collections.Counter(c['tool'] for c in calls)),stage_seconds=dict(totals),
        explicit_decision_count=len(report.get('segmentation_selections',[]))+len([r for r in (report.get('unknown_investigation') or {}).get('regions',[]) if r.get('decision')]),
        private_reasoning_events_omitted=omitted))

# Current implementation snapshot: no credentials, environments, vendor tree or IQ copies.
paths=list((ROOT/'src/spectrum_agent').glob('*.py'))+[ROOT/p for p in (
    'plugins/spectrum-tools/index.mjs','configs/spectrum.patch.yml','configs/analysis_catalog.json','runtime/selected-dsh.json',
    'scripts/ensure-ui-stack.sh','scripts/start-api.sh','scripts/start-ui.sh','scripts/stop-ui-stack.py','scripts/reload-ui-stack.py','scripts/archive_system.py')]
for p in paths:
    if p.exists():
        target=dest/'source'/p.relative_to(ROOT);target.parent.mkdir(parents=True,exist_ok=True);target.write_text(text_clean(p.read_text()))
plugin=(ROOT/'plugins/spectrum-tools/index.mjs').read_text()
tools=[dict(name=m[0],description=m[1]) for m in re.findall(r"^\s*\['([^']+)','([^']+)'",plugin,re.M)]
write_json(dest/'registered_tools.json',tools)
write_json(dest/'inventory.json',dict(generated_utc=datetime.now(timezone.utc).isoformat(),task_count=len(inventory),
    tools_registered=len(tools),tool_call_count=sum(t['tool_calls'] for t in inventory),tasks=inventory,
    status_counts=dict(collections.Counter(t['status'] for t in inventory)),
    note='Contains development/local-adapter tests as well as real tasks; incomplete does not imply the live RF platform failed. No private reasoning content or credential files exported.'))
index=['# 历史任务调用索引','包含实测、录制回放、合成和适配器开发测试；需按来源和状态区分。',
    '|任务|来源|状态|工具调用|耗时s|','|---|---|---|---:|---:|']
for t in inventory:index.append(f"|[{t['task_id']}](tasks/{t['task_id']}/sequence.md)|{t['method']}|{t['status']}|{t['tool_calls']}|{t['elapsed_s']}|")
(dest/'task-index.md').write_text('\n'.join(index)+'\n')
manifest=[]
for p in sorted(dest.rglob('*')):
    if p.is_file() and p.name!='manifest.json':manifest.append(dict(path=str(p.relative_to(dest)),bytes=p.stat().st_size,sha256=hashlib.sha256(p.read_bytes()).hexdigest()))
write_json(dest/'manifest.json',manifest)
print(json.dumps(dict(directory=str(dest),tasks=len(inventory),tools=len(tools),calls=sum(t['tool_calls'] for t in inventory),files=len(manifest)),ensure_ascii=False))
