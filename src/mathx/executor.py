"""Executor seam: where checker code runs (see DESIGN_NOTES.md#claim-checker-mathx-check).

Stage 3 ships the local backend only. The seam's rules, which any future remote
backend (E2B / Daytona / Modal) must also satisfy, are: checked code gets no
shared filesystem with the caller (fresh throwaway cwd), no promised network
access, and no process identity between ``run()`` calls.

``LocalExecutor`` is HYGIENE, NOT A SECURITY BOUNDARY: a subprocess ultimately
runs with the user's privileges, and Python cannot sandbox Python. Real
isolation is what the remote backends are for. A ``session()`` method (stateful
cells, for literal multi-turn TIR) arrives with that upgrade lane.
On timeout the WHOLE process tree is killed, not just the direct child — a
checker that shells out must not leave grandchildren running past the budget.
"""
from __future__ import annotations

import os
import signal
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from typing import Protocol

OUTPUT_CAP = 32_768  # bytes kept per stream in the audit record

# A script whose execution eats this fraction of its wall-clock budget is
# flagged "slow" — an early warning for NP-hard / near-non-terminating checkers,
# short of an outright timeout. Lives here (not check.py) because this module
# owns the execution budget and is a near-leaf: report.py, a pure reader, can
# import it without dragging in the engine.
SLOW_FRACTION = 0.8


@dataclass
class ExecResult:
    stdout: str
    stderr: str
    exit_code: int | None  # None when timed out
    timed_out: bool
    elapsed_ms: int


def is_slow(elapsed_ms: int, *, timed_out: bool, timeout_s: float | None) -> bool:
    """A completed run that ate ≥ SLOW_FRACTION of its budget (not timed out,
    and only meaningful when a budget is known)."""
    if not timeout_s or timed_out:
        return False
    return elapsed_ms >= SLOW_FRACTION * timeout_s * 1000


class Executor(Protocol):
    """What ``check()`` needs from a backend — the seam future remote executors
    (and test fakes) implement."""

    def run(self, code: str, *, timeout_s: float = 60.0) -> ExecResult: ...


class LocalExecutor:
    """``python -I`` in a throwaway cwd, wall-clock timeout, capped output.

    ``-I`` (isolated mode) drops the cwd and user site-packages from the import
    path but keeps the interpreter's own site-packages — so sympy (a mathx
    dependency) stays importable by checker scripts.
    """

    def run(self, code: str, *, timeout_s: float = 60.0) -> ExecResult:
        t0 = time.monotonic()
        with tempfile.TemporaryDirectory(prefix="mathx-exec-") as cwd:
            # start_new_session makes the child its own session (and process
            # group) leader, so on timeout killpg(child_pid) reaps the whole
            # tree — subprocess.run(timeout=...) kills only the direct child.
            proc = subprocess.Popen(
                [sys.executable, "-I", "-c", code],
                cwd=cwd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                start_new_session=True,
            )
            try:
                stdout, stderr = proc.communicate(timeout=timeout_s)
                exit_code: int | None = proc.returncode
                timed_out = False
            except subprocess.TimeoutExpired:
                try:
                    os.killpg(proc.pid, signal.SIGKILL)  # pid == pgid: session leader
                except ProcessLookupError:
                    pass  # child died between the timeout and the kill
                stdout, stderr = proc.communicate()  # reap; collect partial output
                exit_code = None
                timed_out = True
        return ExecResult(
            stdout=stdout[:OUTPUT_CAP].decode(errors="replace"),
            stderr=stderr[:OUTPUT_CAP].decode(errors="replace"),
            exit_code=exit_code,
            timed_out=timed_out,
            elapsed_ms=int((time.monotonic() - t0) * 1000),
        )


def get_executor(name: str | None = None) -> Executor:
    """Resolve an executor by name, defaulting to ``$MATHX_EXECUTOR`` or local."""
    name = name or os.environ.get("MATHX_EXECUTOR", "local")
    if name != "local":
        raise ValueError(
            f"unknown executor {name!r} — only 'local' is implemented; "
            "remote backends (e2b/daytona/modal) are planned, "
            "see DESIGN_NOTES.md#claim-checker-mathx-check"
        )
    return LocalExecutor()
