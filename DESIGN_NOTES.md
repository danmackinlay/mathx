# Design notes (shipped)

Pre-build design notes for mathx's major subsystems, kept as an archive now that the work has
shipped. Each section records the load-bearing decisions, the survey/reference material the
build deferred, and a decision log. Current-state docs live in the [README](README.md) and the
[ROADMAP](ROADMAP.md) — this file is rationale and history, not a live plan.

- [MCP server](#mcp-server) — the handle/poll async decision and per-client wiring
- [Claim checker (`mathx check`)](#claim-checker-mathx-check) — the TIR-lane survey and the executor seam
- [Decompose-check-refine loop (`mathx argue`)](#decompose-check-refine-loop-mathx-argue) — loop policy and the ledger record
- [Answer equivalence (CAS-first, judge-fallback)](#answer-equivalence-cas-first-judge-fallback) — the vote's clustering primitive

---

## MCP server

`mathx mcp-serve` (stdio) shipped as ROADMAP Stage 2 and grew to the full agent-client surface
in Stage 5: `submit_solve` / `submit_check` / `submit_argue` / `poll_job` / `list_jobs` /
`list_ledgers` / `get_ledger` / `recheck_claim` / `challenge_claim`, all over the Stage-2 job
store in [`src/mathx/jobs.py`](src/mathx/jobs.py). Every tool is a thin lifecycle wrapper over
the existing engine — no maths logic lives in [`src/mathx/mcp_server.py`](src/mathx/mcp_server.py).

This section keeps the two things that outlived the build — the load-bearing async decision and
the per-client wiring — plus the decision log. The rest of the pre-build sketch (tool
signatures, job-store layout, worker coroutine-vs-subprocess) is now just the code and the log.

### Why handle/poll, not MCP Tasks

The decision that matters — getting it wrong ships a server that times out in every client. The
four async-in-MCP routes, surveyed mid-2026:

1. **Own the loop → `asyncio`.** The laptop path: `import mathx.engine.solve` and `await` it.
   Not applicable to a server — we own the server, not the caller's loop.
2. **Application-level handle/poll, two ordinary tools.** `submit_*(...)` returns a job id
   instantly; `poll_job(id)` returns status/result instantly. Each call returns fast, so the
   synchronous-tool cap never bites; the agent polls. Works in *any* tool-capable MCP client
   today, no Tasks support required
   ([dev.to/aws](https://dev.to/aws/fix-mcp-timeouts-async-handleid-pattern-8ek)).
3. **Harness-native background async.** Claude Code's `run_in_background`, sub-agents, etc.
   Per-client; not portable; not something an MCP server can rely on.
4. **MCP Tasks (protocol-level).** The
   [2025-11-25 spec](https://modelcontextprotocol.io/specification/2025-11-25/basic/utilities/tasks)
   (cf. SEP-1391) standardises handle/poll into the protocol. Nice when both ends implement it;
   not required; unevenly supported.

**mathx is route #2.** It works in every MCP-capable client regardless of Tasks support, and
sidesteps the synchronous tool-call timeout — e.g. Claude Code's un-raisable ~60 s cap (open
requests [#47076](https://github.com/anthropics/claude-code/issues/47076),
[#22542](https://github.com/anthropics/claude-code/issues/22542)). Both submit and poll return
instantly, so the cap is irrelevant, and MCP Tasks can layer on later — the submit/poll tools
already model the same lifecycle. **Do not** collapse this to a single synchronous `solve`
tool: it times out on every wide fan-out in every client with a tool-call cap.

### Per-client wiring

Assume `MATHX_MODEL`, `MATHX_BASE_URL`, `MATHX_API_KEY` are set in the parent shell. Otherwise
add an `env` block to each client's config.

**Claude Desktop** — `~/Library/Application Support/Claude/claude_desktop_config.json`:

```json
{ "mcpServers": { "mathx": { "command": "mathx", "args": ["mcp-serve"] } } }
```

**Claude Code** — `claude mcp add mathx -- mathx mcp-serve` (per-project; add `--scope user` for
global).

**Goose** — `goose configure` → Add Extension → command type `stdio` → `mathx mcp-serve`. Or edit
`~/.config/goose/config.yaml` directly.

**Qwen-Agent** — in the Assistant config:

```python
Assistant(
    ...,
    function_list=["mcp"],
    mcp_servers={"mathx": {"command": "mathx", "args": ["mcp-serve"]}},
)
```

**Open WebUI** — the [Pipe](integrations/openwebui/) is the preferred face (ROADMAP Stage 5); MCP
works too, native (v0.6.31+): Admin Panel → Settings → MCP → Add stdio server, command
`mathx mcp-serve`. Pre-v0.6.31: via [mcpo](https://github.com/open-webui/mcpo) —
`mcpo --port 8000 -- mathx mcp-serve`, then add the OpenAPI URL as a Tool.

**Cursor** — `~/.cursor/mcp.json`:

```json
{ "mcpServers": { "mathx": { "command": "mathx", "args": ["mcp-serve"] } } }
```

**VS Code Copilot** — `.vscode/mcp.json` per-project (or user settings for global), same shape as
Cursor.

### What stays out

- **MCP Resources or Prompts.** The tool surface is enough. Expose Resources only for a concrete
  need (e.g. making the job store browsable in an Inspector UI).
- **Authentication, multi-user, rate-limiting.** Personal-oracle scope, until mathx grows a
  shared deployment story.

### Decision log

- **2026-06-23** — Initial plan written after the agentskills.io install-skill landed for
  Claude/pi/Hermes. The cross-agent skill-installer research confirmed there is no shared format
  beyond agentskills.io's three adopters; everything else is MCP. The async fact-check confirmed
  MCP Tasks is one of four async routes, not the only one — handle/poll is the right primary.
  MCP work itself is deferred until a second frontend pulls.
- **2026-06-23 (correction)** — Earlier "agentskills.io has only three adopters" framing was
  wrong. The actual landscape: **`~/.agents/skills/` is the emerging cross-tool shared standard**
  ([Goose](https://goose-docs.ai/docs/guides/context-engineering/using-skills),
  [Warp](https://docs.warp.dev/agent-platform/capabilities/skills/),
  [Codex](https://developers.openai.com/codex/skills), Gemini CLI, Multica all read it);
  Claude Code is the holdout
  ([anthropics/claude-code#66352](https://github.com/anthropics/claude-code/issues/66352));
  Goose ALSO discovers `~/.claude/skills/` for backward compat — so `mathx install-skill
  --target=claude` was already silently covering Goose users. Added `--target=agents` for the
  shared dir; MCP's unlock is now narrower (Claude Desktop / Cowork / Qwen-Agent / Open WebUI /
  Cursor / VS Code Copilot, plus the "alternative path" option for skill-supporting clients).
  The decision to defer the server still holds: a second frontend hasn't pulled yet, and the
  skill route covers more of the obvious second-frontend candidates than I'd realised.
- **2026-06-23 (further refinement)** — Qwen-Agent isn't actually MCP-only either. Its
  extension model is programmatic Python (`register_tool` + `BaseTool`), and since mathx ships
  as a library, the cleanest integration is direct import: `from mathx.engine import solve` from
  inside a `BaseTool` subclass. Pattern lives at
  [`examples/qwen_agent_tool.py`](examples/qwen_agent_tool.py). MCP's exclusive unlock narrows
  again — now Claude Desktop / Cowork / Open WebUI / Cursor / VS Code Copilot (Qwen-Agent gets
  MCP as alt, not as primary).
- **2026-06-25** — Deleted mathx's hand-rolled `install-skill` command (and its `--target`
  matrix). The open cross-agent skills CLI (`npx skills` / [skills.sh](https://skills.sh)) already
  discovers the repo's `skills/maths-oracle/SKILL.md` and installs it project-local or
  global (`-g`) to any of 30+ agents (`-a`), with update/remove — so maintaining our own installer
  was redundant. mathx now ships only `solve` + a print-only `doctor` (binary-on-PATH and
  skill-installed checks; never mutates files). The `~/.agents/skills/` vs per-agent-dir detail
  above is now the skills CLI's concern, not ours. Also moved the canonical SKILL.md out of the
  Claude-specific `.claude/skills/` to an agent-neutral top-level `skills/` (still a `npx skills`
  discovery location); `.claude/` now holds only local dev settings.
- **2026-07-03** — Built, as ROADMAP Stage 2 (the "persistent job store" pivot pulled it
  forward — the store is the substrate for every later surface, and MCP is just one reader of
  it). Deviations from the pre-build sketch:
  - **Worker is a detached subprocess, not a coroutine-in-server.** The plan leaned
    coroutine-in-server, but Stage 2 also added CLI verbs (`mathx submit`/`status`/`jobs`), and a
    CLI `submit` *requires* a worker that outlives the submitting process. Once
    `python -m mathx.jobs <job_id>` existed, reusing it from the MCP server meant one code path,
    no in-server task bookkeeping, and jobs that survive an MCP-server bounce for free. The
    coroutine's claimed pros (no subprocess overhead, cancellable) don't matter at fan-out
    timescales.
  - **No TTL pass on server start; pruning is manual** (`mathx jobs --prune HOURS`). The
    roadmap's framing — "runs get identity and history" — makes auto-deleting history wrong.
  - **API key never touches the job file.** Workers resolve `MATHX_API_KEY`/`OPENAI_API_KEY`
    from their environment at run time; `mathx submit --api-key …` passes an explicit key via
    the child's env only.
  - `submit_solve` also grew `max_k` (weak-margin auto-escalation, added in Stage 1 after this
    plan was written).
- **2026-07-05 (Stage 5)** — The full agent-client surface landed:
  - **`check_solve` renamed to `poll_job`** (breaking, pre-deployment — nothing was wired
    anywhere but this machine). The old name meant "poll a job", which stopped being readable
    once claim *checks* existed.
  - New tools: `submit_check`, `submit_argue` (returns job id AND ledger_id immediately — the
    ledger file updates live, so `get_ledger` is the progress stream), `list_jobs` /
    `list_ledgers` (compact, no sample texts — token discipline for agent clients),
    `recheck_claim`, `challenge_claim`. `expand` stays CLI-only: it blocks on a decomposition
    call, which violates handle/poll; make it a job kind if an agent client ever needs it.
  - All submit tools take `profile` (resolution shared with the CLI via
    `config.resolve_provider`). Keys remain env-only.
  - This forced background argue (`kind: "argue"` jobs) — the argue loop's stays-out (below)
    overridden by the handle/poll requirement; its decision log records the reversal. CLI
    `submit --argue` is still not exposed (trivially possible; no CLI pull yet).
  - Open WebUI is NOT this surface's constituency anymore: it integrates via the Pipe
    (`integrations/openwebui/`), per the ROADMAP Stage 5 decision.

---

## Claim checker (`mathx check`)

Stage 3 shipped `mathx check "<claim>"` — claim in, **verdict + evidence** out, the atom of the
Stage-4 decompose–check–refine loop (README's *Checking claims* has the usage). Verdicts are
evidence, not certainty — every record carries its epistemic status ("SymPy symbolic equality",
"held on 200 random instances", "12/16 self-grades agree"), never a bare true/false. This
section is kept for what the build deferred: the 2026-07 model-landscape survey behind the TIR
lanes and the executor seam. Sources in the decision log.

### The verdict stack

Three independent lanes, cheapest-to-run first. `mathx check` should support each behind one
interface and report which lane(s) produced the verdict:

1. **TIR check** — code gets executed and the claim stands or falls on the output.
2. **Self-grade fan-out** — k judge samples grade the claim (VibeThinker-3B-style
   self-critique; DeepSeekMath-V2-style rubric grading as the aspirational ceiling). Reuses the
   existing maj@k machinery with a judge prompt; `self_verify`'s `_judge_one` is the seed.
3. **Consistency** — the existing maj@k vote, run on the claim restated as a question.
   Already built (Stages 1–2); `check` just needs to call it per claim.

### Literal TIR is on the table (2026-07 survey)

Two distinct shapes, both worth having, behind the same interface:

#### Lane 1 (default): checker-authored script, single-shot

Ask a model — any model, including the generalists already in rotation — to write ONE
verification script for the claim (SymPy symbolic check + random-instance numeric testing),
then mathx executes it and parses a structured verdict (PASS / FAIL+counterexample /
INCONCLUSIVE+exception) from stdout. One generation, one execution, no continuation.
**Works over plain chat-completions on every endpoint mathx already supports.** This is the
pragmatic default and what Stage 3 should build first.

#### Lane 2 (upgrade): literal multi-turn TIR

The NuminaMath/OpenMath protocol: sample until the stop string closing a code block, execute,
splice ```` ```output … ``` ```` back in, **continue the same assistant turn**; repeat until a
final boxed answer. Facts that gate it:

- **It needs completion-style continuation** — a raw `/v1/completions` endpoint (or vLLM's
  `continue_final_message`). Plain chat-completions cannot resume mid-message, so most
  chat-only routes (e.g. typical OpenRouter routes) can't drive it.
- **Featherless documents an OpenAI-compatible `/v1/completions` against any catalog model**,
  so the TIR-native models below are drivable from mathx's existing stack. Local vLLM works;
  oMLX continuation support is unverified (test before designing around it).
- Fence/stop conventions differ per model family (ToRA-style ```` ```python ````/
  ```` ```output ```` for Numina/Qwen; NeMo-Skills is the reference implementation for the
  Nemotron family) — so lane 2 costs per-family templating. That plumbing is why it's the
  upgrade lane, not the default.

#### Tool-using checker models available (as of 2026-07)

| Model | Tool use | License | Practical notes |
|---|---|---|---|
| OpenMath-Nemotron 1.5B–32B | TIR is one of 3 modes (CoT/TIR/GenSelect) | CC-BY-4.0 | On Featherless (already a known-good provider); 1.5–14B run locally; TIR + maj@k compose (their evals are maj@64 over TIR runs) |
| Nemotron-Math generation (Dec 2025, arXiv 2512.15489) | Qwen3-30B-A3B fine-tunes, multi-mode incl. Python TIR | NVIDIA open | Claims 100% maj@16 on AIME 24/25 *with* TIR; track as the successor TIR default |
| Qwen2.5-Math 1.5B/7B/72B | ToRA-style TIR | Qwen licence | Prefers greedy decoding — collides with fan-out (README documents this trap) |
| NuminaMath-7B-TIR | The cleanest documented TIR protocol (AIMO-1 winner) | Apache-2.0 | Weak by 2026 standards; valuable as protocol reference, not as the checker |
| Any strong generalist (DeepSeek V4, Qwen3, …) | Writes lane-1 checker scripts on request | — | The lane-1 default; no TIR training needed |

Non-tool verifier lane, for contrast: VibeThinker-3B self-critique (local, MIT);
DeepSeekMath-V2 (trained generative verifier, ~685B on V3.2-Exp base — weights on HF but no
practical hosted route found as of writing; imitate its claim-level rubric *style* in lane 2 of
the verdict stack rather than calling it).

### Execution: local by default, remote behind the same seam

`mathx check` executes model-written code. Two consequences, decided now:

**1. The executor is a seam, not a place.** Everything above needs exactly one interface:

```python
class ExecResult:  # stdout, stderr, exit_code, timed_out, elapsed_ms
class Executor:
    def run(self, code: str, *, timeout_s: float) -> ExecResult        # lane 1
    def session(self) -> Session                                       # lane 2: .exec(cell) with kept state
```

Local default: a subprocess (`python -I`, fresh cwd, wall-clock timeout, output caps).
**Be honest about what that is: hygiene, not a security boundary** — Python cannot sandbox
Python (long, failed history), and a laptop subprocess ultimately runs with the user's
privileges. For a personal workstation running SymPy checks it authored the prompt for, that's
an acceptable start; it must be *stated*, not implied away.

**2. Remote execution slots in as optional Executor backends — design for it now, build it
later.** The 2026 sandbox-service landscape is commoditised and Python-SDK-first, and all of it
maps onto the same seam:

| Service | Shape | Fit |
|---|---|---|
| E2B | Firecracker microVMs, `e2b-code-interpreter` SDK, a Jupyter server per sandbox (stateful cells), ~150ms starts, 24h session cap | Best fit: the Jupyter-session model is isomorphic to lane 2's Session; open-source platform |
| Daytona | ~90ms creates from warm pools, stateful, Python SDK + REST | Fastest starts; same run/exec shape |
| Modal | `modal.Sandbox` for untrusted agent code, gVisor, Python-native SDK, massive concurrency | Best if fan-out parallelism dominates (hundreds of concurrent checks) |

Why remote earns its place eventually: (a) real isolation for model-written code — microVMs
are the honest security upgrade, not more local subprocess cleverness; (b) Stage-4 fan-out
(claims × k checks) parallelises without cooking the laptop. Why not now: a personal oracle
checking SymPy claims doesn't need a SaaS dependency on day one, and the seam makes the
upgrade additive — `MATHX_EXECUTOR=local|e2b|daytona|modal` plus each backend's own env keys,
shipped as optional extras (`mathx[e2b]`, …) so the core stays zero-new-deps. Note the
pleasant asymmetry: a *local* model (oMLX) with *remote* execution works fine — code goes up,
output comes back, and the round-trip is noise against generation time.

What the seam must NOT assume, so the remote backends stay honest implementations of it:
no shared filesystem with the caller, no ambient network access for the checked code, no
process identity between `run()` calls outside a `Session`.

### What stays out

- **Executing solver-side TIR for problem-solving.** Stage 3 executes *checker* code. Making
  `mathx solve` itself TIR-capable (lane 2 for solving) is a separate later decision.
- **A provider registry for executors.** One env var, N optional backends.
- **Proof-strength claims.** No verdict is ever "proven"; the strongest available status is
  "symbolically verified by SymPy under stated assumptions".

### Decision log

- **2026-07-03** — Landscape survey (HF model cards for OpenMath-Nemotron-14B and
  NuminaMath-7B-TIR; Nemotron-Math arXiv 2512.15489; Featherless completions docs; DeepSeekMath-V2
  paper/HF; E2B/Daytona/Modal docs and 2026 sandbox comparisons). Decisions: (1) literal TIR is
  viable on existing endpoints — Featherless `/v1/completions` — but is the *upgrade* lane;
  the default TIR verdict is a single-shot checker-authored SymPy script, which works on any
  chat endpoint. (2) Execution goes behind an Executor seam from day one; local subprocess
  default (hygiene, stated honestly); E2B/Daytona/Modal as deferred optional backends —
  remote is the security/parallelism upgrade, not a day-one dependency.
- **2026-07-03 (built)** — Shipped as designed: `check.py` (lane 1 tir + grade, concurrent,
  `--tir-k`/`--grade-k`, structured VERDICT parsing), `executor.py` (`ExecResult`,
  `LocalExecutor`, `get_executor` honouring `$MATHX_EXECUTOR`), job-store integration
  (`kind: solve|check` on records; `mathx submit --check`), `show` renders verdict records.
  README's "Not a TIR sandbox" line rewritten as planned. Deferred, in order of likely pull:
  lane 2 (literal multi-turn TIR driver + `Executor.session()`), remote executor backends,
  an MCP `submit_check` tool (Stage-5 face work), and the consistency lane as a first-class
  `check` flag (it's just `mathx solve` on the restated claim; call it manually meanwhile).
- **2026-07-03 (compatibility review)** — Reviewed against the three model regimes (pure CoT /
  CoT+critique / CoT+TIR) and against host-agent execution; conclusions pinned as ROADMAP
  *Invariants* rather than re-argued here. Stage-3-specific notes: the grade lane is plain CoT
  (protocol failures land in the abstain bucket, visibly); TIR-native models gain nothing at
  check time until lane 2 exists — and once the lane-2 driver is built for checking,
  solver-side TIR becomes a policy decision, not engineering. If regime-mixing *within* one
  check call is ever needed, it's a pair of small flags (`--tir-model`/`--grade-model`), not a
  redesign.

---

## Decompose-check-refine loop (`mathx argue`)

Design note for ROADMAP Stage 4. Constrained throughout by the three ROADMAP invariants;
decisions only, no re-argued rationale.

### Verbs

```bash
mathx argue "<problem>"                       # run the loop; prints ledger id, then the ledger
mathx show <ledger_id>                        # render a ledger (same reader as runs)
mathx ledger                                  # list ledgers
mathx ledger recheck  <ledger> <claim> [--grade-k 16 …]   # escalate one claim
mathx ledger challenge <ledger> <claim> "<objection>"     # re-check with the objection in the prompt
mathx ledger expand   <ledger> <claim>                    # decompose one claim into checked sub-claims
```

### Loop policy

- **Round 0**: one generalist decomposition sample → `ARGUMENT:` prose + `CLAIMS:` numbered
  list (≤ 8 claims, each self-contained: all quantifiers/definitions inside the claim text).
  Parse leniently; one retry on parse failure, then fail loudly.
- **Check**: every claim without a verdict becomes a Stage-3 check job (`kind: check`),
  fanned out through the Stage-2 store via the same detached worker — the loop only submits
  and polls (invariant 2). Knobs pass through (`--tir-k` default 1, `--grade-k` default 4 —
  lower than `check`'s 8 because cost scales by claim count).
- **Refine** (up to `--rounds` times, default 2): verdict report + scratchpad spliced into the
  next decomposition. Claims restated **verbatim keep their verdicts** (normalized exact-text
  match) and are not re-checked; disappeared claims are retired, never deleted; refuted claims
  land in the scratchpad with their counterexample/detail, and the refiner is told not to
  reuse them.
- **Stop** when every active claim is supported, or rounds are exhausted; either way the
  ledger is `assembled` and the badges tell the truth. Exit code 0 only if all active claims
  are supported, else 2.

### The ledger record

One JSON file per ledger under `$MATHX_LEDGERS_DIR`, else `ledgers/` beside the job store —
same id scheme and atomic writes as jobs. Saved after **every** state change, so any home can
pick it up mid-flight (invariant 3).

```json
{"ledger_id": …, "kind": "ledger", "status": "checking|assembled",
 "problem": …, "argument": …, "rounds_used": 1, "rounds_max": 2,
 "model": …, "base_url": …, "created_at": …, "updated_at": …,
 "claims": [{"id": "c1", "text": …, "parent": null, "round_added": 0,
             "retired_round": null,
             "verdicts": [{"job_id": …, "round": 0, "kind": "check|recheck|challenge"}]}],
 "scratchpad": [{"round": 0, "claim": …, "note": "counterexample: n=5"}]}
```

**Claim state is never cached in the ledger** — it is derived live from the referenced job
records (unchecked / checking / supported / refuted / conflict / unclear / error / retired),
so the job store stays the single source of truth and `recheck` needs no ledger surgery
beyond appending a verdict reference.

### What stays out

- **Math-equivalence claim matching across rounds.** Exact normalized text only; verbatim
  reuse is what the refiner is instructed to do, and equivalence-matching invites silently
  wrong verdict carry-over.
- **DAG dependencies between claims.** `parent` gives one level of tree (for `expand`);
  argument-level dependency tracking waits until the ledger earns it.

### Decision log

- **2026-07-03** — Designed and built in one pass (this doc written first). Everything above
  shipped: `src/mathx/ledger.py` (store + live state derivation), `src/mathx/argue.py`
  (prompts, parsing, loop, expand), `mathx argue` + `mathx ledger` group, `show` renders
  ledgers. Cost ceiling per argue run ≈ (claims × (tir_k + grade_k) + 1 decomposition) ×
  (rounds + 1) samples, worst case.
- **2026-07-05 (stays-out reversal: background argue)** — Stage 5's MCP surface requires
  handle/poll, so `kind: "argue"` jobs now exist: `submit_argue` pre-creates the ledger
  (argue() adopts it via `ledger_id`), the worker runs the loop, and the job result is a thin
  pointer — the ledger stays the artifact. Foreground `mathx argue` unchanged; CLI
  `submit --argue` still not exposed.

---

## Answer equivalence (CAS-first, judge-fallback)

Design note for the vote's core primitive. Built 2026-07-03 (decision log below), as sketched
here: CAS clustering with an opt-in labelled judge tier (`--equiv-judge-model` / profile
`equiv_judge_model`) for the residue the CAS refuses.

### Problem

Clustering candidate answers by equivalence IS the maj@k vote, and the CAS layer
(math-verify) has a well-documented residue that live runs keep hitting: parser conventions
(bare `\log` = base-10), variable renaming (`\sigma_1` vs `s_1`), factored-vs-expanded forms,
outright parse refusals. We have patched the *systematic* cases ($-wrapping, log
normalization) but the residue is permanent: no normalizer merges `\sigma` with `s`, and each
patch is reactive. Field-wide this is a known problem — math-verify itself exists because
rule matching was worse; OpenAI's simple-evals scores MATH with a model-based equality
checker; xVerify and Omni-Judge are models fine-tuned for exactly this judgment.

### Design

Pipeline per unplaced sample vs existing cluster representatives, strictly ordered:

1. **exact** — normalized string match (free).
2. **cas** — `verify(parse_answer(a), parse_answer(b))` as today (cheap, deterministic,
   *checkable* — this stays the honesty anchor).
3. **judge** (optional, off by default) — one short query to the equivalence judge: the two
   answer strings + the problem, strict reply `EQUIVALENT`/`DIFFERENT`, anything else counts
   as `DIFFERENT` (conservative: the known judge failure mode is over-merging).

Mechanics and honesty constraints:

- **Merge basis is recorded per cluster member** (`exact` | `cas` | `judge`) and surfaces in
  the record and in `show`: a margin that owes votes to judge merges says so —
  `8/12 (2 judge merges)`. Extends the "verdicts carry epistemic status" invariant down into
  the vote itself: a CAS merge is evidence, a judge merge is another opinion.
- **Judge = the profile's `meta_model`** by default (it's a meta-task), overridable
  (`equiv_judge_model` profile key / flag) — a fine-tuned specialist judge slots in here as
  just another model name if the survey favours one.
- **Cost bound**: judge consulted only for pairs the CAS refused — ≤ new-samples ×
  cluster-reps short calls, pair-cached within a run, subject to `MATHX_CONCURRENCY`.
- **Engineering note**: clustering (`_cluster`) is sync; the judge makes clustering async. v1
  shape: CAS-cluster synchronously as today, then one async judge pass attempting to merge
  singleton clusters into larger ones, re-tally. Keeps the sync path untouched when the
  judge is off.
- Applies to solve voting and `show`'s agreement marks. The check grade lane is unaffected
  (boolean tally, no equivalence needed).

### What stays out

- Judge-first clustering (CAS stays primary: auditable, free, deterministic).
- Training our own judge.
- Judge ensembles / multi-vote merging in v1 — one labelled call; escalate only if live use
  shows over-merging.

### Decision log

- **2026-07-03** — Sketched after the Featherless e2e (log-base split halved a real margin;
  σ-vs-s residue remains post-fix). Survey agent dispatched: xVerify, Omni-Judge,
  simple-evals equality template, NeMo-Skills judge, math-verify's own roadmap, lighteval /
  lm-eval-harness, arXiv 2604.22597. Adopt-vs-crib-vs-build decision pending its report.
- **2026-07-03 (survey verdict: build, cribbing prompts)** — Nothing off-the-shelf is
  pairwise: xVerify (CC-BY-NC-ND, license-blocked), Omni-Judge (2024-frozen; the Omni-MATH-2
  audit found it wrong in 96% of disagreement cases — exactly the CAS-residue regime),
  CompassVerifier (Apache-2.0, strongest trained option, but response-vs-gold; noted as a
  future dedicated-judge model, and its VerifierBench as a validation set). math-verify
  explicitly declined LLM-judge hooks (issue #58). The judge prompt cribs simple-evals'
  MIT EQUALITY_TEMPLATE (symmetric Expression 1/2 framing) + NeMo-Skills' problem-context and
  Judgement-line format + arXiv 2604.22597's order-bias hygiene (both presentation orders
  must independently say Yes).
- **2026-07-03 (lenient CAS tier rejected on evidence)** — the survey's suggested free win,
  `verify(strict=False)` (positional variable matching), merged NOTHING on the live σ-vs-s
  residue (those variants differ by renaming *plus* form) while happily merging `\mu_1` with
  `\mu_2` (over-merge hazard). Skipped. What WAS adopted from the survey at the CAS tier:
  verify() is asymmetric, so both directions are now checked.
- **2026-07-03 (built)** — engine: `_cluster`/`_tally` refactor with exact→cas(both-ways)→
  judge tiers; `_judge_merge_pass` (top-3 clusters as merge targets, pair-cached across
  escalation rounds, sequential calls); `Result.judge_merges` counted, serialized, and
  labelled in `show`/CLI margins. Opt-in via `--equiv-judge-model` / profile
  `equiv_judge_model`. Check's grade lane unaffected; `show`'s agreement marks stay CAS-only
  (a pure reader must not make network calls).
