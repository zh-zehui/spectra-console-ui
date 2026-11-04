"""Reload project services only when the task queue is idle."""
import hashlib
import json
import os
from pathlib import Path
import signal
import sqlite3
import subprocess
import time

root=Path('/home/sribd/spectrum_agent')
os.chdir(root)
db=sqlite3.connect('runs/tasks.sqlite',timeout=10)
db.execute('BEGIN IMMEDIATE')
if db.execute("SELECT count(*) FROM tasks WHERE status IN ('queued','running')").fetchone()[0]:
    db.rollback();raise RuntimeError('Active task queue; leave services running')
# Release before shutdown: the API worker needs SQLite access to exit cleanly.
db.rollback()
try:
    for name,module in [('api',b'spectrum_agent.api:app'),('ui',b'spectrum_agent.ui_gateway:app')]:
        # A prior failed duplicate launch can leave a stale pidfile. Match exact
        # uvicorn module and project cwd, rather than trusting that pidfile.
        matches=[]
        for entry in Path('/proc').iterdir():
            if not entry.name.isdigit():continue
            try:
                if module in (entry/'cmdline').read_bytes().split(b'\0') and (entry/'cwd').resolve()==root:
                    matches.append(int(entry.name))
            except (FileNotFoundError,PermissionError,ProcessLookupError):continue
        if len(matches)>1:raise RuntimeError('Ambiguous service identity: '+name)
        if not matches:continue
        pid=matches[0];proc=Path('/proc')/str(pid)/'cmdline'
        os.kill(pid,signal.SIGTERM)
        for _ in range(100):
            if not proc.exists() or not proc.read_bytes():break
            time.sleep(.1)
        else:raise RuntimeError('Service did not stop: '+name)
finally:
    db.rollback();db.close()
subprocess.run(['bash','scripts/ensure-ui-stack.sh'],check=True)
manifest=json.loads(Path('docs/source_manifest.json').read_text())
for row in manifest['files']:
    assert hashlib.sha256(Path(row['source']).read_bytes()).hexdigest()==row['sha256'],row['source']
print('Original usrp_client manifest unchanged:',len(manifest['files']),'files')
