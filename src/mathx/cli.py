"""mathx CLI: ``mathx solve …``, ``mathx install-skill``."""
from __future__ import annotations

import asyncio
import json
import shutil
import sys
from pathlib import Path

import click

from mathx.engine import result_to_dict, solve

STRATEGIES = ["cot", "maj@k", "self_verify"]


@click.group()
@click.version_option(package_name="mathx")
def cli() -> None:
    """A maths oracle for AI agents."""


@cli.command(name="solve")
@click.argument("problem")
@click.option(
    "--strategy",
    type=click.Choice(STRATEGIES),
    default="maj@k",
    show_default=True,
)
@click.option("--k", type=int, default=16, show_default=True)
@click.option(
    "--model",
    required=True,
    envvar="MATHX_MODEL",
    help='e.g. "deepseek-ai/DeepSeek-V3"; or set $MATHX_MODEL',
)
@click.option(
    "--base-url",
    required=True,
    envvar="MATHX_BASE_URL",
    help='e.g. "https://api.featherless.ai/v1"; or set $MATHX_BASE_URL',
)
@click.option(
    "--api-key",
    required=True,
    envvar=("MATHX_API_KEY", "OPENAI_API_KEY"),
    help="API key; or set $MATHX_API_KEY (preferred) or $OPENAI_API_KEY",
)
@click.option(
    "--temperature",
    type=float,
    default=None,
    help="default: 0.0 for cot, 0.7 otherwise",
)
@click.option("--max-tokens", type=int, default=16000, show_default=True)
@click.option(
    "--out",
    type=click.Path(dir_okay=False, path_type=Path),
    default=None,
    help="write full JSON to this path (audit trail)",
)
def solve_cmd(
    problem: str,
    strategy: str,
    k: int,
    model: str,
    base_url: str,
    api_key: str,
    temperature: float | None,
    max_tokens: int,
    out: Path | None,
) -> None:
    """Fan out k samples and vote on the answer."""
    result = asyncio.run(
        solve(
            problem,
            model=model,
            base_url=base_url,
            api_key=api_key,
            k=k,
            strategy=strategy,  # type: ignore[arg-type]
            temperature=temperature,
            max_tokens=max_tokens,
        )
    )

    if out is not None:
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(result_to_dict(result), indent=2))

    click.echo(f"answer: {result.answer}")
    click.echo(
        f"margin: {result.margin}   strategy: {result.strategy}   "
        f"model: {result.model}   k: {result.k}"
    )
    click.echo(
        f"tokens: in={result.tokens_in_total} out={result.tokens_out_total}   "
        f"elapsed: {result.elapsed_ms_total} ms"
    )
    if len(result.votes) > 1:
        click.echo("vote split (weight, answer):")
        for rep, w in result.votes.items():
            click.echo(f"  {w:>6.2f}  {rep}")
    if out is not None:
        click.echo(f"json -> {out}", err=True)
    if result.answer is None:
        sys.exit(1)


def _skill_source() -> Path:
    """Path to the SKILL.md dir in the source tree (dev install assumption).

    ``mathx install-skill`` only works when mathx was installed in editable mode
    (``uv tool install -e``) — the .claude/ tree must be alongside src/. Ship as
    package data if the tool ever goes upstream.
    """
    return Path(__file__).resolve().parents[2] / ".claude" / "skills" / "maths-oracle"


# Where each skill-reading client looks. ``agents`` is the emerging cross-tool
# shared standard (Goose, Gemini CLI, Codex, Warp, Multica all read it); the
# others are per-agent dirs for clients that haven't adopted it yet.
TARGETS: dict[str, Path] = {
    "agents": Path.home() / ".agents" / "skills" / "maths-oracle",
    "claude": Path.home() / ".claude" / "skills" / "maths-oracle",
    "pi":     Path.home() / ".pi" / "agent" / "skills" / "maths-oracle",
    "hermes": Path.home() / ".hermes" / "skills" / "maths-oracle",
}


def _install_one(src: Path, dst: Path, *, copy: bool, force: bool) -> str:
    """Install src -> dst. Returns a short status string for printing."""
    if dst.exists() or dst.is_symlink():
        if not force:
            return f"SKIPPED {dst} (already exists; pass --force to overwrite)"
        if dst.is_symlink() or dst.is_file():
            dst.unlink()
        else:
            shutil.rmtree(dst)

    dst.parent.mkdir(parents=True, exist_ok=True)
    if copy:
        shutil.copytree(src, dst)
        return f"copied   {src} -> {dst}"
    dst.symlink_to(src, target_is_directory=True)
    return f"linked   {src} -> {dst}"


@cli.command(name="install-skill")
@click.option(
    "--target",
    type=click.Choice([*TARGETS.keys(), "all"]),
    default="claude",
    show_default=True,
    help="which agent's skills directory to install into",
)
@click.option("--copy", is_flag=True, help="copy instead of symlink")
@click.option("--force", is_flag=True, help="overwrite if destination exists")
def install_skill_cmd(target: str, copy: bool, force: bool) -> None:
    """Install the maths-oracle SKILL.md into an agent's skills directory.

    All targets read the agentskills.io SKILL.md format. ``agents`` is the
    emerging cross-tool shared location:

    \b
      agents  -> ~/.agents/skills/maths-oracle/
                 (Goose, Gemini CLI, Codex, Warp, Multica, ...)
      claude  -> ~/.claude/skills/maths-oracle/
                 (Claude Code; doesn't read ~/.agents/skills/ yet, FR #66352)
      pi      -> ~/.pi/agent/skills/maths-oracle/
      hermes  -> ~/.hermes/skills/maths-oracle/
      all     -> every target whose parent dir already exists

    Goose also reads ~/.claude/skills/ for backward compatibility, so
    ``--target=claude`` also covers Goose. The ``agents`` target is the
    forward-looking choice. For Qwen-Agent / Open WebUI / Claude Desktop, the
    portable path is MCP (see MCP_PLAN.md), currently deferred.
    """
    src = _skill_source()
    if not src.exists():
        raise click.ClickException(
            f"skill source not found at {src}. "
            "Install in editable mode (`uv tool install -e ./mathx`)."
        )

    if target == "all":
        # Only install where the agent appears to be installed (parent of skills/ exists).
        # Avoids creating phantom config trees for clients the user doesn't have.
        for name, dst in TARGETS.items():
            agent_home = dst.parent.parent  # ~/.<agent>/
            if not agent_home.exists():
                click.echo(f"skipped  {name}: {agent_home} does not exist")
                continue
            click.echo(_install_one(src, dst, copy=copy, force=force))
    else:
        click.echo(_install_one(src, TARGETS[target], copy=copy, force=force))


def main() -> None:
    cli()


if __name__ == "__main__":
    main()
