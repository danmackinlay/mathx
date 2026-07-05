"""Claim checking: the Stage-3 primitive (design: CHECK_PLAN.md).

Two verdict lanes, run concurrently, each optional:

- **tir** — ``tir_k`` independent samples each write ONE self-contained
  verification script (sympy symbolic checks + seeded random-instance testing);
  the Executor runs each script; a structured verdict is parsed from stdout.
  This is the single-shot checker-authored lane; literal multi-turn TIR is a
  later upgrade behind the same interface.
- **grade** — ``grade_k`` independent samples grade the claim and vote
  ``\\boxed{TRUE}`` / ``\\boxed{FALSE}``; majority plus margin.

Verdicts are evidence, not certainty: every lane's raw material (generated
code, stdout/stderr, per-sample reasoning) stays in the record as the audit
trail, and the overall status only ever says supported / refuted / conflict /
unclear.
"""
from __future__ import annotations

import asyncio
import re
import time
from dataclasses import dataclass

from mathx.config import ProviderConfig
from mathx.engine import Sample, concurrency_cap, make_client, one_sample, sample_to_dict
from mathx.executor import ExecResult, LocalExecutor, get_executor

CHECKER_SYSTEM = (
    "You are a careful mathematician writing a VERIFICATION SCRIPT for a claim — "
    "you are not being asked to solve a problem.\n"
    "Write ONE self-contained Python script that checks the claim:\n"
    "- Prefer symbolic verification with sympy (equivalence, simplifying a difference "
    "to zero, exact solving).\n"
    "- Where symbolic checking is infeasible, test the claim on many randomly sampled "
    "instances (use a fixed seed) and on edge cases.\n"
    "- Use ONLY the Python standard library, sympy, and mpmath (ships with sympy) — "
    "assume numpy/scipy are NOT installed.\n"
    "- Do not use the network and do not read or write files.\n"
    "- The LAST line the script prints must be exactly one of:\n"
    "  VERDICT: PASS\n"
    "  VERDICT: FAIL\n"
    "  VERDICT: INCONCLUSIVE\n"
    "- Immediately before a FAIL verdict, print 'COUNTEREXAMPLE: <details>'. "
    "Immediately before an INCONCLUSIVE verdict, print 'REASON: <why>'.\n"
    "Reply with ONLY the script, in a single ```python fenced block."
)

GRADER_SYSTEM = (
    "You are reviewing a mathematical claim. Decide whether it is TRUE or FALSE.\n"
    "Reason carefully, then end with the FINAL answer wrapped as \\boxed{TRUE} or "
    "\\boxed{FALSE}. If the claim is ambiguous or you cannot decide, end with "
    "\\boxed{UNDECIDED}."
)

CODE_BLOCK = re.compile(r"```(?:python|py)?\s*\n(.*?)```", re.DOTALL)
VERDICT_LINE = re.compile(r"\s*VERDICT:\s*(PASS|FAIL|INCONCLUSIVE)\s*$", re.IGNORECASE)
NOTE_LINE = re.compile(r"\s*(?:COUNTEREXAMPLE|REASON):\s*(.+)$")


@dataclass
class ScriptRun:
    """One checker script: its generation, execution, and parsed verdict."""

    verdict: str  # pass | fail | inconclusive | error
    note: str | None  # counterexample / reason / error description
    code: str | None
    stdout: str
    stderr: str
    exit_code: int | None
    timed_out: bool
    exec_elapsed_ms: int
    gen: Sample  # the generation, as audit trail


@dataclass
class CheckResult:
    claim: str
    status: str  # supported | refuted | conflict | unclear
    summary: str
    tir_runs: list[ScriptRun]
    grade_samples: list[Sample]
    grade_verdict: str | None  # true | false | split | none; None if lane off
    grade_margin: str | None
    model: str
    base_url: str
    tir_k: int
    grade_k: int
    meta_model: str | None = None  # authored the checker scripts, when != model
    tokens_in_total: int = 0
    tokens_out_total: int = 0
    elapsed_ms_total: int = 0


def extract_code(text: str | None) -> str | None:
    """The LAST fenced python block (final-answer convention, like extract_boxed)."""
    hits = CODE_BLOCK.findall(text or "")
    return hits[-1].strip() if hits else None


