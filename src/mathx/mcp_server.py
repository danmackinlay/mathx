"""MCP surface: ``submit_solve`` / ``check_solve`` over the shared job store.

Application-level handle/poll (see MCP_PLAN.md): both tools return instantly,
so no client tool-call timeout ever bites, and any tool-capable MCP client works
— no MCP-Tasks support required. No engine logic lives here; ``submit_solve``
writes a job record and detaches the same worker subprocess ``mathx submit``
uses, so jobs survive an MCP-server restart too.

Provider config comes from the server's environment (``MATHX_MODEL``,
``MATHX_BASE_URL``, ``MATHX_API_KEY``/``OPENAI_API_KEY``) unless the tool call
overrides model/base_url. The key is env-only and never written to disk.
"""
from __future__ import annotations

import os
from typing import Literal

from mcp.server.fastmcp import FastMCP

from mathx import jobs

server = FastMCP(
    "mathx",
    instructions=(
        "A maths oracle: fan a hard problem out to k LLM samples, cluster the answers "
        "by maths-equivalence, and vote. Call submit_solve (returns a job_id instantly), "
        "then poll check_solve every 20-60s until status is 'complete'. Interpret "
        "result.margin as the confidence signal: a near-unanimous vote is commit-worthy; "
        "a close split means surface the disagreement rather than assert the answer."
    ),
)


@server.tool()
def submit_solve(
    problem: str,
    strategy: Literal["cot", "maj@k", "self_verify"] = "maj@k",
    k: int = 16,
    model: str | None = None,
    base_url: str | None = None,
    temperature: float | None = None,
    max_tokens: int = 16000,
    max_k: int | None = None,
) -> dict:
    """Kick off a maths fan-out in the background. Returns immediately.

    model/base_url fall back to $MATHX_MODEL / $MATHX_BASE_URL. max_k turns on
    auto-escalation: on a weak vote, k doubles up to that cap.
    Returns {"job_id", "status": "running", "started_at"}.
    """
    model = model or os.environ.get("MATHX_MODEL")
    base_url = base_url or os.environ.get("MATHX_BASE_URL")
    missing = [
        name
        for name, value in [
            ("model ($MATHX_MODEL)", model),
            ("base_url ($MATHX_BASE_URL)", base_url),
            ("api key ($MATHX_API_KEY or $OPENAI_API_KEY)",
             os.environ.get("MATHX_API_KEY") or os.environ.get("OPENAI_API_KEY")),
        ]
        if not value
    ]
    if missing:
        return {"status": "error", "error": f"provider not configured: missing {', '.join(missing)}"}
    record = jobs.submit(
        kind="solve",
        args={
            "problem": problem,
            "strategy": strategy,
            "k": k,
            "model": model,
            "base_url": base_url,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "max_k": max_k,
        },
    )
    jobs.spawn_worker(record["job_id"])
    return {
        "job_id": record["job_id"],
        "status": record["status"],
        "started_at": record["started_at"],
    }


@server.tool()
def check_solve(job_id: str) -> dict:
    """Poll a submitted job. Returns immediately.

    status "running" comes with elapsed_ms; "complete" with result (answer,
    margin, votes, per-sample audit trail); "error" with error.
    """
    try:
        return jobs.check(job_id)
    except KeyError:
        return {"job_id": job_id, "status": "error", "error": "unknown job_id"}


def serve() -> None:
    server.run()  # stdio transport
