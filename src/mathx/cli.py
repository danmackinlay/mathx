"""mathx CLI: solve/check/argue, submit/status/jobs, show/ledger, doctor, mcp-serve."""
from __future__ import annotations

import asyncio
import json
import shutil
import sys
import tomllib
from pathlib import Path

import click

from mathx import jobs, ledger
from mathx.argue import argue, expand_claim
from mathx.check import check, check_result_to_dict
from mathx.engine import Sample, result_to_dict, solve
from mathx.report import (
    render_check_report,
    render_check_script,
    render_ledger,
    render_report,
    render_sample,
)

STRATEGIES = ["cot", "maj@k", "self_verify"]

# Options every provider-talking command shares (`solve`, `submit`, `check`).
_ENDPOINT_OPTIONS = [
    click.option(
        "--model",
        required=True,
        envvar="MATHX_MODEL",
        help='e.g. "deepseek/deepseek-v4-pro"; or set $MATHX_MODEL',
    ),
    click.option(
        "--base-url",
        required=True,
        envvar="MATHX_BASE_URL",
        help='e.g. "https://api.featherless.ai/v1"; or set $MATHX_BASE_URL',
    ),
    click.option(
        "--api-key",
        required=True,
        envvar=("MATHX_API_KEY", "OPENAI_API_KEY"),
        help="API key; or set $MATHX_API_KEY (preferred) or $OPENAI_API_KEY",
    ),
    click.option(
        "--temperature",
        type=float,
        default=None,
        help="default: 0.0 for cot, 0.7 otherwise",
    ),
    click.option("--max-tokens", type=int, default=16000, show_default=True),
]

# Solving-specific options (`solve`, `submit` without --check).
_SOLVE_OPTIONS = [
    click.option(
        "--strategy",
        type=click.Choice(STRATEGIES),
        default="maj@k",
        show_default=True,
    ),
    click.option("--k", type=int, default=16, show_default=True),
    click.option(
        "--max-k",
        type=int,
        default=None,
        help="auto-escalate: while the winner holds no strict majority of the vote, "
        "double k and re-vote, up to this many samples total",
    ),
]

# Claim-checking options (`check`, `submit --check`).
_CHECK_OPTIONS = [
    click.option(
        "--tir-k",
        type=int,
        default=1,
        show_default=True,
        help="checker scripts to generate and execute (0 switches the tir lane off)",
    ),
    click.option(
        "--grade-k",
        type=int,
        default=8,
        show_default=True,
        help="TRUE/FALSE grader samples to vote (0 switches the grade lane off)",
    ),
    click.option(
        "--exec-timeout",
        type=float,
        default=60.0,
        show_default=True,
        help="seconds each checker script may run",
    ),
]


def _apply(options):
    def deco(f):
        for option in reversed(options):
            f = option(f)
        return f

    return deco


endpoint_options = _apply(_ENDPOINT_OPTIONS)
provider_options = _apply(_SOLVE_OPTIONS + _ENDPOINT_OPTIONS)
check_options = _apply(_CHECK_OPTIONS)


@click.group()
@click.version_option(package_name="mathx")
def cli() -> None:
    """A maths oracle for AI agents."""


@cli.command(name="solve")
@click.argument("problem")
@provider_options
@click.option(
    "--progress/--no-progress",
    default=None,
    help="stream per-sample progress to stderr [default: on when stderr is a TTY]",
)
@click.option(
    "--out",
    type=click.Path(dir_okay=False, path_type=Path),
    default=None,
    help="write full JSON to this path (audit trail)",
)
def solve_cmd(
    problem: str,
    strategy: str,
    k: int,
    model: str,
    base_url: str,
    api_key: str,
    temperature: float | None,
    max_tokens: int,
    max_k: int | None,
    progress: bool | None,
    out: Path | None,
) -> None:
    """Fan out k samples and vote on the answer."""
    on_sample = on_escalate = None
    show_progress = progress if progress is not None else sys.stderr.isatty()
    if show_progress:

        def on_sample(s: Sample, done: int, planned: int) -> None:
            if s.error is not None:
                status = f"error: {s.error}"
            elif s.boxed is None:
                status = "no \\boxed{...} answer"
            else:
                status = s.boxed
            if len(status) > 60:
                status = status[:59] + "…"
            click.echo(f"[{done}/{planned}] {s.elapsed_ms / 1000:.1f}s  {status}", err=True)

        def on_escalate(margin: str, new_planned: int) -> None:
            click.echo(f"margin {margin} is weak — escalating to k={new_planned}", err=True)

    result = asyncio.run(
        solve(
            problem,
            model=model,
            base_url=base_url,
            api_key=api_key,
            k=k,
            strategy=strategy,  # type: ignore[arg-type]
            temperature=temperature,
            max_tokens=max_tokens,
            max_k=max_k,
            on_sample=on_sample,
            on_escalate=on_escalate,
        )
    )

    if out is not None:
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(result_to_dict(result), indent=2))

    click.echo(f"answer: {result.answer}")
    meta = (
        f"margin: {result.margin}   strategy: {result.strategy}   "
        f"model: {result.model}   k: {result.k}"
    )
    if result.escalations:
        meta += f"   escalations: {result.escalations}"
    click.echo(meta)
    click.echo(
        f"tokens: in={result.tokens_in_total} out={result.tokens_out_total}   "
        f"elapsed: {result.elapsed_ms_total} ms"
    )
    if len(result.votes) > 1:
        click.echo("vote split (weight, answer):")
        for rep, w in result.votes.items():
            click.echo(f"  {w:>6.2f}  {rep}")
    if out is not None:
        click.echo(f"json -> {out}", err=True)
    if result.answer is None:
        sys.exit(1)


