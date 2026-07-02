"""Render a solve run's JSON audit record for humans.

Pure readers over the dict shape written by ``mathx solve --out`` (see
``result_to_dict``): vote histogram, per-sample table, disagreement surfacing.
Nothing here talks to a network; equivalence checks against the winner reuse
the same math-verify primitive the engine votes with.
"""
from __future__ import annotations

from math_verify import parse, verify

BAR_WIDTH = 24


def _equiv(a: str, b: str) -> bool:
    if a == b:
        return True
    try:
        return bool(verify(parse(a), parse(b)))
    except Exception:
        return False


def _one_line(s: str, width: int) -> str:
    s = " ".join(s.split())
    return s if len(s) <= width else s[: width - 1] + "…"


def _bar(weight: float, top: float) -> str:
    if top <= 0 or weight <= 0:
        return ""
    return "█" * max(1, round(BAR_WIDTH * weight / top))


def render_report(run: dict) -> str:
    """One-screen report: header, vote histogram, sample table, disagreement."""
    answer: str | None = run.get("answer")
    votes: dict[str, float] = run.get("votes") or {}
    samples: list[dict] = run.get("samples") or []
    lines: list[str] = []

    if run.get("problem"):
        lines.append(f"problem: {_one_line(run['problem'], 100)}")
    lines.append(f"answer: {answer}")
    meta = (
        f"margin: {run.get('margin')}   strategy: {run.get('strategy')}   "
        f"model: {run.get('model')}   k: {run.get('k')}"
    )
    if run.get("escalations"):
        meta += f"   escalations: {run['escalations']}"
    lines.append(meta)
    lines.append(
        f"tokens: in={run.get('tokens_in_total', 0)} out={run.get('tokens_out_total', 0)}   "
        f"elapsed: {run.get('elapsed_ms_total', 0)} ms"
    )

    if votes:
        lines.append("")
        lines.append("votes (weight, answer):")
        top = max(votes.values())
        for rep, w in sorted(votes.items(), key=lambda kv: kv[1], reverse=True):
            mark = "  ← winner" if rep == answer else ""
            lines.append(f"  {w:>7.2f}  {_bar(w, top):<{BAR_WIDTH}}  {_one_line(rep, 50)}{mark}")

    if samples:
        lines.append("")
        lines.append("samples (✓ votes with winner; `--sample N` prints full reasoning):")
        for i, s in enumerate(samples):
            boxed = s.get("boxed")
            if s.get("error"):
                mark, shown = "!", f"error: {_one_line(s['error'], 60)}"
            elif boxed is None:
                mark, shown = "·", "(no \\boxed{...} answer)"
            elif answer is not None and _equiv(boxed, answer):
                mark, shown = "✓", _one_line(boxed, 50)
            else:
                mark, shown = "✗", _one_line(boxed, 50)
            conf = s.get("confidence")
            conf_s = f"{conf:.2f}" if conf is not None else "—"
            lines.append(
                f"  {i:>3}  {mark}  {shown:<52}  conf={conf_s:<5}  "
                f"out={s.get('tokens_out', 0):<6}  {s.get('elapsed_ms', 0)} ms"
            )

    lines.append("")
    lines.extend(_verdict_lines(answer, votes, samples))
    return "\n".join(lines)


def _verdict_lines(answer: str | None, votes: dict[str, float], samples: list[dict]) -> list[str]:
    """Surface the disagreement: who voted against the winner, who didn't vote."""
    lines: list[str] = []
    n_errors = sum(1 for s in samples if s.get("error"))
    n_silent = sum(1 for s in samples if not s.get("error") and s.get("boxed") is None)

    if answer is None:
        lines.append(
            "no sample produced a \\boxed{...} answer — something is wrong "
            "(bad model, bad prompt, server down); try `mathx doctor`"
        )
    else:
        voters = [s for s in samples if s.get("boxed") is not None]
        dissent = [s for s in voters if not _equiv(s["boxed"], answer)]
        if dissent:
            rivals = sorted(
                ((rep, w) for rep, w in votes.items() if rep != answer),
                key=lambda kv: kv[1],
                reverse=True,
            )
            rival_s = ", ".join(f"{_one_line(rep, 30)} ({w:g})" for rep, w in rivals)
            lines.append(
                f"disagreement: {len(dissent)}/{len(voters)} voters against the winner"
                + (f" — rivals: {rival_s}" if rival_s else "")
            )
        elif voters:
            lines.append(f"unanimous: all {len(voters)} voters agree")
    if n_errors or n_silent:
        parts = []
        if n_errors:
            parts.append(f"{n_errors} errored")
        if n_silent:
            parts.append(f"{n_silent} returned no \\boxed{{...}}")
        lines.append(f"non-voters: {' and '.join(parts)} (of {len(samples)} samples)")
    return lines


def render_sample(run: dict, index: int) -> str:
    """Sample *index*'s full reasoning text, with a one-line header."""
    samples: list[dict] = run.get("samples") or []
    if not 0 <= index < len(samples):
        raise IndexError(f"sample index {index} out of range (run has {len(samples)} samples)")
    s = samples[index]
    head = f"sample {index}: boxed={s.get('boxed')!r}"
    if s.get("confidence") is not None:
        head += f"   confidence={s['confidence']:.2f}"
    head += f"   tokens_out={s.get('tokens_out', 0)}   elapsed={s.get('elapsed_ms', 0)} ms"
    if s.get("error"):
        head += f"\nerror: {s['error']}"
    return f"{head}\n\n{s.get('text') or '(no text)'}"
