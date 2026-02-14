"""Tests for configuration loading."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from pact.config import (
    GlobalConfig,
    ProjectConfig,
    load_global_config,
    load_project_config,
    resolve_backend,
    resolve_model,
)


class TestGlobalConfig:
    def test_defaults(self):
        c = GlobalConfig()
        assert c.model == "claude-opus-4-6"
        assert c.default_budget == 10.00
        assert c.check_interval == 300
        assert "decomposer" in c.role_models
        assert "anthropic" in c.role_backends.values()

    def test_load_missing_file(self, tmp_path: Path):
        c = load_global_config(tmp_path / "nonexistent.yaml")
        assert c.model == "claude-opus-4-6"

    def test_load_from_file(self, tmp_path: Path):
        config_path = tmp_path / "config.yaml"
        config_path.write_text(yaml.dump({
            "model": "claude-sonnet-4-5-20250929",
            "default_budget": 25.00,
            "check_interval": 60,
        }))
        c = load_global_config(config_path)
        assert c.model == "claude-sonnet-4-5-20250929"
        assert c.default_budget == 25.00
        assert c.check_interval == 60

    def test_load_with_role_models(self, tmp_path: Path):
        config_path = tmp_path / "config.yaml"
        config_path.write_text(yaml.dump({
            "role_models": {
                "decomposer": "claude-sonnet-4-5-20250929",
            },
        }))
        c = load_global_config(config_path)
        assert c.role_models["decomposer"] == "claude-sonnet-4-5-20250929"


class TestProjectConfig:
    def test_defaults(self):
        c = ProjectConfig()
        assert c.budget == 10.00
        assert c.backend == "anthropic"

    def test_load_missing_file(self, tmp_path: Path):
        c = load_project_config(tmp_path)
        assert c.budget == 10.00

    def test_load_from_file(self, tmp_path: Path):
        config_path = tmp_path / "pact.yaml"
        config_path.write_text(yaml.dump({
            "budget": 50.00,
            "backend": "claude_code",
        }))
        c = load_project_config(tmp_path)
        assert c.budget == 50.00
        assert c.backend == "claude_code"


class TestResolveModel:
    def test_project_override(self):
        pc = ProjectConfig(role_models={"decomposer": "claude-haiku-4-5-20251001"})
        gc = GlobalConfig()
        assert resolve_model("decomposer", pc, gc) == "claude-haiku-4-5-20251001"

    def test_global_role(self):
        pc = ProjectConfig()
        gc = GlobalConfig()
        assert resolve_model("decomposer", pc, gc) == "claude-opus-4-6"

    def test_fallback_to_global_model(self):
        pc = ProjectConfig()
        gc = GlobalConfig()
        assert resolve_model("unknown_role", pc, gc) == "claude-opus-4-6"

    def test_project_model_fallback(self):
        pc = ProjectConfig(model="claude-sonnet-4-5-20250929")
        gc = GlobalConfig(role_models={})
        assert resolve_model("unknown_role", pc, gc) == "claude-sonnet-4-5-20250929"


class TestResolveBackend:
    def test_project_override(self):
        pc = ProjectConfig(role_backends={"decomposer": "claude_code"})
        gc = GlobalConfig()
        assert resolve_backend("decomposer", pc, gc) == "claude_code"

    def test_global_role(self):
        pc = ProjectConfig()
        gc = GlobalConfig()
        assert resolve_backend("decomposer", pc, gc) == "anthropic"

    def test_fallback(self):
        pc = ProjectConfig(backend="claude_code")
        gc = GlobalConfig(role_backends={})
        assert resolve_backend("unknown", pc, gc) == "claude_code"


class TestBidirectionalConfigFields:
    def test_global_config_bidirectional_defaults(self):
        gc = GlobalConfig()
        assert gc.slack_bot_token == ""
        assert gc.slack_channel == ""
        assert gc.poll_integrations is False
        assert gc.poll_interval == 60
        assert gc.max_poll_attempts == 10
        assert gc.context_max_chars == 4000

    def test_project_config_bidirectional_defaults(self):
        pc = ProjectConfig()
        assert pc.slack_bot_token == ""
        assert pc.slack_channel == ""
        assert pc.poll_integrations is None
        assert pc.poll_interval is None
        assert pc.max_poll_attempts is None
        assert pc.context_max_chars is None

    def test_load_global_config_bidirectional(self, tmp_path: Path):
        config_path = tmp_path / "config.yaml"
        config_path.write_text(yaml.dump({
            "slack_bot_token": "xoxb-test-token",
            "slack_channel": "C0123456789",
            "poll_integrations": True,
            "poll_interval": 30,
            "max_poll_attempts": 5,
            "context_max_chars": 2000,
        }))
        gc = load_global_config(config_path)
        assert gc.slack_bot_token == "xoxb-test-token"
        assert gc.slack_channel == "C0123456789"
        assert gc.poll_integrations is True
        assert gc.poll_interval == 30
        assert gc.max_poll_attempts == 5
        assert gc.context_max_chars == 2000

    def test_load_project_config_bidirectional(self, tmp_path: Path):
        config_path = tmp_path / "pact.yaml"
        config_path.write_text(yaml.dump({
            "slack_bot_token": "xoxb-project",
            "slack_channel": "C999",
            "poll_integrations": True,
            "poll_interval": 45,
            "max_poll_attempts": 20,
            "context_max_chars": 8000,
        }))
        pc = load_project_config(tmp_path)
        assert pc.slack_bot_token == "xoxb-project"
        assert pc.slack_channel == "C999"
        assert pc.poll_integrations is True
        assert pc.poll_interval == 45
        assert pc.max_poll_attempts == 20
        assert pc.context_max_chars == 8000
