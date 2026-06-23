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


@cli.command(name="install-skill")
@click.option("--copy", is_flag=True, help="copy instead of symlink")
@click.option("--force", is_flag=True, help="overwrite if destination exists")
def install_skill_cmd(copy: bool, force: bool) -> None:
    """Install the maths-oracle SKILL.md into ~/.claude/skills/."""
    src = _skill_source()
    if not src.exists():
        raise click.ClickException(
            f"skill source not found at {src}. "
            "Install in editable mode (`uv tool install -e ./mathx`)."
        )

    dst_parent = Path.home() / ".claude" / "skills"
    dst_parent.mkdir(parents=True, exist_ok=True)
    dst = dst_parent / "maths-oracle"

    if dst.exists() or dst.is_symlink():
        if not force:
            raise click.ClickException(
                f"{dst} already exists — pass --force to overwrite"
            )
        if dst.is_symlink() or dst.is_file():
            dst.unlink()
        else:
            shutil.rmtree(dst)

    if copy:
        shutil.copytree(src, dst)
        click.echo(f"copied {src} -> {dst}")
    else:
        dst.symlink_to(src, target_is_directory=True)
        click.echo(f"symlinked {dst} -> {src}")


def main() -> None:
    cli()


if __name__ == "__main__":
    main()
