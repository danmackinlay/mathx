"""Shared test fakery: a scripted OpenAI-compatible endpoint.

``fake_endpoint`` patches ``engine.AsyncOpenAI`` so ``solve()`` builds a real
openai client whose HTTP layer is an in-process ``httpx.MockTransport`` — the
full client/wire path runs, no network. Solver requests are answered from a
scripted reply list in arrival order; judge requests (``self_verify``) are
answered by a content-keyed callable so test outcomes don't depend on task
scheduling order.
"""
from __future__ import annotations

import json
from collections.abc import Callable

import httpx
import pytest
from openai import AsyncOpenAI

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
    """

    def __init__(self, replies: list[str | int], judge: Callable[[str], str] | None = None):
        self.replies = list(replies)
        self.judge = judge
        self.requests: list[dict] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        self.requests.append(body)
        if self.judge is not None and body["messages"][0]["content"] == engine.JUDGE_SYSTEM:
            candidate = body["messages"][1]["content"]
            return httpx.Response(200, json=_completion(body["model"], self.judge(candidate)))
        assert self.replies, "fake endpoint ran out of scripted replies"
        reply = self.replies.pop(0)
        if isinstance(reply, int):
            return httpx.Response(reply, json={"error": {"message": "scripted failure"}})
        return httpx.Response(200, json=_completion(body["model"], reply))


@pytest.fixture
def fake_endpoint(monkeypatch):
    def install(replies: list[str | int], judge: Callable[[str], str] | None = None) -> FakeEndpoint:
        ep = FakeEndpoint(replies, judge)

        def make_client(*, base_url: str, api_key: str) -> AsyncOpenAI:
            return AsyncOpenAI(
                base_url=base_url,
                api_key=api_key,
                http_client=httpx.AsyncClient(transport=httpx.MockTransport(ep.handler)),
            )

        monkeypatch.setattr(engine, "AsyncOpenAI", make_client)
        return ep

    return install
