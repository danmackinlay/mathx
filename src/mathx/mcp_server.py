"""MCP surface for agent clients (Claude Desktop, Cursor, …): handle/poll tools
over the shared job and ledger stores.

Every submit tool returns instantly with an id; ``poll_job`` (né ``check_solve``)
returns instantly with the record — no client tool-call timeout ever bites, and
any tool-capable MCP client works, no MCP-Tasks support required (MCP_PLAN.md).
No engine logic lives here; workers are the same detached subprocesses the CLI
uses, so jobs survive an MCP-server restart.

Provider config resolves exactly as the CLI does — profile > environment — via
``config.resolve_provider``; each submit tool takes an optional ``profile``
name. Keys are env-only and never written to disk.
"""
from __future__ import annotations

from typing import Literal

from mcp.server.fastmcp import FastMCP

from mathx import config, jobs, ledger

server = FastMCP(
    "mathx",
    instructions=(
        "A maths workstation: fan problems out to k samples and vote (submit_solve); "
        "check a single claim with a model-written sympy script plus a TRUE/FALSE vote "
        "(submit_check); or build a full argument with a claim ledger "
        "(submit_argue, then get_ledger for live per-claim verdict badges). All submits "
        "return a job id instantly — poll poll_job every 20-60s until status is "
        "'complete'. Margins and verdicts are evidence, not proof: surface close votes "
        "and conflicts instead of asserting. recheck_claim / challenge_claim escalate "
        "individual ledger claims."
    ),
)


def _provider(
    profile: str | None, **overrides
) -> tuple[config.ProviderConfig | None, dict | None]:
    """(provider, None) on success, (None, error-dict) on failure — resolved
    exactly once. Also exports a profile's concurrency cap for spawned workers."""
    try:
        p = config.resolve_provider(profile=profile, **overrides)
    except ValueError as e:
        return None, {"status": "error", "error": str(e)}
    config.export_concurrency(p)
    return p, None


def _handle(record: dict, **extra) -> dict:
    return {
        "job_id": record["job_id"],
        "status": record["status"],
        "started_at": record["started_at"],
        **extra,
    }


@server.tool()
def submit_solve(
    problem: str,
    strategy: Literal["cot", "maj@k", "self_verify"] = "maj@k",
    k: int = 16,
    max_k: int | None = None,
    profile: str | None = None,
    model: str | None = None,
    base_url: str | None = None,
) -> dict:
    """Fan a problem out to k samples and vote on the boxed answer. Returns a
    job handle immediately; poll with poll_job. max_k enables auto-escalation
    on weak votes. profile selects a mathx.toml profile (else environment)."""
    p, err = _provider(profile, model=model, base_url=base_url)
    if p is None:
        return err
    record = jobs.submit(
        kind="solve",
        args={
            "problem": problem,
            "strategy": strategy,
            "k": k,
            "max_k": max_k,
            "provider": p.to_args(),
        },
    )
    jobs.spawn_worker(record["job_id"], api_key=p.api_key)
    return _handle(record)


@server.tool()
def submit_check(
    claim: str,
    tir_k: int = 1,
    grade_k: int = 8,
    exec_timeout_s: float = 60.0,
    profile: str | None = None,
    model: str | None = None,
    base_url: str | None = None,
) -> dict:
    """Check one mathematical claim: a model writes a sympy verification script
    (executed locally) and grade_k samples vote TRUE/FALSE. The result carries
    supported/refuted/conflict/unclear with the full audit trail. Returns a job
    handle immediately; poll with poll_job."""
    p, err = _provider(profile, model=model, base_url=base_url)
    if p is None:
        return err
    record = jobs.submit(
        kind="check",
        args={
            "claim": claim,
            "tir_k": tir_k,
            "grade_k": grade_k,
            "exec_timeout_s": exec_timeout_s,
            "provider": p.to_args(),
        },
    )
    jobs.spawn_worker(record["job_id"], api_key=p.api_key)
    return _handle(record)


@server.tool()
def submit_argue(
    problem: str,
    rounds: int = 2,
    tir_k: int = 1,
    grade_k: int = 4,
    exec_timeout_s: float = 60.0,
    profile: str | None = None,
    model: str | None = None,
    base_url: str | None = None,
) -> dict:
    """Decompose–check–refine: build an argument whose every claim gets a
    verdict badge. Returns a job handle AND a ledger_id immediately; the ledger
    file updates live as claims are checked — watch it with get_ledger while
    the job runs."""
    p, err = _provider(profile, model=model, base_url=base_url)
    if p is None:
        return err
    led = ledger.create(problem, model=p.model, base_url=p.base_url, rounds_max=rounds)
    record = jobs.submit(
        kind="argue",
        args={
            "problem": problem,
            "rounds": rounds,
            "tir_k": tir_k,
            "grade_k": grade_k,
            "exec_timeout_s": exec_timeout_s,
            "ledger_id": led["ledger_id"],
            "provider": p.to_args(),
        },
    )
    jobs.spawn_worker(record["job_id"], api_key=p.api_key)
    return _handle(record, ledger_id=led["ledger_id"])


