"""Check-engine tests: parsing units, then check() end-to-end with the fake
endpoint and REAL local script execution."""
from __future__ import annotations

import asyncio
from itertools import count

import pytest
from conftest import provider

from mathx.check import (
    CHECKER_SYSTEM,
    GRADER_SYSTEM,
    check,
    check_result_to_dict,
    extract_code,
    parse_script_verdict,
)
from mathx.executor import ExecResult


class FakeExecutor:
    """Returns a canned ExecResult, ignoring the code — lets tests drive
    exec_elapsed_ms / timed_out without real subprocesses."""

    def __init__(self, elapsed_ms: int = 40, timed_out: bool = False):
        self.elapsed_ms, self.timed_out = elapsed_ms, timed_out

    def run(self, code: str, *, timeout_s: float = 60.0) -> ExecResult:
        if self.timed_out:
            return ExecResult("", "", None, True, self.elapsed_ms)
        return ExecResult("VERDICT: PASS", "", 0, False, self.elapsed_ms)

PASS_SCRIPT = 'reply with:\n```python\nprint("VERDICT: PASS")\n```'
FAIL_SCRIPT = (
    "```python\n"
    'print("COUNTEREXAMPLE: n=5 gives 32 < 25 is false")\n'
    'print("VERDICT: FAIL")\n'
    "```"
)


def cycler(replies: list[str]):
    """Order-immune scripted replies: call i gets replies[i % len]."""
    n = count()
    return lambda _user: replies[next(n) % len(replies)]


def run_check(claim: str = "2+2=4", **kwargs):
    return asyncio.run(check(claim, provider=provider(), **kwargs))


def exec_result(stdout: str = "", stderr: str = "", exit_code: int | None = 0, timed_out: bool = False):
    return ExecResult(stdout=stdout, stderr=stderr, exit_code=exit_code, timed_out=timed_out, elapsed_ms=1)


class TestExtractCode:
    def test_last_fenced_block_wins(self):
        text = "```python\nfirst\n```\nthen\n```python\nsecond\n```"
        assert extract_code(text) == "second"

    def test_py_and_bare_fences(self):
        assert extract_code("```py\nx = 1\n```") == "x = 1"
        assert extract_code("```\nx = 2\n```") == "x = 2"

    def test_none_when_absent(self):
        assert extract_code("no code here") is None
        assert extract_code(None) is None


class TestParseScriptVerdict:
    def test_pass(self):
        assert parse_script_verdict(exec_result("checking...\nVERDICT: PASS\n")) == ("pass", None)

    def test_fail_with_counterexample(self):
        verdict, note = parse_script_verdict(
            exec_result("COUNTEREXAMPLE: x=3\nVERDICT: FAIL\n")
        )
        assert verdict == "fail"
        assert note == "x=3"

    def test_inconclusive_with_reason(self):
        verdict, note = parse_script_verdict(
            exec_result("REASON: claim is not decidable numerically\nVERDICT: INCONCLUSIVE\n")
        )
        assert verdict == "inconclusive"
        assert note == "claim is not decidable numerically"

    def test_no_verdict_line(self):
        verdict, note = parse_script_verdict(exec_result("just noise\n"))
        assert verdict == "error"
        assert "no VERDICT line" in note

    def test_crash(self):
        verdict, note = parse_script_verdict(
            exec_result("", "Traceback ...\nZeroDivisionError: division by zero", 1)
        )
        assert verdict == "error"
        assert "ZeroDivisionError" in note

    def test_verdict_but_nonzero_exit_is_suspect(self):
        verdict, note = parse_script_verdict(exec_result("VERDICT: PASS\n", "", 1))
        assert verdict == "error"
        assert "exited 1" in note

    def test_timeout(self):
        assert parse_script_verdict(exec_result(timed_out=True, exit_code=None)) == (
            "error",
            "script timed out",
        )


