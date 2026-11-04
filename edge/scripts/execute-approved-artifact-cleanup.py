"""Execute only the user's approved1660-directory manifest,with revalidation."""
import argparse,json,os,shutil,sqlite3,subprocess,time,urllib.request
from pathlib import Path
from datetime import datetime,timezone

parser=argparse.ArgumentParser(description=__doc__)
parser.add_argument('--execute-approved-manifest',action='store_true',required=True)
parser.parse_args()
root=Path('/home/sribd/spectrum_agent').resolve();base=(root/'artifacts').resolve()
approved_path=root/'docs/testing/artifact-cleanup-audit-20261029.json'
approved=json.loads(approved_path.read_text(encoding='utf-8-sig'))
fresh=json.loads(Path('/tmp/spectra-artifact-cleanup-fresh-20261029.json').read_text())
if time.time()-datetime.fromisoformat(fresh['created_utc']).timestamp()>600:raise SystemExit('Fresh audit expired')
allowed={r['name'] for r in fresh['deletable_test_directories']}
entries=approved['deletable_test_directories'];names={r['name'] for r in entries}
if len(entries)!=1660 or len(names)!=1660 or not names<=allowed:raise SystemExit('Approved manifest no longer matches safe candidates')
if Path(approved['root']).resolve()!=base:raise SystemExit('Unexpected manifest root')
targets=[]
for row in entries:
    p=Path(row['path'])
    if p.name!=row['name'] or p.parent!=base or p.is_symlink() or p.resolve()!=p or not p.is_dir():raise SystemExit('Unsafe or missing target: '+str(p))
    if any(q.is_symlink() for q in p.rglob('*')):raise SystemExit('Symlink found: '+str(p))
    targets.append((p,p.stat().st_ino))

def snapshot_retained():
    result={}
    for folder in base.iterdir():
        if folder.name in names or not folder.is_dir():continue
        for p in folder.rglob('*'):
            if p.is_file():
                s=p.stat();result[str(p)]=(s.st_dev,s.st_ino,s.st_size,s.st_mtime_ns)
    return result
def history():
    return json.load(urllib.request.urlopen('http://127.0.0.1:8780/edge/tasks',timeout=20))['tasks']
def validate_history(tasks):
    issues=[];capture_count=0
    for row in tasks:
        tid=row['id']
        if tid in names:issues.append('Visible task selected: '+tid)
        for f in (root/'runs'/tid/'index').glob('*.json'):
            rec=json.loads(f.read_text())
            if rec.get('kind')=='capture':capture_count+=1
            for key in ('path','metadata_path'):
                if key not in rec:continue
                p=Path(rec[key])
                if not p.exists():issues.append('Missing file: '+str(p))
                if p.is_relative_to(base) and p.relative_to(base).parts[0] in names:issues.append('Retained dependency selected: '+str(p))
    if issues:raise RuntimeError('; '.join(issues[:10]))
    return capture_count

check=subprocess.run([str(root/'.venv/bin/python'),str(root/'scripts/stop-ui-stack.py'),'--check'],cwd=root,capture_output=True,text=True,check=True).stdout
if 'Active queued/running/cancelling tasks: 0' not in check or 'Active Harness/domain worker processes: 0' not in check:raise SystemExit('Active work detected; no deletion')
before_tasks=history();before_ids={r['id'] for r in before_tasks};captures=validate_history(before_tasks)
before_retained=snapshot_retained();free_before=shutil.disk_usage(base).free
receipt=dict(started_utc=datetime.now(timezone.utc).isoformat(),root=str(base),approved_manifest=str(approved_path),
    authorized_directory_count=len(targets),deleted=[],visible_task_ids_before=sorted(before_ids),
    retained_file_paths_checked=len(before_retained),visible_raw_captures_checked=captures,status='running')
receipt_path=root/'docs/testing/artifact-cleanup-deletion-20261029.json'
def save():
    temp=receipt_path.with_suffix('.tmp');temp.write_text(json.dumps(receipt,ensure_ascii=False,indent=2));os.replace(temp,receipt_path)
save()
database=root/'runs/tasks.sqlite'
db=sqlite3.connect(str(database),timeout=10)
try:
    # Briefly serialize task creation with removal; do not stop API/UI services.
    db.execute('BEGIN IMMEDIATE')
    active=db.execute("SELECT count(*) FROM tasks WHERE status IN ('queued','running','cancelling')").fetchone()[0]
    if active:raise RuntimeError('A task became active before deletion')
    for p,inode in targets:
        if p.parent!=base or p.is_symlink() or p.resolve()!=p or p.stat().st_ino!=inode:raise RuntimeError('Target identity changed: '+str(p))
        shutil.rmtree(p)
        receipt['deleted'].append(p.name)
        if len(receipt['deleted'])%100==0:save()
except BaseException as exc:
    receipt.update(status='failed',error=str(exc));save();raise
finally:
    db.rollback();db.close()

after_retained=snapshot_retained();after_tasks=history();after_ids={r['id'] for r in after_tasks}
captures_after=validate_history(after_tasks)
if before_retained!=after_retained:raise RuntimeError('Retained artifact snapshot changed; inspect receipt')
if before_ids!=after_ids:raise RuntimeError('Visible task list changed; inspect receipt')
if any(p.exists() for p,inode in targets):raise RuntimeError('Some approved target paths still exist')
receipt.update(status='succeeded',completed_utc=datetime.now(timezone.utc).isoformat(),
    deleted_directory_count=len(receipt['deleted']),remaining_artifact_directories=sum(p.is_dir() for p in base.iterdir()),
    filesystem_free_bytes_before=free_before,filesystem_free_bytes_after=shutil.disk_usage(base).free,
    filesystem_free_bytes_increase=shutil.disk_usage(base).free-free_before,retained_artifacts_unchanged=True,
    visible_task_ids_after=sorted(after_ids),visible_raw_captures_checked_after=captures_after,
    note='Only exact approved artifacts subdirectories removed. runs,source code,real tasks and services retained. Old removed development-test artifact lookups no longer work.')
save()
print(json.dumps({k:v for k,v in receipt.items() if k not in ('deleted','visible_task_ids_before','visible_task_ids_after')},ensure_ascii=False,indent=2))
