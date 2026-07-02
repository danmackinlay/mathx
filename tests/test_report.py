"""Report renderer tests: pure dict-in, text-out."""
from __future__ import annotations

import pytest

from mathx.report import render_report, render_sample


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
