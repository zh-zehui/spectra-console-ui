"""Stop only this project's idle API/UI processes. --check never changes state."""
import argparse
import ctypes
import os
from pathlib import Path
import platform
import select
import signal
import sqlite3

def pidfd_syscall(number, *arguments):
    """Older Python builds omit pidfd APIs even when the Linux kernel supports them."""
    if platform.system() != 'Linux' or platform.machine() not in ('aarch64', 'x86_64'):
        raise RuntimeError('This platform has no supported pidfd fallback; services left running')
    libc = ctypes.CDLL(None, use_errno=True)
    libc.syscall.restype = ctypes.c_long
    result = libc.syscall(ctypes.c_long(number), *arguments)
    if result == -1:
        error = ctypes.get_errno()
        raise OSError(error, os.strerror(error))
    return result

def open_pidfd(pid):
    if hasattr(os, 'pidfd_open'):
        return os.pidfd_open(pid)
    return pidfd_syscall(434, ctypes.c_int(pid), ctypes.c_uint(0))

def terminate_pidfd(descriptor):
    if hasattr(signal, 'pidfd_send_signal'):
        return signal.pidfd_send_signal(descriptor, signal.SIGTERM)
    return pidfd_syscall(424, ctypes.c_int(descriptor), ctypes.c_int(signal.SIGTERM),
                         ctypes.c_void_p(), ctypes.c_uint(0))

parser=argparse.ArgumentParser(description=__doc__)
parser.add_argument('--check',action='store_true')
args=parser.parse_args()
root=Path('/home/sribd/spectrum_agent').resolve()
database=root/'runs/tasks.sqlite'
active=0
if database.exists():
    with sqlite3.connect(database.as_uri()+'?mode=ro',uri=True) as db:
        active=db.execute("SELECT count(*) FROM tasks WHERE status IN ('queued','running','cancelling')").fetchone()[0]

def owned(entry,module):
    try:
        return entry.stat().st_uid==os.getuid() and (entry/'cwd').resolve()==root and module in (entry/'cmdline').read_bytes().split(b'\0')
    except (OSError,PermissionError):return False

services=[]
manual_jobs=[]
for entry in Path('/proc').iterdir():
    if not entry.name.isdigit():continue
    try:
        if entry.stat().st_uid!=os.getuid() or (entry/'cwd').resolve()!=root:continue
        command=(entry/'cmdline').read_bytes().split(b'\0')
        if b'spectrum_agent.worker' in command or any(str(root/'runtime/dsh').encode() in a for a in command) or any(Path(a.decode(errors='replace')).name=='diagnose_b40_v17.py' for a in command):
            manual_jobs.append(int(entry.name))
    except (OSError,PermissionError):continue
for name,module in [('ui',b'spectrum_agent.ui_gateway:app'),('api',b'spectrum_agent.api:app')]:
    matches=[int(e.name) for e in Path('/proc').iterdir() if e.name.isdigit() and owned(e,module)]
    if len(matches)>1:raise SystemExit('Ambiguous project service identity: '+name)
    services.extend((name,pid,module) for pid in matches)
print('Active queued/running/cancelling tasks:',active)
print('Active Harness/domain worker processes:',len(manual_jobs))
for name,pid,_ in services:print(name,'PID',pid)
if args.check:raise SystemExit(0)
if active or manual_jobs:raise SystemExit('Platform left running. Cancel active/queued tasks in the UI, or finish a manual Harness run, then stop again.')
for name,pid,module in services:
    try:descriptor=open_pidfd(pid)
    except ProcessLookupError:continue
    try:
        if not owned(Path('/proc')/str(pid),module):raise SystemExit('Service identity changed; stop aborted')
        terminate_pidfd(descriptor)
        if not select.select([descriptor],[],[],25)[0]:raise SystemExit(name+' did not exit gracefully; not force-killed')
        print(name,'stopped')
    finally:os.close(descriptor)
print('Platform stopped. Captures, reports and source files are retained.')
