# MCP server plan (deferred)

mathx currently exposes itself as a CLI plus an agentskills.io SKILL.md that Claude Code, pi, and
Hermes auto-discover. The portable extension path for every other MCP-capable agent — Claude
Desktop, Goose, Qwen-Agent, Open WebUI, Cursor, VS Code Copilot — is to also expose mathx as an
MCP server. This doc captures what to build, why, what NOT to build, and the load-bearing
decisions, so a future collaborator can pick up the work without re-deriving the research.

## Why this is deferred

The CLI + skill is enough for Claude Code today, and Claude Code is the daily driver. MCP becomes
the right unlock when one of these starts happening for real:

- A second frontend pulls — Cowork, Open WebUI, Goose, Qwen-Agent shows up in regular use.
- Many sessions need a shared result cache — file-per-job under `--out` already gives this for
  Claude Code, but only because every caller knows the convention; MCP makes the cache
  first-class.
- A non-bash agent (Claude Desktop, Qwen-Agent, Goose) needs to call mathx and shelling out
  isn't acceptable for that client.

Until one of those fires, MCP is a yet-another-server-to-run for no real gain.

## What MCP unlocks

| Client | Without MCP | With MCP |
|---|---|---|
| Claude Code | ✓ via skill + background Bash | also ✓ via MCP (alternative path; either works) |
| Claude Desktop | ✗ | ✓ |
| Cowork | ✗ | ✓ |
| Goose | ✗ | ✓ (Goose's whole extension model is MCP) |
| Qwen-Agent | ✗ (or hand-written Python wrapper) | ✓ via `mcpServers` config |
| Open WebUI | ✗ | ✓ (native since v0.6.31, else via [mcpo](https://github.com/open-webui/mcpo)) |
| Cursor | ✗ | ✓ |
| VS Code Copilot | ✗ | ✓ |
| pi, Hermes | ✓ via skill | also ✓ via MCP if user prefers (both support `mcpServers`) |

## Why handle/poll, not MCP Tasks

This is the most important decision in the doc — getting it wrong ships a server that times out in
every client. Verified picture of async-in-MCP as of mid-2026:

1. **Own the loop → `asyncio`.** The laptop path: `import mathx.engine.solve` and `await` it. No
   MCP, no timeouts. Not applicable to a server — we own the SERVER, not the caller's loop.
2. **Application-level handle/poll, two ordinary tools.** `submit_solve(...)` returns a job id
   instantly; `check_solve(id)` returns status/result instantly. Each call returns fast, so the
   synchronous-tool cap never bites; the agent polls. Works in *any* tool-capable MCP client
   today, no Tasks support required. The pragmatic universal answer
   ([dev.to/aws](https://dev.to/aws/fix-mcp-timeouts-async-handleid-pattern-8ek)).
3. **Harness-native background async.** Claude Code's `run_in_background`, sub-agents, etc.
   Per-client; not portable; not what an MCP server can use.
4. **MCP Tasks (protocol-level).** The
   [2025-11-25 spec](https://modelcontextprotocol.io/specification/2025-11-25/basic/utilities/tasks)
   (cf. SEP-1391) standardises handle/poll into the protocol. Nice when both ends implement it;
   not required; unevenly supported across clients.

**Build this server as route #2 (application-level handle/poll).** Reasons:

- Works in every MCP-capable client today, regardless of whether they implement Tasks.
- Sidesteps the synchronous MCP tool-call timeout — e.g. Claude Code's un-raisable ~60 s cap
  (open feature requests
  [#47076](https://github.com/anthropics/claude-code/issues/47076),
  [#22542](https://github.com/anthropics/claude-code/issues/22542) — no env knob).
  Both `submit_solve` and `check_solve` return instantly, so the cap is irrelevant.
- We can layer MCP Tasks on later if a specific client materially benefits — `submit_solve` /
  `check_solve` already model the same lifecycle.

**Do not** implement this server as a single synchronous `solve` MCP tool. That naïve shape times
out on every wide fan-out in every client with any tool-call cap. This trap is what made me
write down the four-route picture in the first place.

## Design sketch

### Tools

```python
# pseudo-signatures; FastMCP generates the schema from type hints
async def submit_solve(
    problem: str,
    *,
    strategy: Literal["cot", "maj@k", "self_verify"] = "maj@k",
    k: int = 16,
    model: str | None = None,         # falls back to MATHX_MODEL env var
    base_url: str | None = None,      # falls back to MATHX_BASE_URL env var
    temperature: float | None = None,
    max_tokens: int = 16000,
) -> dict:
    """Kick off a fan-out. Returns immediately.

    Returns: {"job_id": str, "status": "running", "started_at": ISO8601}
    """

async def check_solve(job_id: str) -> dict:
    """Poll a previously-submitted job. Returns immediately.

    Returns one of:
      {"job_id", "status": "running",  "started_at", "elapsed_ms"}
      {"job_id", "status": "complete", "result": <full Result JSON>}
      {"job_id", "status": "error",    "error": str}
    """
```

The `result` field inside the `complete` response is exactly the shape `mathx solve --out` writes
today (see README *Output shape*) — same engine path: `mathx.engine.solve(...)`. **No new logic
in the MCP server itself**; it's a job-lifecycle wrapper over the existing engine.

### Job store

File-per-job under `~/.cache/mathx/jobs/<job_id>.json` (honour `XDG_CACHE_HOME` if set). Job id =
ISO timestamp + 4-char random suffix.

- On `submit_solve`: write `{"job_id", "status": "running", "started_at", "args": {...}}`
  atomically (write to `<id>.json.tmp`, rename in place). Spawn the worker (see below).
- Worker on completion: atomically replace the file with
  `{"status": "complete", "result": {...}}` (or `{"status": "error", "error": ...}`).
- On `check_solve`: read the file and return its contents. Missing file → `error: "unknown
  job_id"`.
- Cleanup: small TTL pass (24h+) when the server starts. Or a manual `mathx mcp-prune` subcommand
  if needed.

Why filesystem rather than sqlite/redis: zero new deps, survives MCP-server restart (in-flight
jobs become orphans whose file stays at `status: running` forever — `check_solve` can detect
staleness via `started_at`), trivial to inspect with `cat ~/.cache/mathx/jobs/*.json | jq`.
Promote to sqlite only if multi-server result-sharing or query-by-status becomes a real need.

### Worker — coroutine first, subprocess as fallback

Two options, lean toward (a):

(a) **`asyncio.create_task(engine.solve(...))` inside the FastMCP event loop.** The task writes
its result file on completion. Pros: no subprocess overhead; cleanly cancellable on server
shutdown; uses the existing async API as-is. Cons: killing the MCP server kills in-flight jobs.

(b) **Spawn `mathx solve --out <path>` as a subprocess.** Pros: jobs survive an MCP-server bounce.
Cons: process management, harder to cancel, extra interpreter startup per call.

Start with (a). Promote to (b) only if "server restart kills in-flight jobs" turns out to actually
hurt — for a personal oracle restarted only occasionally, (a) is fine.

### Entry point

New CLI subcommand: `mathx mcp-serve` (stdio transport by default; add `--port` for HTTP/SSE later
if a client needs it).

### Dependencies

Add `mcp[cli]>=1.0` (FastMCP). One dep, pure Python, ~200 KB. Same shape as Click.

## Per-client wiring (have these ready)

Assume `MATHX_MODEL`, `MATHX_BASE_URL`, `MATHX_API_KEY` are set in the parent shell. Otherwise add
an `env` block to each client's config.

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

**Open WebUI** — native MCP (v0.6.31+): Admin Panel → Settings → MCP → Add stdio server, command
`mathx mcp-serve`. Pre-v0.6.31: via [mcpo](https://github.com/open-webui/mcpo) —
`mcpo --port 8000 -- mathx mcp-serve`, then add the OpenAPI URL as a Tool in Open WebUI.

**Cursor** — `~/.cursor/mcp.json`:

```json
{ "mcpServers": { "mathx": { "command": "mathx", "args": ["mcp-serve"] } } }
```

**VS Code Copilot** — `.vscode/mcp.json` per-project (or in user settings for global), same shape
as Cursor.

## Verification (when built)

1. `mathx mcp-serve --help` runs cleanly and shows the two tools.
2. Wire mathx into one MCP client (Claude Desktop is the easiest to test) and ask a hard maths
   question. Confirm the agent calls `submit_solve` (returns immediately with a job id), polls
   `check_solve` a few times, and finally gets the answer JSON.
3. The `result` JSON from `check_solve` matches what `mathx solve --out` writes for the same
   inputs (modulo IDs/timestamps).
4. A `--k 64` job overruns any tool-call cap in the client; the agent still gets its answer.
5. Two concurrent `submit_solve` calls don't collide on filenames.
6. Killing the MCP server mid-job: the next `check_solve` after restart returns
   `status: "running"` with a stale `started_at` (or `status: "error"` if a timeout policy is
   added — TBD).

## What stays out

- **TIR strategy.** Same reason as today — the calling agent has its own Python tool. Revisit if
  a specialist-only-via-fenced-code model enters the rotation.
- **Prover surface.** Separate problem; gated in pudding.
- **Provider registry.** One `--base-url` / `--model` (or env vars). No registry.
- **Audition / eval harness.** Different workflow.
- **MCP Resources or Prompts.** The two-tool surface is enough. Expose Resources only if there's
  a concrete need (e.g. making the job store readable as MCP resources for an Inspector UI).
- **Authentication, multi-user, rate-limiting.** Personal-oracle scope. Out unless mathx grows a
  shared deployment story.

## Decision log

- **2026-06-23** — Initial plan written after the agentskills.io install-skill landed for
  Claude/pi/Hermes. The cross-agent skill-installer research confirmed there is no shared format
  beyond agentskills.io's three adopters; everything else is MCP. The async fact-check confirmed
  MCP Tasks is one of four async routes, not the only one — handle/poll is the right primary.
  MCP work itself is deferred until a second frontend pulls.