@cli.command(name="check")
@click.argument("claim")
@endpoint_options
@check_options
@click.option(
    "--out",
    type=click.Path(dir_okay=False, path_type=Path),
    default=None,
    help="write full JSON to this path (audit trail)",
)
def check_cmd(
    claim: str,
    model: str,
    base_url: str,
    api_key: str,
    temperature: float | None,
    max_tokens: int,
    tir_k: int,
    grade_k: int,
    exec_timeout: float,
    out: Path | None,
) -> None:
    """Check a claim: verdict + evidence, never proof.

    Two lanes — tir (a model writes a sympy verification script; mathx executes
    it in a local subprocess) and grade (a TRUE/FALSE vote of k samples).
    Exit code: 0 supported, 1 refuted, 2 conflict or unclear.
    """
    try:
        result = asyncio.run(
            check(
                claim,
                model=model,
                base_url=base_url,
                api_key=api_key,
                tir_k=tir_k,
                grade_k=grade_k,
                temperature=temperature,
                max_tokens=max_tokens,
                exec_timeout_s=exec_timeout,
            )
        )
    except ValueError as e:
        raise click.ClickException(str(e)) from e

    if out is not None:
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(check_result_to_dict(result), indent=2))

    click.echo(f"claim: {claim}")
    click.echo(f"status: {result.summary}")
    for i, run in enumerate(result.tir_runs):
        note = f" — {run.note}" if run.note else ""
        timing = "timed out" if run.timed_out else f"{run.exec_elapsed_ms} ms"
        click.echo(f"tir[{i}]: {run.verdict}   ({timing}){note}")
    if result.grade_verdict is not None:
        click.echo(f"grade: {result.grade_verdict} {result.grade_margin}")
    click.echo(
        f"tokens: in={result.tokens_in_total} out={result.tokens_out_total}   "
        f"elapsed: {result.elapsed_ms_total} ms"
    )
    if out is not None:
        click.echo(f"json -> {out}", err=True)
    if result.status == "refuted":
        sys.exit(1)
    if result.status in ("conflict", "unclear"):
        sys.exit(2)


@cli.command(name="argue")
@click.argument("problem")
@endpoint_options
@click.option(
    "--rounds",
    type=int,
    default=2,
    show_default=True,
    help="max refine cycles after the initial decomposition",
)
@click.option("--tir-k", type=int, default=1, show_default=True,
              help="checker scripts per claim (0 = lane off)")
@click.option("--grade-k", type=int, default=4, show_default=True,
              help="TRUE/FALSE graders per claim (0 = lane off)")
@click.option("--exec-timeout", type=float, default=60.0, show_default=True,
              help="seconds each checker script may run")
