# mathx

A maths oracle for AI agents. Sample wide, vote by maths-equivalence, return JSON.

Companion / minimum-viable rewrite of the oracle architecture from
[*Maths and proof models, applied*](https://danmackinlay.name/notebook/automatic_maths.html).
Where [pudding](https://github.com/danmackinlay/pudding) became a maths workbench (audition harness,
shim, providers registry, marimo studio), `mathx` is the one thing an agent needs: a CLI it can
dispatch and a JSON file it can read.

## How an agent uses it

```bash
mathx solve "What is 7^999 mod 1000?" \
  --strategy maj@k --k 16 \
  --model deepseek-ai/DeepSeek-V3 \
  --base-url https://api.featherless.ai/v1 \
  --out /tmp/mathx/sweep-0001.json
```

Stdout: human-readable summary (answer, margin, token use). `--out`: full JSON with the answer, the
vote split, and the per-sample audit trail.

In Claude Code, dispatch via Bash with `run_in_background=true`; the file appears when the fan-out
finishes, even if it overruns the synchronous tool timeout. Background Bash is the handle/poll. No
MCP server, no daemon, no queue.

## Strategies

| Strategy | What it does | When |
|---|---|---|
| `cot` | One sample, `T=0` | Quick sanity check. |
| `maj@k` (default) | `k` samples at `T=0.7`, modal equivalence-class winner. | Default — buys real accuracy. |
| `self_verify` | `maj@k` plus a per-sample judge pass that scores 0–1; votes are weighted by judge confidence. | When you suspect the modal answer is plausibly wrong (slower). |

Vote clustering uses [`math-verify`](https://pypi.org/project/math-verify/), so `\frac{1}{2}` and
`0.5` are voted together.

## Install

```bash
git clone <this repo>            # somewhere stable (~/Source/mathx)
cd mathx
uv tool install -e .             # installs `mathx` on PATH
mathx install-skill              # symlinks SKILL.md into ~/.claude/skills/maths-oracle/
```

The install-skill step is what makes Claude Code (or any tool that scans `~/.claude/skills/`) load
the skill and know when to dispatch. Pass `--copy` to copy instead of symlink, `--force` to
overwrite an existing install.

## Configuration

mathx is endpoint-agnostic — pass `--model` and `--base-url` per call. For convenience:

- The API key comes from the env var named by `--api-key-env` (default `OPENAI_API_KEY`), or pass
  `--api-key`.
- `.envrc` loads `.env` via `direnv` if present — drop `FEATHERLESS_API_KEY=…` (or whatever) there.
- The skill suggests `MATHX_MODEL` and `MATHX_BASE_URL` as shell-side defaults that the agent reads.

## Privacy

mathx itself sends prompts to whatever endpoint you point it at. Featherless is no-train. For
sensitive / unpublished work, set `--base-url` to a local oMLX or vLLM endpoint — no other code
change.

## What mathx is NOT

- Not a Lean prover. Provers are a separate problem; see [pudding](https://github.com/danmackinlay/pudding).
- Not a TIR (tool-integrated-reasoning) sandbox. The calling agent already has a Python tool.
- Not a provider registry — one OpenAI-compatible client + flags is enough.
- Not an MCP server (yet). The CLI works because Claude Code has `run_in_background`. Promote to MCP
  when a second frontend (Claude Desktop, Open WebUI, Goose) actually matters.

## Licence

MIT.
