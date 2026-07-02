"""Job store tests: records, lifecycle, pruning, and the worker."""
from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
from datetime import datetime, timedelta, timezone

import pytest

from mathx import jobs

SUBMIT_ARGS = dict(model="test-model", base_url="http://fake.test/v1")


def submit(problem: str = "1+1?", **overrides) -> dict:
    return jobs.submit(problem, **{**SUBMIT_ARGS, **overrides})


class TestStore:
    def test_jobs_dir_honours_env_override(self, isolated_jobs_dir):
        assert jobs.jobs_dir() == isolated_jobs_dir

    def test_submit_writes_running_record(self, isolated_jobs_dir):
        record = submit(k=4, strategy="maj@k", max_k=8)
        assert record["status"] == "running"
        assert record["args"]["problem"] == "1+1?"
        assert record["args"]["k"] == 4
        assert record["args"]["max_k"] == 8
        on_disk = json.loads((isolated_jobs_dir / f"{record['job_id']}.json").read_text())
        assert on_disk == record

    def test_no_secret_ever_touches_disk(self, isolated_jobs_dir, monkeypatch):
        monkeypatch.setenv("MATHX_API_KEY", "sk-hunter2")
        record = submit()
        text = (isolated_jobs_dir / f"{record['job_id']}.json").read_text()
        assert "hunter2" not in text
        assert "api_key" not in text

    def test_read_unknown_id_raises(self):
        with pytest.raises(KeyError, match="unknown job id"):
            jobs.read("20990101T000000Z-dead")

    def test_path_traversal_rejected(self):
        with pytest.raises(KeyError, match="invalid job id"):
            jobs.read("../../etc/passwd")

    def test_check_running_has_elapsed(self):
        record = submit()
        checked = jobs.check(record["job_id"])
        assert checked["status"] == "running"
        assert checked["elapsed_ms"] >= 0

    def test_finalize(self):
        record = submit()
        done = jobs.finalize(record["job_id"], result={"answer": "2", "margin": "1/1"})
        assert done["status"] == "complete"
        assert done["finished_at"]
        assert jobs.check(record["job_id"])["result"]["answer"] == "2"
        assert "elapsed_ms" not in jobs.check(record["job_id"])

    def test_fail(self):
        record = submit()
        jobs.fail(record["job_id"], error="boom")
        checked = jobs.check(record["job_id"])
        assert checked["status"] == "error"
        assert checked["error"] == "boom"

    def test_list_jobs_newest_first(self):
        first = submit("first")
        second = submit("second")
        listed = jobs.list_jobs()
        assert [r["job_id"] for r in listed[:2]] == [second["job_id"], first["job_id"]]

    def test_list_jobs_skips_corrupt_files(self, isolated_jobs_dir):
        submit()
        (isolated_jobs_dir / "garbage.json").write_text("not json {")
        assert len(jobs.list_jobs()) == 1

    def test_prune_removes_old_keeps_recent(self, isolated_jobs_dir):
        old_done = submit("old complete")
        jobs.finalize(old_done["job_id"], result={"answer": "1"})
        old_orphan = submit("old still-running orphan")
        recent = submit("recent")
        long_ago = (datetime.now(timezone.utc) - timedelta(hours=100)).isoformat()
        for job_id, stamp_key in [(old_done["job_id"], "finished_at"), (old_orphan["job_id"], "started_at")]:
            record = jobs.read(job_id)
            record[stamp_key] = long_ago
            (isolated_jobs_dir / f"{job_id}.json").write_text(json.dumps(record))
        assert jobs.prune(hours=72) == 2
        remaining = [r["job_id"] for r in jobs.list_jobs()]
        assert remaining == [recent["job_id"]]


class TestRunJob:
    def test_completes_and_finalizes(self, fake_endpoint, monkeypatch):
        monkeypatch.setenv("MATHX_API_KEY", "test-key")
        fake_endpoint([r"\boxed{2}"] * 2 + [r"\boxed{3}"])
        record = submit(k=3)
        done = asyncio.run(jobs.run_job(record["job_id"]))
        assert done["status"] == "complete"
        assert done["result"]["answer"] == "2"
        assert done["result"]["margin"] == "2/3"
        assert done["result"]["problem"] == "1+1?"

    def test_fails_without_api_key(self, monkeypatch):
        monkeypatch.delenv("MATHX_API_KEY", raising=False)
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        record = submit()
        done = asyncio.run(jobs.run_job(record["job_id"]))
        assert done["status"] == "error"
        assert "MATHX_API_KEY" in done["error"]

    def test_engine_exception_becomes_error_record(self, monkeypatch):
        monkeypatch.setenv("MATHX_API_KEY", "test-key")
        record = submit(strategy="not-a-strategy")
        done = asyncio.run(jobs.run_job(record["job_id"]))
        assert done["status"] == "error"
        assert "ValueError" in done["error"]

    def test_worker_subprocess_entry(self, isolated_jobs_dir):
        # the real `python -m mathx.jobs <id>` path, pointed at a dead endpoint:
        # the sample errors (connection refused), the job still finalizes cleanly
        record = submit(base_url="http://127.0.0.1:9/v1", strategy="cot", k=1)
        env = os.environ | {
            "MATHX_JOBS_DIR": str(isolated_jobs_dir),
            "MATHX_API_KEY": "test-key",
        }
        proc = subprocess.run(
            [sys.executable, "-m", "mathx.jobs", record["job_id"]],
            env=env,
            capture_output=True,
            timeout=120,
        )
        assert proc.returncode == 0, proc.stderr.decode()
        done = jobs.read(record["job_id"])
        assert done["status"] == "complete"
        assert done["result"]["answer"] is None
        assert done["result"]["samples"][0]["error"]
