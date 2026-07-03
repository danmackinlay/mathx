---
name: maths-oracle
description: |
  Dispatch a hard maths sub-problem to the `mathx` oracle — sample-many, vote, return a checked answer — instead of attempting it yourself. Use whenever the user (or a tool in your loop) needs: a closed-form integral or symbolic computation you have stalled on twice; a modular-exponentiation or large-modulus arithmetic result you would otherwise guess; an olympiad-flavoured inequality, divisibility, or combinatorial-counting answer; verification of a maths claim ("is this calculation right?", "double-check this sum"); a confidence margin on an answer where you already have a guess but want a vote. Also use when the user asks to "fan out", "sample several attempts", "consult the oracle", "ask the maths solver", or mentions `mathx`, maj@k, or "voted answer". Do NOT use for trivial arithmetic, simple algebra, plotting, dataframe wrangling, or anything you can solve in one careful step yourself — the oracle costs tokens and minutes; use it only when your own attempt has plausibly failed.
---

# Maths oracle

Dispatch hard maths to `mathx`, an oracle that samples a problem many times against an LLM, clusters answers by maths-equivalence, and returns the modal winner with a confidence margin and a sample-by-sample audit trail. Saves you from confidently committing to a wrong answer on problems where one sample is unreliable but a vote of many is.

## How to dispatch

The fan-out takes minutes for `--k` of 8 or more, so prefer the non-blocking job verbs:

```bash
mathx submit "<problem>" --strategy maj@k --k 16   # prints a job id, returns immediately
mathx status <job_id>   # exit 0 complete / 2 still running / 3 errored; --json for the full record
mathx show <job_id>     # once complete: the human-readable report
```

Poll `mathx status` every 20–60 s (or between other work). `mathx jobs` lists every run, newest first, if you lose a job id.

(With `MATHX_MODEL` / `MATHX_BASE_URL` / `MATHX_API_KEY` set, no provider flags are needed. If they aren't set, ask the user.)

This skill assumes the `mathx` CLI is on PATH. If any call reports "command not found" — or errors before sampling — run `mathx doctor`: it checks the setup and prints the exact install command (`uv tool install …` / `uvx`).

`mathx solve "<problem>" --strategy maj@k --k 16 --out /tmp/mathx/<run-id>.json` is the blocking form — same engine, waits for the vote, writes the same JSON. Use it only for small `--k` or when you'd rather block than poll (e.g. your harness backgrounds the call itself).

## Checking a claim instead of solving a problem

When the user asks "is this right?" / "double-check this" — a CLAIM to verify, not a problem to solve — use `check` rather than `solve`:

```bash
mathx check "<claim>"                  # blocking; exit 0 supported / 1 refuted / 2 conflict-or-unclear
mathx submit --check "<claim>"        # background via the job store, same polling as above
```

Two lanes run: a model-written sympy verification script that mathx executes (`tir`), and a TRUE/FALSE vote of k samples (`grade`). Report the status honestly — it is evidence, not proof: `supported` means the script's checks passed and/or the vote went TRUE, never "proven". On `conflict`, show the user both sides (`mathx show <run> --script 0` prints the checker script and its output).

For a whole argument rather than one claim, `mathx argue "<problem>"` decomposes, checks every claim, and refines — it prints a ledger id (blocking; minutes). Render with `mathx show <ledger_id>`; act on individual claims with `mathx ledger recheck|challenge|expand`. Or drive the loop yourself: decompose the problem in your own reasoning and `mathx submit --check` each claim — same job store either way.

Strategy guidance:

- `--strategy maj@k` (default) is the right choice almost always.
- `--strategy self_verify` if you want each sample weighted by a judge pass — slower, but rescues problems where the modal answer is plausibly wrong.
- `--strategy cot --k 1` only for a quick sanity check.
- Add `--max-k 64` when you can't afford a second dispatch: on a weak vote (no strict
  majority) mathx doubles k and re-votes automatically, up to that cap.

## How to interpret the result

The JSON has:

```json
{
  "answer": "143",
  "margin": "14/16",
  "votes": {"143": 14.0, "43": 2.0},
  "strategy": "maj@k", "model": "…", "k": 16,
  "samples": [{"boxed": "143", "text": "…full reasoning…", …}, …]
}
```

- **`margin`** is the confidence signal. `14/16` means 14 of 16 voters agreed (after maths-equivalence clustering). Treat it like:
  - **≥ 12/16** — trust the answer; commit.
  - **8–11 / 16** — soft majority; mention the disagreement in your reply rather than asserting.
  - **≤ 7/16 or a 6/5/5 split** — re-dispatch with `--max-k 64` (auto-escalation) or surface the disagreement to the user. Don't just commit to the modal answer.
- **`samples[].text`** is the full per-sample reasoning. Useful when the user asks "how did it get there"; otherwise leave it in the file as an audit trail.
- **`mathx show <out>.json`** renders the record for inspection — vote histogram, per-sample answers, disagreement summary — cheaper than reading the raw JSON. `--sample N` prints one sample's full reasoning.
- **`answer: null`** means every sample failed to produce a `\boxed{...}`. Something is wrong (bad model, bad prompt, server down). First run `mathx doctor` to rule out a broken setup, then `--strategy cot --k 1` to get one trace and diagnose.

## Privacy

Dispatching sends the problem text to whatever endpoint mathx is configured for — often an external API. For unpublished or sensitive maths, check with the user before dispatching.