@server.tool()
def poll_job(job_id: str) -> dict:
    """Poll a submitted job. status 'running' comes with elapsed_ms; 'complete'
    with the result (full audit trail); 'error' with the error."""
    try:
        return jobs.check(job_id)
    except KeyError:
        return {"job_id": job_id, "status": "error", "error": "unknown job_id"}


@server.tool()
def list_jobs(limit: int = 20) -> list[dict]:
    """Recent jobs, newest first — compact (no sample texts); poll_job a
    specific id for the full record."""
    out = []
    for r in jobs.list_jobs()[: max(1, limit)]:
        result = r.get("result") or {}
        args = r.get("args", {})
        out.append(
            {
                "job_id": r["job_id"],
                "kind": r.get("kind", "solve"),
                "status": r.get("status"),
                "started_at": r.get("started_at"),
                "subject": (args.get("problem") or args.get("claim") or "")[:120],
                "outcome": result.get("answer") or result.get("status") or r.get("error"),
            }
        )
    return out


def _claims_view(led: dict) -> list[dict]:
    view = []
    for claim in led.get("claims", []):
        state, detail = ledger.claim_state(claim)
        view.append(
            {
                "id": claim["id"],
                "text": claim["text"],
                "parent": claim.get("parent"),
                "state": state,
                "detail": detail,
                "job_id": (claim.get("verdicts") or [{}])[-1].get("job_id"),
            }
        )
    return view


@server.tool()
def get_ledger(ledger_id: str) -> dict:
    """A claim ledger with live per-claim verdict state (derived from the job
    store — safe to call while an argue job is still running)."""
    try:
        led = ledger.read(ledger_id)
    except KeyError:
        return {"ledger_id": ledger_id, "status": "error", "error": "unknown ledger_id"}
    return {
        "ledger_id": led["ledger_id"],
        "status": led["status"],
        "problem": led["problem"],
        "argument": led["argument"],
        "rounds_used": led["rounds_used"],
        "rounds_max": led["rounds_max"],
        "claims": _claims_view(led),
        "scratchpad": led.get("scratchpad", []),
    }


@server.tool()
def list_ledgers(limit: int = 20) -> list[dict]:
    """Recent claim ledgers, newest first — compact."""
    out = []
    for led in ledger.list_ledgers()[: max(1, limit)]:
        out.append(
            {
                "ledger_id": led["ledger_id"],
                "status": led.get("status"),
                "updated_at": led.get("updated_at"),
                "problem": (led.get("problem") or "")[:120],
                "claims": ledger.state_counts(led),
            }
        )
    return out


def _attach(ledger_id: str, claim_id: str, *, kind: str, objection: str | None,
            tir_k: int, grade_k: int, profile: str | None) -> dict:
    p, err = _provider(profile, require=("api_key",))
    if p is None:
        return err
    try:
        led = ledger.read(ledger_id)
        claim = ledger.get_claim(led, claim_id)
    except KeyError as e:
        return {"status": "error", "error": str(e)}
    text = ledger.challenge_text(claim, objection) if objection else None
    job_id = ledger.attach_check(
        led, claim, provider=p, round_=led["rounds_used"], kind=kind,
        text_override=text, tir_k=tir_k, grade_k=grade_k,
    )
    return {"job_id": job_id, "ledger_id": ledger_id, "claim_id": claim_id, "status": "running"}


@server.tool()
def recheck_claim(
    ledger_id: str, claim_id: str, tir_k: int = 1, grade_k: int = 8,
    profile: str | None = None,
) -> dict:
    """Re-check one ledger claim (e.g. at higher grade_k). Returns a job handle;
    the claim's badge refreshes once the job completes."""
    return _attach(ledger_id, claim_id, kind="recheck", objection=None,
                   tir_k=tir_k, grade_k=grade_k, profile=profile)


@server.tool()
def challenge_claim(
    ledger_id: str, claim_id: str, objection: str, tir_k: int = 1, grade_k: int = 8,
    profile: str | None = None,
) -> dict:
    """Re-check one ledger claim with a specific objection put to the checkers
    (e.g. an edge case the claim glosses over)."""
    return _attach(ledger_id, claim_id, kind="challenge", objection=objection,
                   tir_k=tir_k, grade_k=grade_k, profile=profile)


def serve() -> None:
    server.run()  # stdio transport
