"""File-per-job store: named, inspectable background runs.

This module is the substrate everything reads, and it stays a stdlib-only leaf
of the import graph: executing jobs lives in ``mathx.worker`` (which imports
the engines), never here. The store mechanics (atomic writes, ids, path
guarding) are shared with the ledger store via ``mathx._store``.

One JSON file per job under ``$MATHX_JOBS_DIR``, else ``$XDG_CACHE_HOME/mathx/jobs``,
else ``~/.cache/mathx/jobs``. Every surface (CLI ``status``/``jobs``/``show``, MCP
``poll_job``) is just a reader of these files; the only writers are ``submit``
(initial "queued" record) and the worker: ``stamp_worker`` (the worker announcing
itself — "running" + pid + host) and the final "complete"/"error" record, all via
atomic tmp-file-then-rename. Status honesty matters: a record says "queued" until
a worker actually exists (an audit found submit writing "running" before any
worker did — deferred jobs lied), so "running" always implies a stamped pid.

Record shape:

    {"job_id": "20260703T021530Z-a3f2", "status": "queued",
     "started_at": ISO8601, "kind": "solve" | "check", "args": {...}}

``args`` carries the task knobs for its kind — solve: {problem, strategy, k,
max_k}; check: {claim, tir_k, grade_k, exec_timeout_s}; argue: those plus
{rounds, ledger_id} (the ledger is the artifact — the job result is a thin
pointer to it) — and, for every kind, ``args["provider"]``: the endpoint
bundle written by ``ProviderConfig.to_args()`` and rehydrated by the worker
with ``ProviderConfig.from_args()``. Records predating ``kind`` are treated
as solve. Once a worker starts, the record gains ``worker_pid``/``worker_host``
and status "running". When finished, the record gains ``finished_at`` and
either ``result`` (exactly what ``--out`` writes for that kind) or ``error``.

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
import socket
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

from mathx._store import guarded_path, list_records, new_id, now_iso, write_atomic

# Retained aliases: the old private names leaked (tests and the ledger store imported
# them) before the shared conventions moved to mathx._store.
new_job_id = new_id
_write_atomic = write_atomic


def jobs_dir() -> Path:
    if os.environ.get("MATHX_JOBS_DIR"):
        return Path(os.environ["MATHX_JOBS_DIR"])
    cache = Path(os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache")
    return cache / "mathx" / "jobs"


def _job_path(job_id: str) -> Path:
    return guarded_path(jobs_dir(), job_id, label="job")


def submit(*, kind: str = "solve", args: dict) -> dict:
    """Write a fresh "queued" record and return it. Does NOT start the worker —
    the caller decides how (``spawn_worker`` for fire-and-forget, ``run_job``
    inline); the worker itself flips the status to "running" via ``stamp_worker``."""
    if kind not in ("solve", "check", "argue"):
        raise ValueError(f"unknown job kind: {kind!r}")
    job_id = new_id()
    while _job_path(job_id).exists():
        job_id = new_id()
    record = {
        "job_id": job_id,
        "status": "queued",
        "started_at": now_iso(),
        "kind": kind,
        "args": args,
    }
    write_atomic(_job_path(job_id), record)
    return record


def finalize(job_id: str, *, result: dict) -> dict:
    record = read(job_id)
    record.update(status="complete", finished_at=now_iso(), result=result)
    write_atomic(_job_path(job_id), record)
    return record


def fail(job_id: str, *, error: str) -> dict:
    record = read(job_id)
    record.update(status="error", finished_at=now_iso(), error=error)
    write_atomic(_job_path(job_id), record)
    return record


def read(job_id: str) -> dict:
    """The raw record. Raises KeyError for an unknown id."""
    path = _job_path(job_id)
    try:
        return json.loads(path.read_text())
    except FileNotFoundError:
        raise KeyError(f"unknown job id: {job_id}") from None


def stamp_worker(job_id: str) -> dict:
    """Flip the record to "running" and stamp this worker's pid + host, in ONE
    atomic write, making orphans detectable: a 'running' record whose worker is
    gone died without finalizing (live e2e: four workers vanished mid-run and
    their jobs read as running forever). Because status and pid land together,
    "running" always implies a probe-able pid — a record still "queued" past the
    spawn window means the worker never started (see ``argue.STAMP_GRACE_S``)."""
    record = read(job_id)
    record.update(status="running", worker_pid=os.getpid(), worker_host=socket.gethostname())
    write_atomic(_job_path(job_id), record)
    return record


def worker_alive(record: dict) -> bool | None:
    """True/False when the record names a worker pid probe-able from here; None
    when liveness is unknowable: no pid stamped yet, or the pid was stamped on a
    different host (a shared or rebooted job dir — a foreign pid is meaningless
    locally, so we refuse to guess). Records with a pid but no ``worker_host``
    predate host stamping and are treated as local, preserving their old
    semantics. Residual risk, stated plainly: pids recycle, so a long-dead
    worker's pid may have been reused by an unrelated process and read as True.
    Accepted — a start-time check would need psutil, and the poll loops only
    act on False, which pid recycling cannot produce for a live worker."""
    pid = record.get("worker_pid")
    if not pid:
        return None
    host = record.get("worker_host")
    if host is not None and host != socket.gethostname():
        return None
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True  # exists, owned by someone else


def check(job_id: str) -> dict:
    """The record, plus live ``elapsed_ms`` while unfinished (queued or running)
    and worker liveness once a pid is stamped."""
    record = read(job_id)
    if record.get("status") in ("queued", "running"):
        started = datetime.fromisoformat(record["started_at"])
        record["elapsed_ms"] = int((datetime.now(timezone.utc) - started).total_seconds() * 1000)
        alive = worker_alive(record)
        if alive is not None:
            record["worker_alive"] = alive
    return record


def list_jobs() -> list[dict]:
    """All readable records, newest first. Corrupt/partial files are skipped."""
    records = list_records(jobs_dir(), "job_id")
    records.sort(key=lambda r: r.get("started_at", ""), reverse=True)
    return records


def prune(*, hours: float) -> int:
    """Delete records older than *hours* (by finish time, or start time if never
    finished — a "queued"/"running" job that old is an orphan). Returns how many."""
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
