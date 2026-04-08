"""Tests for tiered model selection (P2-3) and ResolvedConfig."""
import pytest
from pact.config import (
    ModelTierConfig, ResolvedConfig, resolve_all, resolve_model_tiers,
    GlobalConfig, ProjectConfig,
)


class TestModelTierConfig:
    def test_defaults(self):
        mtc = ModelTierConfig()
        assert mtc.primary == "claude-opus-4-6"
        assert mtc.research == "claude-sonnet-4-5-20250929"
        assert mtc.fast == "claude-haiku-4-5-20251001"

    def test_custom_values(self):
        mtc = ModelTierConfig(primary="custom-primary", research="custom-research")
        assert mtc.primary == "custom-primary"
        assert mtc.research == "custom-research"
        assert mtc.fast == "claude-haiku-4-5-20251001"


class TestResolveModelTiers:
    def test_global_only(self):
        g = GlobalConfig()
        result = resolve_model_tiers(g)
        assert result.primary == "claude-opus-4-6"

    def test_project_overrides_global(self):
        g = GlobalConfig()
        p = ProjectConfig()
        p.model_tiers = ModelTierConfig(primary="custom-opus")
        result = resolve_model_tiers(g, p)
        assert result.primary == "custom-opus"

    def test_project_none_uses_global(self):
        g = GlobalConfig()
        g.model_tiers = ModelTierConfig(research="custom-sonnet")
        p = ProjectConfig()
        p.model_tiers = None
        result = resolve_model_tiers(g, p)
        assert result.research == "custom-sonnet"

    def test_no_project(self):
        g = GlobalConfig()
        result = resolve_model_tiers(g, None)
        assert result == g.model_tiers


class TestLoadModelTiers:
    def test_load_global_config_with_tiers(self, tmp_path):
        from pact.config import load_global_config
        config_file = tmp_path / "config.yaml"
        config_file.write_text("""
model_tiers:
  primary: custom-opus
  research: custom-sonnet
  fast: custom-haiku
""")
        cfg = load_global_config(tmp_path / "config.yaml")
        assert cfg.model_tiers.primary == "custom-opus"
        assert cfg.model_tiers.research == "custom-sonnet"
        assert cfg.model_tiers.fast == "custom-haiku"

    def test_load_global_config_without_tiers(self, tmp_path):
        from pact.config import load_global_config
        config_file = tmp_path / "config.yaml"
        config_file.write_text("model: claude-opus-4-6\n")
        cfg = load_global_config(tmp_path / "config.yaml")
        # Should use defaults
        assert cfg.model_tiers.primary == "claude-opus-4-6"

    def test_load_project_config_with_tiers(self, tmp_path):
        from pact.config import load_project_config
        config_file = tmp_path / "pact.yaml"
        config_file.write_text("""
model_tiers:
  research: fast-sonnet
""")
        cfg = load_project_config(tmp_path)
        assert cfg.model_tiers.research == "fast-sonnet"
        assert cfg.model_tiers.primary == "claude-opus-4-6"  # default


class TestTemporaryModel:
    def test_temporary_model_exists(self):
        from pact.agents.research import _temporary_model
        assert callable(_temporary_model)

    def test_research_phase_accepts_model(self):
        import inspect
        from pact.agents.research import research_phase
        sig = inspect.signature(research_phase)
        assert "research_model" in sig.parameters

    def test_plan_evaluate_accepts_model(self):
        import inspect
        from pact.agents.research import plan_and_evaluate
        sig = inspect.signature(plan_and_evaluate)
        assert "research_model" in sig.parameters


class TestResolvedConfig:
    """Tests for the ResolvedConfig dataclass and resolve_all()."""

    def test_resolve_all_defaults(self):
        rc = resolve_all(ProjectConfig(), GlobalConfig())
        assert isinstance(rc, ResolvedConfig)
        assert "code_author" in rc.models
        assert "code_author" in rc.backends
        assert rc.build_mode is not None
        assert rc.parallel is not None
        assert rc.model_tiers is not None

    def test_resolve_all_project_overrides(self):
        pc = ProjectConfig(role_models={"code_author": "custom-model"})
        gc = GlobalConfig()
        rc = resolve_all(pc, gc)
        assert rc.models["code_author"] == "custom-model"
        # Other roles should use global default
        assert rc.models["decomposer"] == gc.model

    def test_resolve_all_backend_override(self):
        pc = ProjectConfig(role_backends={"code_author": "claude_code"})
        gc = GlobalConfig()
        rc = resolve_all(pc, gc)
        assert rc.backends["code_author"] == "claude_code"

    def test_resolve_all_covers_all_roles(self):
        rc = resolve_all(ProjectConfig(), GlobalConfig())
        from pact.config import AGENT_ROLES
        for role in AGENT_ROLES:
            assert role in rc.models
            assert role in rc.backends
