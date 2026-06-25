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
interpret the margin; `mathx install-skill` wires it into the agent's skills directory.

## Output shape

`--out` writes JSON of this shape:

```json
{
  "answer": "143",
  "margin": "14/16",
  "votes": {"143": 14.0, "43": 2.0},
  "strategy": "maj@k",
  "model": "deepseek-ai/DeepSeek-V3",
  "base_url": "https://api.featherless.ai/v1",
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

## Install

```bash
git clone <this repo> ~/Source/mathx      # somewhere stable, since install-skill symlinks from it
cd ~/Source/mathx
uv tool install -e .                      # puts `mathx` on PATH
mathx install-skill                       # default --target=claude: ~/.claude/skills/maths-oracle/
```

Editable install is required: `mathx install-skill` resolves the SKILL.md source through
`__file__` and so needs the repo at a stable path. Pass `--copy` to copy instead of symlink,
`--force` to overwrite an existing install.

## Installing for other agents

The SKILL.md format mathx ships ([agentskills.io](https://agentskills.io)) has adoption across a
growing set of clients. `~/.agents/skills/` is the emerging cross-tool shared location
([Goose calls it "the recommended standard"](https://goose-docs.ai/docs/guides/context-engineering/using-skills);
[Warp marks it "(recommended)"](https://docs.warp.dev/agent-platform/capabilities/skills/);
Codex, Gemini CLI, Multica all read it). Claude Code is still the holdout
([feature request #66352](https://github.com/anthropics/claude-code/issues/66352) tracks adding it).
pi and Hermes keep their own per-agent dirs.

`install-skill --target=` writes the skill into the chosen location:

| `--target` | Wires into | Clients that read it |
|---|---|---|
| `agents` | `~/.agents/skills/maths-oracle/` | Goose, Codex, Gemini CLI, Warp, Multica, others on the shared standard |
| `claude` (default) | `~/.claude/skills/maths-oracle/` | Claude Code (also a backward-compat path for Goose) |
| `pi` | `~/.pi/agent/skills/maths-oracle/` | [pi](https://github.com/earendil-works/pi) |
| `hermes` | `~/.hermes/skills/maths-oracle/` | [Hermes Agent](https://github.com/nousresearch/hermes-agent) |
| `all` | every target whose parent dir already exists | (silently skips clients you don't have) |

Re-run with `--force` to overwrite an existing install. The default stays `claude` because that's
the daily-driver target — for forward-looking cross-tool reach, pass `--target=agents`, or use
`--target=all` to symlink into every location at once. (Goose specifically also reads
`~/.claude/skills/` for backward compatibility, so `--target=claude` also covers it.)

For agents whose extension model isn't a SKILL.md at all — **Qwen-Agent**, **Open WebUI**,
**Claude Desktop**, **Cursor**, **VS Code Copilot** — the portable path is an MCP server. The
design and per-client wiring snippets live in [`MCP_PLAN.md`](MCP_PLAN.md), but the server itself
is deferred until a second frontend pulls. Until then, those agents can call `mathx solve` by
shelling out from a hand-written tool wrapper (Qwen-Agent's `register_tool`, a Custom GPT action,
etc.) — see each agent's docs.

Worth knowing about the cross-tool picture: it's improving but uneven. The agentskills.io spec
and the `~/.agents/skills/` convention are real progress towards "write a skill once, every
agent finds it." Claude Code's holdout and a few per-agent dirs (pi, Hermes, `~/.cursor/skills/`,
`~/.gemini/config/skills/`) mean a symlink-per-target installer is still useful in mid-2026, but
the long arc is consolidation onto the shared directory.

## Environment variables

Click reads these as first-class defaults — set them once in `.envrc`/shell-rc and the agent calls
`mathx solve "…"` with no provider flags.

| Var | Purpose |
|---|---|
| `MATHX_MODEL` | Model name, e.g. `deepseek-ai/DeepSeek-V3`. |
| `MATHX_BASE_URL` | OpenAI-compatible endpoint, e.g. `https://api.featherless.ai/v1`. |
| `MATHX_API_KEY` | Preferred. Set to whatever provider's key value. |
| `OPENAI_API_KEY` | Fallback if `MATHX_API_KEY` is not set. |

The repo's `.envrc` does `dotenv_if_exists`, so a `.env` file (git-ignored) is the convenient
place for these.

## Code layout

```
src/mathx/
  engine.py    sample, judge, cluster-and-vote, solve(); the maths logic
  cli.py       click group with `solve` + `install-skill` subcommands
.claude/skills/maths-oracle/
  SKILL.md     agent-facing trigger phrases + dispatch recipe
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
