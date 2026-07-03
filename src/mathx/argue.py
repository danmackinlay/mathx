"""The decompose–check–refine loop (``mathx argue``) — ROADMAP Stage 4.

Built ON the primitives (invariant 2): decomposition is one generalist sample;
every claim is checked by a Stage-3 check job fanned out through the Stage-2
store (this module only submits and polls); refinement splices the verdict
report and the scratchpad of refuted claims back into the next decomposition.
The ledger file is saved after every state change, so any home can pick a run
up mid-flight. Loop policy and record shape: LOOP_PLAN.md.
"""
from __future__ import annotations

import asyncio
import re
from collections.abc import Callable

from openai import AsyncOpenAI

from mathx import jobs, ledger
from mathx.engine import _one_sample

DECOMPOSER_SYSTEM = (
    "You are a careful mathematician structuring a checkable argument.\n"
    "Given a problem, produce a concise argument that answers it, decomposed into atomic "
    "claims. Each claim must be a complete, self-contained mathematical statement: state all "
    "quantifiers, definitions and constraints inside the claim itself — no pronouns, no "
    "references to the problem or to other claims. Someone reading ONE claim in isolation "
    "must be able to judge it true or false. Use at most 8 claims. "
    "For inline maths use $...$.\n"
    "Reply in EXACTLY this format:\n"
    "ARGUMENT:\n"
    "<the argument, in prose>\n"
    "CLAIMS:\n"
    "1. <first claim>\n"
    "2. <second claim>\n"
    "..."
)

CLAIM_LINE = re.compile(r"^\s*(?:\d+[.)]|[-*•])\s+(.+?)\s*$", re.MULTILINE)


def parse_decomposition(text: str | None) -> tuple[str, list[str]] | None:
    """(argument, claims) from an ARGUMENT:/CLAIMS: reply, or None if unparseable."""
    if not text:
        return None
    m = re.search(r"CLAIMS:\s*\n", text)
    if m is None:
        return None
    head, tail = text[: m.start()], text[m.end():]
    claims = [c.strip() for c in CLAIM_LINE.findall(tail)]
    if not claims:
        return None
    am = re.search(r"ARGUMENT:\s*\n?", head)
    argument = (head[am.end():] if am else head).strip()
    return argument, claims


def _norm(text: str) -> str:
    return " ".join(text.split())


async def _decompose(client, model, user, *, temperature, max_tokens, emit) -> tuple[str, list[str]]:
    for attempt in (1, 2):
        s = await _one_sample(
            client, model, user, temperature=temperature, max_tokens=max_tokens,
            system=DECOMPOSER_SYSTEM,
        )
        if s.error is not None:
            raise RuntimeError(f"decomposition sample failed: {s.error}")
        parsed = parse_decomposition(s.text)
        if parsed is not None:
            return parsed
        if attempt == 1:
            emit("decomposition had no ARGUMENT:/CLAIMS: structure — retrying once")
    raise RuntimeError("decomposition failed twice: no ARGUMENT:/CLAIMS: structure in reply")


def _refine_user(problem: str, led: dict, current: list[dict]) -> str:
    verdict_lines = []
    for claim in current:
        state, detail = ledger.claim_state(claim)
        line = f"- {state.upper()}: {claim['text']}"
        if state != "supported" and detail:
            line += f" — {detail}"
        verdict_lines.append(line)
    parts = [
        f"Problem:\n{problem}",
        f"Your previous argument:\n{led['argument']}",
        "Verdicts on its claims:\n" + "\n".join(verdict_lines),
    ]
    if led["scratchpad"]:
        pad = "\n".join(f"- {e['claim']} ({e['note']})" for e in led["scratchpad"])
        parts.append("Previously refuted claims — do NOT reuse these or trivial restatements:\n" + pad)
    parts.append(
        "Revise the argument: repair or route around the refuted and unclear claims. "
        "Keep supported claims VERBATIM wherever possible — verbatim claims keep their "
        "verification. Reply in the same ARGUMENT:/CLAIMS: format."
    )
    return "\n\n".join(parts)


