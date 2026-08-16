"""Open WebUI Pipe tests: import the Function file, drive all three modes."""
from __future__ import annotations

import asyncio
import importlib.util
from pathlib import Path

import pytest

PIPE_PATH = Path(__file__).parent.parent / "integrations" / "openwebui" / "mathx_pipe.py"


@pytest.fixture
def pipe_module():
    spec = importlib.util.spec_from_file_location("mathx_pipe", PIPE_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def pipe(pipe_module):
    return pipe_module.Pipe()


@pytest.fixture
def provider_env(monkeypatch):
    monkeypatch.setenv("MATHX_MODEL", "test-model")
    monkeypatch.setenv("MATHX_BASE_URL", "http://fake.test/v1")
    monkeypatch.setenv("MATHX_API_KEY", "test-key")


def run_pipe(pipe, mode: str, text: str):
    events = []

    async def emitter(event):
        events.append(event)

    body = {"model": f"mathx_pipe.{mode}", "messages": [{"role": "user", "content": text}]}

    async def main():
        reply = await pipe.pipe(body, emitter)
        await asyncio.sleep(0.05)  # drain fire-and-forget emitter tasks
        return reply

    reply = asyncio.run(main())
    return reply, events


class TestPipe:
    def test_picker_entries(self, pipe):
        assert [entry["id"] for entry in pipe.pipes()] == ["solve", "check", "argue"]

    def test_valve_defaults_track_the_declared_constants(self, pipe):
        from mathx.argue import ARGUE_GRADE_K
        from mathx.check import DEFAULT_GRADE_K, DEFAULT_TIR_K

        assert pipe.valves.CHECK_GRADE_K == DEFAULT_GRADE_K
        assert pipe.valves.CHECK_TIR_K == DEFAULT_TIR_K
        assert pipe.valves.ARGUE_GRADE_K == ARGUE_GRADE_K
        assert pipe.valves.ARGUE_TIR_K == DEFAULT_TIR_K

    def test_solve_mode(self, pipe, provider_env, fake_endpoint):
        fake_endpoint([r"\boxed{42}"] * 3)
        pipe.valves.SOLVE_K = 3
        reply, events = run_pipe(pipe, "solve", "6*7?")
        assert "**answer:** $42$" in reply
        assert "margin 3/3" in reply
        descriptions = [e["data"]["description"] for e in events if e["type"] == "status"]
        assert any("sample" in d for d in descriptions)
        assert events[-1]["data"]["done"] is True

    def test_check_mode(self, pipe, provider_env, fake_endpoint):
        from mathx.check import CHECKER_SYSTEM, GRADER_SYSTEM

        fake_endpoint(by_system={
            CHECKER_SYSTEM: '```python\nprint("VERDICT: PASS")\n```',
            GRADER_SYSTEM: r"\boxed{TRUE}",
        })
        pipe.valves.CHECK_GRADE_K = 2
        reply, events = run_pipe(pipe, "check", "2+2=4")
        assert reply.startswith("**supported**")
        assert "evidence, not proof" in reply

    def test_argue_mode_streams_ledger_progress(
        self, pipe_module, pipe, provider_env, fake_endpoint, inline_workers, monkeypatch
    ):
        import functools

        from mathx.argue import DECOMPOSER_SYSTEM, argue as real_argue
        from mathx.check import GRADER_SYSTEM

        # fast internal polling for the test
        monkeypatch.setattr(pipe_module, "argue", functools.partial(real_argue, poll_s=0.01))
        fake_endpoint(by_system={
            DECOMPOSER_SYSTEM: "ARGUMENT:\nBecause.\nCLAIMS:\n1. Claim alpha.\n",
            GRADER_SYSTEM: r"\boxed{TRUE}",
        })
        pipe.valves.ARGUE_TIR_K = 0
        pipe.valves.ARGUE_GRADE_K = 1
        reply, events = run_pipe(pipe, "argue", "why?")
        assert "Because." in reply
        assert "✓ supported" in reply
        assert "ledger id" in reply
        descriptions = [e["data"]["description"] for e in events if e["type"] == "status"]
        assert any("decomposing" in d for d in descriptions)
        assert any("c1 supported" in d for d in descriptions)

    def test_provider_error_is_a_chat_message(self, pipe, monkeypatch):
        for var in ("MATHX_MODEL", "MATHX_BASE_URL", "MATHX_API_KEY", "OPENAI_API_KEY"):
            monkeypatch.delenv(var, raising=False)
        reply, _ = run_pipe(pipe, "solve", "6*7?")
        assert reply.startswith("mathx: provider not configured")

    def test_empty_message(self, pipe, provider_env):
        reply, _ = run_pipe(pipe, "solve", "   ")
        assert reply.startswith("mathx: give me a problem")