class TestCheck:
    def test_supported_when_lanes_agree(self, fake_endpoint):
        fake_endpoint(by_system={
            CHECKER_SYSTEM: PASS_SCRIPT,
            GRADER_SYSTEM: cycler([r"\boxed{TRUE}"] * 3),
        })
        r = run_check(tir_k=1, grade_k=3)
        assert r.status == "supported"
        assert r.tir_runs[0].verdict == "pass"
        assert r.grade_verdict == "true"
        assert r.grade_margin == "3/3"
        assert "tir: pass" in r.summary and "grade: true" in r.summary

    def test_conflict_when_lanes_disagree(self, fake_endpoint):
        fake_endpoint(by_system={
            CHECKER_SYSTEM: FAIL_SCRIPT,
            GRADER_SYSTEM: cycler([r"\boxed{TRUE}"] * 3),
        })
        r = run_check(tir_k=1, grade_k=3)
        assert r.status == "conflict"
        assert r.tir_runs[0].verdict == "fail"
        assert r.tir_runs[0].note == "n=5 gives 32 < 25 is false"

    def test_refuted(self, fake_endpoint):
        fake_endpoint(by_system={
            CHECKER_SYSTEM: FAIL_SCRIPT,
            GRADER_SYSTEM: cycler([r"\boxed{FALSE}"] * 3),
        })
        assert run_check(tir_k=1, grade_k=3).status == "refuted"

    def test_grade_majority_and_abstain(self, fake_endpoint):
        fake_endpoint(by_system={
            GRADER_SYSTEM: cycler(
                [r"\boxed{TRUE}", r"\boxed{TRUE}", r"\boxed{FALSE}", r"\boxed{UNDECIDED}"]
            ),
        })
        r = run_check(tir_k=0, grade_k=4)
        assert r.grade_verdict == "true"
        assert r.grade_margin == "2/3"  # UNDECIDED abstains
        assert r.status == "supported"
        assert r.tir_runs == []

    def test_grade_tolerates_latex_wrapped_votes(self, fake_endpoint):
        # live cloud e2e: a grader boxed \text{FALSE} and went uncounted
        fake_endpoint(by_system={
            GRADER_SYSTEM: cycler([r"\boxed{\text{FALSE}}", r"\boxed{\mathrm{FALSE}}", r"\boxed{False.}"]),
        })
        r = run_check(tir_k=0, grade_k=3)
        assert r.grade_verdict == "false"
        assert r.grade_margin == "3/3"

    def test_grade_errors_counted_apart_from_abstentions(self, fake_endpoint):
        # live cloud e2e: four 503s rendered as "abstain", hiding the real story
        fake_endpoint([r"\boxed{TRUE}", 400, "no box here"])
        r = run_check(tir_k=0, grade_k=3)
        d = check_result_to_dict(r)
        assert d["grade"]["true"] == 1
        assert d["grade"]["errors"] == 1
        assert d["grade"]["abstain"] == 1
        assert r.grade_margin == "1/1"

    def test_grade_split_is_unclear(self, fake_endpoint):
        fake_endpoint(by_system={
            GRADER_SYSTEM: cycler([r"\boxed{TRUE}", r"\boxed{FALSE}"]),
        })
        r = run_check(tir_k=0, grade_k=2)
        assert r.grade_verdict == "split"
        assert r.status == "unclear"

    def test_generation_without_code_block(self, fake_endpoint):
        fake_endpoint(by_system={
            CHECKER_SYSTEM: "I cannot write a script for this.",
        })
        r = run_check(tir_k=1, grade_k=0)
        assert r.tir_runs[0].verdict == "error"
        assert "no ```python block" in r.tir_runs[0].note
        assert r.status == "unclear"

    def test_tir_split_across_scripts(self, fake_endpoint):
        fake_endpoint(by_system={
            CHECKER_SYSTEM: cycler([PASS_SCRIPT, FAIL_SCRIPT]),
        })
        r = run_check(tir_k=2, grade_k=0)
        assert sorted(run.verdict for run in r.tir_runs) == ["fail", "pass"]
        assert "tir: split" in r.summary
        assert r.status == "unclear"

    def test_script_timeout_is_error(self, fake_endpoint):
        fake_endpoint(by_system={
            CHECKER_SYSTEM: "```python\nimport time; time.sleep(60)\n```",
        })
        r = run_check(tir_k=1, grade_k=0, exec_timeout_s=1.0)
        assert r.tir_runs[0].verdict == "error"
        assert r.tir_runs[0].timed_out

    def test_both_lanes_off_rejected(self):
        with pytest.raises(ValueError, match="at least one lane"):
            run_check(tir_k=0, grade_k=0)


class TestExecSummary:
    def test_rollup_totals(self, fake_endpoint):
        fake_endpoint(by_system={CHECKER_SYSTEM: PASS_SCRIPT})
        d = check_result_to_dict(
            run_check(tir_k=3, grade_k=0, executor=FakeExecutor(elapsed_ms=40))
        )
        ex = d["exec"]
        assert ex["n_scripts"] == 3
        assert ex["ms_total"] == 120 and ex["ms_max"] == 40
        assert ex["n_timed_out"] == 0 and ex["n_slow"] == 0
        assert d["exec_timeout_s"] == 60.0

    def test_timeout_counted(self, fake_endpoint):
        fake_endpoint(by_system={CHECKER_SYSTEM: PASS_SCRIPT})
        d = check_result_to_dict(
            run_check(tir_k=1, grade_k=0, exec_timeout_s=2.0,
                      executor=FakeExecutor(timed_out=True))
        )
        assert d["exec"]["n_timed_out"] == 1

    def test_slow_counted(self, fake_endpoint):
        # 1800 ms ≥ 0.8 × 2000 ms budget → slow, but not timed out
        fake_endpoint(by_system={CHECKER_SYSTEM: PASS_SCRIPT})
        d = check_result_to_dict(
            run_check(tir_k=1, grade_k=0, exec_timeout_s=2.0,
                      executor=FakeExecutor(elapsed_ms=1800))
        )
        assert d["exec"]["n_slow"] == 1 and d["exec"]["n_timed_out"] == 0

    def test_on_script_fires_per_script(self, fake_endpoint):
        fake_endpoint(by_system={CHECKER_SYSTEM: PASS_SCRIPT})
        seen: list[tuple[int, int]] = []
        asyncio.run(
            check("2+2=4", provider=provider(), tir_k=3, grade_k=0,
                  executor=FakeExecutor(),
                  on_script=lambda run, done, planned: seen.append((done, planned)))
        )
        assert [d for d, _ in seen] == [1, 2, 3]  # monotonic done
        assert all(p == 3 for _, p in seen)

    def test_serialization_shape(self, fake_endpoint):
        fake_endpoint(by_system={
            CHECKER_SYSTEM: PASS_SCRIPT,
            GRADER_SYSTEM: cycler([r"\boxed{TRUE}", r"\boxed{FALSE}"]),
        })
        d = check_result_to_dict(run_check(tir_k=1, grade_k=2))
        assert d["kind"] == "check"
        assert d["claim"] == "2+2=4"
        assert d["tir"][0]["verdict"] == "pass"
        assert d["tir"][0]["code"] == 'print("VERDICT: PASS")'
        assert "VERDICT: PASS" in d["tir"][0]["stdout"]
        assert d["tir"][0]["gen"]["text"]  # generation kept as audit trail
        assert d["grade"]["true"] == 1 and d["grade"]["false"] == 1
        assert len(d["grade"]["samples"]) == 2
        assert d["tokens_out_total"] == 3 * 20