def argue_cmd(
    problem: str,
    model: str,
    base_url: str,
    api_key: str,
    temperature: float | None,
    max_tokens: int,
    rounds: int,
    tir_k: int,
    grade_k: int,
    exec_timeout: float,
) -> None:
    """Decompose–check–refine: build an argument with a checked claim ledger.

    Decomposes the problem into self-contained claims, checks each via a
    background check job, and refines the argument from failed verdicts, up to
    --rounds times. Prints the ledger id first, then the assembled ledger.
    Exit code: 0 if every claim ends supported, 2 otherwise.
    """
    try:
        led = asyncio.run(
            argue(
                problem,
                model=model,
                base_url=base_url,
                api_key=api_key,
                rounds=rounds,
                tir_k=tir_k,
                grade_k=grade_k,
                temperature=temperature,
                max_tokens=max_tokens,
                exec_timeout_s=exec_timeout,
                on_event=lambda msg: click.echo(msg, err=True),
            )
        )
    except (RuntimeError, ValueError) as e:
        raise click.ClickException(str(e)) from e
    click.echo(led["ledger_id"])
    click.echo(render_ledger(led, ledger.claim_state))
    active = [c for c in led["claims"] if c["retired_round"] is None]
    if any(ledger.claim_state(c)[0] != "supported" for c in active):
        sys.exit(2)


