# Roadmap: mathx → solver workstation

## Context

mathx today is a thin maj@k oracle: CLI + SKILL.md, MCP deferred (see [MCP_PLAN.md](MCP_PLAN.md)). The goal is to grow it gradually into a **solver workstation** — the solver-side equivalent of [AxProverBase](https://github.com/Axiomatic-AI/ax-prover-base): a harness for interactively exploring mathematical arguments in human-readable form, with *soft* verification (TIR checks, claim-level self-grading, fan-out consistency) instead of a Lean compiler. Explicitly not the prover path — AxProverBase owns that end.

Background: [automatic_maths](https://danmackinlay.name/notebook/automatic_maths) and [ai_reasoning](https://danmackinlay.name/notebook/ai_reasoning).

## Why the architecture already supports this

1. `solve()` in [src/mathx/engine.py](src/mathx/engine.py) is already async (`asyncio.gather` over k samples).
2. Every run already serializes to a complete JSON audit record (`result_to_dict`: answer, margin, votes, per-sample traces).
3. [MCP_PLAN.md](MCP_PLAN.md) already designs the async-handle pattern (submit/check + file-per-job store).
4. Crucially: `math_verify`-based equivalence checking in `_cluster_and_vote` is already a *claim-checker primitive* — the oracle can become the inner call of a claim-level loop without changing identity.

Strategic pivot: grow the workstation **around a persistent job store**, not by bloating the engine. One-shot CLI calls become named, inspectable runs; every surface (CLI report, MCP `check_solve`, Open WebUI) is just a reader of the same files.

## The solver equivalent of AxProverBase (design sketch)

| AxProverBase (prover) | Solver-side analogue |
|---|---|
| Target: Lean proof | Structured natural-language argument: claims + justifications |
| Verifier: Lean compiler (exact) | Verdict stack: TIR (SymPy symbolic check, random-instance numeric testing), claim-level self-grading (DeepSeekMath-V2 / VibeThinker CLRA style), maj@k consistency (mathx today) |
| Feedback: compiler error trace | Feedback: the counterexample, the low-confidence claim, the vote split |
| Memory: scratchpad of failed lemmas/tactics | Scratchpad of refuted claims and failed approaches across attempts |
| Mathlib search | Reference retrieval (defer; start with nothing) |
| Review: sorry-check + LLM reviewer | Review: every-claim-has-a-verdict check + LLM reviewer for weakened statements |
| Proof state: Lean goal panel | **Claim ledger**: claim tree with verdict badges — the interactive workstation surface |

Honesty constraint: verdicts are evidence, not certainty. The display must carry epistemic status ("checked on 200 random instances" ≠ "proven"). Human stays the judge; the harness organizes evidence. That's the feature, not the limitation.

## Staged roadmap

**Stage 1 — display what's already computed** (pure reader) — ✅ done
- `mathx show <run.json>`: vote histogram, margin, per-sample answers, disagreement surfacing (`src/mathx/report.py`).
- Live progress during solve (`--progress`, on by default on a TTY); auto-escalation on weak margin via `--max-k` (no strict majority → double k, re-vote over all samples, repeat up to the cap).

**Stage 2 — job store + async handles** (implement [MCP_PLAN.md](MCP_PLAN.md)) — ✅ done
- File-per-job store (`src/mathx/jobs.py`, `$MATHX_JOBS_DIR` / `~/.cache/mathx/jobs`) → `submit`/`status`/`jobs` CLI verbs; MCP server `mathx mcp-serve` (`submit_solve`/`check_solve`, `src/mathx/mcp_server.py`). Both submit paths detach the same worker (`python -m mathx.jobs <id>`), so jobs survive their submitter. Runs get identity and history — the substrate every later surface reads.

**Stage 3 — claim-checker primitive** (design: [CHECK_PLAN.md](CHECK_PLAN.md)) — ✅ done
- `mathx check "<claim>"` (`src/mathx/check.py`): two concurrent verdict lanes — `tir` (model writes a SymPy verification script; the `Executor` seam in `src/mathx/executor.py` runs it locally and parses VERDICT/COUNTEREXAMPLE from stdout) and `grade` (k-sample TRUE/FALSE vote). Status: supported / refuted / conflict / unclear; full audit trail (code, output, reasoning) in the record; `mathx submit --check` runs it through the Stage-2 job store; `mathx show` renders verdict records (`--script N` for checker code+output).
- 2026-07 survey result: literal multi-turn TIR is viable on existing endpoints (Featherless serves `/v1/completions`; OpenMath-Nemotron/Nemotron-Math are TIR-native, CC-BY-4.0) — deferred as the upgrade lane behind the same interface. Remote executors (E2B/Daytona/Modal) deferred behind the `Executor` seam.

**Stage 4 — the decompose–check–refine loop** (the actual AxProverBase-equivalent)
- Plan: decompose problem into claims (generalist call). Check: Stage-3 primitive per claim, fanned out via Stage-2 jobs. Refine: failed verdicts + memory scratchpad spliced back. Assemble: human-readable argument + claim ledger with per-claim verdict badges.
- Interactive verbs on the ledger: expand a claim, challenge it, re-check at higher k.

**Stage 5 — the face**
- MCP registration in Open WebUI (free after Stage 2); optionally a Pipeline that owns the loop and streams claim-ledger progress. A ledger TUI/web view once the loop earns it.

**Cross-cutting prerequisite** — ✅ done: pytest suite in `tests/` with a mocked OpenAI-compatible endpoint (httpx `MockTransport` under the real openai client — full wire path, no network); covers the pure helpers, `solve()` incl. escalation, the report renderers, and both CLI verbs. `uv run pytest`.

Every stage is independently useful, and the oracle never stops being the thin swappable thing — it just gets called per claim instead of per problem.
