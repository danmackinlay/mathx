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

import json
import os
import tomllib
from pathlib import Path

PROFILE_KEYS = {
    "model",
    "meta_model",
    "equiv_judge_model",
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


def resolve_provider(
    *,
    profile: str | None = None,
    model: str | None = None,
    base_url: str | None = None,
    api_key: str | None = None,
    temperature: float | None = None,
    max_tokens: int | None = None,
    top_p: float | None = None,
    extra_body: dict | str | None = None,
    meta_model: str | None = None,
    equiv_judge_model: str | None = None,
    require: tuple = ("model", "base_url", "api_key"),
) -> dict:
    """Merge explicit values > profile > environment into provider settings.

    Every surface (CLI, MCP server, Open WebUI Pipe) resolves through here.
    ``require`` names keys that must resolve (ledger verbs relax model/base_url
    because the ledger supplies their fallback). Raises ValueError with a
    human-readable message; callers translate to their surface's error type.
    """
    prof = resolve_profile(profile)

    def pick(explicit, key: str, *env_names: str):
        if explicit is not None:
            return explicit
        if key in prof:
            return prof[key]
        for env in env_names:
            if os.environ.get(env):
                return os.environ[env]
        return None

    if api_key is None and prof.get("api_key_env"):
        api_key = os.environ.get(prof["api_key_env"])
        if not api_key:
            raise ValueError(
                f"profile names api_key_env={prof['api_key_env']!r} but that "
                "environment variable is empty"
            )
    if api_key is None:
        api_key = os.environ.get("MATHX_API_KEY") or os.environ.get("OPENAI_API_KEY")

    if isinstance(extra_body, str):
        try:
            extra_body = json.loads(extra_body)
        except json.JSONDecodeError as e:
            raise ValueError(f"extra_body is not valid JSON: {e}") from e
    if extra_body is None:
        extra_body = prof.get("extra_body")

    resolved = {
        "model": pick(model, "model", "MATHX_MODEL"),
        "base_url": pick(base_url, "base_url", "MATHX_BASE_URL"),
        "api_key": api_key,
        "temperature": pick(temperature, "temperature"),
        "max_tokens": pick(max_tokens, "max_tokens") or 16000,
        "top_p": pick(top_p, "top_p"),
        "extra_body": extra_body,
        "max_retries": prof.get("max_retries"),
        "meta_model": pick(meta_model, "meta_model"),
        "equiv_judge_model": pick(equiv_judge_model, "equiv_judge_model"),
    }
    # a profile's concurrency reaches this process AND its spawned workers via env
    if prof.get("concurrency") and not os.environ.get("MATHX_CONCURRENCY"):
        os.environ["MATHX_CONCURRENCY"] = str(prof["concurrency"])

    hints = {
        "model": "model (flag/profile/$MATHX_MODEL)",
        "base_url": "base_url (flag/profile/$MATHX_BASE_URL)",
        "api_key": "api key (flag/profile api_key_env/$MATHX_API_KEY)",
    }
    missing = [hints[key] for key in require if not resolved.get(key)]
    if missing:
        raise ValueError("provider not configured; missing " + "; ".join(missing))
    return resolved


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
