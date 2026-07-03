"""Named profiles: reusable bundles of endpoint + role-model settings.

NOT a provider registry (that stays on the forbidden list): a profile carries
zero provider-specific logic — it only supplies defaults for flags that already
exist. Precedence everywhere: explicit flag > profile value > bare environment
variable > built-in default.

Discovery: ``mathx.toml`` in the working directory or an ancestor, else
``$XDG_CONFIG_HOME/mathx/config.toml`` (default ``~/.config/mathx/config.toml``).

Shape:

    [profiles.local]
    base_url = "http://localhost:8000/v1"
    model = "vibethinker-bf16"      # object-level maths: solve, grade, judge
    meta_model = "qwen3.6-35b"      # meta-tasks: decomposition, checker scripts
    temperature = 1.0
    top_p = 0.95
    max_tokens = 6000
    max_retries = 0
    concurrency = 3

    [profiles.cloud]
    base_url = "https://openrouter.ai/api/v1"
    model = "deepseek/deepseek-v4-flash"
    api_key_env = "OPENROUTER_API_KEY"
    extra_body = { reasoning = { effort = "high" } }

The role split exists because maths specialists are routinely bad at the
meta-tasks (live e2e: a specialist rambled 16k tokens without producing a
checker script); ``meta_model`` defaults to ``model`` when unset. API keys
never live in the file — ``api_key_env`` names the variable that holds one.
"""
from __future__ import annotations

import os
import tomllib
from pathlib import Path

PROFILE_KEYS = {
    "model",
    "meta_model",
    "base_url",
    "api_key_env",
    "temperature",
    "top_p",
    "max_tokens",
    "max_retries",
    "concurrency",
    "extra_body",
}


def find_config() -> Path | None:
    for d in (Path.cwd(), *Path.cwd().parents):
        candidate = d / "mathx.toml"
        if candidate.is_file():
            return candidate
    xdg = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config")
    candidate = xdg / "mathx" / "config.toml"
    return candidate if candidate.is_file() else None


def load_profiles(path: Path) -> dict[str, dict]:
    try:
        data = tomllib.loads(path.read_text())
    except (OSError, tomllib.TOMLDecodeError) as e:
        raise ValueError(f"cannot read {path}: {e}") from e
    profiles = data.get("profiles", {})
    if not isinstance(profiles, dict):
        raise ValueError(f"{path}: [profiles.<name>] tables expected")
    for name, profile in profiles.items():
        if not isinstance(profile, dict):
            raise ValueError(f"{path}: profile {name!r} is not a table")
        if "api_key" in profile:
            raise ValueError(
                f"{path}: profile {name!r} sets 'api_key' — keys never live in "
                "config files; use api_key_env to NAME the environment variable"
            )
        unknown = set(profile) - PROFILE_KEYS
        if unknown:
            raise ValueError(
                f"{path}: profile {name!r} has unknown key(s) {sorted(unknown)}; "
                f"known keys: {sorted(PROFILE_KEYS)}"
            )
    return profiles


def resolve_profile(name: str | None) -> dict:
    """The named profile's settings ({} when no profile applies).

    ``name`` falls back to $MATHX_PROFILE. Naming a profile without a config
    file, or a profile that isn't defined, is an error — silence would mean
    silently using the wrong model.
    """
    name = name or os.environ.get("MATHX_PROFILE") or None
    if name is None:
        return {}
    path = find_config()
    if path is None:
        raise ValueError(
            f"profile {name!r} requested but no config file found "
            "(mathx.toml beside/above the working dir, or ~/.config/mathx/config.toml)"
        )
    profiles = load_profiles(path)
    if name not in profiles:
        raise ValueError(
            f"no profile {name!r} in {path} (has: {', '.join(sorted(profiles)) or 'none'})"
        )
    return profiles[name]