def parse_script_verdict(res: ExecResult) -> tuple[str, str | None]:
    """(verdict, note) from a checker script's execution."""
    if res.timed_out:
        return "error", "script timed out"
    lines = res.stdout.splitlines()
    for line in reversed(lines):
        m = VERDICT_LINE.match(line)
        if m is None:
            continue
        if res.exit_code != 0:
            return "error", f"script printed a VERDICT but exited {res.exit_code}"
        note = None
        for candidate in reversed(lines):
            m2 = NOTE_LINE.match(candidate)
            if m2:
                note = m2.group(1).strip()
                break
        return m.group(1).lower(), note
    if res.exit_code != 0:
        stderr_tail = res.stderr.strip().splitlines()[-1] if res.stderr.strip() else "no stderr"
        return "error", f"script exited {res.exit_code}: {stderr_tail}"
    return "error", "script printed no VERDICT line"


_TEXT_WRAPPER = re.compile(r"\\(?:text|mathrm|mathbf|textbf|textsc|textit)\s*\{([^{}]*)\}")


def _normalize_grade(boxed: str | None) -> str:
    """TRUE/FALSE/UNDECIDED from a grader's boxed answer, tolerating LaTeX
    wrappers (live e2e: a grader answered ``\\text{FALSE}`` and went uncounted)."""
    s = boxed or ""
    for _ in range(3):  # wrappers can nest a little
        s = _TEXT_WRAPPER.sub(r"\1", s)
    return re.sub(r"[^A-Za-z]", "", s).upper()


def _tally_grades(samples: list[Sample]) -> tuple[str, str, int, int, int, int]:
    """(verdict, margin, true, false, abstain, errors) from grader samples.

    Errors (transport/server failures) are counted apart from genuine
    abstentions — four 503s and an UNDECIDED are different facts."""
    n_true = n_false = abstain = errors = 0
    for s in samples:
        if s.error is not None:
            errors += 1
            continue
        word = _normalize_grade(s.boxed)
        if word == "TRUE":
            n_true += 1
        elif word == "FALSE":
            n_false += 1
        else:
            abstain += 1
    voters = n_true + n_false
    if voters == 0:
        return "none", "0/0", n_true, n_false, abstain, errors
    if n_true == n_false:
        verdict = "split"
    else:
        verdict = "true" if n_true > n_false else "false"
    return verdict, f"{max(n_true, n_false)}/{voters}", n_true, n_false, abstain, errors


def _aggregate_tir(runs: list[ScriptRun]) -> str | None:
    verdicts = [r.verdict for r in runs]
    if not verdicts:
        return None
    has_pass, has_fail = "pass" in verdicts, "fail" in verdicts
    if has_pass and has_fail:
        return "split"
    if has_fail:
        return "fail"
    if has_pass:
        return "pass"
    if "inconclusive" in verdicts:
        return "inconclusive"
    return "error"


def _overall_status(tir_agg: str | None, grade_verdict: str | None) -> str:
    # a split lane is internal disagreement: it contributes to neither side
    positive = tir_agg == "pass" or grade_verdict == "true"
    negative = tir_agg == "fail" or grade_verdict == "false"
    if positive and negative:
        return "conflict"
    if negative:
        return "refuted"
    if positive:
        return "supported"
    return "unclear"


def _summarize(
    status: str,
    tir_runs: list[ScriptRun],
    tir_agg: str | None,
    grade_verdict: str | None,
    grade_margin: str | None,
) -> str:
    parts = []
    if tir_agg is not None:
        n = len(tir_runs)
        ok = sum(1 for r in tir_runs if r.verdict in ("pass", "fail", "inconclusive"))
        detail = f"{ok}/{n} scripts ran" if ok < n else f"{n} script{'s' if n != 1 else ''}"
        parts.append(f"tir: {tir_agg} ({detail})")
    if grade_verdict is not None:
        parts.append(f"grade: {grade_verdict} ({grade_margin})")
    return f"{status} — {' · '.join(parts)}" if parts else status


