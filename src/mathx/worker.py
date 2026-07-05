"""The job worker: execute a submitted job and finalize its record.

Entry point: ``python -m mathx.worker <job_id>`` — spawned detached by
``jobs.spawn_worker``, so jobs survive whoever submitted them.

Layering: the store (``jobs.py``) is the stdlib-only substrate every surface
imports; this module is the opposite end of the graph — it dispatches on job
kind and therefore imports every engine, and nothing imports it back (the
solve/check/argue modules must stay ignorant of how they get scheduled).
"""
from __future__ import annotations

import asyncio
import os
import sys

from mathx import jobs
from mathx.argue import argue
from mathx.check import check, check_result_to_dict
from mathx.engine import result_to_dict, solve
from mathx.ledger import state_counts


async def run_job(job_id: str) -> dict:
    """Execute a submitted job and finalize its record."""
    record = jobs.stamp_worker(job_id)  # make this worker's death detectable
    kind = record.get("kind", "solve")  # pre-Stage-3 records carry no kind
    args = record["args"]
    api_key = os.environ.get("MATHX_API_KEY") or os.environ.get("OPENAI_API_KEY")
    if not api_key:
        return jobs.fail(job_id, error="no API key in environment: set MATHX_API_KEY (or OPENAI_API_KEY)")
    try:
        if kind == "argue":
            led = await argue(
                args["problem"],
                model=args["model"],
                base_url=args["base_url"],
                api_key=api_key,
                rounds=args.get("rounds", 2),
                tir_k=args.get("tir_k", 1),
                grade_k=args.get("grade_k", 4),
                temperature=args.get("temperature"),
                max_tokens=args.get("max_tokens", 16000),
                exec_timeout_s=args.get("exec_timeout_s", 60.0),
                meta_model=args.get("meta_model"),
                top_p=args.get("top_p"),
                extra_body=args.get("extra_body"),
                max_retries=args.get("max_retries"),
                ledger_id=args.get("ledger_id"),
                poll_s=args.get("poll_s", 2.0),
            )
            # the ledger is the artifact; the job result is a thin pointer
            payload = {
                "kind": "argue",
                "ledger_id": led["ledger_id"],
                "status": led["status"],
                "rounds_used": led["rounds_used"],
                "claims": state_counts(led),
            }
        elif kind == "check":
            result = await check(
                args["claim"],
                model=args["model"],
                base_url=args["base_url"],
                api_key=api_key,
                tir_k=args.get("tir_k", 1),
                grade_k=args.get("grade_k", 8),
                temperature=args.get("temperature"),
                max_tokens=args.get("max_tokens", 16000),
                exec_timeout_s=args.get("exec_timeout_s", 60.0),
                meta_model=args.get("meta_model"),
                top_p=args.get("top_p"),
                extra_body=args.get("extra_body"),
                max_retries=args.get("max_retries"),
            )
            payload = check_result_to_dict(result)
        elif kind == "solve":
            result = await solve(
                args["problem"],
                model=args["model"],
                base_url=args["base_url"],
                api_key=api_key,
                k=args["k"],
                strategy=args["strategy"],
                temperature=args["temperature"],
                max_tokens=args["max_tokens"],
                max_k=args["max_k"],
                top_p=args.get("top_p"),
                extra_body=args.get("extra_body"),
                max_retries=args.get("max_retries"),
                equiv_judge_model=args.get("equiv_judge_model"),
            )
            payload = result_to_dict(result)
        else:
            return jobs.fail(job_id, error=f"unknown job kind: {kind!r}")
    except Exception as e:
        return jobs.fail(job_id, error=f"{type(e).__name__}: {e}")
    return jobs.finalize(job_id, result=payload)


if __name__ == "__main__":
    asyncio.run(run_job(sys.argv[1]))
