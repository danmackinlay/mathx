"""mathx engine: sample wide, cluster by maths-equivalence, vote.

Three strategies:

- ``cot``         — one sample at T=0; baseline.
- ``maj@k``       — k samples at non-zero T; modal-equivalence-class winner. Default.
- ``self_verify`` — k samples, each scored by a judge pass; weight votes by judge confidence.
"""
from __future__ import annotations

import asyncio
import os
import random
import re
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal

from math_verify import parse, verify
from openai import AsyncOpenAI

Strategy = Literal["cot", "maj@k", "self_verify"]

THINK_BLOCK = re.compile(r"<think>.*?</think>\s*", re.DOTALL)

SYSTEM_PROMPT = (
    "You are a careful mathematician. Solve the problem, then state the final answer.\n"
    "Wrap the FINAL answer in \\boxed{...}. Box exactly the form the problem asks for — a "
    "value, an expression, or (if an identity/equation/relation is requested) an equation. "
    "Do NOT wrap the answer in a restatement of the question (no 'KL(...) = ' prefix around "
    "an expression answer), and use no \\displaystyle and no surrounding prose in the box: "
    "answers are compared by computer algebra, and notation it cannot interpret splits the "
    "vote.\n"
    "For inline maths use $...$ and for display use $$...$$ — never \\(...\\) or \\[...\\]."
)

JUDGE_SYSTEM = (
    "You are reviewing a candidate solution to a maths problem. "
    "Rate, from 0.0 to 1.0, how confident you are that the boxed final answer is correct. "
    "Reply with ONLY the number, on a single line."
)


@dataclass
class Sample:
    text: str | None
    boxed: str | None
    confidence: float | None = None
    error: str | None = None
    tokens_in: int = 0
    tokens_out: int = 0
    elapsed_ms: int = 0


@dataclass
class Result:
    answer: str | None
    margin: str
    votes: dict[str, float]
    samples: list[Sample]
    strategy: str
    model: str
    base_url: str
    k: int
    problem: str = ""
    escalations: int = 0
    tokens_in_total: int = 0
    tokens_out_total: int = 0
    elapsed_ms_total: int = 0


def concurrency_cap() -> int | None:
    """$MATHX_CONCURRENCY: max in-flight requests this process should hold
    against the endpoint. Unset/invalid means unlimited. Small local servers
    admit a handful of generations and let the rest age toward their request
    timeout (live e2e: 3 slots, ~300 s guillotine) — cap to the server's real
    parallelism when fanning out against one."""
    raw = os.environ.get("MATHX_CONCURRENCY", "").strip()
    try:
        n = int(raw)
    except ValueError:
        return None
    return n if n > 0 else None


def parse_answer(answer: str):
    """math-verify parse, $-wrapped first: the LaTeX extractor only engages on
    delimited maths, and bare formula strings otherwise parse to nothing (live
    e2e finding — every cluster became a singleton). Falls back to a raw parse
    for plain numerics."""
    try:
        hits = parse(f"${answer}$")
    except Exception:
        hits = []
    return hits if hits else parse(answer)


def _post_think(content: str | None) -> str | None:
    """Strip leading <think>…</think> if the server didn't already.

    Servers vary: vLLM-with-reasoning-parser and oMLX put reasoning in a separate field;
    raw vLLM-without-parser leaves <think>…</think> in content. Stripping is a no-op for
    the former and the right thing for the latter.
    """
    if content is None:
        return None
    return THINK_BLOCK.sub("", content, count=1)


def extract_boxed(text: str | None) -> str | None:
    """Return the LAST \\boxed{...} contents in *text* (the final answer).

    Brace-matching scan, not a regex: formula answers routinely nest braces
    two-plus deep (``\\frac{s^{2}}{t}``, ``D_{\\text{KL}}``), which no fixed
    regex depth survives. ``\\{``/``\\}`` are literal braces and don't count;
    an unterminated box (truncation) falls back to the previous one.
    """
    if not text:
        return None
    marker = "\\boxed{"
    idx = text.rfind(marker)
    while idx != -1:
        i = idx + len(marker)
        depth = 1
        while i < len(text):
            ch = text[i]
            if ch == "\\":
                i += 2
                continue
            if ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    inner = text[idx + len(marker): i].strip()
                    return inner or None
            i += 1
        idx = text.rfind(marker, 0, idx)  # unterminated: try an earlier box
    return None


