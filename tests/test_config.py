"""Profile config tests: discovery, validation, resolution, CLI integration."""
from __future__ import annotations

import json

import pytest
from click.testing import CliRunner

from mathx import config
from mathx.check import CHECKER_SYSTEM, GRADER_SYSTEM
from mathx.cli import cli

TOML = """
[profiles.local]
base_url = "http://localhost:8000/v1"
model = "specialist-3b"
meta_model = "generalist-35b"
temperature = 1.0
top_p = 0.95
max_tokens = 6000
max_retries = 0
concurrency = 3

[profiles.cloud]
base_url = "https://openrouter.ai/api/v1"
model = "some/cloud-model"
api_key_env = "MY_ROUTER_KEY"
extra_body = { reasoning = { effort = "high" } }
"""


@pytest.fixture
def config_dir(tmp_path, monkeypatch):
    (tmp_path / "mathx.toml").write_text(TOML)
    monkeypatch.chdir(tmp_path)
    return tmp_path


class TestDiscoveryAndLoading:
    def test_found_in_ancestor(self, config_dir, monkeypatch, tmp_path):
        child = tmp_path / "sub" / "dir"
        child.mkdir(parents=True)
        monkeypatch.chdir(child)
        assert config.find_config() == tmp_path / "mathx.toml"

    def test_xdg_fallback(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)  # no mathx.toml here
        xdg = tmp_path / "xdg"
        (xdg / "mathx").mkdir(parents=True)
        (xdg / "mathx" / "config.toml").write_text(TOML)
        monkeypatch.setenv("XDG_CONFIG_HOME", str(xdg))
        assert config.find_config() == xdg / "mathx" / "config.toml"

    def test_none_when_absent(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "empty"))
        assert config.find_config() is None

    def test_unknown_key_rejected(self, tmp_path):
        p = tmp_path / "mathx.toml"
        p.write_text("[profiles.x]\nmodle = 'typo'\n")
        with pytest.raises(ValueError, match="unknown key.*modle"):
            config.load_profiles(p)

    def test_api_key_in_file_rejected(self, tmp_path):
        p = tmp_path / "mathx.toml"
        p.write_text("[profiles.x]\napi_key = 'sk-nope'\n")
        with pytest.raises(ValueError, match="keys never live in"):
            config.load_profiles(p)


class TestResolveProfile:
    def test_no_profile_is_empty(self, config_dir):
        assert config.resolve_profile(None) == {}

    def test_by_name_and_by_env(self, config_dir, monkeypatch):
        assert config.resolve_profile("local")["model"] == "specialist-3b"
        monkeypatch.setenv("MATHX_PROFILE", "local")
        assert config.resolve_profile(None)["meta_model"] == "generalist-35b"

    def test_unknown_profile_errors(self, config_dir):
        with pytest.raises(ValueError, match="no profile 'nope'"):
            config.resolve_profile("nope")

    def test_profile_without_config_errors(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "empty"))
        with pytest.raises(ValueError, match="no config file found"):
            config.resolve_profile("local")


class TestProfileCli:
    def invoke(self, *args):
        return CliRunner().invoke(cli, list(args))

    def test_solve_uses_profile_settings(self, config_dir, fake_endpoint, monkeypatch):
        monkeypatch.setenv("MATHX_API_KEY", "k")
        ep = fake_endpoint([r"\boxed{4}"] * 2)
        result = self.invoke("solve", "2+2?", "--profile", "local", "--k", "2", "--no-progress")
        assert result.exit_code == 0, result.output
        req = ep.requests[0]
        assert req["model"] == "specialist-3b"
        assert req["temperature"] == 1.0
        assert req["top_p"] == 0.95
        assert req["max_tokens"] == 6000

    def test_flag_beats_profile_beats_env(self, config_dir, fake_endpoint, monkeypatch):
        monkeypatch.setenv("MATHX_API_KEY", "k")
        monkeypatch.setenv("MATHX_MODEL", "env-model")
        ep = fake_endpoint([r"\boxed{4}"])
        result = self.invoke(
            "solve", "2+2?", "--profile", "local", "--model", "flag-model",
            "--strategy", "cot", "--no-progress",
        )
        assert result.exit_code == 0, result.output
        assert ep.requests[0]["model"] == "flag-model"

    def test_profile_concurrency_reaches_env(self, config_dir, fake_endpoint, monkeypatch):
        import os

        monkeypatch.setenv("MATHX_API_KEY", "k")
        fake_endpoint([r"\boxed{4}"])
        self.invoke("solve", "2+2?", "--profile", "local", "--strategy", "cot", "--no-progress")
        assert os.environ.get("MATHX_CONCURRENCY") == "3"

    def test_api_key_env_indirection(self, config_dir, fake_endpoint, monkeypatch):
        monkeypatch.delenv("MATHX_API_KEY", raising=False)
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        result = self.invoke("solve", "2+2?", "--profile", "cloud", "--no-progress")
        assert result.exit_code != 0
        assert "MY_ROUTER_KEY" in result.output
        monkeypatch.setenv("MY_ROUTER_KEY", "sk-router")
        ep = fake_endpoint([r"\boxed{4}"])
        result = self.invoke("solve", "2+2?", "--profile", "cloud", "--strategy", "cot", "--no-progress")
        assert result.exit_code == 0, result.output

    def test_extra_body_from_profile_reaches_request(self, config_dir, fake_endpoint, monkeypatch):
        monkeypatch.setenv("MY_ROUTER_KEY", "sk-router")
        ep = fake_endpoint([r"\boxed{4}"])
        result = self.invoke("solve", "2+2?", "--profile", "cloud", "--strategy", "cot", "--no-progress")
        assert result.exit_code == 0, result.output
        assert ep.requests[0]["reasoning"] == {"effort": "high"}

    def test_extra_body_flag_must_be_json(self, config_dir, monkeypatch):
        monkeypatch.setenv("MATHX_API_KEY", "k")
        result = self.invoke(
            "solve", "2+2?", "--profile", "local", "--extra-body", "not json"
        )
        assert result.exit_code != 0
        assert "not valid JSON" in result.output

    def test_missing_provider_is_a_clear_error(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        for var in ("MATHX_MODEL", "MATHX_BASE_URL", "MATHX_API_KEY", "OPENAI_API_KEY"):
            monkeypatch.delenv(var, raising=False)
        result = self.invoke("solve", "2+2?")
        assert result.exit_code != 0
        assert "provider not configured" in result.output

    def test_check_routes_meta_tasks_to_meta_model(self, config_dir, fake_endpoint, monkeypatch):
        monkeypatch.setenv("MATHX_API_KEY", "k")
        ep = fake_endpoint(by_system={
            CHECKER_SYSTEM: '```python\nprint("VERDICT: PASS")\n```',
            GRADER_SYSTEM: r"\boxed{TRUE}",
        })
        result = self.invoke("check", "2+2=4", "--profile", "local", "--grade-k", "2")
        assert result.exit_code == 0, result.output
        by_role = {req["messages"][0]["content"]: req["model"] for req in ep.requests}
        assert by_role[CHECKER_SYSTEM] == "generalist-35b"  # the meta-task
        assert by_role[GRADER_SYSTEM] == "specialist-3b"  # object-level grading