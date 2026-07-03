# Stage 3 plan: `mathx check` — the claim-checker primitive

Design note for ROADMAP Stage 3, in the same spirit as [MCP_PLAN.md](MCP_PLAN.md): capture the
research and the load-bearing decisions before building, so the build doesn't re-derive them.
Written 2026-07-03 from a model-landscape survey; sources in the decision log.

## What it is

```bash
mathx check "<claim>"        # e.g. "for all n≥1, 2^n > n^2 fails only for n∈{2,3,4}"
```

Claim in; **verdict + evidence** out. This is the atom of the Stage-4 decompose–check–refine
loop: the oracle stops being called per *problem* and starts being called per *claim*. Verdicts
are evidence, not certainty — every verdict record must carry its epistemic status ("SymPy
symbolic equality", "held on 200 random instances", "12/16 self-grades agree"), never a bare
true/false.

## The verdict stack

Three independent lanes, cheapest-to-run first. `mathx check` should support each behind one
interface and report which lane(s) produced the verdict:

1. **TIR check** — code gets executed and the claim stands or falls on the output.
2. **Self-grade fan-out** — k judge samples grade the claim (VibeThinker-3B-style
   self-critique; DeepSeekMath-V2-style rubric grading as the aspirational ceiling). Reuses the
   existing maj@k machinery with a judge prompt; `self_verify`'s `_judge_one` is the seed.
3. **Consistency** — the existing maj@k vote, run on the claim restated as a question.
   Already built (Stages 1–2); `check` just needs to call it per claim.

## Literal TIR is on the table (2026-07 survey)

Two distinct shapes, both worth having, behind the same interface:

### Lane 1 (default): checker-authored script, single-shot

Ask a model — any model, including the generalists already in rotation — to write ONE
verification script for the claim (SymPy symbolic check + random-instance numeric testing),
then mathx executes it and parses a structured verdict (PASS / FAIL+counterexample /
INCONCLUSIVE+exception) from stdout. One generation, one execution, no continuation.
**Works over plain chat-completions on every endpoint mathx already supports.** This is the
pragmatic default and what Stage 3 should build first.

### Lane 2 (upgrade): literal multi-turn TIR

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

### Tool-using checker models available (as of 2026-07)

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

## Execution: local by default, remote behind the same seam

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

## Consequences elsewhere

- README's "**Not a TIR sandbox**" line dies when this ships: claim-checking executes
  model-written code, which is a sandbox whether we like it or not. The "calling agent has its
  own Python" argument covered *solving*; it never covered *checking* — the point of a verdict
  is that the harness, not the solver, ran it.
- Verdict records go in the job store (Stage 2) like any run: a `check` is a job whose result
  carries `verdict`, `evidence`, `lane`, and the executed code + output as the audit trail.
  `mathx show` learns to render verdict records.

## What stays out

- **Executing solver-side TIR for problem-solving.** Stage 3 executes *checker* code. Making
  `mathx solve` itself TIR-capable (lane 2 for solving) is a separate later decision.
- **A provider registry for executors.** One env var, N optional backends.
- **Proof-strength claims.** No verdict is ever "proven"; the strongest available status is
  "symbolically verified by SymPy under stated assumptions".

## Decision log

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
