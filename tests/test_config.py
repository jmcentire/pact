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
