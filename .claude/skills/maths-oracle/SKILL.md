---
name: maths-oracle
description: |
  Dispatch a hard maths sub-problem to the `mathx` oracle — sample-many, vote, return a checked answer — instead of attempting it yourself. Use whenever the user (or a tool in your loop) needs: a closed-form integral or symbolic computation you have stalled on twice; a modular-exponentiation or large-modulus arithmetic result you would otherwise guess; an olympiad-flavoured inequality, divisibility, or combinatorial-counting answer; verification of a maths claim ("is this calculation right?", "double-check this sum"); a confidence margin on an answer where you already have a guess but want a vote. Also use when the user asks to "fan out", "sample several attempts", "consult the oracle", "ask the maths solver", or mentions `mathx`, maj@k, or "voted answer". Do NOT use for trivial arithmetic, simple algebra, plotting, dataframe wrangling, or anything you can solve in one careful step yourself — the oracle costs tokens and minutes; reach for it only when your own attempt has plausibly failed.
---

# Maths oracle

Dispatch hard maths to `mathx`, an oracle that samples a problem many times against an LLM, clusters answers by maths-equivalence, and returns the modal winner with a confidence margin and a sample-by-sample audit trail. Saves you from confidently committing to a wrong answer on problems where one sample is unreliable but a vote of many is.

## When to dispatch

Use the oracle for:

- A definite integral or sum you have not closed in two tries.
- Modular exponentiation, big factorials, anything where small arithmetic errors compound.
- A claim the user wants double-checked ("is 7^999 mod 1000 = 43 or 143?").
- Anywhere a confidence margin matters more than a single answer.

Do NOT use for:

- Trivial arithmetic or simple algebra.
- Anything you can solve in one careful step.
- Tasks where the cost (~tokens + minutes) outweighs the answer's worth.

## How to dispatch

`mathx solve` blocks until the fan-out finishes; for `--k` of 8 or more that can be minutes. Use Bash's `run_in_background=true` and read the result file when you're ready:

```bash
mathx solve "<problem>" \
  --strategy maj@k --k 16 \
  --model "$MATHX_MODEL" --base-url "$MATHX_BASE_URL" \
  --out /tmp/mathx/<run-id>.json
```

- Pick `<run-id>` as a short slug (e.g. the date + a 4-char nonce) so concurrent dispatches don't collide.
- `--strategy maj@k` is the default and the right choice almost always. Use `--strategy self_verify` if you want each sample weighted by a judge pass (slower; rescues problems where the modal answer is plausibly wrong). Use `--strategy cot --k 1` only for a quick sanity check.
- Background Bash is the handle/poll mechanism: the call returns immediately with a shell id; check `/tmp/mathx/<run-id>.json` periodically (every 30–60 s for `--k 16`, less often for larger sweeps); the JSON appears when done.

If `MATHX_MODEL` / `MATHX_BASE_URL` aren't set in the user's shell, ask the user or pick a sensible default from `~/.config/mathx/` if present.

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
  - **≤ 7/16 or a 6/5/5 split** — escalate `--k` (try 32 or 64) or surface the disagreement to the user. Don't quietly commit.
- **`samples[].text`** is the full per-sample reasoning. Useful when the user asks "how did it get there"; otherwise leave it in the file as an audit trail.
- **`answer: null`** means every sample failed to produce a `\boxed{...}`. Something is wrong (bad model, bad prompt, server down). Try `--strategy cot --k 1` to get one trace and diagnose.

## Cost and privacy

- Featherless and other public endpoints are no-train. For published / non-sensitive problems they're the right default.
- For unpublished proofs or sensitive maths, point `--base-url` at a local model (oMLX, vLLM, etc.) — no other code change.
- Each `--k 16` run is typically a few cents on a frontier generalist via Featherless; bigger sweeps scale linearly.
