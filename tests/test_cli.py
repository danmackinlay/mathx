"""CLI tests: `solve` end-to-end against the fake endpoint, the job verbs, `show`."""
from __future__ import annotations

import json

import pytest
from click.testing import CliRunner

from mathx import jobs
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


@pytest.fixture
def no_spawn(monkeypatch):
    spawned = []
    monkeypatch.setattr(jobs, "spawn_worker", lambda job_id, **kw: spawned.append((job_id, kw)))
    return spawned


def submit_job(problem: str = "6*7?") -> dict:
    return jobs.submit(problem, model="m", base_url="http://b/v1")


class TestSubmit:
    def test_prints_job_id_and_spawns_with_key(self, no_spawn):
        result = invoke("submit", "6*7?", *PROVIDER_ARGS, "--k", "4")
        assert result.exit_code == 0, result.output
        job_id = result.stdout.strip()
        assert no_spawn == [(job_id, {"api_key": "test-key"})]
        record = jobs.read(job_id)
        assert record["status"] == "running"
        assert record["args"]["problem"] == "6*7?"
        assert record["args"]["k"] == 4
        assert f"mathx status {job_id}" in result.stderr


class TestStatus:
    def test_running_exits_2(self):
        record = submit_job()
        result = invoke("status", record["job_id"])
        assert result.exit_code == 2
        assert "running" in result.output
        assert "6*7?" in result.output

    def test_complete_exits_0(self):
        record = submit_job()
        jobs.finalize(record["job_id"], result={"answer": "42", "margin": "3/4", "k": 4})
        result = invoke("status", record["job_id"])
        assert result.exit_code == 0, result.output
        assert "answer: 42" in result.output
        assert "margin: 3/4" in result.output

    def test_errored_exits_3(self):
        record = submit_job()
        jobs.fail(record["job_id"], error="kaboom")
        result = invoke("status", record["job_id"])
        assert result.exit_code == 3
        assert "kaboom" in result.output

    def test_json_flag(self):
        record = submit_job()
        result = invoke("status", record["job_id"], "--json")
        assert result.exit_code == 2
        parsed = json.loads(result.stdout)
        assert parsed["job_id"] == record["job_id"]
        assert parsed["status"] == "running"

    def test_unknown_id(self):
        result = invoke("status", "20990101T000000Z-dead")
        assert result.exit_code == 1
        assert "unknown job id" in result.output


class TestJobs:
    def test_empty(self):
        result = invoke("jobs")
        assert result.exit_code == 0
        assert "no jobs yet" in result.output

    def test_lists_newest_first_with_outcome(self):
        done = submit_job("solved one")
        jobs.finalize(done["job_id"], result={"answer": "42", "margin": "4/4"})
        running = submit_job("still going")
        result = invoke("jobs")
        assert result.exit_code == 0, result.output
        lines = result.output.strip().splitlines()
        assert running["job_id"] in lines[0] and "still going" in lines[0]
        assert done["job_id"] in lines[1] and "42 (4/4)" in lines[1]

    def test_json_flag(self):
        submit_job()
        parsed = json.loads(invoke("jobs", "--json").stdout)
        assert len(parsed) == 1 and parsed[0]["status"] == "running"

    def test_prune(self):
        record = submit_job()
        jobs.finalize(record["job_id"], result={"answer": "1"})
        result = invoke("jobs", "--prune", "0")
        assert "pruned 1 job(s)" in result.stderr
        assert "no jobs yet" in result.stdout


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

    def test_complete_job_id_renders_report(self):
        record = submit_job()
        jobs.finalize(
            record["job_id"],
            result={
                "problem": "6*7?",
                "answer": "42",
                "margin": "1/1",
                "votes": {"42": 1.0},
                "samples": [{"boxed": "42", "text": "the reasoning"}],
            },
        )
        result = invoke("show", record["job_id"])
        assert result.exit_code == 0, result.output
        assert "answer: 42" in result.output
        sample = invoke("show", record["job_id"], "--sample", "0")
        assert "the reasoning" in sample.output

    def test_running_job_id_is_a_polite_error(self):
        record = submit_job()
        result = invoke("show", record["job_id"])
        assert result.exit_code != 0
        assert "still running" in result.output
        assert f"mathx status {record['job_id']}" in result.output

    def test_errored_job_id_shows_error(self):
        record = submit_job()
        jobs.fail(record["job_id"], error="kaboom")
        result = invoke("show", record["job_id"])
        assert result.exit_code != 0
        assert "kaboom" in result.output
