"""mathx engine: sample wide, cluster by maths-equivalence, vote.

Three strategies:

- ``cot``         — one sample at T=0; baseline.
- ``maj@k``       — k samples at non-zero T; modal-equivalence-class winner. Default.
- ``self_verify`` — k samples, each scored by a judge pass; weight votes by judge confidence.
"""
from __future__ import annotations

import asyncio
import re
import time
from dataclasses import dataclass, field
from typing import Literal

from math_verify import parse, verify
from openai import AsyncOpenAI

Strategy = Literal["cot", "maj@k", "self_verify"]

BOXED = re.compile(r"\\boxed\{((?:[^{}]|\{[^{}]*\})*)\}")
THINK_BLOCK = re.compile(r"<think>.*?</think>\s*", re.DOTALL)

SYSTEM_PROMPT = (
    "You are a careful mathematician. Solve the problem, then state the final answer.\n"
    "Wrap the FINAL answer in \\boxed{...}.\n"
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
    tokens_in_total: int = 0
    tokens_out_total: int = 0
    elapsed_ms_total: int = 0


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
    """Return the LAST \\boxed{...} contents in *text* (the final answer)."""
    if not text:
        return None
    hits = BOXED.findall(text)
    return hits[-1].strip() if hits else None


async def _one_sample(
    client: AsyncOpenAI,
    model: str,
    problem: str,
    *,
    temperature: float,
    max_tokens: int,
) -> Sample:
    t0 = time.monotonic()
    try:
        resp = await client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": problem},
            ],
            temperature=temperature,
            max_tokens=max_tokens,
        )
        msg = resp.choices[0].message
        text = _post_think(msg.content)
        usage = resp.usage
        return Sample(
            text=text,
            boxed=extract_boxed(text),
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
                if verify(parse(rep), parse(s.boxed)):
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
) -> Result:
    """Run the strategy, cluster, and return a Result.

    For ``cot``: k is ignored, T defaults to 0.0. For ``maj@k`` / ``self_verify``:
    T defaults to 0.7. Pass an explicit ``temperature`` to override.
    """
    t0 = time.monotonic()
    client = AsyncOpenAI(base_url=base_url, api_key=api_key)

    if strategy == "cot":
        kk, temp = 1, (0.0 if temperature is None else temperature)
    elif strategy in ("maj@k", "self_verify"):
        kk, temp = max(1, k), (0.7 if temperature is None else temperature)
    else:
        raise ValueError(f"unknown strategy: {strategy}")

    samples = await asyncio.gather(
        *[
            _one_sample(client, model, problem, temperature=temp, max_tokens=max_tokens)
            for _ in range(kk)
        ]
    )

    if strategy == "self_verify":
        async def annotate(s: Sample) -> Sample:
            if s.text is None or s.boxed is None:
                s.confidence = 0.0
            else:
                s.confidence = await _judge_one(client, model, problem, s.text)
            return s

        samples = await asyncio.gather(*[annotate(s) for s in samples])

    winner, margin, votes = _cluster_and_vote(samples)

    return Result(
        answer=winner,
        margin=margin,
        votes=votes,
        samples=samples,
        strategy=strategy,
        model=model,
        base_url=base_url,
        k=kk,
        tokens_in_total=sum(s.tokens_in for s in samples),
        tokens_out_total=sum(s.tokens_out for s in samples),
        elapsed_ms_total=int((time.monotonic() - t0) * 1000),
    )


def result_to_dict(r: Result) -> dict:
    """JSON-friendly serialization; ``samples[].text`` is the full audit trail."""
    return {
        "answer": r.answer,
        "margin": r.margin,
        "votes": r.votes,
        "strategy": r.strategy,
        "model": r.model,
        "base_url": r.base_url,
        "k": r.k,
        "tokens_in_total": r.tokens_in_total,
        "tokens_out_total": r.tokens_out_total,
        "elapsed_ms_total": r.elapsed_ms_total,
        "samples": [
            {
                "boxed": s.boxed,
                "confidence": s.confidence,
                "error": s.error,
                "tokens_in": s.tokens_in,
                "tokens_out": s.tokens_out,
                "elapsed_ms": s.elapsed_ms,
                "text": s.text,
            }
            for s in r.samples
        ],
    }
