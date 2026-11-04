"""Read-only review of old development-test run directories. No removal."""
import ast,json,re,sqlite3,urllib.request
from pathlib import Path
from collections import Counter,defaultdict
from datetime import datetime,timezone

root=Path('/home/sribd/spectrum_agent');base=root/'runs'
folders={p.name:p for p in base.iterdir() if p.is_dir() and not p.is_symlink()}
names=set(folders);words=re.compile(r'[A-Za-z0-9_-]+')
def read(p):
    try:return json.loads(p.read_text(encoding='utf-8-sig'))
    except (OSError,ValueError):return {}
def refs(value):
    result=set()
    def visit(v):
        if isinstance(v,str):result.update(names.intersection(words.findall(v)))
        elif isinstance(v,dict):
            for x in v.values():visit(x)
        elif isinstance(v,list):
            for x in v:visit(x)
    visit(value);return result
def prefix(node):
    if isinstance(node,ast.Constant) and isinstance(node.value,str):return node.value
    if isinstance(node,ast.BinOp) and isinstance(node.op,ast.Add):return prefix(node.left)
    if isinstance(node,ast.JoinedStr) and node.values:return prefix(node.values[0])
    return None
patterns=defaultdict(list)
for p in (root/'tests').glob('test_*.py'):
    for node in ast.walk(ast.parse(p.read_text(encoding='utf-8-sig'))):
        if isinstance(node,ast.Call) and isinstance(node.func,ast.Name) and node.func.id=='Context' and node.args:
            value=prefix(node.args[0])
            if value:patterns[value].append(str(p.relative_to(root)))

receipt=read(root/'docs/testing/artifact-cleanup-deletion-20261029.json')
if receipt.get('status')!='succeeded':raise SystemExit('No successful artifact-cleanup receipt')
previously_cleaned=set(receipt['deleted'])
with sqlite3.connect((base/'tasks.sqlite').as_uri()+'?mode=ro',uri=True) as db:
    database_tasks={r[0]:r[1] for r in db.execute('SELECT id,status FROM tasks')}
visible={r['id'] for r in json.load(urllib.request.urlopen('http://127.0.0.1:8780/edge/tasks',timeout=20))['tasks']}
candidate={};excluded={};dependencies={}
for name,folder in folders.items():
    matched=[p for p in patterns if name==p or re.fullmatch(re.escape(p)+r'[0-9a-f]{8,32}',name)]
    if matched or name in previously_cleaned:
        candidate[name]=dict(test_patterns=matched,artifacts_previously_deleted=name in previously_cleaned)
    dep=set()
    for fn in ('request.json','continuation.json','recording-replay.json'):
        dep.update(refs(read(folder/fn)))
    for p in (folder/'index').glob('*.json'):dep.update(refs(read(p)))
    dependencies[name]=dep-{name}
    if name not in candidate:continue
    if (root/'artifacts'/name).exists():excluded[name]='Artifacts still retained; keep paired run directory'
    if name in database_tasks:excluded[name]='Registered task database entry; retain'
    if name in visible:excluded[name]='Visible in web history; retain'
    if any(p.is_symlink() for p in folder.rglob('*')):excluded[name]='Symlink present; manual review required'
    report=read(folder/'report.json');outcome=read(folder/'outcome.json')
    captures=report.get('captures') or []
    if captures and not captures[0].get('metadata',{}).get('simulated') and outcome:
        excluded[name]='Non-simulated report with outcome; may be eligible for history display'

docrefs=defaultdict(list)
for directory in ('docs','scripts','configs'):
    for p in (root/directory).rglob('*'):
        if not p.is_file() or p.suffix not in ('.json','.jsonl','.txt','.md','.py','.yml','.yaml','.mjs','.cjs'):continue
        if p.name.startswith(('artifact-cleanup-','runs-cleanup-')) or p.stat().st_size>16*1024**2:continue
        try:found=names.intersection(words.findall(p.read_text(encoding='utf-8-sig')))
        except (OSError,UnicodeError):continue
        for name in found:docrefs[name].append(str(p.relative_to(root)))
for name in candidate:
    if name in docrefs:excluded[name]='Referenced by documentation/scripts: '+', '.join(docrefs[name][:3])
remaining=set(candidate)-set(excluded)
while True:
    protected={target:owner for owner,targets in dependencies.items() if owner not in remaining for target in targets if target in remaining}
    if not protected:break
    for name,owner in protected.items():excluded[name]='Referenced by retained run '+owner
    remaining-=set(protected)

all_stats={};items={}
for name,folder in folders.items():
    items[name]=[]
    for p in folder.rglob('*'):
        if p.is_file() and not p.is_symlink():
            s=p.stat();key=(s.st_dev,s.st_ino);all_stats[key]=s;items[name].append((key,s.st_size))
def estimate(group):
    links=Counter(k for n in group for k,size in items[n])
    return dict(directories=len(group),file_paths=sum(len(items[n]) for n in group),logical_bytes=sum(size for n in group for k,size in items[n]),
        estimated_reclaim_bytes=sum(all_stats[k].st_blocks*512 for k,count in links.items() if count==all_stats[k].st_nlink))
groups=defaultdict(list)
for name in remaining:
    patterns_for_name=candidate[name]['test_patterns']
    groups[patterns_for_name[0] if patterns_for_name else re.sub(r'_[0-9a-f]{8,32}$','',name)].append(name)
result=dict(created_utc=datetime.now(timezone.utc).isoformat(),root=str(base),read_only=True,total_run_directories=len(folders),
    summary=estimate(remaining),previously_cleaned_artifact_run_count=len(remaining&previously_cleaned),additional_orphan_test_run_count=len(remaining-previously_cleaned),
    visible_task_count=len(visible),visible_candidate_overlap=sorted(visible&remaining),
    groups=[dict(pattern=k,**estimate(v)) for k,v in sorted(groups.items(),key=lambda kv:len(kv[1]),reverse=True)],
    deletable_test_run_directories=[dict(name=n,path=str(folders[n]),**candidate[n]) for n in sorted(remaining)],
    retained_test_directories=[dict(name=n,reason=excluded[n]) for n in sorted(excluded)],
    retained_other_directories=sorted(names-set(candidate)),
    shared_files_to_retain=['tasks.sqlite','tasks.sqlite-wal','tasks.sqlite-shm','api.pid','ui.pid','api.stdout','api.stderr','ui.stdout','ui.stderr'],
    note='Only verified development-test run directories without retained artifacts. Keep database tasks,visible tasks,non-simulated completed reports,documented baselines and transitive provenance. Removing candidates loses their old test logs/reports/indices. No deletion performed.')
print(json.dumps(result,ensure_ascii=False,indent=2))
