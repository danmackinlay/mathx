"""Executor tests: real subprocesses, kept fast."""
from __future__ import annotations

import pytest

from mathx.executor import OUTPUT_CAP, LocalExecutor, get_executor


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
