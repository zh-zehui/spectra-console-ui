"""Export observable tools/results and task reports only, never private model reasoning or IQ."""
import argparse,hashlib,json,re,zipfile
from pathlib import Path
from spectrum_agent.common import ROOT,safe_id,dump
parser=argparse.ArgumentParser(description=__doc__)
parser.add_argument('task_id');parser.add_argument('--label',required=True);args=parser.parse_args()
source=ROOT/'runs'/safe_id(args.task_id);label=safe_id(args.label)
target=ROOT/'docs/testing'/(label+'-trace');target.mkdir(parents=True,exist_ok=True)
private={'thinking','reasoning','reasoning_content','reasoningContent','chain_of_thought'}
def clean(value):
    if isinstance(value,dict):return {k:('[REDACTED]' if re.search('api.?key|password|authorization',k,re.I) else clean(v)) for k,v in value.items() if k not in private}
    if isinstance(value,list):return [clean(v) for v in value]
    if isinstance(value,str):return re.sub(r'sk-[A-Za-z0-9_-]{16,}','[REDACTED]',value)
    return value
files=[]
for name in ('request.json','outcome.json','report.json','report.md','timing.json','timing.md','events.jsonl','continuation.json','analysis-stop.json'):
    path=source/name
    if not path.exists():continue
    if path.suffix=='.json':dump(target/name,clean(json.loads(path.read_text())))
    elif path.suffix=='.jsonl':
        rows=[clean(json.loads(line)) for line in path.read_text().splitlines() if json.loads(line).get('type') not in private]
        (target/name).write_text('\n'.join(json.dumps(r,ensure_ascii=False) for r in rows)+'\n',encoding='utf-8')
    else:(target/name).write_text(clean(path.read_text()),encoding='utf-8')
    files.append(name)
calls=[];by_id={};omitted=0
for line in (source/'dsh.jsonl').read_text().splitlines():
    e=json.loads(line)
    if e.get('type') in private:omitted+=1
    elif e.get('type')=='tool_call':
        c=dict(ordinal=len(calls)+1,call_id=e['callId'],tool=e['tool'],arguments=clean(e.get('input',{})));calls.append(c);by_id[e['callId']]=c
    elif e.get('type')=='tool_result' and e.get('callId') in by_id:
        result=e.get('result')
        if isinstance(result,str):
            try:result=json.loads(result)
            except json.JSONDecodeError:pass
        by_id[e['callId']]['result']=clean(result)
events=[json.loads(line) for line in (source/'events.jsonl').read_text().splitlines()]
for c in calls:
    end=next((e for e in events if e.get('phase')=='end' and e.get('call_id')==c['call_id'] and e.get('stage')==c['tool']),None)
    c['worker_elapsed_ms']=end.get('elapsed_ms') if end else None
dump(target/'calls.json',calls);files.append('calls.json')
dump(target/'archive_metadata.json',dict(task_id=args.task_id,observable_tool_calls=len(calls),
    omitted_private_reasoning_events=omitted,raw_dsh_sha256=hashlib.sha256((source/'dsh.jsonl').read_bytes()).hexdigest(),iq_exported=False,
    note='Missing timing is unknown, not zero. Public tool rationales and final narratives are not independently validated.'))
files.append('archive_metadata.json')
archive=ROOT/'docs/testing'/(label+'-trace.zip')
with zipfile.ZipFile(archive,'w',zipfile.ZIP_DEFLATED) as z:
    for name in files:z.write(target/name,arcname=target.name+'/'+name)
print(json.dumps(dict(archive=str(archive),public_calls=len(calls),bytes=archive.stat().st_size)))
