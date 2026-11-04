"""Read-only cleanup proposal. Never removes or rewrites project files."""
import ast,json,re,sqlite3,sys
from pathlib import Path
from collections import Counter,defaultdict
from datetime import datetime,timezone

root=Path('/home/sribd/spectrum_agent');base=root/'artifacts'
folders={p.name:p for p in base.iterdir() if p.is_dir() and not p.is_symlink()}
names=set(folders);words=re.compile(r'[A-Za-z0-9_-]+')
def read(path):
    try:return json.loads(path.read_text(encoding='utf-8'))
    except (OSError,ValueError):return {}
def references(value):
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
for source in (root/'tests').glob('test_*.py'):
    try:tree=ast.parse(source.read_text(encoding='utf-8-sig'))
    except (OSError,SyntaxError):continue
    for node in ast.walk(tree):
        if isinstance(node,ast.Call) and isinstance(node.func,ast.Name) and node.func.id=='Context' and node.args:
            p=prefix(node.args[0])
            if p:patterns[p].append(str(source.relative_to(root)))

rows={}
database=root/'runs/tasks.sqlite'
if database.exists():
    with sqlite3.connect(database.as_uri()+'?mode=ro',uri=True) as db:
        rows={r[0]:r[1] for r in db.execute('SELECT id,status FROM tasks')}

candidate={};excluded={};deps={}
for name,folder in folders.items():
    source_patterns=[p for p in patterns if name==p or re.fullmatch(re.escape(p)+r'[0-9a-f]{8,32}',name)]
    if source_patterns:candidate[name]=dict(test_patterns=source_patterns,source_tests=sorted({s for p in source_patterns for s in patterns[p]}))
    run=root/'runs'/name;references_from_run=set();real_capture=False
    for filename in ('request.json','continuation.json','recording-replay.json'):
        references_from_run.update(references(read(run/filename)))
    for path in (run/'index').glob('*.json'):
        record=read(path);references_from_run.update(references(record))
        if record.get('kind')=='capture':
            m=read(Path(record.get('metadata_path','/nonexistent')))
            if m.get('kind')=='rf_capture' or m.get('host_stream_command_utc'):real_capture=True
    deps[name]=references_from_run-{name}
    if name in candidate and name in rows:excluded[name]='Task exists in task database; retain conservatively'
    if name in candidate and real_capture:excluded[name]='Actual RF-capture metadata present; retain conservatively'
    if name in candidate and any(p.is_symlink() for p in folder.rglob('*')):excluded[name]='Symlink present; manual review required'

# Retain regression/documented datasets,even if they were originally tests.
document_references=defaultdict(list)
for directory in ('docs','scripts','configs'):
    for p in (root/directory).rglob('*'):
        if not p.is_file() or p.suffix not in ('.json','.jsonl','.txt','.md','.py','.yml','.yaml','.mjs','.cjs'):continue
        # Cleanup inventories list candidates for review; they are not baselines
        # that must protect those same candidates from the next audit.
        if p.name.startswith('artifact-cleanup-'):continue
        if p.stat().st_size>16*1024**2:continue
        try:mentioned=names.intersection(words.findall(p.read_text(encoding='utf-8-sig')))
        except (OSError,UnicodeError):continue
        for name in mentioned:document_references[name].append(str(p.relative_to(root)))
for name in candidate:
    if name in document_references:excluded[name]='Referenced by documentation or scripts: '+', '.join(document_references[name][:3])
remaining=set(candidate)-set(excluded)
while True:
    protected={source:owner for owner,targets in deps.items() if owner not in remaining for source in targets if source in remaining}
    if not protected:break
    for name,owner in protected.items():excluded[name]='Referenced by retained task '+owner
    remaining-=set(protected)

all_stats={};folder_files={}
for name,folder in folders.items():
    items=[]
    for p in folder.rglob('*'):
        if p.is_file() and not p.is_symlink():
            s=p.stat();key=(s.st_dev,s.st_ino);all_stats[key]=s;items.append((key,s.st_size))
    folder_files[name]=items
def estimate(group):
    links=Counter(key for name in group for key,size in folder_files[name])
    return dict(directories=len(group),file_paths=sum(len(folder_files[n]) for n in group),
        logical_bytes=sum(size for n in group for key,size in folder_files[n]),
        estimated_reclaim_bytes=sum(all_stats[k].st_blocks*512 for k,n in links.items() if n==all_stats[k].st_nlink))
grouped=defaultdict(list)
for name in sorted(remaining):
    grouped[candidate[name]['test_patterns'][0]].append(name)
result=dict(created_utc=datetime.now(timezone.utc).isoformat(),root=str(base),read_only=True,
    policy='Exact manifest only. Verified unit-test Context names; exclude database tasks,real RF captures,documented baselines,symlinks and retained-task provenance dependencies. Do not use name wildcards as deletion authorization.',
    summary=estimate(remaining),groups=[dict(pattern=k,**estimate(v)) for k,v in sorted(grouped.items(),key=lambda kv:len(kv[1]),reverse=True)],
    deletable_test_directories=[dict(name=n,path=str(folders[n]),**candidate[n]) for n in sorted(remaining)],
    retained_test_directories=[dict(name=n,reason=excluded[n]) for n in sorted(excluded)],
    retained_other_directories=sorted(names-set(candidate)),
    note='Estimate counts a file only when all existing hardlinks are in the proposed group; directory overhead excluded. Deleting candidates invalidates their own old test reports/index entries. Current state must be rechecked before actual deletion. No deletion performed.')
print(json.dumps(result,ensure_ascii=False,indent=2))