async def _one_sample(
    client: AsyncOpenAI,
    model: str,
    problem: str,
    *,
    temperature: float,
    max_tokens: int,
    system: str = SYSTEM_PROMPT,
    top_p: float | None = None,
    extra_body: dict | None = None,
) -> Sample:
    t0 = time.monotonic()
    kwargs: dict = {}
    if top_p is not None:
        kwargs["top_p"] = top_p
    if extra_body:
        # provider-dialect passthrough (reasoning effort, thinking toggles, …):
        # mathx models none of it, the endpoint gets it verbatim
        kwargs["extra_body"] = extra_body
    try:
        resp = await client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": problem},
            ],
            temperature=temperature,
            max_tokens=max_tokens,
            # distinct per-request seed: some servers (live e2e: vllm-mlx)
            # otherwise reproduce byte-identical completions for identical
            # prompts, silently degenerating the fan-out
            seed=random.randrange(2**31),
            **kwargs,
        )
        msg = resp.choices[0].message
        text = _post_think(msg.content)
        usage = resp.usage
        return Sample(
            text=text,
            boxed=extract_boxed(text),
            # a "successful" response with no content is a failure, and saying so
            # beats a silent abstention (live e2e: local server returned empty
            # completions under load)
            error=None if text else "empty completion",
            tokens_in=getattr(usage, "prompt_tokens", 0) or 0,
            tokens_out=getattr(usage, "completion_tokens", 0) or 0,
            elapsed_ms=int((time.monotonic() - t0) * 1000),
        )
    except Exception as e:  # network, server, parse, anything
        return Sample(
            text=None,
            boxed=None,
            error=f"{type(e).__name__}: {e}",
            elapsed_ms=int((time.monotonic() - t0) * 1000),
        )


async def _judge_one(client: AsyncOpenAI, model: str, problem: str, candidate: str) -> float:
    try:
        resp = await client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": JUDGE_SYSTEM},
                {
                    "role": "user",
                    "content": f"Problem:\n{problem}\n\nCandidate solution:\n{candidate}",
                },
            ],
            temperature=0.0,
            max_tokens=8,
        )
        out = (resp.choices[0].message.content or "").strip()
        m = re.search(r"[-+]?\d*\.?\d+", out)
        return max(0.0, min(1.0, float(m.group(0)))) if m else 0.5
    except Exception:
        return 0.5


def _cluster_and_vote(samples: list[Sample]) -> tuple[str | None, str, dict[str, float]]:
    """Cluster boxed answers by math-verify equivalence; return winner / margin / votes.

    Weight defaults to 1.0; if a sample has a ``confidence`` (self_verify), that's its weight.
    """
    clusters: list[tuple[str, float, list[Sample]]] = []
    n_voters = 0
    for s in samples:
        if s.boxed is None:
            continue
        n_voters += 1
        weight = 1.0 if s.confidence is None else s.confidence
        placed = False
        for i, (rep, w, members) in enumerate(clusters):
            try:
                if verify(parse_answer(rep), parse_answer(s.boxed)):
                    clusters[i] = (rep, w + weight, [*members, s])
                    placed = True
                    break
            except Exception:
                # math-verify can throw on weird inputs; treat as non-equivalent
                pass
        if not placed:
            clusters.append((s.boxed, weight, [s]))

    if not clusters:
        return None, "0/0", {}
    clusters.sort(key=lambda c: c[1], reverse=True)
    winner, _, top_members = clusters[0]
    margin = f"{len(top_members)}/{n_voters}"
    votes = {rep: round(w, 3) for (rep, w, _) in clusters}
    return winner, margin, votes


def _winner_share(answer: str | None, votes: dict[str, float]) -> float:
    """Winner's fraction of the total vote weight (0.0 when there is no winner)."""
    if answer is None or not votes:
        return 0.0
    total = sum(votes.values())
    return votes.get(answer, 0.0) / total if total else 0.0


