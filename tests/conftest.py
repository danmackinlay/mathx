"""Shared test fakery: a scripted OpenAI-compatible endpoint.

``fake_endpoint`` patches ``engine.AsyncOpenAI`` so ``solve()`` builds a real
openai client whose HTTP layer is an in-process ``httpx.MockTransport`` — the
full client/wire path runs, no network. Solver requests are answered from a
scripted reply list in arrival order; judge requests (``self_verify``) are
answered by a content-keyed callable so test outcomes don't depend on task
scheduling order.
"""
from __future__ import annotations

import asyncio
import json
from collections.abc import Callable

import httpx
import pytest
from openai import AsyncOpenAI

import mathx.argue as argue_mod
import mathx.check as check_mod
import mathx.engine as engine


def _completion(model: str, content: str) -> dict:
    return {
        "id": "chatcmpl-test",
        "object": "chat.completion",
        "created": 1,
        "model": model,
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": content},
                "finish_reason": "stop",
            }
        ],
        "usage": {"prompt_tokens": 10, "completion_tokens": 20, "total_tokens": 30},
    }


class FakeEndpoint:
    """``replies``: one entry per expected solver call — a content string, or an
    int HTTP status to fail that call (use 400: openai retries 5xx/429).
    ``judge``: maps the candidate text of a judge request to its reply string.
    ``by_system``: routes requests by exact system prompt to a reply string or a
    ``callable(user_content) -> reply`` — needed when differently-prompted call
    families run concurrently (check's script-writer vs graders), where arrival
    order must not matter.
    """

    def __init__(
        self,
        replies: list[str | int] = (),
        judge: Callable[[str], str] | None = None,
        by_system: dict[str, str | Callable[[str], str]] | None = None,
    ):
        self.replies = list(replies)
        self.judge = judge
        self.by_system = by_system or {}
        self.requests: list[dict] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        self.requests.append(body)
        system = body["messages"][0]["content"]
        user = body["messages"][1]["content"]
        if self.judge is not None and system == engine.JUDGE_SYSTEM:
            return httpx.Response(200, json=_completion(body["model"], self.judge(user)))
        if system in self.by_system:
            responder = self.by_system[system]
            reply = responder(user) if callable(responder) else responder
            return httpx.Response(200, json=_completion(body["model"], reply))
        assert self.replies, "fake endpoint ran out of scripted replies"
        reply = self.replies.pop(0)
        if isinstance(reply, int):
            return httpx.Response(reply, json={"error": {"message": "scripted failure"}})
        return httpx.Response(200, json=_completion(body["model"], reply))


@pytest.fixture(autouse=True)
def isolated_jobs_dir(tmp_path, monkeypatch):
    """Point the job store at a per-test directory so tests never touch ~/.cache,
    and keep the developer's own profile/pacing env out of test behavior."""
    d = tmp_path / "jobs"
    monkeypatch.setenv("MATHX_JOBS_DIR", str(d))
    monkeypatch.delenv("MATHX_PROFILE", raising=False)
    monkeypatch.delenv("MATHX_CONCURRENCY", raising=False)
    return d


@pytest.fixture
def inline_workers(monkeypatch):
    """Replace the detached worker subprocess with in-process tasks, so spawned
    check jobs run against the fake endpoint inside the caller's event loop."""
    import mathx.jobs as jobs
    import mathx.worker as worker

    tasks = []

    def spawn(job_id: str, **_kw) -> None:
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return  # spawned outside a loop (e.g. a submit tool): test drives it manually
        tasks.append(loop.create_task(worker.run_job(job_id)))

    monkeypatch.setenv("MATHX_API_KEY", "test-key")
    monkeypatch.setattr(jobs, "spawn_worker", spawn)
    return tasks


@pytest.fixture
def fake_endpoint(monkeypatch):
    def install(
        replies: list[str | int] = (),
        judge: Callable[[str], str] | None = None,
        by_system: dict[str, str | Callable[[str], str]] | None = None,
    ) -> FakeEndpoint:
        ep = FakeEndpoint(replies, judge, by_system)

        def make_client(*, base_url: str, api_key: str, **client_kwargs) -> AsyncOpenAI:
            return AsyncOpenAI(
                base_url=base_url,
                api_key=api_key,
                http_client=httpx.AsyncClient(transport=httpx.MockTransport(ep.handler)),
                **client_kwargs,
            )

        monkeypatch.setattr(engine, "AsyncOpenAI", make_client)
        monkeypatch.setattr(check_mod, "AsyncOpenAI", make_client)
        monkeypatch.setattr(argue_mod, "AsyncOpenAI", make_client)
        return ep

    return install
