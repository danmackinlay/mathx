# MCP server — design notes (shipped)

`mathx mcp-serve` (stdio) shipped as ROADMAP Stage 2 and grew to the full agent-client surface
in Stage 5: `submit_solve` / `submit_check` / `submit_argue` / `poll_job` / `list_jobs` /
`list_ledgers` / `get_ledger` / `recheck_claim` / `challenge_claim`, all over the Stage-2 job
store in [`src/mathx/jobs.py`](src/mathx/jobs.py). Every tool is a thin lifecycle wrapper over
the existing engine — no maths logic lives in [`src/mathx/mcp_server.py`](src/mathx/mcp_server.py).

This doc keeps the two things that outlived the build — the load-bearing async decision and the
per-client wiring — plus the decision log. The rest of the pre-build sketch (tool signatures,
job-store layout, worker coroutine-vs-subprocess) is now just the code and the log.

## Why handle/poll, not MCP Tasks

The decision that matters — getting it wrong ships a server that times out in every client. The
four async-in-MCP routes, surveyed mid-2026:

1. **Own the loop → `asyncio`.** The laptop path: `import mathx.engine.solve` and `await` it.
   Not applicable to a server — we own the server, not the caller's loop.
2. **Application-level handle/poll, two ordinary tools.** `submit_*(...)` returns a job id
   instantly; `poll_job(id)` returns status/result instantly. Each call returns fast, so the
   synchronous-tool cap never bites; the agent polls. Works in *any* tool-capable MCP client
   today, no Tasks support required
   ([dev.to/aws](https://dev.to/aws/fix-mcp-timeouts-async-handleid-pattern-8ek)).
3. **Harness-native background async.** Claude Code's `run_in_background`, sub-agents, etc.
   Per-client; not portable; not something an MCP server can rely on.
4. **MCP Tasks (protocol-level).** The
   [2025-11-25 spec](https://modelcontextprotocol.io/specification/2025-11-25/basic/utilities/tasks)
   (cf. SEP-1391) standardises handle/poll into the protocol. Nice when both ends implement it;
   not required; unevenly supported.

**mathx is route #2.** It works in every MCP-capable client regardless of Tasks support, and
sidesteps the synchronous tool-call timeout — e.g. Claude Code's un-raisable ~60 s cap (open
requests [#47076](https://github.com/anthropics/claude-code/issues/47076),
[#22542](https://github.com/anthropics/claude-code/issues/22542)). Both submit and poll return
instantly, so the cap is irrelevant, and MCP Tasks can layer on later — the submit/poll tools
already model the same lifecycle. **Do not** collapse this to a single synchronous `solve`
tool: it times out on every wide fan-out in every client with a tool-call cap.

## Per-client wiring

Assume `MATHX_MODEL`, `MATHX_BASE_URL`, `MATHX_API_KEY` are set in the parent shell. Otherwise
add an `env` block to each client's config.

**Claude Desktop** — `~/Library/Application Support/Claude/claude_desktop_config.json`:

```json
{ "mcpServers": { "mathx": { "command": "mathx", "args": ["mcp-serve"] } } }
```

**Claude Code** — `claude mcp add mathx -- mathx mcp-serve` (per-project; add `--scope user` for
global).

**Goose** — `goose configure` → Add Extension → command type `stdio` → `mathx mcp-serve`. Or edit
`~/.config/goose/config.yaml` directly.

**Qwen-Agent** — in the Assistant config:

```python
Assistant(
    ...,
    function_list=["mcp"],
    mcp_servers={"mathx": {"command": "mathx", "args": ["mcp-serve"]}},
)
```

**Open WebUI** — the [Pipe](integrations/openwebui/) is the preferred face (ROADMAP Stage 5); MCP
works too, native (v0.6.31+): Admin Panel → Settings → MCP → Add stdio server, command
`mathx mcp-serve`. Pre-v0.6.31: via [mcpo](https://github.com/open-webui/mcpo) —
`mcpo --port 8000 -- mathx mcp-serve`, then add the OpenAPI URL as a Tool.

**Cursor** — `~/.cursor/mcp.json`:

```json
{ "mcpServers": { "mathx": { "command": "mathx", "args": ["mcp-serve"] } } }
```

**VS Code Copilot** — `.vscode/mcp.json` per-project (or user settings for global), same shape as
Cursor.

## What stays out

- **MCP Resources or Prompts.** The tool surface is enough. Expose Resources only for a concrete
  need (e.g. making the job store browsable in an Inspector UI).
- **Authentication, multi-user, rate-limiting.** Personal-oracle scope, until mathx grows a
  shared deployment story.

## Decision log

- **2026-06-23** — Initial plan written after the agentskills.io install-skill landed for
  Claude/pi/Hermes. The cross-agent skill-installer research confirmed there is no shared format
  beyond agentskills.io's three adopters; everything else is MCP. The async fact-check confirmed
  MCP Tasks is one of four async routes, not the only one — handle/poll is the right primary.
  MCP work itself is deferred until a second frontend pulls.
- **2026-06-23 (correction)** — Earlier "agentskills.io has only three adopters" framing was
  wrong. The actual landscape: **`~/.agents/skills/` is the emerging cross-tool shared standard**
  ([Goose](https://goose-docs.ai/docs/guides/context-engineering/using-skills),
  [Warp](https://docs.warp.dev/agent-platform/capabilities/skills/),
  [Codex](https://developers.openai.com/codex/skills), Gemini CLI, Multica all read it);
  Claude Code is the holdout
  ([anthropics/claude-code#66352](https://github.com/anthropics/claude-code/issues/66352));
  Goose ALSO discovers `~/.claude/skills/` for backward compat — so `mathx install-skill
  --target=claude` was already silently covering Goose users. Added `--target=agents` for the
  shared dir; MCP's unlock is now narrower (Claude Desktop / Cowork / Qwen-Agent / Open WebUI /
  Cursor / VS Code Copilot, plus the "alternative path" option for skill-supporting clients).
  The decision to defer the server still holds: a second frontend hasn't pulled yet, and the
  skill route covers more of the obvious second-frontend candidates than I'd realised.
- **2026-06-23 (further refinement)** — Qwen-Agent isn't actually MCP-only either. Its
  extension model is programmatic Python (`register_tool` + `BaseTool`), and since mathx ships
  as a library, the cleanest integration is direct import: `from mathx.engine import solve` from
  inside a `BaseTool` subclass. Pattern lives at
  [`examples/qwen_agent_tool.py`](examples/qwen_agent_tool.py). MCP's exclusive unlock narrows
  again — now Claude Desktop / Cowork / Open WebUI / Cursor / VS Code Copilot (Qwen-Agent gets
  MCP as alt, not as primary).
- **2026-06-25** — Deleted mathx's hand-rolled `install-skill` command (and its `--target`
  matrix). The open cross-agent skills CLI (`npx skills` / [skills.sh](https://skills.sh)) already
  discovers the repo's `skills/maths-oracle/SKILL.md` and installs it project-local or
  global (`-g`) to any of 30+ agents (`-a`), with update/remove — so maintaining our own installer
  was redundant. mathx now ships only `solve` + a print-only `doctor` (binary-on-PATH and
  skill-installed checks; never mutates files). The `~/.agents/skills/` vs per-agent-dir detail
  above is now the skills CLI's concern, not ours. Also moved the canonical SKILL.md out of the
  Claude-specific `.claude/skills/` to an agent-neutral top-level `skills/` (still a `npx skills`
  discovery location); `.claude/` now holds only local dev settings.
- **2026-07-03** — Built, as ROADMAP Stage 2 (the "persistent job store" pivot pulled it
  forward — the store is the substrate for every later surface, and MCP is just one reader of
  it). Deviations from the pre-build sketch:
  - **Worker is a detached subprocess, not a coroutine-in-server.** The plan leaned
    coroutine-in-server, but Stage 2 also added CLI verbs (`mathx submit`/`status`/`jobs`), and a
    CLI `submit` *requires* a worker that outlives the submitting process. Once
    `python -m mathx.jobs <job_id>` existed, reusing it from the MCP server meant one code path,
    no in-server task bookkeeping, and jobs that survive an MCP-server bounce for free. The
    coroutine's claimed pros (no subprocess overhead, cancellable) don't matter at fan-out
    timescales.
  - **No TTL pass on server start; pruning is manual** (`mathx jobs --prune HOURS`). The
    roadmap's framing — "runs get identity and history" — makes auto-deleting history wrong.
  - **API key never touches the job file.** Workers resolve `MATHX_API_KEY`/`OPENAI_API_KEY`
    from their environment at run time; `mathx submit --api-key …` passes an explicit key via
    the child's env only.
  - `submit_solve` also grew `max_k` (weak-margin auto-escalation, added in Stage 1 after this
    plan was written).
- **2026-07-05 (Stage 5)** — The full agent-client surface landed:
  - **`check_solve` renamed to `poll_job`** (breaking, pre-deployment — nothing was wired
    anywhere but this machine). The old name meant "poll a job", which stopped being readable
    once claim *checks* existed.
  - New tools: `submit_check`, `submit_argue` (returns job id AND ledger_id immediately — the
    ledger file updates live, so `get_ledger` is the progress stream), `list_jobs` /
    `list_ledgers` (compact, no sample texts — token discipline for agent clients),
    `recheck_claim`, `challenge_claim`. `expand` stays CLI-only: it blocks on a decomposition
    call, which violates handle/poll; make it a job kind if an agent client ever needs it.
  - All submit tools take `profile` (resolution shared with the CLI via
    `config.resolve_provider`). Keys remain env-only.
  - This forced background argue (`kind: "argue"` jobs) — LOOP_PLAN's stays-out overridden by
    the handle/poll requirement; its decision log records the reversal. CLI `submit --argue`
    is still not exposed (trivially possible; no CLI pull yet).
  - Open WebUI is NOT this surface's constituency anymore: it integrates via the Pipe
    (`integrations/openwebui/`), per the ROADMAP Stage 5 decision.