@cli.command(name="submit")
@click.argument("problem", metavar="PROBLEM_OR_CLAIM")
@provider_options
@check_options
@click.option(
    "--check",
    "as_check",
    is_flag=True,
    help="treat the argument as a CLAIM and run `mathx check` in the background "
    "(--tir-k/--grade-k/--exec-timeout apply; --strategy/--k/--max-k don't)",
)
def submit_cmd(
    problem: str,
    strategy: str,
    k: int,
    model: str,
    base_url: str,
    api_key: str,
    temperature: float | None,
    max_tokens: int,
    max_k: int | None,
    tir_k: int,
    grade_k: int,
    exec_timeout: float,
    as_check: bool,
) -> None:
    """Dispatch a solve (or, with --check, a claim check) in the background.

    Prints the job id and returns at once; the work runs in a detached worker
    that outlives this command. Poll with `mathx status <job_id>`; when
    complete, `mathx show <job_id>` renders it.
    """
    if as_check:
        args = {
            "claim": problem,
            "tir_k": tir_k,
            "grade_k": grade_k,
            "exec_timeout_s": exec_timeout,
            "model": model,
            "base_url": base_url,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        record = jobs.submit(kind="check", args=args)
    else:
        args = {
            "problem": problem,
            "strategy": strategy,
            "k": k,
            "model": model,
            "base_url": base_url,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "max_k": max_k,
        }
        record = jobs.submit(kind="solve", args=args)
    jobs.spawn_worker(record["job_id"], api_key=api_key)
    click.echo(record["job_id"])
    click.echo(
        f"poll: mathx status {record['job_id']}   report when done: mathx show {record['job_id']}",
        err=True,
    )


@cli.command(name="status")
@click.argument("job_id")
@click.option("--json", "as_json", is_flag=True, help="print the raw job record")
def status_cmd(job_id: str, as_json: bool) -> None:
    """Check a background job. Exit code: 0 complete, 2 still running, 3 job errored."""
    try:
        record = jobs.check(job_id)
    except KeyError as e:
        raise click.ClickException(str(e)) from e
    if as_json:
        click.echo(json.dumps(record, indent=2))
    status = record.get("status")
    args = record.get("args", {})
    subject = args.get("problem") or args.get("claim") or ""
    if status == "running":
        if not as_json:
            click.echo(f"job {job_id}: running   elapsed: {record.get('elapsed_ms', 0) / 1000:.0f} s")
            click.echo(f"{record.get('kind', 'solve')}: {subject}")
        sys.exit(2)
    if status == "error":
        if not as_json:
            click.echo(f"job {job_id}: error")
            click.echo(record.get("error", "(no error recorded)"))
        sys.exit(3)
    if not as_json:
        result = record.get("result") or {}
        click.echo(f"job {job_id}: complete")
        if result.get("kind") == "check":
            click.echo(f"status: {result.get('summary') or result.get('status')}")
        else:
            click.echo(
                f"answer: {result.get('answer')}   margin: {result.get('margin')}   "
                f"k: {result.get('k')}"
            )
        click.echo(f"full report: mathx show {job_id}", err=True)


@cli.command(name="jobs")
@click.option("--json", "as_json", is_flag=True, help="print raw job records")
@click.option(
    "--prune",
    type=float,
    default=None,
    metavar="HOURS",
    help="first delete records older than HOURS (by finish time; by start time "
    "for never-finished orphans)",
)
def jobs_cmd(as_json: bool, prune: float | None) -> None:
    """List background jobs, newest first."""
    if prune is not None:
        click.echo(f"pruned {jobs.prune(hours=prune)} job(s)", err=True)
    records = jobs.list_jobs()
    if as_json:
        click.echo(json.dumps(records, indent=2))
        return
    if not records:
        click.echo("no jobs yet (dispatch one with `mathx submit`)")
        return
    for r in records:
        result = r.get("result") or {}
        if r.get("status") == "complete":
            if result.get("kind") == "check":
                margin = (result.get("grade") or {}).get("margin")
                outcome = result.get("status", "") + (f" ({margin})" if margin else "")
            else:
                outcome = f"{result.get('answer')} ({result.get('margin')})"
        elif r.get("status") == "error":
            outcome = r.get("error") or ""
        else:
            outcome = ""
        outcome = " ".join(outcome.split())
        if len(outcome) > 24:
            outcome = outcome[:23] + "…"
        args = r.get("args", {})
        subject = " ".join(str(args.get("problem") or args.get("claim") or "").split())
        if len(subject) > 40:
            subject = subject[:39] + "…"
        started = (r.get("started_at") or "")[:19]
        click.echo(f"{r['job_id']}  {r.get('status', '?'):<8}  {started}  {outcome:<24}  {subject}")


@cli.command(name="show")
@click.argument("run")
@click.option(
    "--sample",
    "sample_index",
    type=int,
    default=None,
    metavar="N",
    help="print sample N's full reasoning (for check records: grader sample N)",
)
@click.option(
    "--script",
    "script_index",
    type=int,
    default=None,
    metavar="N",
    help="check records only: print checker script N's code and output",
)
def show_cmd(run: str, sample_index: int | None, script_index: int | None) -> None:
    """Render an audit record — a JSON file from `--out`, a job id, or a ledger id."""
    path = Path(run)
    if path.is_file():
        try:
            record = json.loads(path.read_text())
        except json.JSONDecodeError as e:
            raise click.ClickException(f"{run} is not valid JSON: {e}") from e
        if not isinstance(record, dict):
            raise click.ClickException(f"{run} is not a mathx run record")
    else:
        try:
            record = jobs.check(run)
        except KeyError:
            try:
                record = ledger.read(run)
            except KeyError:
                raise click.ClickException(f"no such file, job id, or ledger id: {run}") from None
        if record.get("status") == "running":
            raise click.ClickException(
                f"job {run} is still running "
                f"({record.get('elapsed_ms', 0) / 1000:.0f} s elapsed) — poll with `mathx status {run}`"
            )
        if record.get("status") == "error":
            raise click.ClickException(f"job {run} errored: {record.get('error')}")
    # a job record wraps the run under "result"; a --out file IS the run
    run_dict = record.get("result") if "result" in record else record
    kind = run_dict.get("kind") or ("check" if "claim" in run_dict else "solve")
    try:
        if kind == "ledger":
            if sample_index is not None or script_index is not None:
                raise click.ClickException(
                    "--sample/--script don't apply to ledgers; use them on a claim's job id"
                )
            text = render_ledger(run_dict, ledger.claim_state)
        elif kind == "check":
            if script_index is not None:
                text = render_check_script(run_dict, script_index)
            elif sample_index is not None:
                grade = run_dict.get("grade") or {}
                text = render_sample({"samples": grade.get("samples") or []}, sample_index)
            else:
                text = render_check_report(run_dict)
        else:
            if script_index is not None:
                raise click.ClickException("--script only applies to check records")
            text = (
                render_report(run_dict)
                if sample_index is None
                else render_sample(run_dict, sample_index)
            )
    except IndexError as e:
        raise click.ClickException(str(e)) from e
    click.echo(text)


@cli.group(name="ledger", invoke_without_command=True)
@click.pass_context
def ledger_group(ctx: click.Context) -> None:
    """List claim ledgers, or act on one (recheck / challenge / expand)."""
    if ctx.invoked_subcommand is not None:
        return
    records = ledger.list_ledgers()
    if not records:
        click.echo("no ledgers yet (build one with `mathx argue`)")
        return
    for led in records:
        counts = ledger.state_counts(led)
        summary = " ".join(f"{n}{_STATE_ABBREV.get(s, s[:1])}" for s, n in sorted(counts.items()))
        problem = " ".join(str(led.get("problem", "")).split())
        if len(problem) > 40:
            problem = problem[:39] + "…"
        updated = (led.get("updated_at") or "")[:19]
        click.echo(
            f"{led['ledger_id']}  {led.get('status', '?'):<9}  {updated}  {summary:<20}  {problem}"
        )


_STATE_ABBREV = {
    "supported": "✓", "refuted": "✗", "unclear": "?", "conflict": "!",
    "error": "!", "checking": "…", "unchecked": "·", "retired": "–", "missing": "?",
}


def _load_ledger_claim(ledger_id: str, claim_id: str) -> tuple[dict, dict]:
    try:
        led = ledger.read(ledger_id)
        return led, ledger.get_claim(led, claim_id)
    except KeyError as e:
        raise click.ClickException(str(e)) from e


@ledger_group.command(name="recheck")
@click.argument("ledger_id")
@click.argument("claim_id")
@endpoint_options
@check_options
def recheck_cmd(
    ledger_id: str, claim_id: str, model: str, base_url: str, api_key: str,
    temperature: float | None, max_tokens: int, tir_k: int, grade_k: int, exec_timeout: float,
) -> None:
    """Re-check one claim (e.g. at higher --grade-k); badges refresh on next render."""
    led, claim = _load_ledger_claim(ledger_id, claim_id)
    job_id = ledger.attach_check(
        led, claim, api_key=api_key, round_=led["rounds_used"], kind="recheck",
        model=model, base_url=base_url, tir_k=tir_k, grade_k=grade_k,
        exec_timeout_s=exec_timeout, temperature=temperature, max_tokens=max_tokens,
    )
    click.echo(job_id)
    click.echo(f"rechecking {claim_id}; render with: mathx show {ledger_id}", err=True)


@ledger_group.command(name="challenge")
@click.argument("ledger_id")
@click.argument("claim_id")
@click.argument("objection")
@endpoint_options
@check_options
def challenge_cmd(
    ledger_id: str, claim_id: str, objection: str, model: str, base_url: str, api_key: str,
    temperature: float | None, max_tokens: int, tir_k: int, grade_k: int, exec_timeout: float,
) -> None:
    """Re-check one claim with a specific objection put to the checkers."""
    led, claim = _load_ledger_claim(ledger_id, claim_id)
    text = (
        f"{claim['text']}\n\nWhen judging this claim, specifically address the "
        f"following objection: {objection}"
    )
    job_id = ledger.attach_check(
        led, claim, api_key=api_key, round_=led["rounds_used"], kind="challenge",
        text_override=text, model=model, base_url=base_url, tir_k=tir_k, grade_k=grade_k,
        exec_timeout_s=exec_timeout, temperature=temperature, max_tokens=max_tokens,
    )
    click.echo(job_id)
    click.echo(f"challenging {claim_id}; render with: mathx show {ledger_id}", err=True)


@ledger_group.command(name="expand")
@click.argument("ledger_id")
@click.argument("claim_id")
@endpoint_options
@check_options
def expand_cmd(
    ledger_id: str, claim_id: str, model: str, base_url: str, api_key: str,
    temperature: float | None, max_tokens: int, tir_k: int, grade_k: int, exec_timeout: float,
) -> None:
    """Decompose one claim into sub-claims and check each of them."""
    led, claim = _load_ledger_claim(ledger_id, claim_id)
    try:
        children = asyncio.run(
            expand_claim(
                led, claim, api_key=api_key, model=model, base_url=base_url,
                tir_k=tir_k, grade_k=grade_k, temperature=temperature,
                max_tokens=max_tokens, exec_timeout_s=exec_timeout,
            )
        )
    except RuntimeError as e:
        raise click.ClickException(str(e)) from e
    for child in children:
        click.echo(f"{child['id']}  {child['text']}")
    click.echo(
        f"{len(children)} sub-claims checking; render with: mathx show {ledger_id}", err=True
    )


@cli.command(name="mcp-serve")
def mcp_serve_cmd() -> None:
    """Run the MCP server (stdio): submit_solve / check_solve over the job store."""
    from mathx.mcp_server import serve

    serve()


# The maths-oracle SKILL.md ships in this repo at skills/ (agent-neutral); install it
# with the cross-agent open-skills CLI: `npx skills add danmackinlay/mathx`
# (project-local by default, `-g` for global, `-a <agent>` to target one).
# `mathx doctor` below diagnoses a setup but installs nothing.

GIT_REPO = "git+https://github.com/danmackinlay/mathx"

# Where the open-skills CLI drops the skill, per agent (project + home roots).
_SKILL_SUBDIRS = (
    ".claude/skills",
    ".agents/skills",
    ".agent/skills",
    ".codex/skills",
    ".cursor/skills",
    ".gemini/skills",
    ".opencode/skills",
)
_SKIP_DIRS = {".venv", "venv", ".git", "node_modules", "__pycache__",
              "site-packages", ".tox", "dist", "build"}


def _find_up(start: Path, name: str) -> Path | None:
    """Return the nearest `name` in `start` or an ancestor, else None."""
    for d in (start, *start.parents):
        candidate = d / name
        if candidate.is_file():
            return candidate
    return None


def _dep_name(spec: str) -> str:
    """Package name from a PEP 508 dependency spec (no regex needed)."""
    s = spec.strip()
    for sep in (" ", "@", "[", "<", ">", "=", "!", "~", ";", "("):
        s = s.split(sep)[0]
    return s.strip().lower().replace("_", "-")


def _read_pyproject(path: Path) -> tuple[str, bool]:
    """Return (project name, whether mathx is a declared dependency)."""
    try:
        data = tomllib.loads(path.read_text())
    except (OSError, tomllib.TOMLDecodeError):
        return "", False
    proj = data.get("project", {})
    name = (proj.get("name") or "").lower()
    deps: list[str] = list(proj.get("dependencies", []))
    for grp in proj.get("optional-dependencies", {}).values():
        deps += grp
    for grp in data.get("dependency-groups", {}).values():  # PEP 735
        deps += [g for g in grp if isinstance(g, str)]
    return name, any(_dep_name(d) == "mathx" for d in deps)


def _project_imports_mathx(root: Path) -> bool:
    """Best-effort: does any .py under `root` import mathx (a library use)?"""
    try:
        for py in root.rglob("*.py"):
            if _SKIP_DIRS & set(py.parts):
                continue
            try:
                text = py.read_text(errors="ignore")
            except OSError:
                continue
            if "import mathx" in text or "from mathx" in text:
                return True
    except OSError:
        return False
    return False


def _find_skill() -> list[Path]:
    """Dirs where a maths-oracle SKILL.md is already installed (project + home)."""
    found: list[Path] = []
    for root in (Path.cwd(), Path.home()):
        for sub in _SKILL_SUBDIRS:
            d = root / sub / "maths-oracle"
            if (d / "SKILL.md").exists():
                found.append(d)
    return found


@cli.command(name="doctor")
def doctor_cmd() -> None:
    """Diagnose a mathx setup and print fixes. Changes nothing.

    Checks whether `mathx` is on PATH, recommends how to install it
    for the current project (a Python project that imports mathx wants it as a
    dependency; otherwise an isolated `uv tool install` / `uvx` is cleaner),
    and reports whether the maths-oracle skill is installed.
    """
    echo = click.echo

    mathx_path = shutil.which("mathx")
    if mathx_path:
        echo(f"✓ mathx on PATH: {mathx_path}")
    else:
        echo("✗ mathx: not found on PATH")

    pyproject = _find_up(Path.cwd(), "pyproject.toml")
    if pyproject is None:
        echo("• context: no pyproject.toml found — treat as a non-Python project")
        echo("  install mathx with (pick one):")
        echo(f"    uv tool install {GIT_REPO}")
        echo(f"    uvx --from {GIT_REPO} mathx solve …   # ephemeral, no install")
    else:
        name, has_dep = _read_pyproject(pyproject)
        root = pyproject.parent
        echo(f"• context: Python project '{name or root.name}' at {root}")
        if name == "mathx":
            echo("  this is the mathx repo itself:")
            echo("    uv tool install -e .                 # dev install on PATH")
        elif has_dep:
            echo("  mathx is already a declared dependency here. ✓")
        elif _project_imports_mathx(root):
            echo("  this project imports mathx as a library — add it as a dep:")
            echo(f"    uv add {GIT_REPO}")
        else:
            echo("  this project would shell out to `mathx` (doesn't import it).")
            echo("  prefer an isolated install over polluting project deps:")
            echo(f"    uv tool install {GIT_REPO}")
            echo(f"    uvx --from {GIT_REPO} mathx solve …   # ephemeral")

    skills = _find_skill()
    if skills:
        echo("✓ maths-oracle skill installed at:")
        for s in skills:
            echo(f"    {s}")
    else:
        echo("✗ maths-oracle skill: not found in this project or home dir")
        echo("  install it with the open-skills CLI:")
        echo("    npx skills add danmackinlay/mathx       # project-local (default)")
        echo("    npx skills add danmackinlay/mathx -g    # global")

    echo("")
    echo("note: mathx isn't on PyPI yet, so commands resolve via the git repo.")


def main() -> None:
    cli()


if __name__ == "__main__":
    main()
