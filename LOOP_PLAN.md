# Stage 4 plan: `mathx argue` — the decompose–check–refine loop

Design note for ROADMAP Stage 4. Constrained throughout by the three ROADMAP invariants;
decisions only, no re-argued rationale.

## Verbs

```bash
mathx argue "<problem>"                       # run the loop; prints ledger id, then the ledger
mathx show <ledger_id>                        # render a ledger (same reader as runs)
mathx ledger                                  # list ledgers
mathx ledger recheck  <ledger> <claim> [--grade-k 16 …]   # escalate one claim
mathx ledger challenge <ledger> <claim> "<objection>"     # re-check with the objection in the prompt
mathx ledger expand   <ledger> <claim>                    # decompose one claim into checked sub-claims
```

## Loop policy

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

## The ledger record

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

## What stays out

- **Background `argue` (a `kind: argue` job).** Foreground with stderr progress for now; the
  agent home already gets async by driving `submit --check` itself.
- **MCP tools for argue/ledger.** Stage 5, with the rest of the owed MCP surface.
- **Math-equivalence claim matching across rounds.** Exact normalized text only; verbatim
  reuse is what the refiner is instructed to do, and equivalence-matching invites silently
  wrong verdict carry-over.
- **DAG dependencies between claims.** `parent` gives one level of tree (for `expand`);
  argument-level dependency tracking waits until the ledger earns it.

## Decision log

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
