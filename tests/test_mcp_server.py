"""MCP server tests: the tool functions, called directly (no transport needed)."""
from __future__ import annotations

import asyncio

import pytest

from mathx import jobs, ledger, mcp_server, worker

TOOLS = {
    "submit_solve",
    "submit_check",
    "submit_argue",
    "poll_job",
    "list_jobs",
    "get_ledger",
    "list_ledgers",
    "recheck_claim",
    "challenge_claim",
    "expand_claim",
}


@pytest.fixture
def no_spawn(monkeypatch):
    """Capture spawn_worker calls instead of forking real workers."""
    spawned = []
    monkeypatch.setattr(jobs, "spawn_worker", lambda job_id, **kw: spawned.append(job_id))
    return spawned


@pytest.fixture
def provider_env(monkeypatch):
    monkeypatch.setenv("MATHX_MODEL", "test-model")
    monkeypatch.setenv("MATHX_BASE_URL", "http://fake.test/v1")
    monkeypatch.setenv("MATHX_API_KEY", "test-key")


class TestTools:
    def test_full_tool_set_registered(self):
        tools = asyncio.run(mcp_server.server.list_tools())
        assert {t.name for t in tools} == TOOLS

    def test_submit_solve_returns_handle_and_spawns(self, no_spawn, provider_env):
        out = mcp_server.submit_solve("1+1?", k=4)
        assert out["status"] == "queued"  # honest until the worker stamps itself
        assert no_spawn == [out["job_id"]]
        record = jobs.read(out["job_id"])
        assert record["kind"] == "solve"
        assert record["args"]["provider"]["model"] == "test-model"
        assert record["args"]["k"] == 4

    def test_submit_without_provider_errors(self, no_spawn, monkeypatch):
        for var in ("MATHX_MODEL", "MATHX_BASE_URL", "MATHX_API_KEY", "OPENAI_API_KEY"):
            monkeypatch.delenv(var, raising=False)
        out = mcp_server.submit_solve("1+1?")
        assert out["status"] == "error"
        assert "MATHX_MODEL" in out["error"]
        assert not no_spawn

    def test_submit_solve_with_profile(self, no_spawn, monkeypatch, tmp_path):
        (tmp_path / "mathx.toml").write_text(
            '[profiles.p]\nbase_url = "http://prof.test/v1"\nmodel = "prof-model"\n'
            'meta_model = "prof-meta"\n'
        )
        monkeypatch.chdir(tmp_path)
        monkeypatch.setenv("MATHX_API_KEY", "k")
        out = mcp_server.submit_solve("1+1?", profile="p")
        assert out["status"] == "queued"
        assert jobs.read(out["job_id"])["args"]["provider"]["model"] == "prof-model"

    def test_submit_check(self, no_spawn, provider_env):
        out = mcp_server.submit_check("2+2=4", grade_k=6)
        record = jobs.read(out["job_id"])
        assert record["kind"] == "check"
        assert record["args"]["claim"] == "2+2=4"
        assert record["args"]["grade_k"] == 6
        assert no_spawn == [out["job_id"]]

    def test_poll_job_queued_then_complete(self, no_spawn, provider_env, fake_endpoint):
        fake_endpoint([r"\boxed{2}"] * 2)
        out = mcp_server.submit_solve("1+1?", k=2)
        polled = mcp_server.poll_job(out["job_id"])
        assert polled["status"] == "queued"  # spawn captured — no worker has stamped yet
        assert polled["elapsed_ms"] >= 0
        asyncio.run(worker.run_job(out["job_id"]))  # stand in for the worker
        polled = mcp_server.poll_job(out["job_id"])
        assert polled["status"] == "complete"
        assert polled["result"]["answer"] == "2"

    def test_poll_job_unknown_id(self):
        out = mcp_server.poll_job("20990101T000000Z-dead")
        assert out["status"] == "error"
        assert out["error"] == "unknown job_id"

    def test_list_jobs_is_compact(self, no_spawn, provider_env):
        out1 = mcp_server.submit_solve("first problem")
        mcp_server.submit_check("second claim")
        listing = mcp_server.list_jobs()
        assert len(listing) == 2
        assert listing[1]["job_id"] == out1["job_id"]
        assert listing[0]["kind"] == "check"
        assert listing[0]["subject"] == "second claim"
        assert all("samples" not in str(entry.keys()) for entry in listing)


