"""Render a solve run's JSON audit record for humans.

Pure readers over the dict shape written by ``mathx solve --out`` (see
``result_to_dict``): vote histogram, per-sample table, disagreement surfacing.
Nothing here talks to a network; equivalence checks against the winner reuse
the same math-verify primitive the engine votes with.
"""
from __future__ import annotations

from math_verify import verify

from mathx.engine import parse_answer

BAR_WIDTH = 24


def _equiv(a: str, b: str) -> bool:
    if a == b:
        return True
    try:
        pa, pb = parse_answer(a), parse_answer(b)
        return bool(verify(pa, pb) or verify(pb, pa))  # verify() is asymmetric
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
    if run.get("judge_merges"):
        meta += f"   judge merges: {run['judge_merges']} (LLM-judged equivalence, weaker than CAS)"
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
        legend = "✓ votes with winner"
        if run.get("judge_merges"):
            legend += "; ≈ counted into a larger cluster by the equivalence judge"
        lines.append(f"samples ({legend}; `--sample N` prints full reasoning):")
        for i, s in enumerate(samples):
            boxed = s.get("boxed")
            if s.get("error"):
                mark, shown = "!", f"error: {_one_line(s['error'], 60)}"
            elif boxed is None:
                mark, shown = "·", "(no \\boxed{...} answer)"
            elif answer is not None and _equiv(boxed, answer):
                mark, shown = "✓", _one_line(boxed, 50)
            elif s.get("merge_basis") == "judge":
                mark, shown = "≈", _one_line(boxed, 50)
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


EVIDENCE_NOTE = (
    "verdicts are evidence, not proof: a passing script checked instances/symbolics, "
    "a grade is a vote"
)


def render_check_report(run: dict) -> str:
    """One-screen report for a `mathx check` record."""
    lines: list[str] = []
    if run.get("claim"):
        lines.append(f"claim: {_one_line(run['claim'], 100)}")
    lines.append(f"status: {run.get('summary') or run.get('status')}")
    lines.append(
        f"model: {run.get('model')}   tir_k: {run.get('tir_k')}   grade_k: {run.get('grade_k')}"
    )
    lines.append(
        f"tokens: in={run.get('tokens_in_total', 0)} out={run.get('tokens_out_total', 0)}   "
        f"elapsed: {run.get('elapsed_ms_total', 0)} ms"
    )

    tir_runs: list[dict] = run.get("tir") or []
    if tir_runs:
        lines.append("")
        lines.append("tir scripts (`--script N` prints the code and its output):")
        for i, r in enumerate(tir_runs):
            note = f" — {_one_line(r['note'], 70)}" if r.get("note") else ""
            timing = "timed out" if r.get("timed_out") else f"{r.get('exec_elapsed_ms', 0)} ms"
            lines.append(f"  {i:>3}  {r.get('verdict', '?'):<12}  {timing:>9}{note}")

    grade: dict | None = run.get("grade")
    if grade:
        lines.append("")
        parts = (
            f"{grade.get('true', 0)} true / {grade.get('false', 0)} false / "
            f"{grade.get('abstain', 0)} abstain"
        )
        if grade.get("errors"):
            parts += f" / {grade['errors']} errored"
        lines.append(
            f"grade: {grade.get('verdict')} {grade.get('margin')} — {parts} "
            "(`--sample N` prints a grader's reasoning)"
        )

    lines.append("")
    lines.append(EVIDENCE_NOTE)
    return "\n".join(lines)


_STATE_GLYPH = {
    "supported": "✓",
    "refuted": "✗",
    "conflict": "!",
    "unclear": "?",
    "error": "!",
    "checking": "…",
    "unchecked": "·",
    "retired": "–",
    "missing": "?",
}


def render_ledger(led: dict, state_of) -> str:
    """One-screen claim ledger; ``state_of(claim) -> (state, detail)`` derives
    live badges (pass ``mathx.ledger.claim_state`` outside tests)."""
    lines = [
        f"ledger: {led.get('ledger_id')}   status: {led.get('status')}   "
        f"rounds: {led.get('rounds_used')}/{led.get('rounds_max')}   model: {led.get('model')}"
    ]
    if led.get("problem"):
        lines.append(f"problem: {_one_line(led['problem'], 100)}")
    if led.get("argument"):
        lines += ["", "argument:", led["argument"].rstrip()]

    claims: list[dict] = led.get("claims") or []
    if claims:
        lines += ["", "claims (live badges from the job store):"]

        def emit(claim: dict, indent: str) -> None:
            state, detail = state_of(claim)
            glyph = _STATE_GLYPH.get(state, "?")
            lines.append(
                f"{indent}{claim['id']:>4}  {glyph} {state:<10} {_one_line(claim['text'], 76)}"
            )
            verdicts = claim.get("verdicts") or []
            trail = f"[{verdicts[-1]['job_id']}]" if verdicts else ""
            if (detail or trail) and state != "retired":
                body = " ".join(p for p in (_one_line(detail, 60), trail) if p)
                lines.append(f"{indent}      {'':<12} {body}")

        by_parent: dict[str | None, list[dict]] = {}
        for claim in claims:
            by_parent.setdefault(claim.get("parent"), []).append(claim)
        for top in by_parent.get(None, []):
            emit(top, "  ")
            for child in by_parent.get(top["id"], []):
                emit(child, "      ")

    if led.get("scratchpad"):
        lines += ["", "scratchpad (refuted along the way):"]
        for entry in led["scratchpad"]:
            note = f" — {_one_line(entry['note'], 50)}" if entry.get("note") else ""
            lines.append(f"  r{entry.get('round')}: {_one_line(entry['claim'], 70)}{note}")

    lines += [
        "",
        EVIDENCE_NOTE,
        "`mathx show <job_id>` for any claim's audit; `mathx ledger recheck <ledger> <claim>` to escalate",
    ]
    return "\n".join(lines)


def render_check_script(run: dict, index: int) -> str:
    """Checker script *index*: its code, then what it printed."""
    tir_runs: list[dict] = run.get("tir") or []
    if not 0 <= index < len(tir_runs):
        raise IndexError(f"script index {index} out of range (run has {len(tir_runs)} scripts)")
    r = tir_runs[index]
    head = f"script {index}: verdict={r.get('verdict')}"
    if r.get("note"):
        head += f"   note: {r['note']}"
    head += f"\nexit={r.get('exit_code')}   timed_out={r.get('timed_out')}   {r.get('exec_elapsed_ms', 0)} ms"
    parts = [head, "", "--- code ---", r.get("code") or "(no code extracted)"]
    if r.get("stdout"):
        parts += ["", "--- stdout ---", r["stdout"].rstrip()]
    if r.get("stderr"):
        parts += ["", "--- stderr ---", r["stderr"].rstrip()]
    return "\n".join(parts)
