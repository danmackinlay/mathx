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
from mathx.engine import concurrency_cap as _concurrency_cap

DECOMPOSER_SYSTEM = (
    "You are a careful mathematician structuring a checkable argument.\n"
    "Given a problem, produce a concise argument that answers it, decomposed into atomic "
    "claims. Each claim must be a complete, self-contained mathematical statement: state all "
    "quantifiers, definitions and constraints inside the claim itself — no pronouns, no "
    "references to the problem or to other claims. Someone reading ONE claim in isolation "
    "must be able to judge it true or false. Use at most 8 claims. "
    "For inline maths use $...$.\n"
    "Reply with the final answer ONLY — no drafts, no commentary, and the two headers below "
    "exactly once each — in EXACTLY this format:\n"
    "ARGUMENT:\n"
    "<the argument, in prose>\n"
    "CLAIMS:\n"
    "1. <first claim>\n"
    "2. <second claim>\n"
    "..."
)

CLAIM_LINE = re.compile(r"^\s*(?:\d+[.)]|[-*•])\s+(.+?)\s*$", re.MULTILINE)
_HEADER = re.compile(r"^\s*(ARGUMENT|CLAIMS):.*$", re.MULTILINE)
_PLACEHOLDER = re.compile(r"^<.*>$")
MAX_CLAIMS = 12  # more than this means the reply leaked drafts, not a decomposition


def parse_decomposition(text: str | None) -> tuple[str, list[str]] | None:
    """(argument, claims) from an ARGUMENT:/CLAIMS: reply, or None if unparseable.

    Reasoning-tuned models sometimes spill drafts into the reply — including
    echoes of the format template above — so headers can occur several times
    (live e2e: a 23-"claim" parse). Candidate CLAIMS blocks are segmented at
    headers and validated (no template placeholders, ≤ MAX_CLAIMS lines); the
    LAST valid block wins, per the final-answer convention.
    """
    if not text:
        return None
    headers = list(_HEADER.finditer(text))
    chosen: tuple[str, list[str]] | None = None
    for i, h in enumerate(headers):
        if h.group(1) != "CLAIMS":
            continue
        block_end = headers[i + 1].start() if i + 1 < len(headers) else len(text)
        claims = [c.strip() for c in CLAIM_LINE.findall(text[h.end(): block_end])]
        if not 1 <= len(claims) <= MAX_CLAIMS:
            continue
        if any(_PLACEHOLDER.match(c) for c in claims):
            continue  # an echo of the format template, not an answer
        # argument = prose between the nearest preceding ARGUMENT: header and this block
        argument = ""
        for j in range(i - 1, -1, -1):
            if headers[j].group(1) == "ARGUMENT":
                argument = text[headers[j].end(): h.start()].strip()
                break
        chosen = (argument, claims)
    return chosen


def _norm(text: str) -> str:
    return " ".join(text.split())


def _scratch_note(claim: dict) -> str:
    """The densest morsel for the refiner: a concrete counterexample beats a
    lane summary. Truncated — scratchpad text re-enters every refine prompt."""
    for verdict in reversed(claim.get("verdicts") or []):
        try:
            result = jobs.read(verdict["job_id"]).get("result") or {}
        except KeyError:
            continue
        for run in result.get("tir") or []:
            if run.get("verdict") == "fail" and run.get("note"):
                return _norm(run["note"])[:120]
        break
    _, detail = ledger.claim_state(claim)
    return _norm(detail)[:120]


