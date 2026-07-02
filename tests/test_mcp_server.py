"""MCP server tests: the tool functions, called directly (no transport needed)."""
from __future__ import annotations

import asyncio

import pytest

from mathx import jobs, mcp_server


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
    def test_both_tools_registered(self):
        tools = asyncio.run(mcp_server.server.list_tools())
        assert {t.name for t in tools} == {"submit_solve", "check_solve"}

    def test_submit_returns_handle_and_spawns(self, no_spawn, provider_env):
        out = mcp_server.submit_solve("1+1?", k=4)
        assert out["status"] == "running"
        assert no_spawn == [out["job_id"]]
        record = jobs.read(out["job_id"])
        assert record["args"]["model"] == "test-model"
        assert record["args"]["k"] == 4

    def test_submit_without_provider_errors(self, no_spawn, monkeypatch):
        for var in ("MATHX_MODEL", "MATHX_BASE_URL", "MATHX_API_KEY", "OPENAI_API_KEY"):
            monkeypatch.delenv(var, raising=False)
        out = mcp_server.submit_solve("1+1?")
        assert out["status"] == "error"
        assert "MATHX_MODEL" in out["error"]
        assert not no_spawn

    def test_check_running_then_complete(self, no_spawn, provider_env, fake_endpoint):
        fake_endpoint([r"\boxed{2}"] * 2)
        out = mcp_server.submit_solve("1+1?", k=2)
        polled = mcp_server.check_solve(out["job_id"])
        assert polled["status"] == "running"
        assert polled["elapsed_ms"] >= 0
        asyncio.run(jobs.run_job(out["job_id"]))  # stand in for the worker
        polled = mcp_server.check_solve(out["job_id"])
        assert polled["status"] == "complete"
        assert polled["result"]["answer"] == "2"
        assert polled["result"]["margin"] == "2/2"

    def test_check_unknown_id(self):
        out = mcp_server.check_solve("20990101T000000Z-dead")
        assert out["status"] == "error"
        assert out["error"] == "unknown job_id"
