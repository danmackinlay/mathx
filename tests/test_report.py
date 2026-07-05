"""Report renderer tests: pure dict-in, text-out."""
from __future__ import annotations

import pytest

from mathx.report import (
    render_check_report,
    render_check_script,
    render_report,
    render_sample,
)


def make_run(**overrides) -> dict:
    run = {
        "problem": "What is 6*7?",
        "answer": "42",
        "margin": "2/4",
        "votes": {"42": 2.0, "41": 1.0, "40": 1.0},
        "strategy": "maj@k",
        "escalations": 0,
        "model": "test-model",
        "base_url": "http://fake.test/v1",
        "k": 4,
        "tokens_in_total": 40,
        "tokens_out_total": 80,
        "elapsed_ms_total": 1234,
        "samples": [
            {"boxed": "42", "confidence": None, "error": None,
             "tokens_in": 10, "tokens_out": 20, "elapsed_ms": 300, "text": "reasoning A"},
            {"boxed": "42", "confidence": None, "error": None,
             "tokens_in": 10, "tokens_out": 20, "elapsed_ms": 310, "text": "reasoning B"},
            {"boxed": "41", "confidence": None, "error": None,
             "tokens_in": 10, "tokens_out": 20, "elapsed_ms": 320, "text": "reasoning C"},
            {"boxed": "40", "confidence": None, "error": None,
             "tokens_in": 10, "tokens_out": 20, "elapsed_ms": 330, "text": "reasoning D"},
        ],
    }
    run.update(overrides)
    return run


class TestRenderReport:
    def test_header(self):
        out = render_report(make_run())
        assert "problem: What is 6*7?" in out
        assert "answer: 42" in out
        assert "margin: 2/4" in out
        assert "tokens: in=40 out=80" in out

    def test_vote_histogram_marks_winner_and_sorts(self):
        out = render_report(make_run())
        lines = out.splitlines()
        vote_lines = [l for l in lines if "█" in l]
        assert len(vote_lines) == 3
        assert "42" in vote_lines[0] and "← winner" in vote_lines[0]
        assert "← winner" not in vote_lines[1]

    def test_equivalent_dissenter_counts_with_winner(self):
        # 1/2 is maths-equivalent to 0.5: it must get the ✓, not the ✗
        run = make_run(
            answer="0.5",
            margin="2/2",
            votes={"0.5": 2.0},
            samples=[
                {"boxed": "0.5", "text": "a"},
                {"boxed": r"\frac{1}{2}", "text": "b"},
            ],
        )
        out = render_report(run)
        assert "✗" not in out
        assert "unanimous: all 2 voters agree" in out

    def test_disagreement_surfaced(self):
        out = render_report(make_run())
        assert "disagreement: 2/4 voters against the winner" in out
        assert "41 (1)" in out and "40 (1)" in out

    def test_escalations_shown_when_nonzero(self):
        assert "escalations: 2" in render_report(make_run(escalations=2))
        assert "escalations" not in render_report(make_run())

    def test_non_voters_counted(self):
        run = make_run(
            samples=make_run()["samples"]
            + [
                {"boxed": None, "error": "APIError: boom", "text": None},
                {"boxed": None, "error": None, "text": "gave up"},
            ]
        )
        out = render_report(run)
        assert "1 errored" in out
        assert "1 returned no \\boxed{...}" in out

    def test_no_answer_run(self):
        run = make_run(
            answer=None,
            margin="0/0",
            votes={},
            samples=[{"boxed": None, "error": "APIError: boom", "text": None}],
        )
        out = render_report(run)
        assert "answer: None" in out
        assert "no sample produced a \\boxed{...} answer" in out
        assert "mathx doctor" in out


class TestRenderSample:
    def test_full_text_with_header(self):
        out = render_sample(make_run(), 2)
        assert "sample 2" in out
        assert "boxed='41'" in out
        assert "reasoning C" in out

    def test_out_of_range(self):
        with pytest.raises(IndexError, match="run has 4 samples"):
            render_sample(make_run(), 4)
        with pytest.raises(IndexError):
            render_sample(make_run(), -1)


def make_check(tir: list[dict] | None = None, **overrides) -> dict:
    tir = [{"verdict": "pass", "note": None, "exit_code": 0, "timed_out": False,
            "exec_elapsed_ms": 40, "code": "print('VERDICT: PASS')", "stdout": "VERDICT: PASS",
            "stderr": "", "gen": {"text": "gen"}}] if tir is None else tir
    run = {
        "kind": "check", "claim": "2+2=4", "status": "supported",
        "summary": "supported — tir: pass (1 script)",
        "model": "test-model", "meta_model": None, "base_url": "http://fake.test/v1",
        "tir_k": len(tir), "grade_k": 0,
        "tokens_in_total": 10, "tokens_out_total": 20, "elapsed_ms_total": 5000,
        "exec_timeout_s": 60.0,
        "exec": {"n_scripts": len(tir), "ms_total": sum(r["exec_elapsed_ms"] for r in tir),
                 "ms_max": max((r["exec_elapsed_ms"] for r in tir), default=0),
                 "n_timed_out": sum(1 for r in tir if r["timed_out"]),
                 "n_slow": sum(1 for r in tir if not r["timed_out"] and r["exec_elapsed_ms"] >= 0.8 * 60_000)},
        "tir": tir, "grade": None,
    }
    run.update(overrides)
    return run


class TestRenderCheckReport:
    def test_terse_execution_line_no_warnings(self):
        out = render_check_report(make_check())
        assert "execution: 1 script, 40 ms total, slowest 40 ms" in out
        assert "⚠" not in out

    def test_timeout_flagged_prominently(self):
        tir = [{"verdict": "error", "note": "script timed out", "exit_code": None,
                "timed_out": True, "exec_elapsed_ms": 60000, "code": "while True: pass",
                "stdout": "", "stderr": "", "gen": {"text": "g"}}]
        out = render_check_report(make_check(tir))
        assert "⚠ 1 timed out" in out
        assert "timed out" in out  # per-script line too

    def test_slow_script_flagged(self):
        tir = [{"verdict": "pass", "note": None, "exit_code": 0, "timed_out": False,
                "exec_elapsed_ms": 55000, "code": "c", "stdout": "VERDICT: PASS",
                "stderr": "", "gen": {"text": "g"}}]
        out = render_check_report(make_check(tir))
        assert "slow (≥80% of 60s)" in out

    def test_no_exec_block_when_no_tir(self):
        run = make_check(tir=[], exec={"n_scripts": 0})
        assert "execution:" not in render_check_report(run)


class TestRenderCheckScript:
    def test_budget_shown(self):
        out = render_check_script(make_check(), 0)
        assert "budget 60s" in out
        assert "timed_out=False" in out
