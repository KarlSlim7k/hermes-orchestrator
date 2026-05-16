"""Tests para config.py (T-06bis)."""

import json
import os
import tempfile
from pathlib import Path

import pytest

from src.core.config import (
    load_config,
    save_config,
    get_agent_config,
    _deep_merge,
    _default_config,
    _apply_env_overrides,
    _parse_capabilities,
)
from src.core.models import AgentCapability, SystemConfig


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _write_json(data: dict) -> str:
    f = tempfile.NamedTemporaryFile(suffix=".json", delete=False, mode="w")
    json.dump(data, f)
    f.close()
    return f.name


# ---------------------------------------------------------------------------
# Defaults
# ---------------------------------------------------------------------------

class TestDefaultConfig:
    def test_default_has_agents(self):
        data = _default_config()
        assert len(data["agents"]) >= 1
        assert data["agents"][0]["name"] == "opencode"

    def test_default_has_security(self):
        data = _default_config()
        assert data["security"]["require_confirmation_for_commit"] is True
        assert data["security"]["require_confirmation_for_push"] is True
        assert data["security"]["require_confirmation_for_pr"] is True
        assert data["security"]["max_concurrent_tasks"] == 3

    def test_default_has_channels(self):
        data = _default_config()
        assert data["channels"]["telegram_enabled"] is False
        assert data["channels"]["web_enabled"] is False
        assert data["channels"]["web_port"] == 8000


# ---------------------------------------------------------------------------
# Load from defaults (no file)
# ---------------------------------------------------------------------------

class TestLoadConfigDefaults:
    def test_load_defaults_returns_valid_config(self):
        config = load_config(config_path=None, env_overrides=False)
        assert isinstance(config, SystemConfig)
        assert len(config.agents) >= 1

    def test_default_agent_is_opencode(self):
        config = load_config(config_path=None, env_overrides=False)
        assert config.default_agent == "opencode"

    def test_default_repo_path_is_cwd(self):
        config = load_config(config_path=None, env_overrides=False)
        assert config.repository_path == os.getcwd()


# ---------------------------------------------------------------------------
# Load from JSON file
# ---------------------------------------------------------------------------

class TestLoadConfigFromFile:
    def test_load_json_config(self):
        data = {
            "default_agent": "opencode",
            "repository_path": "/tmp/test-repo",
            "security": {
                "require_confirmation_for_commit": False,
                "require_confirmation_for_push": True,
                "require_confirmation_for_pr": True,
                "max_concurrent_tasks": 5,
            },
            "channels": {
                "telegram_enabled": True,
                "telegram_token": "test-token",
                "web_enabled": True,
                "web_port": 9000,
            },
            "agents": [
                {
                    "name": "opencode",
                    "cli_command": "opencode",
                    "capabilities": ["analysis", "editing"],
                    "supports_progress": True,
                    "timeout_seconds": 300,
                },
            ],
        }
        path = _write_json(data)
        try:
            config = load_config(path, env_overrides=False)
            assert config.default_agent == "opencode"
            assert config.repository_path == "/tmp/test-repo"
            assert config.security.require_confirmation_for_commit is False
            assert config.security.max_concurrent_tasks == 5
            assert config.channels.telegram_enabled is True
            assert config.channels.telegram_token == "test-token"
            assert config.channels.web_port == 9000
            assert len(config.agents) == 1
            assert config.agents[0].capabilities == [
                AgentCapability.ANALYSIS,
                AgentCapability.EDITING,
            ]
        finally:
            os.unlink(path)

    def test_load_partial_config_merges_with_defaults(self):
        """Un config parcial debe mergear con los defaults."""
        data = {"repository_path": "/custom/path"}
        path = _write_json(data)
        try:
            config = load_config(path, env_overrides=False)
            assert config.repository_path == "/custom/path"
            # Defaults still present
            assert config.default_agent == "opencode"
            assert len(config.agents) >= 1
        finally:
            os.unlink(path)

    def test_load_nonexistent_file_raises(self):
        with pytest.raises(FileNotFoundError):
            load_config("/tmp/this-file-does-not-exist.yaml")

    def test_load_unsupported_format_raises(self):
        path = tempfile.NamedTemporaryFile(suffix=".txt", delete=False, mode="w")
        path.write("hello")
        path.close()
        try:
            with pytest.raises(ValueError, match="Unsupported config format"):
                load_config(path.name)
        finally:
            os.unlink(path.name)


# ---------------------------------------------------------------------------
# Environment variable overrides
# ---------------------------------------------------------------------------

