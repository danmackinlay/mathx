"""File-per-job store: named, inspectable background runs.

This module is the substrate everything reads, and it stays a stdlib-only leaf
of the import graph: executing jobs lives in ``mathx.worker`` (which imports
the engines), never here.

One JSON file per job under ``$MATHX_JOBS_DIR``, else ``$XDG_CACHE_HOME/mathx/jobs``,
else ``~/.cache/mathx/jobs``. Every surface (CLI ``status``/``jobs``/``show``, MCP
``poll_job``) is just a reader of these files; the only writers are ``submit``
(initial "running" record) and the worker (final "complete"/"error" record), both
via atomic tmp-file-then-rename.

Record shape:

    {"job_id": "20260703T021530Z-a3f2", "status": "running",
     "started_at": ISO8601, "kind": "solve" | "check", "args": {...}}

``args`` carries the task knobs for its kind — solve: {problem, strategy, k,
max_k}; check: {claim, tir_k, grade_k, exec_timeout_s}; argue: those plus
{rounds, ledger_id} (the ledger is the artifact — the job result is a thin
pointer to it) — and, for every kind, ``args["provider"]``: the endpoint
bundle written by ``ProviderConfig.to_args()`` and rehydrated by the worker
with ``ProviderConfig.from_args()``. Records predating ``kind`` are treated
as solve. When finished, the record gains
``finished_at`` and either ``result`` (exactly what ``--out`` writes for that
kind) or ``error``.

The API key is NEVER written to disk: the worker resolves it from its
environment (``MATHX_API_KEY``/``OPENAI_API_KEY``) at run time; ``spawn_worker``
can inject an explicitly-passed key into the child's environment only.

The worker entry point is ``python -m mathx.worker <job_id>`` — spawned
detached by both ``mathx submit`` and the MCP server, so jobs survive whoever
submitted them.
"""
from __future__ import annotations

import json
import os
import secrets
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path


def jobs_dir() -> Path:
    if os.environ.get("MATHX_JOBS_DIR"):
        return Path(os.environ["MATHX_JOBS_DIR"])
    cache = Path(os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache")
    return cache / "mathx" / "jobs"


def _job_path(job_id: str) -> Path:
    if "/" in job_id or os.sep in job_id or ".." in job_id:
        raise KeyError(f"invalid job id: {job_id!r}")
    return jobs_dir() / f"{job_id}.json"


def _write_atomic(path: Path, record: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(record, indent=2))
    tmp.rename(path)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def new_job_id() -> str:
    return time.strftime("%Y%m%dT%H%M%SZ", time.gmtime()) + "-" + secrets.token_hex(2)


def submit(*, kind: str = "solve", args: dict) -> dict:
    """Write a fresh "running" record and return it. Does NOT start the worker —
    the caller decides how (``spawn_worker`` for fire-and-forget, ``run_job`` inline)."""
    if kind not in ("solve", "check", "argue"):
        raise ValueError(f"unknown job kind: {kind!r}")
    job_id = new_job_id()
    while _job_path(job_id).exists():
        job_id = new_job_id()
    record = {
        "job_id": job_id,
        "status": "running",
        "started_at": _now(),
        "kind": kind,
        "args": args,
    }
    _write_atomic(_job_path(job_id), record)
    return record


def finalize(job_id: str, *, result: dict) -> dict:
    record = read(job_id)
    record.update(status="complete", finished_at=_now(), result=result)
    _write_atomic(_job_path(job_id), record)
    return record


def fail(job_id: str, *, error: str) -> dict:
    record = read(job_id)
    record.update(status="error", finished_at=_now(), error=error)
    _write_atomic(_job_path(job_id), record)
    return record


def read(job_id: str) -> dict:
    """The raw record. Raises KeyError for an unknown id."""
    path = _job_path(job_id)
    try:
        return json.loads(path.read_text())
    except FileNotFoundError:
        raise KeyError(f"unknown job id: {job_id}") from None


def stamp_worker(job_id: str) -> dict:
    """Record the executing worker's pid, making orphans detectable: a
    'running' record whose worker is gone died without finalizing (live e2e:
    four workers vanished mid-run and their jobs read as running forever)."""
    record = read(job_id)
    record["worker_pid"] = os.getpid()
    _write_atomic(_job_path(job_id), record)
    return record


def worker_alive(record: dict) -> bool | None:
    """True/False when the record names a worker pid on this host; None when
    liveness is unknowable (no pid stamped yet)."""
    pid = record.get("worker_pid")
    if not pid:
        return None
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True  # exists, owned by someone else


def check(job_id: str) -> dict:
    """The record, plus live ``elapsed_ms`` and worker liveness while running."""
    record = read(job_id)
    if record.get("status") == "running":
        started = datetime.fromisoformat(record["started_at"])
        record["elapsed_ms"] = int((datetime.now(timezone.utc) - started).total_seconds() * 1000)
        alive = worker_alive(record)
        if alive is not None:
            record["worker_alive"] = alive
    return record


def list_jobs() -> list[dict]:
    """All readable records, newest first. Corrupt/partial files are skipped."""
    records = []
    if jobs_dir().is_dir():
        for path in jobs_dir().glob("*.json"):
            try:
                record = json.loads(path.read_text())
            except (OSError, json.JSONDecodeError):
                continue
            if isinstance(record, dict) and "job_id" in record:
                records.append(record)
    records.sort(key=lambda r: r.get("started_at", ""), reverse=True)
    return records


def prune(*, hours: float) -> int:
    """Delete records older than *hours* (by finish time, or start time if never
    finished — a "running" job that old is an orphan). Returns how many."""
    cutoff = datetime.now(timezone.utc).timestamp() - hours * 3600
    n = 0
    for record in list_jobs():
        stamp = record.get("finished_at") or record.get("started_at")
        if stamp and datetime.fromisoformat(stamp).timestamp() < cutoff:
            _job_path(record["job_id"]).unlink(missing_ok=True)
            n += 1
    return n


def spawn_worker(job_id: str, *, api_key: str | None = None) -> None:
    """Run the job in a detached child that outlives this process."""
    env = os.environ.copy()
    if api_key:
        env["MATHX_API_KEY"] = api_key
    subprocess.Popen(
        [sys.executable, "-m", "mathx.worker", job_id],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
        env=env,
    )
