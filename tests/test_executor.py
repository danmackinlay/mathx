"""Executor tests: real subprocesses, kept fast."""
from __future__ import annotations

import os
import time

import pytest

from mathx.executor import OUTPUT_CAP, LocalExecutor, get_executor, is_slow


class TestLocalExecutor:
    def test_captures_stdout_and_exit(self):
        res = LocalExecutor().run("print('hello'); print('world')")
        assert res.exit_code == 0
        assert not res.timed_out
        assert res.stdout == "hello\nworld\n"
        assert res.stderr == ""
        assert res.elapsed_ms >= 0

    def test_nonzero_exit_and_stderr(self):
        res = LocalExecutor().run("raise SystemExit('boom')")
        assert res.exit_code == 1
        assert "boom" in res.stderr

    def test_timeout_kills(self):
        res = LocalExecutor().run("import time; time.sleep(60)", timeout_s=1.0)
        assert res.timed_out
        assert res.exit_code is None
        assert res.elapsed_ms < 30_000

    def test_timeout_kills_whole_process_tree(self):
        # subprocess.run(timeout=...) kills only the direct child; a checker that
        # shells out must not leave grandchildren running past the budget. The
        # script prints its grandchild's pid (flushed, so it survives the kill as
        # partial output), then sleeps past the budget.
        code = (
            "import subprocess, sys, time\n"
            "p = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])\n"
            "print(p.pid, flush=True)\n"
            "time.sleep(60)\n"
        )
        t0 = time.monotonic()
        res = LocalExecutor().run(code, timeout_s=1.5)
        assert res.timed_out
        assert res.exit_code is None
        assert time.monotonic() - t0 < 15  # returned near the budget, not after 60 s
        pid = int(res.stdout.split()[0])
        # best-effort: SIGKILL delivery/reaping is async — poll briefly for the
        # grandchild to vanish (kill(pid, 0) raising means it's gone)
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            try:
                os.kill(pid, 0)
            except ProcessLookupError:
                break
            time.sleep(0.05)
        else:
            os.kill(pid, 9)  # don't leak it into the test environment
            pytest.fail(f"grandchild {pid} survived the process-tree kill")

    def test_output_capped(self):
        res = LocalExecutor().run(f"print('x' * {OUTPUT_CAP * 2})")
        assert len(res.stdout) <= OUTPUT_CAP

    def test_sympy_importable_under_isolated_mode(self):
        # -I must still see the venv's site-packages, or checker scripts are useless
        res = LocalExecutor().run("import sympy; print('VERDICT: PASS')")
        assert res.exit_code == 0, res.stderr
        assert "VERDICT: PASS" in res.stdout

    def test_fresh_cwd_not_the_callers(self):
        res = LocalExecutor().run("import os; print(os.getcwd())")
        assert "mathx-exec-" in res.stdout


class TestIsSlow:
    """The scalar slow-metric predicate (single source: executor owns the budget)."""

    def test_at_and_below_threshold(self):
        assert is_slow(1600, timed_out=False, timeout_s=2.0)  # exactly 0.8 × budget
        assert not is_slow(1599, timed_out=False, timeout_s=2.0)

    def test_timed_out_is_not_slow(self):
        # timeouts are their own (louder) flag; slow means "finished, barely in budget"
        assert not is_slow(60_000, timed_out=True, timeout_s=60.0)

    def test_unknown_budget_never_flags(self):
        assert not is_slow(10_000, timed_out=False, timeout_s=None)
        assert not is_slow(10_000, timed_out=False, timeout_s=0)


class TestGetExecutor:
    def test_default_local(self, monkeypatch):
        monkeypatch.delenv("MATHX_EXECUTOR", raising=False)
        assert isinstance(get_executor(), LocalExecutor)

    def test_env_override_local(self, monkeypatch):
        monkeypatch.setenv("MATHX_EXECUTOR", "local")
        assert isinstance(get_executor(), LocalExecutor)

    def test_unknown_backend_rejected(self, monkeypatch):
        monkeypatch.setenv("MATHX_EXECUTOR", "e2b")
        with pytest.raises(ValueError, match="only 'local' is implemented"):
            get_executor()
