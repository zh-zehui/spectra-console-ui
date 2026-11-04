"""Reversible task-list removal. IQ, reports, provenance and queue rows are retained."""
import json,sqlite3
from pathlib import Path
from .common import ROOT,DomainError,dump,safe_id,utc

MARKER='record-deleted.json'
TERMINAL={'succeeded','partial','failed','cancelled'}


def removed(task_id):
    return (ROOT/'runs'/safe_id(task_id)/MARKER).exists()


def set_removed(task_id,deleted=True):
    task_id=safe_id(task_id);run=(ROOT/'runs'/task_id).resolve()
    if not run.is_relative_to((ROOT/'runs').resolve()) or not run.is_dir():
        raise DomainError('NOT_FOUND','Task record not found')
    database=ROOT/'runs/tasks.sqlite';connection=None;status=None
    try:
        if database.exists():
            connection=sqlite3.connect(str(database),timeout=10);connection.execute('BEGIN IMMEDIATE')
            row=connection.execute('SELECT status FROM tasks WHERE id=?',(task_id,)).fetchone()
            if row:status=row[0]
        if status is None:
            outcome=run/'outcome.json'
            if outcome.exists():status=json.loads(outcome.read_text(encoding='utf-8')).get('status')
        if status not in TERMINAL:
            raise DomainError('TASK_ACTIVE','Finish or cancel the task before removing its record')
        marker=run/MARKER
        if deleted:
            if not marker.exists():dump(marker,dict(task_id=task_id,removed_utc=utc(),iq_retained=True,analysis_retained=True))
        elif marker.exists():marker.unlink()
        if connection:connection.commit()
        return dict(task_id=task_id,record_removed=deleted,iq_retained=True,analysis_retained=True,
            note='Removed from history only; no disk-space reclamation and no artifact deletion')
    finally:
        if connection:connection.close()
