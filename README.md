# mathx

A minimal mathematical oracle for AI agents. CLI dispatch, JSON return, optional voting.

We point any OpenAI-compatible chat endpoint at a maths problem, sample it `k` times, cluster the
answers by [math-verify](https://pypi.org/project/math-verify/) equivalence (so `\frac{1}{2}` votes
together with `0.5`), and write the modal cluster — with a confidence margin and a per-sample audit
trail — to a JSON file the calling agent reads. Voting is optional: `--strategy cot` is one sample
at temperature 0.

Coded during while writing [a blog post](https://danmackinlay.name/notebook/automatic_maths.html) on applied LLM-for-math.
In fact, this is the second such project; there is an older bloatier project called
[`pudding`](https://github.com/danmackinlay/pudding).

## Install

mathx is two pieces: the `mathx` **binary** (the oracle the agent shells out to) and the
**`SKILL.md`** that teaches the agent when to call it.

**Binary** — put `mathx` on PATH:

```bash
uv tool install git+https://github.com/danmackinlay/mathx   # isolated, global
# …or, from a clone you want to hack on:
git clone https://github.com/danmackinlay/mathx && cd mathx && uv tool install -e .
```

(mathx isn't on PyPI yet, so installs resolve via the git repo, not a bare `mathx` name.)

**Skill** — install it with the open cross-agent skills CLI ,[skills.sh](https://skills.sh):

```bash
npx skills add danmackinlay/mathx                  # project-local (default)
npx skills add danmackinlay/mathx -g               # global, all your projects
npx skills add danmackinlay/mathx -a claude-code   # target a specific agent
```

`npx skills` discovers the bundled `SKILL.md`, installs it for any of 30+ coding agents, and
handles updates and removal.
Run `mathx doctor` any time to check that the binary is on PATH and the skill is installed; it prints
the right command if either is missing.

**Other agents.** For tools whose extension model isn't a `SKILL.md` — Qwen-Agent, Open WebUI,
Claude Desktop — we could add an MCP server (deferred; design in
[`MCP_PLAN.md`](MCP_PLAN.md)).
Qwen-Agent can skip MCP and import `mathx.engine.solve` directly;
see [`examples/qwen_agent_tool.py`](examples/qwen_agent_tool.py).

## Environment variables

Set them once in `.envrc`/shell-rc and the agent calls `mathx solve "…"` without requiring provider flags.

| Var | Purpose |
|---|---|
| `MATHX_MODEL` | Model name, e.g. `deepseek/deepseek-v4-pro`. |
| `MATHX_BASE_URL` | OpenAI-compatible endpoint, e.g. `https://api.featherless.ai/v1`. |
| `MATHX_API_KEY` | Preferred. Set to whatever provider's key value. |
| `OPENAI_API_KEY` | Fallback if `MATHX_API_KEY` is not set. |

The repo's `.envrc` does `dotenv_if_exists`, so a `.env` file (git-ignored) is the convenient
place for these.

## What mathx is NOT

- Not a Lean prover. See [pudding](https://github.com/danmackinlay/pudding) for the gated
  Lean-prover surface.
- Not a TIR sandbox. The calling agent has its own Python.
- Not a provider registry. One OpenAI-compatible client plus flags.
- Not an MCP server (yet).
- Not a benchmark / audition harness.

## Status

Early. Wired end-to-end (engine, CLI, skill, install).
Test it against your prefered backend:

```bash
mathx solve "7^999 mod 1000" --strategy maj@k --k 16
```

against a competent generalist endpoint should return `143` with high certainty.
Interestingly  `Qwen2.5-Math-72B` returns `43` unanimously.

## How an agent uses it

A typical call:

```bash
mathx solve "What is 7^999 mod 1000?" \
  --strategy maj@k --k 16 \
  --out /tmp/mathx/sweep-0001.json
```

`--model`, `--base-url`, and `--api-key` are required but read from env vars by default (see
*Environment variables*). Stdout is a one-screen summary (answer, margin, vote split, token use);
`--out` writes the structured JSON the calling agent parses.

If the agent's harness supports background tool execution (Claude Code's `run_in_background=true`),
dispatch in the background and poll the `--out` file when the fan-out finishes.
In a synchronous-only harness, the call just blocks; Maybe this time out?
The shipped `SKILL.md` teaches the agent when to dispatch and how to
interpret the margin; `npx skills add danmackinlay/mathx` wires it into the agent's skills
directory (see *Install*).

## Output shape

`--out` writes JSON of this shape:

```json
{
  "answer": "143",
  "margin": "14/16",
  "votes": {"143": 14.0, "43": 2.0},
  "strategy": "maj@k",
  "model": "deepseek/deepseek-v4-pro",
  "base_url": "https://openrouter.ai/api/v1",
  "k": 16,
  "tokens_in_total": 4096,
  "tokens_out_total": 25184,
  "elapsed_ms_total": 47210,
  "samples": [
    {
      "boxed": "143",
      "confidence": null,
      "error": null,
      "tokens_in": 256,
      "tokens_out": 1574,
      "elapsed_ms": 4218,
      "text": "…full reasoning, with any leading <think>…</think> already stripped…"
    }
  ]
}
```

- **`answer`** — the boxed string of the winning equivalence cluster, or `null` if no sample
  produced a `\boxed{…}`.
- **`margin`** — `<top_cluster_size>/<n_voters>`. The skill teaches the agent to treat
  `≥ 12/16` as commit-worthy, `8–11/16` as a soft majority worth surfacing, `≤ 7/16` as escalate
  or punt.
- **`votes`** — every equivalence-cluster representative with its accumulated weight (sample count
  for `cot`/`maj@k`; sum of judge confidences for `self_verify`).
- **`samples[].confidence`** — only populated by `self_verify` (the judge's 0–1 score).
- **`samples[].text`** — the full per-sample reasoning, kept as audit trail. Can be large.

## Strategies

| Strategy | What it does | When |
|---|---|---|
| `cot` | One sample at `T=0`. | Quick sanity check; no voting. |
| `maj@k` (default) | `k` samples at `T=0.7`, modal equivalence-class winner. | Default; improves accuracy over a single shot. |
| `self_verify` | `maj@k` plus a per-sample judge pass scoring 0–1; votes are weighted by judge confidence. | When the modal answer is plausibly wrong. Slower; ~2× tokens. |

`tir` (tool-integrated reasoning) is deferred — see *Extending*.

## Code layout

```
src/mathx/
  engine.py    sample, judge, cluster-and-vote, solve(); the maths logic
  cli.py       click group with `solve` + `doctor` subcommands
skills/maths-oracle/
  SKILL.md     agent-facing trigger phrases + dispatch recipe (any agent via npx skills)
```

The engine is one file by design. Public API: `from mathx import solve` returns a `Result`
dataclass; `mathx.engine.result_to_dict` is the JSON serialiser used by the CLI. Anything Python
that wants to call mathx programmatically uses `solve(...)` directly and skips the CLI / file dance.

## Known-good models and providers

The model determines how much help mathx is.
Here are some interesting starting options for  `--model` / `--base-url`..

### Cloud generalists

Frontier reasoners do pretty good on mathematics on open maths leaderboards and
have the big practical advantage of being easily rentable per token. Any of these is a reasonable default for `--strategy maj@k` or `self_verify`:

| Model | Endpoint | Notes |
|---|---|---|
| DeepSeek V4 Pro / Flash (`deepseek/deepseek-v4-pro`, `deepseek/deepseek-v4-flash` on OpenRouter; `deepseek-reasoner`/`deepseek-chat` on the direct API) | `https://api.deepseek.com/v1` (direct, cheap, no-train) or [OpenRouter](https://openrouter.ai) | Pro is the reasoning/maths flagship, Flash is the fast/cheap tier. The pragmatic default — strong on AIME / MATH at a fraction of frontier-API prices. |
| Qwen3-235B-A22B-Thinking | OpenRouter | MoE thinking model.  |
| Claude Opus | Anthropic direct (Messages API; needs an OpenAI-compat shim) or OpenRouter | Top-of-leaderboard maths in mid-2026. Pricey; Anthropic's first-party API may train on prompts depending on plan — route via OpenRouter or your enterprise terms if that matters. |

### Cloud specialists

Mathematics-focussed

| Model | Endpoint | What for |
|---|---|---|
| `nvidia/OpenMath-Nemotron-{14B,32B}` | [Featherless](https://featherless.ai) | AIMO-2-winning solver family. CoT-only via mathx until we build TIR. |
| `AceMath-*` | Featherless | CC-BY-NC: research/personal only. |

### Local picks

[Tested on Mac](https://danmackinlay.name/notebook/local_llm_mac.html#models-math).

| Model | Size | Sampling (server-side) | Why |
|---|---|---|---|
| [VibeThinker-3B](https://huggingface.co/WeiboAI/VibeThinker-3B) | ~3 GB 8-bit | temp 1.0 / top-p 0.95 / 64K+ out | Tiny solver claiming frontier-level verifiable maths at 3B (MIT licence). The starting case. |
| [DeepSeek-R1-0528-Qwen3-8B](https://huggingface.co/deepseek-ai/DeepSeek-R1-0528-Qwen3-8B) | ~5 GB | temp 0.6 / top-p 0.95 / ≥64K out | Small-model maths generalist — AIME-2024 86%, the one to beat in the 8B class. |
| [OpenMath-Nemotron-14B](https://huggingface.co/nvidia/OpenMath-Nemotron-14B) | ~8 GB | temp 0.6 / top-p 0.95 | Mid-size solver; ~the 32B's score at half the RAM. CoT-only via mathx. |

Point mathx at the local server: `--base-url http://localhost:8000/v1 --api-key x` (the key is
unused but mathx requires *something* in the slot).

### Greedy-only solvers don't fan out

Some solvers (Qwen2.5-Math is the documented one) want greedy decoding
(`do_sample=False`, T=0).
Others (e.g. Vibethinker)  want the opposite.
With T=0 every sample is identical, so `maj@k` collapses to one duplicated answer.
Either use such a model with `--strategy cot --k 1`, or specify higher temperature sampling.

## Extending

- **A new strategy.** Add a branch to `solve()`'s strategy dispatch in `engine.py` and a
  `STRATEGIES` entry in `cli.py`. If the strategy changes how votes accumulate (like
  `self_verify`'s confidence-weighting), the hook is `_cluster_and_vote()` reading
  `Sample.confidence`.
- **A new endpoint.** No code change — pass `--base-url` and `--model`, or set the env vars.
- **TIR (tool-integrated reasoning).** Currently deferred. Would require a Python kernel + fenced-
  code template parsing + splice-back. The calling agent already has a Python tool, so adding TIR
  here mostly matters when a specialist model that *only* talks via fenced code (e.g.
  OpenMath-Nemotron, Qwen2.5-Math) enters the rotation.
- **An MCP server.** Deferred. The portable path for everything that isn't an agentskills.io
  client (Claude Desktop, Goose, Qwen-Agent, Open WebUI, Cursor, VS Code Copilot). Design,
  triggers, the handle/poll vs MCP-Tasks reasoning, and per-client wiring snippets are in
  [`MCP_PLAN.md`](MCP_PLAN.md).

## Privacy

mathx sends prompts to whatever `--base-url` points at. For unpublished or sensitive work, point it
at a local oMLX or vLLM endpoint — no other change.

## Licence

MIT.