class TestEnvOverrides:
    def setup_method(self):
        self._old_env = {}
        for key in os.environ:
            if key.startswith("HERMES_"):
                self._old_env[key] = os.environ[key]

    def teardown_method(self):
        # Restore original env
        for key in list(os.environ.keys()):
            if key.startswith("HERMES_"):
                del os.environ[key]
        for key, val in self._old_env.items():
            os.environ[key] = val

    def test_telegram_enabled_via_env(self):
        os.environ["HERMES_TELEGRAM_ENABLED"] = "true"
        os.environ["HERMES_TELEGRAM_TOKEN"] = "env-token"
        data = _apply_env_overrides({})
        assert data["channels"]["telegram_enabled"] is True
        assert data["channels"]["telegram_token"] == "env-token"

    def test_web_port_via_env(self):
        os.environ["HERMES_WEB_PORT"] = "3000"
        data = _apply_env_overrides({})
        assert data["channels"]["web_port"] == 3000

    def test_security_overrides_via_env(self):
        os.environ["HERMES_CONFIRM_COMMIT"] = "0"
        os.environ["HERMES_MAX_CONCURRENT_TASKS"] = "10"
        data = _apply_env_overrides({"security": {}})
        # HERMES_CONFIRM_COMMIT=0 no agrega la key (solo overrides positivos)
        assert data["security"]["max_concurrent_tasks"] == 10

    def test_repo_path_via_env(self):
        os.environ["HERMES_REPO_PATH"] = "/env/repo"
        data = _apply_env_overrides({})
        assert data["repository_path"] == "/env/repo"

    def test_default_agent_via_env(self):
        os.environ["HERMES_DEFAULT_AGENT"] = "codex"
        data = _apply_env_overrides({})
        assert data["default_agent"] == "codex"


# ---------------------------------------------------------------------------
# Deep merge
# ---------------------------------------------------------------------------

class TestDeepMerge:
    def test_merge_nested_dicts(self):
        base = {"a": {"b": 1, "c": 2}}
        override = {"a": {"b": 10, "d": 3}}
        result = _deep_merge(base, override)
        assert result["a"]["b"] == 10
        assert result["a"]["c"] == 2
        assert result["a"]["d"] == 3

    def test_merge_replaces_scalar(self):
        base = {"x": 1}
        override = {"x": 2}
        _deep_merge(base, override)
        assert base["x"] == 2

    def test_merge_adds_new_keys(self):
        base = {"a": 1}
        override = {"b": 2}
        _deep_merge(base, override)
        assert base == {"a": 1, "b": 2}


# ---------------------------------------------------------------------------
# Capability parsing
# ---------------------------------------------------------------------------

class TestParseCapabilities:
    def test_parse_valid_capabilities(self):
        caps = _parse_capabilities(["analysis", "editing", "git_ops"])
        assert caps == [
            AgentCapability.ANALYSIS,
            AgentCapability.EDITING,
            AgentCapability.GIT_OPS,
        ]

    def test_parse_ignores_unknown(self):
        caps = _parse_capabilities(["analysis", "unknown_cap", "editing"])
        assert caps == [AgentCapability.ANALYSIS, AgentCapability.EDITING]


# ---------------------------------------------------------------------------
# Save config
# ---------------------------------------------------------------------------

class TestSaveConfig:
    def test_save_and_reload_json(self):
        config = load_config(config_path=None, env_overrides=False)
        path = tempfile.NamedTemporaryFile(suffix=".json", delete=False)
        path.close()
        try:
            save_config(config, path.name)
            reloaded = load_config(path.name, env_overrides=False)
            assert reloaded.default_agent == config.default_agent
            assert reloaded.repository_path == config.repository_path
            assert len(reloaded.agents) == len(config.agents)
        finally:
            os.unlink(path.name)

    def test_save_unsupported_format_raises(self):
        config = load_config(config_path=None, env_overrides=False)
        with pytest.raises(ValueError, match="Unsupported format"):
            save_config(config, "/tmp/test.txt")


# ---------------------------------------------------------------------------
# Get agent config
# ---------------------------------------------------------------------------

class TestGetAgentConfig:
    def test_get_default_agent(self):
        config = load_config(config_path=None, env_overrides=False)
        agent = get_agent_config(config)
        assert agent.name == config.default_agent

    def test_get_agent_by_name(self):
        config = load_config(config_path=None, env_overrides=False)
        agent = get_agent_config(config, name="opencode")
        assert agent.name == "opencode"

    def test_get_nonexistent_agent_raises(self):
        config = load_config(config_path=None, env_overrides=False)
        with pytest.raises(ValueError, match="not found"):
            get_agent_config(config, name="nonexistent-agent")