async def solve(
    problem: str,
    *,
    model: str,
    base_url: str,
    api_key: str,
    k: int = 16,
    strategy: Strategy = "maj@k",
    temperature: float | None = None,
    max_tokens: int = 16000,
    max_k: int | None = None,
    top_p: float | None = None,
    extra_body: dict | None = None,
    max_retries: int | None = None,
    on_sample: Callable[[Sample, int, int], None] | None = None,
    on_escalate: Callable[[str, int], None] | None = None,
) -> Result:
    """Run the strategy, cluster, and return a Result.

    For ``cot``: k is ignored, T defaults to 0.0. For ``maj@k`` / ``self_verify``:
    T defaults to 0.7. Pass an explicit ``temperature`` to override.

    ``max_k`` turns on auto-escalation: after voting, if the winner holds no
    strict majority of the vote weight (a 6/5/5-style split) OR fewer than half
    the samples cast a vote (mass abstention — e.g. truncated reasoning), double
    the sample count and re-vote over everything drawn so far, until both a
    strict majority and a voter quorum hold or ``max_k`` samples have been
    drawn. No winner at all (zero boxed answers) does NOT escalate — that's a
    setup problem, not a split. Ignored for ``cot``.

    Progress hooks (both optional, called from the event loop):
    ``on_sample(sample, done, planned)`` after each sample lands (and, for
    ``self_verify``, is judged); ``on_escalate(margin, new_planned)`` when a weak
    margin triggers another round.
    """
    t0 = time.monotonic()
    client_kwargs: dict = {"base_url": base_url, "api_key": api_key}
    if max_retries is not None:
        # SDK default (2) suits cloud endpoints; 0 suits a single-user local
        # server, where retrying a doomed long generation only amplifies load
        client_kwargs["max_retries"] = max_retries
    client = AsyncOpenAI(**client_kwargs)

    if strategy == "cot":
        kk, temp = 1, (0.0 if temperature is None else temperature)
    elif strategy in ("maj@k", "self_verify"):
        kk, temp = max(1, k), (0.7 if temperature is None else temperature)
    else:
        raise ValueError(f"unknown strategy: {strategy}")

    samples: list[Sample] = []
    planned = kk
    escalations = 0
    done = 0
    cap = concurrency_cap()
    sem = asyncio.Semaphore(cap) if cap else None

    async def one() -> Sample:
        nonlocal done
        if sem is not None:
            await sem.acquire()
        try:
            s = await _one_sample(
                client, model, problem, temperature=temp, max_tokens=max_tokens,
                top_p=top_p, extra_body=extra_body,
            )
            if strategy == "self_verify":
                if s.text is None or s.boxed is None:
                    s.confidence = 0.0
                else:
                    s.confidence = await _judge_one(client, model, problem, s.text)
        finally:
            if sem is not None:
                sem.release()
        done += 1
        if on_sample is not None:
            on_sample(s, done, planned)
        return s

    while True:
        new = await asyncio.gather(*[one() for _ in range(planned - len(samples))])
        samples.extend(new)
        winner, margin, votes = _cluster_and_vote(samples)
        if max_k is None or strategy == "cot" or winner is None:
            break
        n_voters = sum(1 for s in samples if s.boxed is not None)
        strong = _winner_share(winner, votes) > 0.5 and 2 * n_voters >= len(samples)
        if strong or len(samples) >= max_k:
            break
        planned = min(len(samples) * 2, max_k)
        escalations += 1
        if on_escalate is not None:
            on_escalate(margin, planned)

    return Result(
        answer=winner,
        margin=margin,
        votes=votes,
        samples=samples,
        strategy=strategy,
        model=model,
        base_url=base_url,
        k=len(samples),
        problem=problem,
        escalations=escalations,
        tokens_in_total=sum(s.tokens_in for s in samples),
        tokens_out_total=sum(s.tokens_out for s in samples),
        elapsed_ms_total=int((time.monotonic() - t0) * 1000),
    )


def sample_to_dict(s: Sample) -> dict:
    return {
        "boxed": s.boxed,
        "confidence": s.confidence,
        "error": s.error,
        "tokens_in": s.tokens_in,
        "tokens_out": s.tokens_out,
        "elapsed_ms": s.elapsed_ms,
        "text": s.text,
    }


def result_to_dict(r: Result) -> dict:
    """JSON-friendly serialization; ``samples[].text`` is the full audit trail."""
    return {
        "kind": "solve",
        "problem": r.problem,
        "answer": r.answer,
        "margin": r.margin,
        "votes": r.votes,
        "strategy": r.strategy,
        "escalations": r.escalations,
        "model": r.model,
        "base_url": r.base_url,
        "k": r.k,
        "tokens_in_total": r.tokens_in_total,
        "tokens_out_total": r.tokens_out_total,
        "elapsed_ms_total": r.elapsed_ms_total,
        "samples": [sample_to_dict(s) for s in r.samples],
    }
