"""mathx CLI: ``mathx solve …``, ``mathx install-skill``."""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import shutil
import sys
from pathlib import Path

from mathx.engine import result_to_dict, solve


def _solve_cmd(args: argparse.Namespace) -> int:
    api_key = args.api_key or os.environ.get(args.api_key_env)
    if not api_key:
        sys.stderr.write(
            f"error: no API key — pass --api-key or set ${args.api_key_env}\n"
        )
        return 2

    result = asyncio.run(
        solve(
            args.problem,
            model=args.model,
            base_url=args.base_url,
            api_key=api_key,
            k=args.k,
            strategy=args.strategy,
            temperature=args.temperature,
            max_tokens=args.max_tokens,
        )
    )

    if args.out:
        out = Path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(result_to_dict(result), indent=2))

    # human-readable summary on stdout
    print(f"answer: {result.answer}")
    print(
        f"margin: {result.margin}   strategy: {result.strategy}   "
        f"model: {result.model}   k: {result.k}"
    )
    print(
        f"tokens: in={result.tokens_in_total} out={result.tokens_out_total}   "
        f"elapsed: {result.elapsed_ms_total} ms"
    )
    if len(result.votes) > 1:
        print("vote split (weight, answer):")
        for rep, w in result.votes.items():
            print(f"  {w:>6.2f}  {rep}")
    if args.out:
        sys.stderr.write(f"json -> {args.out}\n")
    return 0 if result.answer is not None else 1


def _skill_source() -> Path:
    """Path to the SKILL.md dir in the source tree (dev install assumption).

    ``mathx install-skill`` currently only works when mathx was installed in editable
    mode (``uv tool install -e``) — the .claude/ tree must be alongside src/. Document
    this in the README; ship as package data if the tool ever goes upstream.
    """
    return Path(__file__).resolve().parents[2] / ".claude" / "skills" / "maths-oracle"


def _install_skill_cmd(args: argparse.Namespace) -> int:
    src = _skill_source()
    if not src.exists():
        sys.stderr.write(
            f"error: skill source not found at {src}\n"
            "Are we installed in editable mode (`uv tool install -e ./mathx`)?\n"
        )
        return 2

    dst_parent = Path.home() / ".claude" / "skills"
    dst_parent.mkdir(parents=True, exist_ok=True)
    dst = dst_parent / "maths-oracle"

    if dst.exists() or dst.is_symlink():
        if not args.force:
            sys.stderr.write(
                f"error: {dst} already exists — pass --force to overwrite\n"
            )
            return 2
        if dst.is_symlink() or dst.is_file():
            dst.unlink()
        else:
            shutil.rmtree(dst)

    if args.copy:
        shutil.copytree(src, dst)
        print(f"copied {src} -> {dst}")
    else:
        dst.symlink_to(src, target_is_directory=True)
        print(f"symlinked {dst} -> {src}")
    return 0


def main() -> int:
    p = argparse.ArgumentParser(
        prog="mathx", description="A maths oracle for AI agents."
    )
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("solve", help="fan out k samples and vote")
    s.add_argument("problem", help="the problem (natural language; LaTeX OK)")
    s.add_argument(
        "--strategy",
        default="maj@k",
        choices=("cot", "maj@k", "self_verify"),
        help="(default: %(default)s)",
    )
    s.add_argument("--k", type=int, default=16, help="(default: %(default)s)")
    s.add_argument("--model", required=True, help='e.g. "deepseek-ai/DeepSeek-V3"')
    s.add_argument(
        "--base-url", required=True, help='e.g. "https://api.featherless.ai/v1"'
    )
    s.add_argument("--api-key", default=None, help="overrides --api-key-env")
    s.add_argument(
        "--api-key-env",
        default="OPENAI_API_KEY",
        help="env var holding the API key (default: %(default)s)",
    )
    s.add_argument(
        "--temperature",
        type=float,
        default=None,
        help="default: 0.0 for cot, 0.7 otherwise",
    )
    s.add_argument(
        "--max-tokens", type=int, default=16000, help="(default: %(default)s)"
    )
    s.add_argument(
        "--out", default=None, help="write full JSON to this path (audit trail)"
    )
    s.set_defaults(func=_solve_cmd)

    i = sub.add_parser(
        "install-skill",
        help="install the maths-oracle SKILL.md into ~/.claude/skills/",
    )
    i.add_argument("--copy", action="store_true", help="copy instead of symlink")
    i.add_argument(
        "--force", action="store_true", help="overwrite if destination exists"
    )
    i.set_defaults(func=_install_skill_cmd)

    args = p.parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
