# mathx → solver workstation (build record)

**Status:** all five stages and both cross-cutting tracks shipped (2026-07). The one
deliberately unbuilt piece is the ledger TUI/web view (Stage 5, gated). This is no longer a plan
to execute but the map of what was built and why — the staged shape it grew along, kept as the
index into the [design notes](DESIGN_NOTES.md) and the code, and the home of the invariants that
guard further work.

## Context

mathx began as a thin maj@k oracle: CLI + SKILL.md, MCP deferred (design notes: [DESIGN_NOTES.md](DESIGN_NOTES.md#mcp-server)). The goal was to grow it gradually into a **solver workstation** — the solver-side equivalent of [AxProverBase](https://github.com/Axiomatic-AI/ax-prover-base): a harness for interactively exploring mathematical arguments in human-readable form, with *soft* verification (TIR checks, claim-level self-grading, fan-out consistency) instead of a Lean compiler. Explicitly not the prover path — AxProverBase owns that end.

Background: [automatic_maths](https://danmackinlay.name/notebook/automatic_maths) and [ai_reasoning](https://danmackinlay.name/notebook/ai_reasoning).

## Why the pivot was cheap

Four properties of the thin oracle meant the workstation could be grown around it rather than bolted on:

1. `solve()` in [src/mathx/engine.py](src/mathx/engine.py) was already async (`asyncio.gather` over k samples).
2. Every run already serialized to a complete JSON audit record (`result_to_dict`: answer, margin, votes, per-sample traces).
3. The [MCP design notes](DESIGN_NOTES.md#mcp-server) had already worked out the async-handle pattern (submit/check + file-per-job store).
4. Crucially: `math_verify`-based equivalence checking in the engine's `_cluster` pass was already a *claim-checker primitive* — the oracle could become the inner call of a claim-level loop without changing identity.

The pivot that followed: grow the workstation **around a persistent job store**, not by bloating the engine. One-shot CLI calls became named, inspectable runs; every surface (CLI report, MCP `poll_job`, Open WebUI) is just a reader of the same files.

## The solver equivalent of AxProverBase

The mapping that guided the build — the right-hand column is what shipped:

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

## Invariants (pinned 2026-07-03 — drift guards)

Every stage must preserve these three. A proposed change that breaks one is the signal to stop and rethink — this is where pudding drifted.

1. **The model is a text-in/text-out sampler.** Everything beyond pure CoT — judge passes, grading, checker scripts, (later) literal TIR — is an optional lane/strategy selected per call. Pure-CoT models run every stage, degrading *visibly* (abstains, weaker margins), never silently. Regimes mix per invocation: solver, grader, and checker models may differ.
2. **The loop has three homes, all clients of the same primitives.** (a) A host agent driving CLI/MCP verbs (Claude Desktop/Code — works now, maximum exploratory flexibility); (b) the Stage-4 mathx verb; (c) `solve()`/`check()` imported into custom Python. Stage 4 is built ON submit/check/jobs, never around them — a loop that bypasses the primitives demotes the other two homes.
3. **Every unit of work is a persistent, self-describing record.** A run/verdict is a job file carrying inputs, evidence, and audit trail; nothing encodes who drove the loop. The Stage-4 claim ledger is therefore itself a file (claim tree → job ids), so a loop started in one home can be inspected, challenged, and resumed from another.

## The stages, as built

**Stage 1 — display what's already computed** (pure reader) — ✅ done
- `mathx show <run.json>`: vote histogram, margin, per-sample answers, disagreement surfacing (`src/mathx/report.py`).
- Live progress during solve (`--progress`, on by default on a TTY); auto-escalation on weak margin via `--max-k` (no strict majority → double k, re-vote over all samples, repeat up to the cap).

**Stage 2 — job store + async handles** (design: [DESIGN_NOTES.md](DESIGN_NOTES.md#mcp-server)) — ✅ done
- File-per-job store (`src/mathx/jobs.py`, `$MATHX_JOBS_DIR` / `~/.cache/mathx/jobs`) → `submit`/`status`/`jobs` CLI verbs; MCP server `mathx mcp-serve` (`submit_solve`/`poll_job`, `src/mathx/mcp_server.py`). Both submit paths detach the same worker (`python -m mathx.jobs <id>`), so jobs survive their submitter. Runs get identity and history — the substrate every later surface reads.

**Stage 3 — claim-checker primitive** (design: [DESIGN_NOTES.md](DESIGN_NOTES.md#claim-checker-mathx-check)) — ✅ done
- `mathx check "<claim>"` (`src/mathx/check.py`): two concurrent verdict lanes — `tir` (model writes a SymPy verification script; the `Executor` seam in `src/mathx/executor.py` runs it locally and parses VERDICT/COUNTEREXAMPLE from stdout) and `grade` (k-sample TRUE/FALSE vote). Status: supported / refuted / conflict / unclear; full audit trail (code, output, reasoning) in the record; `mathx submit --check` runs it through the Stage-2 job store; `mathx show` renders verdict records (`--script N` for checker code+output).
- 2026-07 survey result: literal multi-turn TIR is viable on existing endpoints (Featherless serves `/v1/completions`; OpenMath-Nemotron/Nemotron-Math are TIR-native, CC-BY-4.0) — deferred as the upgrade lane behind the same interface. Remote executors (E2B/Daytona/Modal) deferred behind the `Executor` seam.

**Stage 4 — the decompose–check–refine loop** (the actual AxProverBase-equivalent; design: [DESIGN_NOTES.md](DESIGN_NOTES.md#decompose-check-refine-loop-mathx-argue)) — ✅ done
- `mathx argue` (`src/mathx/argue.py`): decompose into self-contained claims (generalist call) → Stage-3 check job per claim via the Stage-2 store → refine from failed verdicts + scratchpad of refuted claims, up to `--rounds`. The claim ledger (`src/mathx/ledger.py`) is a persistent file (claim tree → job ids); state derived live from the job store; `mathx show <ledger_id>` renders badges.
- Interactive verbs shipped: `mathx ledger recheck` (higher k), `challenge` (objection in the prompt), `expand` (checked sub-claims, one tree level); all accept `--model` overrides (regime mixing).

**Stage 5 — the face** — ✅ done (TUI still gated)
- Open WebUI: the **Pipe is the primary integration**, not MCP (decided 2026-07-04; OWUI's chat-tool loop would put the served model in charge of polling a long handle, which specialists can't do). Shipped: `integrations/openwebui/mathx_pipe.py` — solve/check/argue as model-picker entries, loop in code, `on_event`/`on_sample` streamed to the status emitter, provider via profiles. MCP registration in OWUI remains a free extra.
- MCP surface for the agent-client family (Claude Desktop/Cursor/Copilot) — ✅ shipped in `mcp_server.py`: `submit_check`, `submit_argue` (+ pre-created ledger id; forced `kind: argue` jobs — see the [argue-loop decision log](DESIGN_NOTES.md#decompose-check-refine-loop-mathx-argue)), `poll_job` (né `check_solve`), `list_jobs`/`list_ledgers` (compact), `get_ledger` (live badges), `recheck_claim`/`challenge_claim`, `profile` on every submit.
- A ledger TUI/web view once the loop earns it — still gated, deliberately unbuilt.

**Cross-cutting prerequisite** — ✅ done: pytest suite in `tests/` with a mocked OpenAI-compatible endpoint (httpx `MockTransport` under the real openai client — full wire path, no network); covers the pure helpers, `solve()` incl. escalation, the report renderers, and both CLI verbs. `uv run pytest`.

**Cross-cutting: answer equivalence** (design: [DESIGN_NOTES.md](DESIGN_NOTES.md#answer-equivalence-cas-first-judge-fallback)) — ✅ done: the vote's core primitive is CAS-first (math-verify) with an opt-in labelled LLM judge-fallback for the residue (variable renaming, forms the CAS refuses). The survey favoured build-cribbing-prompts over adopting a trained judge; `--equiv-judge-model` / profile `equiv_judge_model`.

Every stage is independently useful, and the oracle never stops being the thin swappable thing — it just gets called per claim instead of per problem.
