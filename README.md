# mathx

A minimal mathematical oracle for AI agents. CLI dispatch, JSON return, optional voting.

We point any OpenAI-compatible chat endpoint at a maths problem, sample it `k` times, cluster the
answers by [math-verify](https://pypi.org/project/math-verify/) equivalence (so `\frac{1}{2}` votes
together with `0.5`), and write the modal cluster — with a confidence margin and a per-sample audit
trail — to a JSON file the calling agent reads. Voting is optional: `--strategy cot` is one sample
at temperature 0.

The companion blog notebook is
[*Maths and proof models, applied*](https://danmackinlay.name/notebook/automatic_maths.html);
the broader, older workbench mathx carves out of is
[`pudding`](https://github.com/danmackinlay/pudding).

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
dispatch in the background and poll the `--out` file when the fan-out finishes — no MCP server,
no daemon, no queue. In a synchronous-only harness, the call just blocks; raise the harness's
tool-timeout if needed. The shipped `SKILL.md` teaches the agent when to dispatch and how to
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

## Known-good models and providers

The model determines how much help mathx is. Picks below are distilled from the longer reasoning
in [*Maths and proof models, applied*](https://danmackinlay.name/notebook/automatic_maths.html)
and the Mac-side table in
[*Local LLMs on a Mac → models for mathematical reasoning*](https://danmackinlay.name/notebook/local_llm_mac.html#models-math)
— this is the cookbook version: what to pass as `--model` / `--base-url` and why.

### Cloud generalists (sensible starting point)

Frontier reasoners increasingly out-score the narrow specialists on open maths leaderboards and
have the big practical advantage of being rentable per token. Any of these is a reasonable default
for `--strategy maj@k` or `self_verify`:

| Model | Endpoint | Notes |
|---|---|---|
| DeepSeek V4 Pro / Flash (`deepseek/deepseek-v4-pro`, `deepseek/deepseek-v4-flash` on OpenRouter; `deepseek-reasoner`/`deepseek-chat` on the direct API) | `https://api.deepseek.com/v1` (direct, cheap, no-train) or [OpenRouter](https://openrouter.ai) | Pro is the reasoning/maths flagship, Flash is the fast/cheap tier. The pragmatic default — strong on AIME / MATH at a fraction of frontier-API prices. |
| Qwen3-235B-A22B-Thinking | OpenRouter; [Featherless](https://featherless.ai) | MoE thinking model. Featherless caps concurrency per plan — bad for wide `--k`. |
| Claude Opus / Sonnet (current) | Anthropic direct (Messages API; needs an OpenAI-compat shim) or OpenRouter | Top-of-leaderboard maths in mid-2026. Pricey; Anthropic's first-party API may train on prompts depending on plan — route via OpenRouter or your enterprise terms if that matters. |

### Cloud specialists (when you want a narrow model)

| Model | Endpoint | What for |
|---|---|---|
| `nvidia/OpenMath-Nemotron-{14B,32B}` | Featherless (only serverless home for the narrow solvers) | AIMO-2-winning solver family. CoT-only via mathx (TIR mode wants NeMo-Skills). |
| `AceMath-*` | Featherless | CC-BY-NC: research/personal only. |
| `deepseek-ai/DeepSeek-Prover-V2-671B` | [Novita](https://novita.ai) (~$0.70 / $2.50 per 1M in/out) | The big Lean prover. Not for mathx — pair with a `lean-repl` loop in [pudding](https://github.com/danmackinlay/pudding). |

### Local picks (Mac, via [oMLX](https://omlx.ai) / Ollama)

VibeThinker-3B running on a Mac via oMLX is the worked example that bootstrapped mathx. Three
laptop-runnable picks, lifted from the Mac notes:

| Model | Size | Sampling (server-side) | Why |
|---|---|---|---|
| [VibeThinker-3B](https://huggingface.co/WeiboAI/VibeThinker-3B) | ~3 GB 8-bit | temp 1.0 / top-p 0.95 / 64K+ out | Tiny solver claiming frontier-level verifiable maths at 3B (MIT licence). The starting case. |
| [DeepSeek-R1-0528-Qwen3-8B](https://huggingface.co/deepseek-ai/DeepSeek-R1-0528-Qwen3-8B) | ~5 GB | temp 0.6 / top-p 0.95 / ≥64K out | Small-model maths generalist — AIME-2024 86%, the one to beat in the 8B class. |
| [OpenMath-Nemotron-14B](https://huggingface.co/nvidia/OpenMath-Nemotron-14B) | ~8 GB | temp 0.6 / top-p 0.95 | Mid-size solver; ~the 32B's score at half the RAM. CoT-only via mathx. |

Point mathx at the local server: `--base-url http://localhost:8000/v1 --api-key x` (the key is
unused but mathx requires *something* in the slot). The full Mac model table — with bigger
options like Nemotron-Cascade-2 and the agentic models for the driver — is in the
[Mac notes](https://danmackinlay.name/notebook/local_llm_mac.html#models-math).

### Greedy-only solvers don't fan out

A real footgun: some solvers (Qwen2.5-Math is the documented one) want greedy decoding
(`do_sample=False`, T=0). With T=0 every sample is identical, so `maj@k` collapses to one
duplicated answer. Either use such a model with `--strategy cot --k 1`, or override the server's
sampling defaults to allow non-zero T (and accept that the model wasn't trained for it).

### Providers in passing

| Provider | Type | Notes |
|---|---|---|
| localhost | self-hosted | oMLX, Ollama, vLLM, llama.cpp. Strongest privacy. Set `--base-url http://localhost:<port>/v1`. |
| [OpenRouter](https://openrouter.ai) | aggregator | One endpoint for many models; delegates retention upstream — review the per-route policy if it matters. |
| [DeepSeek](https://api-docs.deepseek.com/) | direct | Cheap, no-train on the platform-API plan. |
| Featherless | serverless | No-train. Concurrency-capped per plan — fine for `cot`, bad for wide `--k`. |
| Novita | serverless | Where DeepSeek-Prover-V2-671B is cheap. Relevant for the Lean side (not mathx). |

For unpublished maths, the privacy ranking from the blog: self-hosting is strongest; the metered
no-train shortlist (Novita, Featherless) is the next rung; first-party APIs may train on inputs
depending on plan; OpenRouter delegates retention upstream. Pull unpublished proofs off may-train
endpoints; route them through localhost or your own [Modal](https://modal.com)/[RunPod](https://runpod.io)
deployment.

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

**Skill** — install it with the open cross-agent skills CLI ([skills.sh](https://skills.sh)):

```bash
npx skills add danmackinlay/mathx                  # project-local (default)
npx skills add danmackinlay/mathx -g               # global, all your projects
npx skills add danmackinlay/mathx -a claude-code   # target a specific agent
```

`npx skills` discovers the bundled `SKILL.md`, installs it for any of 30+ coding agents, and
handles updates and removal — so mathx carries no per-agent install matrix of its own. Run
`mathx doctor` any time to check that the binary is on PATH and the skill is installed; it prints
the right command if either is missing.

**Other agents.** For tools whose extension model isn't a `SKILL.md` — Qwen-Agent, Open WebUI,
Claude Desktop — the portable path is an MCP server (deferred; design in
[`MCP_PLAN.md`](MCP_PLAN.md)). Qwen-Agent can skip MCP and import `mathx.engine.solve` directly;
see [`examples/qwen_agent_tool.py`](examples/qwen_agent_tool.py).

## Environment variables

Click reads these as first-class defaults — set them once in `.envrc`/shell-rc and the agent calls
`mathx solve "…"` with no provider flags.

| Var | Purpose |
|---|---|
| `MATHX_MODEL` | Model name, e.g. `deepseek/deepseek-v4-pro`. |
| `MATHX_BASE_URL` | OpenAI-compatible endpoint, e.g. `https://api.featherless.ai/v1`. |
| `MATHX_API_KEY` | Preferred. Set to whatever provider's key value. |
| `OPENAI_API_KEY` | Fallback if `MATHX_API_KEY` is not set. |

The repo's `.envrc` does `dotenv_if_exists`, so a `.env` file (git-ignored) is the convenient
place for these.

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

## What mathx is NOT

- Not a Lean prover. See [pudding](https://github.com/danmackinlay/pudding) for the gated
  Lean-prover surface.
- Not a TIR sandbox. The calling agent has its own Python.
- Not a provider registry. One OpenAI-compatible client plus flags.
- Not an MCP server (yet). See *Extending*.
- Not a benchmark / audition harness. That's a different workflow; pudding's `eval.py` is one
  example.

## Status

Early. Wired end-to-end (engine, CLI, skill, install). Offline smoke tests confirm boxed extraction
and math-verify clustering (`1/2 ≡ 0.5` votes together). **No live API smoke test has been run
yet.** The suggested first check:

```bash
mathx solve "7^999 mod 1000" --strategy maj@k --k 16
```

against a competent generalist endpoint should return `143` with a wide margin. (The
specialist `Qwen2.5-Math-72B` returns `43` unanimously — the won't-trust-the-tool failure
documented in pudding's README — which is the case mathx routes around by pointing at a
generalist.)

## Licence

MIT.
