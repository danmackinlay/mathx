"""mathx CLI: ``mathx solve …``, ``mathx doctor``."""
from __future__ import annotations

import asyncio
import json
import shutil
import sys
import tomllib
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
    help='e.g. "deepseek/deepseek-v4-pro"; or set $MATHX_MODEL',
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


# The maths-oracle SKILL.md ships in this repo at skills/ (agent-neutral); install it
# with the cross-agent open-skills CLI: `npx skills add danmackinlay/mathx`
# (project-local by default, `-g` for global, `-a <agent>` to target one).
# `mathx doctor` below diagnoses a setup but installs nothing.

GIT_REPO = "git+https://github.com/danmackinlay/mathx"

# Where the open-skills CLI drops the skill, per agent (project + home roots).
_SKILL_SUBDIRS = (
    ".claude/skills",
    ".agents/skills",
    ".agent/skills",
    ".codex/skills",
    ".cursor/skills",
    ".gemini/skills",
    ".opencode/skills",
)
_SKIP_DIRS = {".venv", "venv", ".git", "node_modules", "__pycache__",
              "site-packages", ".tox", "dist", "build"}


def _find_up(start: Path, name: str) -> Path | None:
    """Return the nearest `name` in `start` or an ancestor, else None."""
    for d in (start, *start.parents):
        candidate = d / name
        if candidate.is_file():
            return candidate
    return None


def _dep_name(spec: str) -> str:
    """Package name from a PEP 508 dependency spec (no regex needed)."""
    s = spec.strip()
    for sep in (" ", "@", "[", "<", ">", "=", "!", "~", ";", "("):
        s = s.split(sep)[0]
    return s.strip().lower().replace("_", "-")


def _read_pyproject(path: Path) -> tuple[str, bool]:
    """Return (project name, whether mathx is a declared dependency)."""
    try:
        data = tomllib.loads(path.read_text())
    except (OSError, tomllib.TOMLDecodeError):
        return "", False
    proj = data.get("project", {})
    name = (proj.get("name") or "").lower()
    deps: list[str] = list(proj.get("dependencies", []))
    for grp in proj.get("optional-dependencies", {}).values():
        deps += grp
    for grp in data.get("dependency-groups", {}).values():  # PEP 735
        deps += [g for g in grp if isinstance(g, str)]
    return name, any(_dep_name(d) == "mathx" for d in deps)


def _project_imports_mathx(root: Path) -> bool:
    """Best-effort: does any .py under `root` import mathx (a library use)?"""
    try:
        for py in root.rglob("*.py"):
            if _SKIP_DIRS & set(py.parts):
                continue
            try:
                text = py.read_text(errors="ignore")
            except OSError:
                continue
            if "import mathx" in text or "from mathx" in text:
                return True
    except OSError:
        return False
    return False


def _find_skill() -> list[Path]:
    """Dirs where a maths-oracle SKILL.md is already installed (project + home)."""
    found: list[Path] = []
    for root in (Path.cwd(), Path.home()):
        for sub in _SKILL_SUBDIRS:
            d = root / sub / "maths-oracle"
            if (d / "SKILL.md").exists():
                found.append(d)
    return found


@cli.command(name="doctor")
def doctor_cmd() -> None:
    """Diagnose a mathx setup and print fixes. Changes nothing.

    Checks whether `mathx` is on PATH, recommends how to install it
    for the current project (a Python project that imports mathx wants it as a
    dependency; otherwise an isolated `uv tool install` / `uvx` is cleaner),
    and reports whether the maths-oracle skill is installed.
    """
    echo = click.echo

    mathx_path = shutil.which("mathx")
    if mathx_path:
        echo(f"✓ mathx on PATH: {mathx_path}")
    else:
        echo("✗ mathx: not found on PATH")

    pyproject = _find_up(Path.cwd(), "pyproject.toml")
    if pyproject is None:
        echo("• context: no pyproject.toml found — treat as a non-Python project")
        echo("  install mathx with (pick one):")
        echo(f"    uv tool install {GIT_REPO}")
        echo(f"    uvx --from {GIT_REPO} mathx solve …   # ephemeral, no install")
    else:
        name, has_dep = _read_pyproject(pyproject)
        root = pyproject.parent
        echo(f"• context: Python project '{name or root.name}' at {root}")
        if name == "mathx":
            echo("  this is the mathx repo itself:")
            echo("    uv tool install -e .                 # dev install on PATH")
        elif has_dep:
            echo("  mathx is already a declared dependency here. ✓")
        elif _project_imports_mathx(root):
            echo("  this project imports mathx as a library — add it as a dep:")
            echo(f"    uv add {GIT_REPO}")
        else:
            echo("  this project would shell out to `mathx` (doesn't import it).")
            echo("  prefer an isolated install over polluting project deps:")
            echo(f"    uv tool install {GIT_REPO}")
            echo(f"    uvx --from {GIT_REPO} mathx solve …   # ephemeral")

    skills = _find_skill()
    if skills:
        echo("✓ maths-oracle skill installed at:")
        for s in skills:
            echo(f"    {s}")
    else:
        echo("✗ maths-oracle skill: not found in this project or home dir")
        echo("  install it with the open-skills CLI:")
        echo("    npx skills add danmackinlay/mathx       # project-local (default)")
        echo("    npx skills add danmackinlay/mathx -g    # global")

    echo("")
    echo("note: mathx isn't on PyPI yet, so commands resolve via the git repo.")


def main() -> None:
    cli()


if __name__ == "__main__":
    main()
