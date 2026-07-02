"""CLI tests: `solve` end-to-end against the fake endpoint, and `show`."""
from __future__ import annotations

import json

from click.testing import CliRunner

from mathx.cli import cli

PROVIDER_ARGS = [
    "--model", "test-model",
    "--base-url", "http://fake.test/v1",
    "--api-key", "test-key",
]


def invoke(*args):
    return CliRunner().invoke(cli, list(args))


def write_run(tmp_path, **overrides):
    run = {
        "problem": "6*7?",
        "answer": "42",
        "margin": "2/3",
        "votes": {"42": 2.0, "41": 1.0},
        "strategy": "maj@k",
        "escalations": 0,
        "model": "m",
        "base_url": "http://b/v1",
        "k": 3,
        "tokens_in_total": 30,
        "tokens_out_total": 60,
        "elapsed_ms_total": 999,
        "samples": [
            {"boxed": "42", "confidence": None, "error": None,
             "tokens_in": 10, "tokens_out": 20, "elapsed_ms": 333, "text": "full reasoning here"},
            {"boxed": "42", "text": "more reasoning"},
            {"boxed": "41", "text": "dissenting reasoning"},
        ],
    }
    run.update(overrides)
    path = tmp_path / "run.json"
    path.write_text(json.dumps(run))
    return path


class TestSolve:
    def test_end_to_end(self, fake_endpoint, tmp_path):
        fake_endpoint([r"\boxed{42}"] * 2 + [r"\boxed{41}"])
        out = tmp_path / "run.json"
        result = invoke("solve", "6*7?", *PROVIDER_ARGS, "--k", "3", "--out", str(out))
        assert result.exit_code == 0, result.output
        assert "answer: 42" in result.output
        assert "margin: 2/3" in result.output
        run = json.loads(out.read_text())
        assert run["problem"] == "6*7?"
        assert run["answer"] == "42"
        assert len(run["samples"]) == 3

    def test_progress_streams_to_stderr(self, fake_endpoint):
        fake_endpoint([r"\boxed{42}"] * 2)
        result = invoke("solve", "6*7?", *PROVIDER_ARGS, "--k", "2", "--progress")
        assert result.exit_code == 0, result.output
        assert "[1/2]" in result.stderr
        assert "[2/2]" in result.stderr
        assert "[1/2]" not in result.stdout

    def test_escalation_reported(self, fake_endpoint):
        fake_endpoint([r"\boxed{41}", r"\boxed{42}"] * 2)
        result = invoke(
            "solve", "6*7?", *PROVIDER_ARGS, "--k", "2", "--max-k", "4", "--progress"
        )
        assert "escalating to k=4" in result.stderr
        assert "escalations: 1" in result.stdout

    def test_exit_code_1_without_answer(self, fake_endpoint):
        fake_endpoint(["stumped", "no idea"])
        result = invoke("solve", "6*7?", *PROVIDER_ARGS, "--k", "2")
        assert result.exit_code == 1
        assert "answer: None" in result.output


class TestShow:
    def test_report(self, tmp_path):
        result = invoke("show", str(write_run(tmp_path)))
        assert result.exit_code == 0, result.output
        assert "answer: 42" in result.output
        assert "← winner" in result.output
        assert "disagreement: 1/3 voters against the winner" in result.output

    def test_sample_dump(self, tmp_path):
        result = invoke("show", str(write_run(tmp_path)), "--sample", "0")
        assert result.exit_code == 0, result.output
        assert "full reasoning here" in result.output

    def test_sample_out_of_range(self, tmp_path):
        result = invoke("show", str(write_run(tmp_path)), "--sample", "9")
        assert result.exit_code != 0
        assert "out of range" in result.output

    def test_missing_file(self, tmp_path):
        result = invoke("show", str(tmp_path / "nope.json"))
        assert result.exit_code != 0

    def test_invalid_json(self, tmp_path):
        bad = tmp_path / "bad.json"
        bad.write_text("not json {")
        result = invoke("show", str(bad))
        assert result.exit_code != 0
        assert "not valid JSON" in result.output
