# Open WebUI integration

The Pipe is the primary Open WebUI face (ROADMAP Stage 5): the loop runs in
mathx code and streams progress via the status emitter, so it works no matter
which model a profile routes each role to — including specialists that can't
drive tools at all.

## Install

1. Make `mathx` importable in Open WebUI's Python environment. The docstring's
   `requirements:` line does this automatically when you upload the Function
   (Open WebUI pip-installs it); for a Docker deployment you can instead bake
   `pip install git+https://github.com/danmackinlay/mathx` into the image.
2. Admin Panel → **Functions** → import [`mathx_pipe.py`](mathx_pipe.py) and
   enable it. Three entries appear in the model picker:
   *mathx · solve*, *mathx · check claim*, *mathx · argue (claim ledger)*.
3. Configure the provider where the **Open WebUI process** can see it: either
   set the `PROFILE` valve to a profile in `~/.config/mathx/config.toml`
   (recommended — carries the model/meta_model role split, sampling, pacing),
   or export `MATHX_MODEL` / `MATHX_BASE_URL` / `MATHX_API_KEY` in Open WebUI's
   environment.

## Use

Pick a mathx entry as the model and type the problem (solve/argue) or the
claim (check) as a normal chat message. Progress streams as status lines
(per-sample completions, per-claim verdicts, refinement rounds); the reply is
the rendered report or ledger. Ledgers and jobs land in the same stores the
CLI reads, so a ledger built in chat can be inspected or escalated from the
terminal (`mathx show <ledger-id>`, `mathx ledger recheck …`).

MCP registration in Open WebUI also works (`mathx mcp-serve`) but is the
agent-client path — in chat it leaves orchestration to the served model's
tool-calling, which specialist maths models can't do. Prefer the Pipe here.
