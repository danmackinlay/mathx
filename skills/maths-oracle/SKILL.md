---
name: maths-oracle
description: |
  Dispatch a hard maths sub-problem to the `mathx` oracle — sample-many, vote, return a checked answer — instead of attempting it yourself. Use whenever the user (or a tool in your loop) needs: a closed-form integral or symbolic computation you have stalled on twice; a modular-exponentiation or large-modulus arithmetic result you would otherwise guess; an olympiad-flavoured inequality, divisibility, or combinatorial-counting answer; verification of a maths claim ("is this calculation right?", "double-check this sum"); a confidence margin on an answer where you already have a guess but want a vote. Also use when the user asks to "fan out", "sample several attempts", "consult the oracle", "ask the maths solver", or mentions `mathx`, maj@k, or "voted answer". Do NOT use for trivial arithmetic, simple algebra, plotting, dataframe wrangling, or anything you can solve in one careful step yourself — the oracle costs tokens and minutes; use it only when your own attempt has plausibly failed.
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

`mathx solve` blocks until the fan-out finishes; for `--k` of 8 or more that can be minutes. The shape:

```bash
mathx solve "<problem>" \
  --strategy maj@k --k 16 \
  --out /tmp/mathx/<run-id>.json
```

(With `MATHX_MODEL` / `MATHX_BASE_URL` / `MATHX_API_KEY` set, no provider flags are needed. If they aren't set, ask the user.)

This skill assumes the `mathx` CLI is on PATH. If `mathx solve` reports "command not found" — or any call errors before sampling — run `mathx doctor`: it checks the setup and prints the exact install command (`uv tool install …` / `uvx`).

Pick `<run-id>` as a short slug (e.g. the date + a 4-char nonce) so concurrent dispatches don't collide.

If your harness supports background tool execution, kick the call off in the background and poll `<out>` when it appears — that frees the agent to keep doing other things while the fan-out runs and avoids tripping any tool-call timeout. In Claude Code, pass `run_in_background=true` to the Bash tool. In a synchronous-only harness, just run it and accept the wait (or raise the harness's tool-timeout if it's bounded too low).

Strategy guidance:

- `--strategy maj@k` (default) is the right choice almost always.
- `--strategy self_verify` if you want each sample weighted by a judge pass — slower, but rescues problems where the modal answer is plausibly wrong.
- `--strategy cot --k 1` only for a quick sanity check.

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
  - **≤ 7/16 or a 6/5/5 split** — escalate `--k` (try 32 or 64) or surface the disagreement to the user. Don't just commit to the modal answer.
- **`samples[].text`** is the full per-sample reasoning. Useful when the user asks "how did it get there"; otherwise leave it in the file as an audit trail.
- **`answer: null`** means every sample failed to produce a `\boxed{...}`. Something is wrong (bad model, bad prompt, server down). First run `mathx doctor` to rule out a broken setup, then `--strategy cot --k 1` to get one trace and diagnose.

## Cost and privacy

- Featherless and other public endpoints are no-train. For published / non-sensitive problems they're the right default.
- For unpublished proofs or sensitive maths, point `--base-url` at a local model (oMLX, vLLM, etc.) — no other code change.
- Each `--k 16` run is typically a few cents on a frontier generalist via Featherless; bigger sweeps scale linearly.
