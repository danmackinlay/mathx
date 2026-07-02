"""Executor seam: where checker code runs (see CHECK_PLAN.md).

Stage 3 ships the local backend only. The seam's rules, which any future remote
backend (E2B / Daytona / Modal) must also satisfy, are: checked code gets no
shared filesystem with the caller (fresh throwaway cwd), no promised network
access, and no process identity between ``run()`` calls.

``LocalExecutor`` is HYGIENE, NOT A SECURITY BOUNDARY: a subprocess ultimately
runs with the user's privileges, and Python cannot sandbox Python. Real
isolation is what the remote backends are for. A ``session()`` method (stateful
cells, for literal multi-turn TIR) arrives with that upgrade lane.
"""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass

OUTPUT_CAP = 32_768  # bytes kept per stream in the audit record


@dataclass
class ExecResult:
    stdout: str
    stderr: str
    exit_code: int | None  # None when timed out
    timed_out: bool
    elapsed_ms: int


class LocalExecutor:
    """``python -I`` in a throwaway cwd, wall-clock timeout, capped output.

    ``-I`` (isolated mode) drops the cwd and user site-packages from the import
    path but keeps the interpreter's own site-packages — so sympy (a mathx
    dependency) stays importable by checker scripts.
    """

    def run(self, code: str, *, timeout_s: float = 60.0) -> ExecResult:
        t0 = time.monotonic()
        with tempfile.TemporaryDirectory(prefix="mathx-exec-") as cwd:
            try:
                proc = subprocess.run(
                    [sys.executable, "-I", "-c", code],
                    cwd=cwd,
                    capture_output=True,
                    timeout=timeout_s,
                )
                stdout, stderr = proc.stdout, proc.stderr
                exit_code: int | None = proc.returncode
                timed_out = False
            except subprocess.TimeoutExpired as e:
                stdout = e.stdout or b""
                stderr = e.stderr or b""
                exit_code = None
                timed_out = True
        return ExecResult(
            stdout=stdout[:OUTPUT_CAP].decode(errors="replace"),
            stderr=stderr[:OUTPUT_CAP].decode(errors="replace"),
            exit_code=exit_code,
            timed_out=timed_out,
            elapsed_ms=int((time.monotonic() - t0) * 1000),
        )


def get_executor(name: str | None = None) -> LocalExecutor:
    """Resolve an executor by name, defaulting to ``$MATHX_EXECUTOR`` or local."""
    name = name or os.environ.get("MATHX_EXECUTOR", "local")
    if name != "local":
        raise ValueError(
            f"unknown executor {name!r} — only 'local' is implemented; "
            "remote backends (e2b/daytona/modal) are planned, see CHECK_PLAN.md"
        )
    return LocalExecutor()