async def _decompose(
    client, model, user, *, temperature, max_tokens, emit, transcript: list | None = None
) -> tuple[str, list[str]]:
    """One decomposition, one retry. Raw attempt texts are appended to
    *transcript* (the ledger keeps them: the argument's provenance is audit
    trail like everything else)."""
    for attempt in (1, 2):
        s = await _one_sample(
            client, model, user, temperature=temperature, max_tokens=max_tokens,
            system=DECOMPOSER_SYSTEM,
        )
        if transcript is not None:
            transcript.append(s.text)
        if s.error is not None:
            raise RuntimeError(f"decomposition sample failed: {s.error}")
        parsed = parse_decomposition(s.text)
        if parsed is not None:
            return parsed
        if attempt == 1:
            emit("decomposition had no valid ARGUMENT:/CLAIMS: structure — retrying once")
    raise RuntimeError("decomposition failed twice: no valid ARGUMENT:/CLAIMS: structure in reply")


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
    meta_model: str | None = None,
    top_p: float | None = None,
    extra_body: dict | None = None,
    max_retries: int | None = None,
    ledger_id: str | None = None,
    spawn=None,
    on_event: Callable[[str], None] | None = None,
) -> dict:
    """Run the loop; return the assembled ledger record.

    ``rounds`` = max refine cycles after the initial decomposition.
    ``meta_model`` (default: ``model``) does the meta-tasks — decomposition
    here, checker-script authorship inside each check job — so a narrow
    specialist can keep the grading seat without being handed jobs it is bad
    at. ``ledger_id`` adopts a pre-created (still-empty) ledger, so a
    submitter can hand the id to its caller before the loop starts. ``spawn``
    overrides how check workers start (tests run them in-process).
    """
    emit = on_event or (lambda _msg: None)
    client_kwargs: dict = {"base_url": base_url, "api_key": api_key}
    if max_retries is not None:
        client_kwargs["max_retries"] = max_retries
    client = AsyncOpenAI(**client_kwargs)
    temp = 0.7 if temperature is None else temperature
    check_kwargs = dict(
        api_key=api_key, tir_k=tir_k, grade_k=grade_k, exec_timeout_s=exec_timeout_s,
        temperature=temperature, max_tokens=max_tokens, meta_model=meta_model,
        top_p=top_p, extra_body=extra_body, max_retries=max_retries, spawn=spawn,
    )

    if ledger_id is not None:
        led = ledger.read(ledger_id)
        if led.get("claims"):
            raise ValueError(f"ledger {ledger_id} already has claims; argue starts fresh ones")
    else:
        led = ledger.create(problem, model=model, base_url=base_url, rounds_max=rounds)
    led["decompositions"] = []
    emit(f"ledger: {led['ledger_id']}")

    # cap concurrent check jobs so k×claims fan-outs don't stampede a small
    # server (live e2e: 23 simultaneous jobs vs 3 server slots = mass timeouts);
    # each worker also self-caps its requests via the same env var
    per_job = max(1, tir_k + grade_k)
    cap = _concurrency_cap()
    max_jobs = max(1, cap // per_job) if cap else None

    user = f"Problem:\n{problem}"
    for rnd in range(rounds + 1):
        led["rounds_used"] = rnd
        emit(f"round {rnd}: {'decomposing' if rnd == 0 else 'refining'}…")
        argument, texts = await _decompose(
            client, meta_model or model, user, temperature=temp, max_tokens=max_tokens,
            emit=emit, transcript=led["decompositions"],
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
        queued: list[str] = []
        deferred = lambda job_id, **_kw: queued.append(job_id)  # noqa: E731
        pending = {
            ledger.attach_check(led, claim, round_=rnd, **{**check_kwargs, "spawn": deferred}): claim
            for claim in to_check
        }
        in_flight: set[str] = set()

        def launch_up_to_cap() -> None:
            while queued and (max_jobs is None or len(in_flight) < max_jobs):
                job_id = queued.pop(0)
                (spawn or jobs.spawn_worker)(job_id, api_key=api_key)
                in_flight.add(job_id)

        launch_up_to_cap()
        while pending:
            await asyncio.sleep(poll_s)
            for job_id, claim in list(pending.items()):
                if job_id not in in_flight:
                    continue
                record = jobs.read(job_id)
                if record.get("status") == "running":
                    if jobs.worker_alive(record) is False:
                        # orphan: the worker died without finalizing — fail the
                        # record and move on instead of polling a ghost forever
                        jobs.fail(job_id, error="worker died without finalizing (orphan)")
                    else:
                        continue
                state, detail = ledger.claim_state(claim)
                emit(f"  {claim['id']} {state}" + (f" — {detail}" if detail else ""))
                del pending[job_id]
                in_flight.discard(job_id)
                launch_up_to_cap()

        unsupported = []
        pad_seen = {_norm(e["claim"]) for e in led["scratchpad"]}
        for claim in current:
            state, _ = ledger.claim_state(claim)
            if state in ("refuted", "conflict") and _norm(claim["text"]) not in pad_seen:
                led["scratchpad"].append(
                    {"round": rnd, "claim": claim["text"], "note": _scratch_note(claim)}
                )
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
    meta_model: str | None = None,
    top_p: float | None = None,
    extra_body: dict | None = None,
    max_retries: int | None = None,
    spawn=None,
) -> list[dict]:
    """Decompose one claim into checked sub-claims (``mathx ledger expand``).

    model/base_url default to the ledger's; overriding is legitimate regime
    mixing. The sub-decomposition itself runs on ``meta_model`` (default:
    the resolved model) — it's a meta-task.
    """
    model = model or led["model"]
    base_url = base_url or led["base_url"]
    client_kwargs: dict = {"base_url": base_url, "api_key": api_key}
    if max_retries is not None:
        client_kwargs["max_retries"] = max_retries
    client = AsyncOpenAI(**client_kwargs)
    temp = 0.7 if temperature is None else temperature
    user = (
        "Decompose the following claim into 2–5 sub-claims that together imply it. "
        "Treat the claim as the problem.\n\n"
        f"Claim:\n{claim['text']}"
    )
    _, texts = await _decompose(
        client, meta_model or model, user, temperature=temp, max_tokens=max_tokens,
        emit=lambda _m: None,
    )
    rnd = led["rounds_used"]
    children = [
        ledger.add_claim(led, text, round_added=rnd, parent=claim["id"]) for text in texts
    ]
    for child in children:
        ledger.attach_check(
            led, child, round_=rnd, api_key=api_key, model=model, base_url=base_url,
            tir_k=tir_k, grade_k=grade_k, exec_timeout_s=exec_timeout_s,
            temperature=temperature, max_tokens=max_tokens, meta_model=meta_model,
            top_p=top_p, extra_body=extra_body, max_retries=max_retries, spawn=spawn,
        )
    return children
