"""Claim-ledger store: the persistent artifact of the decompose–check–refine loop.

One JSON file per ledger under ``$MATHX_LEDGERS_DIR``, else a ``ledgers/`` dir
beside the job store (see LOOP_PLAN.md for the record shape). The ledger is
deliberately a FILE — ROADMAP invariant 3 — so a loop started in one home
(host agent / ``mathx argue`` / library) can be inspected, challenged, and
resumed from another. Claim state is never cached here: it is derived live from
the referenced job records, keeping the job store the single source of truth.
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path

from mathx import jobs
from mathx.jobs import _write_atomic, new_job_id  # same id + atomic-write conventions


def ledgers_dir() -> Path:
    if os.environ.get("MATHX_LEDGERS_DIR"):
        return Path(os.environ["MATHX_LEDGERS_DIR"])
    return jobs.jobs_dir().parent / "ledgers"


def _path(ledger_id: str) -> Path:
    if "/" in ledger_id or os.sep in ledger_id or ".." in ledger_id:
        raise KeyError(f"invalid ledger id: {ledger_id!r}")
    return ledgers_dir() / f"{ledger_id}.json"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def create(problem: str, *, model: str, base_url: str, rounds_max: int) -> dict:
    ledger_id = new_job_id()
    while _path(ledger_id).exists():
        ledger_id = new_job_id()
    led = {
        "ledger_id": ledger_id,
        "kind": "ledger",
        "status": "checking",
        "problem": problem,
        "argument": "",
        "rounds_used": 0,
        "rounds_max": rounds_max,
        "model": model,
        "base_url": base_url,
        "created_at": _now(),
        "updated_at": _now(),
        "claims": [],
        "scratchpad": [],
    }
    save(led)
    return led


def save(led: dict) -> None:
    led["updated_at"] = _now()
    _write_atomic(_path(led["ledger_id"]), led)


def read(ledger_id: str) -> dict:
    try:
        return json.loads(_path(ledger_id).read_text())
    except FileNotFoundError:
        raise KeyError(f"unknown ledger id: {ledger_id}") from None


def list_ledgers() -> list[dict]:
    records = []
    if ledgers_dir().is_dir():
        for path in ledgers_dir().glob("*.json"):
            try:
                led = json.loads(path.read_text())
            except (OSError, json.JSONDecodeError):
                continue
            if isinstance(led, dict) and "ledger_id" in led:
                records.append(led)
    records.sort(key=lambda r: r.get("updated_at", ""), reverse=True)
    return records


def add_claim(led: dict, text: str, *, round_added: int, parent: str | None = None) -> dict:
    claim = {
        "id": f"c{len(led['claims']) + 1}",
        "text": text,
        "parent": parent,
        "round_added": round_added,
        "retired_round": None,
        "verdicts": [],
    }
    led["claims"].append(claim)
    return claim


def get_claim(led: dict, claim_id: str) -> dict:
    for claim in led["claims"]:
        if claim["id"] == claim_id:
            return claim
    raise KeyError(f"no claim {claim_id!r} in ledger {led['ledger_id']} "
                   f"(has {', '.join(c['id'] for c in led['claims']) or 'none'})")


def attach_check(
    led: dict,
    claim: dict,
    *,
    api_key: str,
    round_: int,
    kind: str = "check",
    text_override: str | None = None,
    model: str | None = None,
    base_url: str | None = None,
    tir_k: int = 1,
    grade_k: int = 4,
    exec_timeout_s: float = 60.0,
    temperature: float | None = None,
    max_tokens: int = 16000,
    meta_model: str | None = None,
    top_p: float | None = None,
    extra_body: dict | None = None,
    max_retries: int | None = None,
    spawn=None,
) -> str:
    """Submit a check job for a claim, record the reference, start the worker.

    model/base_url default to the ledger's but may be overridden — rechecking
    with a different (e.g. stronger) model is legitimate regime mixing.
    ``text_override`` lets ``challenge`` check a modified statement while the
    ledger keeps the original claim text.
    """
    record = jobs.submit(
        kind="check",
        args={
            "claim": text_override or claim["text"],
            "tir_k": tir_k,
            "grade_k": grade_k,
            "exec_timeout_s": exec_timeout_s,
            "model": model or led["model"],
            "base_url": base_url or led["base_url"],
            "temperature": temperature,
            "max_tokens": max_tokens,
            "meta_model": meta_model,
            "top_p": top_p,
            "extra_body": extra_body,
            "max_retries": max_retries,
        },
    )
    claim["verdicts"].append({"job_id": record["job_id"], "round": round_, "kind": kind})
    (spawn or jobs.spawn_worker)(record["job_id"], api_key=api_key)
    save(led)
    return record["job_id"]


def claim_state(claim: dict) -> tuple[str, str]:
    """(state, detail), derived live from the claim's latest verdict job."""
    if claim.get("retired_round") is not None:
        return "retired", f"round {claim['retired_round']}"
    verdicts = claim.get("verdicts") or []
    if not verdicts:
        return "unchecked", ""
    job_id = verdicts[-1]["job_id"]
    try:
        record = jobs.read(job_id)
    except KeyError:
        return "missing", f"job {job_id} not in store"
    if record.get("status") == "running":
        return "checking", ""  # the verdict reference already names the job
    if record.get("status") == "error":
        return "error", record.get("error") or ""
    result = record.get("result") or {}
    return result.get("status", "error"), result.get("summary", "")


def state_counts(led: dict) -> dict[str, int]:
    counts: dict[str, int] = {}
    for claim in led.get("claims", []):
        state, _ = claim_state(claim)
        counts[state] = counts.get(state, 0) + 1
    return counts
