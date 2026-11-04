from __future__ import annotations
import contextlib
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import re
import time
import uuid

ROOT = Path(os.environ.get("SPECTRUM_ROOT", "/home/sribd/spectrum_agent")).resolve()


class DomainError(Exception):
    def __init__(self, code, message):
        self.code = code
        super().__init__(message)


def dump(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")
    temp.replace(path)


def safe_id(value):
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,100}", value):
        raise DomainError("INVALID_ID", "Invalid identifier")
    return value


def utc():
    return dt.datetime.now(dt.timezone.utc).isoformat()


class Context:
    def __init__(self, task_id):
        self.task_id = safe_id(task_id)
        self.run = ROOT / "runs" / self.task_id
        self.run.mkdir(parents=True, exist_ok=True)

    def check(self):
        if (self.run / "cancel").exists():
            raise DomainError("CANCELLED", "Task cancellation requested")
        task = self.run / "request.json"
        if task.exists():
            deadline = json.loads(task.read_text()).get("deadline_unix")
            if deadline and time.time() > deadline:
                raise DomainError("DEADLINE", "Task deadline exceeded")

    def event(self, stage, **fields):
        record = dict(task_id=self.task_id, utc=utc(), stage=stage, **fields)
        with (self.run / "events.jsonl").open("a", encoding="utf-8") as out:
            out.write(json.dumps(record, ensure_ascii=False, allow_nan=False) + "\n")

    @contextlib.contextmanager
    def span(self, stage, **fields):
        started = time.perf_counter_ns()
        span_id = uuid.uuid4().hex
        status = "succeeded"
        error = None
        self.event(stage, phase="start", span_id=span_id, **fields)
        try:
            self.check()
            yield
        except Exception as exc:
            status, error = "failed", getattr(exc, "code", type(exc).__name__)
            raise
        finally:
            self.event(stage, phase="end", span_id=span_id, status=status,
                       elapsed_ms=(time.perf_counter_ns()-started)/1e6, error_code=error, **fields)

    def artifact(self, kind, suffix="json"):
        name = kind + "_" + uuid.uuid4().hex[:16]
        path = ROOT / "artifacts" / self.task_id / (name + "." + suffix)
        path.parent.mkdir(parents=True, exist_ok=True)
        return name, path

    def register(self, artifact_id, path, **metadata):
        path = Path(path).resolve()
        base = (ROOT / "artifacts" / self.task_id).resolve()
        if not path.is_relative_to(base):
            raise DomainError("OUT_OF_SCOPE", "Artifact outside task")
        record = dict(artifact_id=artifact_id, task_id=self.task_id,
                      path=str(path), size_bytes=path.stat().st_size, **metadata)
        digest=hashlib.sha256()
        with path.open("rb") as source:
            for block in iter(lambda:source.read(4*1024*1024),b""):
                digest.update(block)
        record["sha256"]=digest.hexdigest()
        dump(self.run / "index" / (safe_id(artifact_id)+".json"), record)
        return record

    def resolve(self, artifact_id):
        index = self.run / "index" / (safe_id(artifact_id)+".json")
        if not index.exists():
            raise DomainError("ARTIFACT_NOT_FOUND", "Artifact not owned by this task")
        record = json.loads(index.read_text())
        base = (ROOT / "artifacts" / self.task_id).resolve()
        if not Path(record["path"]).resolve().is_relative_to(base):
            raise DomainError("OUT_OF_SCOPE", "Invalid artifact path")
        return record

    def json_artifact(self, kind, value):
        aid, path = self.artifact(kind)
        dump(path, value)
        self.register(aid, path, kind=kind)
        return aid


def request_constraints(ctx):
    path = ctx.run / "request.json"
    return json.loads(path.read_text()).get("constraints", {}) if path.exists() else {}


def finite_number(value, name, low, high):
    import math
    if isinstance(value, bool) or not isinstance(value, (float, int)) or not math.isfinite(value):
        raise DomainError("INVALID_ARGUMENT", name + " must be finite")
    if not low <= value <= high:
        raise DomainError("RESOURCE_LIMIT", f"{name} outside allowed range [{low}, {high}]; received {value}")
    return value