class TestArgueAndLedgerTools:
    def test_submit_argue_returns_ledger_immediately(self, no_spawn, provider_env):
        out = mcp_server.submit_argue("why?", rounds=1)
        assert out["status"] == "queued"
        assert out["ledger_id"]
        led = ledger.read(out["ledger_id"])
        assert led["claims"] == []  # pre-created, loop not yet run
        record = jobs.read(out["job_id"])
        assert record["kind"] == "argue"
        assert record["args"]["ledger_id"] == out["ledger_id"]
        assert no_spawn == [out["job_id"]]

    def test_argue_worker_end_to_end(self, provider_env, fake_endpoint, inline_workers):
        from mathx.argue import DECOMPOSER_SYSTEM
        from mathx.check import GRADER_SYSTEM

        fake_endpoint(by_system={
            DECOMPOSER_SYSTEM: "ARGUMENT:\nBecause.\nCLAIMS:\n1. Claim alpha.\n",
            GRADER_SYSTEM: r"\boxed{TRUE}",
        })
        out = mcp_server.submit_argue("why?", rounds=1, tir_k=0, grade_k=1)
        # make the worker's internal poll fast, then run it inline
        record = jobs.read(out["job_id"])
        record["args"]["poll_s"] = 0.01
        jobs._write_atomic(jobs._job_path(out["job_id"]), record)
        done = asyncio.run(worker.run_job(out["job_id"]))
        assert done["status"] == "complete"
        assert done["result"]["kind"] == "argue"
        assert done["result"]["ledger_id"] == out["ledger_id"]
        assert done["result"]["claims"] == {"supported": 1}

        view = mcp_server.get_ledger(out["ledger_id"])
        assert view["status"] == "assembled"
        assert view["claims"][0]["state"] == "supported"
        assert view["claims"][0]["job_id"]

    def test_get_ledger_unknown(self):
        out = mcp_server.get_ledger("20990101T000000Z-dead")
        assert out["status"] == "error"

    def test_list_ledgers_compact(self, provider_env, no_spawn):
        out = mcp_server.submit_argue("p1")
        listing = mcp_server.list_ledgers()
        assert listing[0]["ledger_id"] == out["ledger_id"]
        assert listing[0]["claims"] == {}

    def test_recheck_and_challenge(self, no_spawn, provider_env):
        led = ledger.create("p", model="m", base_url="http://b/v1", rounds_max=1)
        claim = ledger.add_claim(led, "Claim alpha.", round_added=0)
        ledger.save(led)

        out = mcp_server.recheck_claim(led["ledger_id"], claim["id"], grade_k=16)
        assert out["status"] == "running"
        assert jobs.read(out["job_id"])["args"]["grade_k"] == 16
        assert out["job_id"] in no_spawn

        out2 = mcp_server.challenge_claim(led["ledger_id"], claim["id"], "what about n=0?")
        args = jobs.read(out2["job_id"])["args"]
        assert "what about n=0?" in args["claim"]
        persisted = ledger.read(led["ledger_id"])
        kinds = [v["kind"] for v in persisted["claims"][0]["verdicts"]]
        assert kinds == ["recheck", "challenge"]

    def test_recheck_unknown_claim(self, no_spawn, provider_env):
        led = ledger.create("p", model="m", base_url="http://b/v1", rounds_max=1)
        out = mcp_server.recheck_claim(led["ledger_id"], "c9")
        assert out["status"] == "error"
        assert "no claim 'c9'" in out["error"]

    def test_expand_claim_adds_checked_children(self, no_spawn, provider_env, fake_endpoint):
        from mathx.argue import DECOMPOSER_SYSTEM

        fake_endpoint(by_system={
            DECOMPOSER_SYSTEM: "ARGUMENT:\nx\nCLAIMS:\n1. Sub one.\n2. Sub two.\n"
        })
        led = ledger.create("p", model="test-model", base_url="http://fake.test/v1", rounds_max=1)
        claim = ledger.add_claim(led, "Claim alpha.", round_added=0)
        ledger.save(led)
        out = asyncio.run(mcp_server.expand_claim(led["ledger_id"], claim["id"]))
        assert out["status"] == "checking"
        assert out["ledger_id"] == led["ledger_id"] and out["claim_id"] == claim["id"]
        assert [c["text"] for c in out["children"]] == ["Sub one.", "Sub two."]
        assert len(no_spawn) == 2  # each sub-claim got its own check job
        persisted = ledger.read(led["ledger_id"])
        children = [c for c in persisted["claims"] if c["parent"] == claim["id"]]
        assert [c["id"] for c in children] == [c["id"] for c in out["children"]]

    def test_expand_unknown_claim(self, no_spawn, provider_env):
        led = ledger.create("p", model="m", base_url="http://b/v1", rounds_max=1)
        out = asyncio.run(mcp_server.expand_claim(led["ledger_id"], "c9"))
        assert out["status"] == "error"
        assert "no claim 'c9'" in out["error"]
        assert not no_spawn