async def argue(
    problem: str,
    *,
    model: str,
    base_url: str,
    api_key: str,
    rounds: int = 2,
    tir_k: int = 1,
    grade_k: int = 4,
    temperature: float | None = None,
    max_tokens: int = 16000,
    exec_timeout_s: float = 60.0,
    poll_s: float = 2.0,
    spawn=None,
    on_event: Callable[[str], None] | None = None,
) -> dict:
    """Run the loop; return the assembled ledger record.

    ``rounds`` = max refine cycles after the initial decomposition. ``spawn``
    overrides how check workers start (tests run them in-process).
    """
    emit = on_event or (lambda _msg: None)
    client = AsyncOpenAI(base_url=base_url, api_key=api_key)
    temp = 0.7 if temperature is None else temperature
    check_kwargs = dict(
        api_key=api_key, tir_k=tir_k, grade_k=grade_k, exec_timeout_s=exec_timeout_s,
        temperature=temperature, max_tokens=max_tokens, spawn=spawn,
    )

    led = ledger.create(problem, model=model, base_url=base_url, rounds_max=rounds)
    emit(f"ledger: {led['ledger_id']}")

    user = f"Problem:\n{problem}"
    for rnd in range(rounds + 1):
        led["rounds_used"] = rnd
        emit(f"round {rnd}: {'decomposing' if rnd == 0 else 'refining'}…")
        argument, texts = await _decompose(
            client, model, user, temperature=temp, max_tokens=max_tokens, emit=emit
        )
        led["argument"] = argument

        # carry over verbatim claims, add new ones, retire the disappeared
        active = {_norm(c["text"]): c for c in led["claims"] if c["retired_round"] is None}
        current = []
        for text in texts:
            claim = active.pop(_norm(text), None) or ledger.add_claim(led, text, round_added=rnd)
            current.append(claim)
        for claim in active.values():
            claim["retired_round"] = rnd
        ledger.save(led)

        to_check = [c for c in current if not c["verdicts"]]
        emit(f"round {rnd}: {len(current)} claims, checking {len(to_check)}")
        pending = {
            ledger.attach_check(led, claim, round_=rnd, **check_kwargs): claim
            for claim in to_check
        }
        while pending:
            await asyncio.sleep(poll_s)
            for job_id, claim in list(pending.items()):
                if jobs.read(job_id).get("status") == "running":
                    continue
                state, detail = ledger.claim_state(claim)
                emit(f"  {claim['id']} {state}" + (f" — {detail}" if detail else ""))
                del pending[job_id]

        unsupported = []
        pad_seen = {_norm(e["claim"]) for e in led["scratchpad"]}
        for claim in current:
            state, detail = ledger.claim_state(claim)
            if state in ("refuted", "conflict") and _norm(claim["text"]) not in pad_seen:
                led["scratchpad"].append({"round": rnd, "claim": claim["text"], "note": detail})
            if state != "supported":
                unsupported.append(claim)
        ledger.save(led)

        if not unsupported:
            emit(f"round {rnd}: all {len(current)} claims supported")
            break
        emit(f"round {rnd}: {len(unsupported)} of {len(current)} claims not supported")
        if rnd == rounds:
            break
        user = _refine_user(problem, led, current)

    led["status"] = "assembled"
    ledger.save(led)
    return led


async def expand_claim(
    led: dict,
    claim: dict,
    *,
    api_key: str,
    model: str | None = None,
    base_url: str | None = None,
    tir_k: int = 1,
    grade_k: int = 4,
    temperature: float | None = None,
    max_tokens: int = 16000,
    exec_timeout_s: float = 60.0,
    spawn=None,
) -> list[dict]:
    """Decompose one claim into checked sub-claims (``mathx ledger expand``).

    model/base_url default to the ledger's; overriding is legitimate regime
    mixing (e.g. expand with a stronger generalist).
    """
    model = model or led["model"]
    base_url = base_url or led["base_url"]
    client = AsyncOpenAI(base_url=base_url, api_key=api_key)
    temp = 0.7 if temperature is None else temperature
    user = (
        "Decompose the following claim into 2–5 sub-claims that together imply it. "
        "Treat the claim as the problem.\n\n"
        f"Claim:\n{claim['text']}"
    )
    _, texts = await _decompose(
        client, model, user, temperature=temp, max_tokens=max_tokens, emit=lambda _m: None
    )
    rnd = led["rounds_used"]
    children = [
        ledger.add_claim(led, text, round_added=rnd, parent=claim["id"]) for text in texts
    ]
    for child in children:
        ledger.attach_check(
            led, child, round_=rnd, api_key=api_key, model=model, base_url=base_url,
            tir_k=tir_k, grade_k=grade_k, exec_timeout_s=exec_timeout_s,
            temperature=temperature, max_tokens=max_tokens, spawn=spawn,
        )
    return children
