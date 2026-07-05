# mathx

A minimal mathematical oracle for AI agents.
If you ask it to prove something it dispatches to the specialist sub-model, with optional multi-sampling and voting.

We point any OpenAI-compatible chat endpoint at a maths problem, sample it `k` times, detect equivalence using [math-verify](https://pypi.org/project/math-verify/) (so `\frac{1}{2}` is the same as `0.5`), and return the modal answer, with confidence margin and audit trail.

Voting is optional: `--strategy cot` is one sample
at temperature 0.

Coded while writing [a blog post](https://danmackinlay.name/notebook/automatic_maths.html) on applied LLM-for-math.
In fact, this is the second such project; there is an older bloatier project called
[`pudding`](https://github.com/danmackinlay/pudding).

## Install

mathx is two pieces: the `mathx` **CLI** (the oracle the agent shells out to) and the
**`SKILL.md`** that teaches the agent when to call it.

**CLI** — put `mathx` on PATH:

```bash
uv tool install git+https://github.com/danmackinlay/mathx   # isolated, global
# …or, from a clone you want to hack on:
git clone https://github.com/danmackinlay/mathx && cd mathx && uv tool install -e .
```

(mathx isn't on PyPI yet, so installs resolve via the git repo, not a bare `mathx` name.)

**Skill** — install it with the open cross-agent skills CLI, [skills.sh](https://skills.sh):

```bash
npx skills add danmackinlay/mathx                  # project-local (default)
npx skills add danmackinlay/mathx -g               # global, all your projects
npx skills add danmackinlay/mathx -a claude-code   # target a specific agent
```

`npx skills` discovers the bundled `SKILL.md`, installs it for any of 30+ coding agents, and
handles updates and removal.
Run `mathx doctor` any time to check that `mathx` is on PATH and the skill is installed; it prints
the right command if either is missing.

**Other agents.** Agent clients (Claude Desktop, Cursor, VS Code Copilot) get the bundled
MCP server: `mathx mcp-serve` (stdio) — handle/poll tools (`submit_solve` / `submit_check` /
`submit_argue` / `poll_job` / `list_jobs`, plus ledger tools `get_ledger` / `list_ledgers` /
`recheck_claim` / `challenge_claim`), all instant-return so no client tool-call timeout ever
bites; per-client wiring snippets are in [`DESIGN_NOTES.md`](DESIGN_NOTES.md#mcp-server).
**Open WebUI** gets the Pipe instead — the loop runs in mathx code and streams ledger
progress into chat, independent of the served model's tool-calling ability:
[`integrations/openwebui/`](integrations/openwebui/).
Qwen-Agent can skip both and import `mathx.engine.solve` directly;
see [`examples/qwen_agent_tool.py`](examples/qwen_agent_tool.py).

## Configuration: profiles and environment variables

Every provider-talking command needs a model, an OpenAI-compatible endpoint, and a key, and
resolves each setting as **flag > profile > environment variable**.

**Profiles** live in `mathx.toml` (working dir or any ancestor) or
`~/.config/mathx/config.toml`, and bundle the settings that travel together:

```toml
[profiles.local]
base_url = "http://localhost:8000/v1"
model = "vibethinker-bf16"     # object-level maths: solve, grade, judge
meta_model = "qwen3.6-35b"     # meta-tasks: decomposition, checker scripts
temperature = 1.0
top_p = 0.95
max_tokens = 6000              # keep one sample inside the server's request timeout
max_retries = 0                # don't re-send doomed requests to a single-user server
concurrency = 3                # the server's real parallelism

[profiles.cloud]
base_url = "https://openrouter.ai/api/v1"
model = "deepseek/deepseek-v4-flash"
api_key_env = "OPENROUTER_API_KEY"   # NAMES the env var; keys never live in this file
extra_body = { reasoning = { effort = "high" } }   # provider-dialect passthrough, verbatim
```

Select with `--profile local` or `MATHX_PROFILE=local`. The `model`/`meta_model` split exists
because maths specialists are routinely bad at the meta-tasks (writing verification scripts,
emitting structured decompositions) while being excellent solvers and graders — the profile
records that division of labour once, so `mathx argue --profile local` can never accidentally
hand the specialist a job it can't do. `mathx doctor` reports which config file and profiles
it can see.

**Bare environment variables** still work with no config file at all:

| Var | Purpose |
|---|---|
| `MATHX_MODEL` | Model name, e.g. `deepseek/deepseek-v4-pro`. |
| `MATHX_BASE_URL` | OpenAI-compatible endpoint, e.g. `https://api.featherless.ai/v1`. |
| `MATHX_API_KEY` | Set to whatever provider's key value. (Falls back to `OPENAI_API_KEY`) |
| `MATHX_JOBS_DIR` | Optional. Where background job records live; defaults to `$XDG_CACHE_HOME/mathx/jobs`, else `~/.cache/mathx/jobs`. |
| `MATHX_EXECUTOR` | Optional. Where `mathx check` runs checker scripts. Only `local` (the default) exists today. |
| `MATHX_LEDGERS_DIR` | Optional. Where claim ledgers live; defaults to a `ledgers/` dir beside the job store. |
| `MATHX_CONCURRENCY` | Optional. Max in-flight requests per process (and the job-launch budget for `argue`). Unset = unlimited. Set it to a small local server's real parallelism, or fan-outs queue into its request timeout. |

Set them however you set env vars, or pass
`--model` / `--base-url` / `--api-key` explicitly. mathx just reads the environment; it ships no
`.env` loader of its own. `--top-p` and `--extra-body '<json>'` exist as flags too;
`max_retries` is profile-only.

The repo does include a one-line `.envrc` (`dotenv_if_exists`): if you hack on mathx from a clone
with [direnv](https://direnv.net), it auto-loads a git-ignored `.env` so a provider key stays handy
while you test.

## What mathx is NOT

- Not a Lean prover. See [pudding](https://github.com/danmackinlay/pudding) for the gated
  Lean-prover surface.
- Not a *solver-side* TIR sandbox: `mathx solve` never executes solver code — the calling agent
  has its own Python. (`mathx check` *does* execute model-written **checker** scripts, in a
  hygiene-sandboxed local subprocess — timeout, fresh cwd, capped output. That is deliberately
  not called a security boundary; see [DESIGN_NOTES.md](DESIGN_NOTES.md#claim-checker-mathx-check) for the honest framing and
  the planned remote-isolation backends.)
- Not a provider registry. One OpenAI-compatible client plus flags; named profiles in
  `mathx.toml` only bundle those same flags (zero provider-specific code — dialect extras
  pass through verbatim via `extra_body`).
- Not a benchmark / audition harness.
- Not a frontend / renderer. mathx pins the backend to `$…$` / `$$…$$` delimiters, but whether that maths actually renders is up to the client you read it in.

## Status

Early. Wired end-to-end (engine, CLI, skill, install), with an offline pytest suite
(`uv run pytest` — samples are served by a mocked OpenAI-compatible endpoint, no key needed).
Test it against your prefered backend:

```bash
mathx solve "7^999 mod 1000" --strategy maj@k --k 16
```

against a competent generalist endpoint should return `143` with high certainty.
Interestingly, `Qwen2.5-Math-72B` returns `43` unanimously.

That's the ten-second pipe check. For the real tour — recover-and-verify a formula, audit a
plausible-but-false belief, build a claim ledger — work through [EXAMPLES.md](EXAMPLES.md).

## How an agent uses it

A typical call:

```bash
mathx solve "What is 7^999 mod 1000?" \
  --strategy maj@k --k 16 \
  --out /tmp/mathx/sweep-0001.json
```

`--model`, `--base-url`, and `--api-key` are required but read from env vars by default (see
*Environment variables*). Stdout is a one-screen summary (answer, margin, vote split, token use);
`--out` writes the structured JSON the calling agent parses. When stderr is a TTY, per-sample
progress streams there as the fan-out runs (`--progress/--no-progress` to force it either way).

Two options worth knowing:

- `--max-k 64` — auto-escalation. If the winning cluster holds no strict majority of the vote
  (a 6/5/5-style split), mathx doubles the sample count and re-votes over everything drawn so
  far, up to 64 samples total. The JSON records how many escalations fired.
- `mathx show <run.json>` — render a past run's audit record: vote histogram, per-sample
  answers with agree/disagree marks (by the same math-verify equivalence the vote used), and a
  disagreement summary. `mathx show <run.json> --sample 3` prints sample 3's full reasoning.

For anything long-running, prefer the job verbs over blocking:

```bash
mathx submit "What is 7^999 mod 1000?" --k 32   # prints a job id, returns immediately
mathx status <job_id>    # exit 0 complete / 2 running / 3 errored; --json for the record
mathx jobs               # all runs, newest first; --prune HOURS deletes old records
mathx show <job_id>      # render a finished job (same reader as for --out files)
```

`submit` writes a `running` record to the job store (see `MATHX_JOBS_DIR`) and detaches a
worker that outlives the CLI call; the record flips to `complete`/`error` when the fan-out
lands. Runs get identity and history: every surface — `status`, `jobs`, `show`, the MCP
server's `poll_job` — is just a reader of the same files. The API key is never written to
disk; workers read it from the environment.

## Checking claims

`mathx check` is the claim-level primitive (design: [DESIGN_NOTES.md](DESIGN_NOTES.md#claim-checker-mathx-check)) — verdict
plus evidence, never proof:

```bash
mathx check "for integer n >= 1, the sum of the first n odd numbers is n^2"
mathx submit --check "<claim>"       # same thing, in the background via the job store
```

Two verdict lanes run concurrently:

- **tir** (`--tir-k`, default 1): a model writes one self-contained verification script
  (sympy symbolic checks plus seeded random-instance testing); mathx executes it in a local
  subprocess (`--exec-timeout`, default 60 s) and parses a structured
  `VERDICT: PASS/FAIL/INCONCLUSIVE` from stdout, with any `COUNTEREXAMPLE:`/`REASON:` line
  surfaced.
- **grade** (`--grade-k`, default 8): k independent samples vote `\boxed{TRUE}` /
  `\boxed{FALSE}` on the claim; majority plus margin.

The overall status is `supported` / `refuted` / `conflict` / `unclear` (exit codes 0 / 1 / 2 /
2), and the full audit trail — generated code, its stdout/stderr, every grader's reasoning —
lands in the JSON record. `mathx show <run>` renders it; `--script N` prints a checker
script and its output, `--sample N` a grader's reasoning. `MATHX_EXECUTOR` picks where
checker scripts run (only `local` today; remote sandbox backends are planned).

## Building an argument

`mathx argue` runs the decompose–check–refine loop (design: [DESIGN_NOTES.md](DESIGN_NOTES.md#decompose-check-refine-loop-mathx-argue)):

```bash
mathx argue "Show that the sum of the first n odd numbers is n^2."
```

The problem is decomposed into self-contained claims; each claim becomes a background check
job; refuted or unclear verdicts (plus a scratchpad of everything refuted so far) are spliced
into a refinement pass, up to `--rounds` times. The result is a **claim ledger** — a
persistent file listing the argument and every claim with a live verdict badge derived from
the job store. Exit code 0 only if every active claim ends supported.

```bash
mathx show <ledger_id>                              # render the ledger, live badges
mathx ledger                                        # list ledgers
mathx ledger recheck  <ledger> <claim> --grade-k 16 # escalate one claim
mathx ledger challenge <ledger> <claim> "<objection>"
mathx ledger expand   <ledger> <claim>              # decompose into checked sub-claims
```

Claims restated verbatim across rounds keep their verdicts; dropped claims are retired, never
deleted. `recheck`/`challenge`/`expand` accept `--model` overrides — re-examining a claim
with a stronger model is legitimate regime mixing. Ledgers live beside the job store
(`MATHX_LEDGERS_DIR` to relocate).

## Design invariants

Three properties hold across every surface. [ROADMAP.md](ROADMAP.md) states them in full and
pins them as drift guards so feature work can't erode them:

- **Any CoT model is enough** — the model is a text-in/text-out sampler; judge, grade, and
  checker-script lanes are optional per call, and a model bad at one degrades visibly
  (abstains), never silently. Regimes mix freely across calls.
- **The loop has three homes** — a host agent driving the CLI/MCP verbs, the `mathx argue`
  verb, and `solve(...)`/`check(...)` imported into custom Python — all reading and writing the
  same job store.
- **Everything is a record** — runs, verdicts, and ledgers are self-describing JSON carrying a
  full audit trail and epistemic status ("held on 200 random instances" ≠ proven); no record
  encodes who drove the loop, so work can be resumed from any surface.

The shipped `SKILL.md` teaches the agent when to dispatch and how to interpret the margin;
`npx skills add danmackinlay/mathx` wires it into the agent's skills directory (see *Install*).

## Output shape

`--out` writes JSON of this shape:

```json
{
  "problem": "What is 7^999 mod 1000?",
  "answer": "143",
  "margin": "14/16",
  "votes": {"143": 14.0, "43": 2.0},
  "strategy": "maj@k",
  "escalations": 0,
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
- **`escalations`** — how many times a weak margin triggered a doubling of `k` (only nonzero when
  `--max-k` is passed); `k` is the total number of samples actually drawn.
- **`samples[].confidence`** — only populated by `self_verify` (the judge's 0–1 score).
- **`samples[].text`** — the full per-sample reasoning, kept as audit trail. Can be large. Maths
  in it uses `$…$` / `$$…$$` (mathx pins the model to these — `\(…\)` / `\[…\]` render as raw
  source in clients like Goose/OpenCode/Jan); rendering it is the calling client's job.

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
  engine.py       sample, judge, cluster + tally, solve(); the maths logic
  config.py       ProviderConfig: the endpoint bundle every layer shares (resolve_provider, to_args/from_args)
  check.py        claim checking: tir script lane + grade vote lane (`mathx check`)
  executor.py     where checker code runs: local subprocess today, remote seam for later
  argue.py        decompose–check–refine loop (`mathx argue`) + claim expansion
  ledger.py       claim-ledger store; live claim state derived from the job store
  report.py       pure renderers over run/check/ledger JSON (`mathx show`)
  jobs.py         file-per-job store (stdlib-only leaf: every surface reads it)
  worker.py       job execution by kind (`python -m mathx.worker <id>`; imports the engines)
  mcp_server.py   FastMCP tools: submit_solve/check/argue, poll_job, ledger tools
  cli.py          click group: solve, check, argue, submit, status, jobs, show, ledger, …
integrations/openwebui/
  mathx_pipe.py   Open WebUI Pipe: solve/check/argue in the model picker, live progress
skills/maths-oracle/
  SKILL.md     agent-facing trigger phrases + dispatch recipe (any agent via npx skills)
tests/
  conftest.py  fake OpenAI-compatible endpoint (httpx MockTransport under the real client)
  test_*.py    engine, check, executor, jobs, report, MCP, CLI — `uv run pytest`, offline
```

The engine is one file by design. Public API: `from mathx import solve, ProviderConfig` —
`solve(problem, provider=ProviderConfig(model=…, base_url=…, api_key=…), k=16)` returns a
`Result` dataclass; `mathx.engine.result_to_dict` is the JSON serialiser used by the CLI.
`ProviderConfig` is the one endpoint bundle every layer shares (engines take it, job records
store it via `to_args()`, workers rehydrate it via `from_args()`); task-shaped knobs (`k`,
`strategy`, `tir_k`, `rounds`, …) stay explicit parameters. Anything Python that wants to call
mathx programmatically uses `solve(...)` directly and skips the CLI / file dance —
`config.resolve_provider(profile=…)` builds the `ProviderConfig` from flags/profile/env if you
want the same resolution the CLI does.

## Known-good models and providers

The model determines how much help mathx is.
Here are some interesting starting options for `--model` / `--base-url`.

### Cloud generalists

Frontier reasoners score well on the open maths leaderboards and
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
  `self_verify`'s confidence-weighting), the hook is `_cluster()`/`_tally()` reading
  `Sample.confidence`.
- **A new endpoint.** No code change — pass `--base-url` and `--model`, or set the env vars.
- **A new endpoint knob.** One field on `ProviderConfig` in `config.py` (plus its `pick()` line
  in `resolve_provider` and, if flag-worthy, a `_ENDPOINT_OPTIONS` entry in `cli.py`) — it then
  reaches every engine, job record, worker, and surface without further threading.
- **TIR (tool-integrated reasoning).** Currently deferred. Would require a Python kernel + fenced-
  code template parsing + splice-back. The calling agent already has a Python tool, so adding TIR
  here mostly matters when a specialist model that *only* talks via fenced code (e.g.
  OpenMath-Nemotron, Qwen2.5-Math) enters the rotation.
- **The MCP server** ships: `mathx mcp-serve` (stdio) exposes the full submit/poll/ledger
  surface (`submit_solve` / `submit_check` / `submit_argue` / `poll_job` / `list_jobs`, plus
  the ledger tools) over the same job store as the CLI verbs. The handle/poll-vs-MCP-Tasks
  reasoning and per-client wiring snippets are in [`DESIGN_NOTES.md`](DESIGN_NOTES.md#mcp-server).

## Privacy

mathx sends prompts to whatever `--base-url` points at. For unpublished or sensitive work, point it
at a local oMLX or vLLM endpoint — no other change.

## Licence

MIT.