async def check(
    claim: str,
    *,
    provider: ProviderConfig,
    tir_k: int = 1,
    grade_k: int = 8,
    exec_timeout_s: float = 60.0,
    executor: LocalExecutor | None = None,
) -> CheckResult:
    """Run the enabled verdict lanes concurrently and aggregate.

    Set ``tir_k=0`` or ``grade_k=0`` to switch a lane off (not both).
    ``provider.meta_model`` (default: ``provider.model``) authors the checker
    scripts — a meta-task that narrow maths specialists are routinely bad at;
    grading stays on ``model``, which specialists are good at.
    """
    if tir_k <= 0 and grade_k <= 0:
        raise ValueError("at least one lane must be on: tir_k or grade_k must be > 0")
    t0 = time.monotonic()
    model, meta_model = provider.model, provider.meta_model
    client = make_client(provider)
    executor = executor or get_executor()
    temp = 0.7 if provider.temperature is None else provider.temperature
    prompt = f"Claim:\n{claim}"
    cap = concurrency_cap()
    sem = asyncio.Semaphore(cap) if cap else None

    async def sample(use_model: str, system: str) -> Sample:
        if sem is not None:
            async with sem:
                return await one_sample(
                    client, use_model, prompt, temperature=temp,
                    max_tokens=provider.max_tokens, system=system,
                    top_p=provider.top_p, extra_body=provider.extra_body,
                )
        return await one_sample(
            client, use_model, prompt, temperature=temp,
            max_tokens=provider.max_tokens, system=system,
            top_p=provider.top_p, extra_body=provider.extra_body,
        )

    async def one_tir() -> ScriptRun:
        gen = await sample(meta_model or model, CHECKER_SYSTEM)
        if gen.error is not None:
            return ScriptRun("error", f"generation failed: {gen.error}", None, "", "", None, False, 0, gen)
        code = extract_code(gen.text)
        if code is None:
            return ScriptRun("error", "generation contained no ```python block", None, "", "", None, False, 0, gen)
        res = await asyncio.to_thread(executor.run, code, timeout_s=exec_timeout_s)
        verdict, note = parse_script_verdict(res)
        return ScriptRun(
            verdict, note, code, res.stdout, res.stderr, res.exit_code, res.timed_out, res.elapsed_ms, gen
        )

    async def one_grade() -> Sample:
        return await sample(model, GRADER_SYSTEM)

    tir_runs, grade_samples = await asyncio.gather(
        asyncio.gather(*[one_tir() for _ in range(max(0, tir_k))]),
        asyncio.gather(*[one_grade() for _ in range(max(0, grade_k))]),
    )
    tir_runs, grade_samples = list(tir_runs), list(grade_samples)

    tir_agg = _aggregate_tir(tir_runs)
    if grade_samples:
        grade_verdict, grade_margin, *_rest = _tally_grades(grade_samples)
    else:
        grade_verdict = grade_margin = None
    status = _overall_status(tir_agg, grade_verdict)

    all_samples = [r.gen for r in tir_runs] + grade_samples
    return CheckResult(
        claim=claim,
        status=status,
        summary=_summarize(status, tir_runs, tir_agg, grade_verdict, grade_margin),
        tir_runs=tir_runs,
        grade_samples=grade_samples,
        grade_verdict=grade_verdict,
        grade_margin=grade_margin,
        model=model,
        base_url=provider.base_url,
        tir_k=max(0, tir_k),
        grade_k=max(0, grade_k),
        meta_model=meta_model if meta_model and meta_model != model else None,
        tokens_in_total=sum(s.tokens_in for s in all_samples),
        tokens_out_total=sum(s.tokens_out for s in all_samples),
        elapsed_ms_total=int((time.monotonic() - t0) * 1000),
    )


def check_result_to_dict(r: CheckResult) -> dict:
    """JSON-friendly serialization; code/stdout/reasoning kept as audit trail."""
    n_true, n_false, abstain, errors = 0, 0, 0, 0
    if r.grade_samples:
        _, _, n_true, n_false, abstain, errors = _tally_grades(r.grade_samples)
    return {
        "kind": "check",
        "claim": r.claim,
        "status": r.status,
        "summary": r.summary,
        "model": r.model,
        "meta_model": r.meta_model,
        "base_url": r.base_url,
        "tir_k": r.tir_k,
        "grade_k": r.grade_k,
        "tokens_in_total": r.tokens_in_total,
        "tokens_out_total": r.tokens_out_total,
        "elapsed_ms_total": r.elapsed_ms_total,
        "tir": [
            {
                "verdict": run.verdict,
                "note": run.note,
                "exit_code": run.exit_code,
                "timed_out": run.timed_out,
                "exec_elapsed_ms": run.exec_elapsed_ms,
                "code": run.code,
                "stdout": run.stdout,
                "stderr": run.stderr,
                "gen": sample_to_dict(run.gen),
            }
            for run in r.tir_runs
        ],
        "grade": None
        if r.grade_verdict is None
        else {
            "verdict": r.grade_verdict,
            "margin": r.grade_margin,
            "true": n_true,
            "false": n_false,
            "abstain": abstain,
            "errors": errors,
            "samples": [sample_to_dict(s) for s in r.grade_samples],
        },
    }
