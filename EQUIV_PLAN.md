# Answer equivalence — CAS-first, judge-fallback (shipped)

Design note for the vote's core primitive. Built 2026-07-03 (decision log below), as sketched
here: CAS clustering with an opt-in labelled judge tier (`--equiv-judge-model` / profile
`equiv_judge_model`) for the residue the CAS refuses.

## Problem

Clustering candidate answers by equivalence IS the maj@k vote, and the CAS layer
(math-verify) has a well-documented residue that live runs keep hitting: parser conventions
(bare `\log` = base-10), variable renaming (`\sigma_1` vs `s_1`), factored-vs-expanded forms,
outright parse refusals. We have patched the *systematic* cases ($-wrapping, log
normalization) but the residue is permanent: no normalizer merges `\sigma` with `s`, and each
patch is reactive. Field-wide this is a known problem — math-verify itself exists because
rule matching was worse; OpenAI's simple-evals scores MATH with a model-based equality
checker; xVerify and Omni-Judge are models fine-tuned for exactly this judgment.

## Design

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
- **Engineering note**: `_cluster_and_vote` is sync; the judge makes clustering async. v1
  shape: CAS-cluster synchronously as today, then one async judge pass attempting to merge
  singleton clusters into larger ones, re-tally. Keeps the sync path untouched when the
  judge is off.
- Applies to solve voting and `show`'s agreement marks. The check grade lane is unaffected
  (boolean tally, no equivalence needed).

## What stays out

- Judge-first clustering (CAS stays primary: auditable, free, deterministic).
- Training our own judge.
- Judge ensembles / multi-vote merging in v1 — one labelled call; escalate only if live use
  shows over-merging.

## Decision log

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
