"""Tests for BuildMode enum and config resolution."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from pact.config import (
    BuildMode,
    GlobalConfig,
    ProjectConfig,
    load_global_config,
    load_project_config,
    resolve_build_mode,
)


class TestBuildModeEnum:
    """BuildMode StrEnum values."""

    def test_unary_value(self):
        assert BuildMode.UNARY == "unary"
        assert BuildMode.UNARY.value == "unary"

    def test_auto_value(self):
        assert BuildMode.AUTO == "auto"
        assert BuildMode.AUTO.value == "auto"

    def test_hierarchy_value(self):
        assert BuildMode.HIERARCHY == "hierarchy"
        assert BuildMode.HIERARCHY.value == "hierarchy"

    def test_is_str_enum(self):
        assert isinstance(BuildMode.UNARY, str)
        assert isinstance(BuildMode.AUTO, str)

    def test_from_string(self):
        assert BuildMode("unary") == BuildMode.UNARY
        assert BuildMode("auto") == BuildMode.AUTO
        assert BuildMode("hierarchy") == BuildMode.HIERARCHY

    def test_invalid_raises(self):
        with pytest.raises(ValueError):
            BuildMode("invalid_mode")

    def test_all_members(self):
        members = list(BuildMode)
        assert len(members) == 3
        assert BuildMode.UNARY in members
        assert BuildMode.AUTO in members
        assert BuildMode.HIERARCHY in members


class TestResolveBuildMode:
    """resolve_build_mode() priority and fallback."""

    def test_project_override(self):
        pc = ProjectConfig(build_mode="unary")
        gc = GlobalConfig()
        assert resolve_build_mode(pc, gc) == BuildMode.UNARY

    def test_global_default(self):
        pc = ProjectConfig()  # build_mode=None
        gc = GlobalConfig(build_mode="hierarchy")
        assert resolve_build_mode(pc, gc) == BuildMode.HIERARCHY

    def test_auto_fallback(self):
        pc = ProjectConfig()
        gc = GlobalConfig()  # build_mode="auto" by default
        assert resolve_build_mode(pc, gc) == BuildMode.AUTO

    def test_invalid_mode_falls_back_to_auto(self):
        pc = ProjectConfig(build_mode="nonsense")
        gc = GlobalConfig()
        assert resolve_build_mode(pc, gc) == BuildMode.AUTO

    def test_project_none_uses_global(self):
        pc = ProjectConfig(build_mode=None)
        gc = GlobalConfig(build_mode="unary")
        assert resolve_build_mode(pc, gc) == BuildMode.UNARY

    def test_project_empty_string_uses_global(self):
        """Empty string is falsy, should fall through to global."""
        pc = ProjectConfig(build_mode="")
        gc = GlobalConfig(build_mode="hierarchy")
        assert resolve_build_mode(pc, gc) == BuildMode.HIERARCHY

    def test_both_none_uses_auto(self):
        pc = ProjectConfig(build_mode=None)
        gc = GlobalConfig(build_mode="auto")
        assert resolve_build_mode(pc, gc) == BuildMode.AUTO


class TestGlobalConfigBuildMode:
    """build_mode in GlobalConfig."""

    def test_default_is_auto(self):
        gc = GlobalConfig()
        assert gc.build_mode == "auto"

    def test_load_from_yaml(self, tmp_path: Path):
        config_path = tmp_path / "config.yaml"
        config_path.write_text(yaml.dump({"build_mode": "unary"}))
        gc = load_global_config(config_path)
        assert gc.build_mode == "unary"

    def test_load_missing_defaults_to_auto(self, tmp_path: Path):
        config_path = tmp_path / "config.yaml"
        config_path.write_text(yaml.dump({"model": "claude-sonnet-4-5-20250929"}))
        gc = load_global_config(config_path)
        assert gc.build_mode == "auto"

    def test_load_hierarchy(self, tmp_path: Path):
        config_path = tmp_path / "config.yaml"
        config_path.write_text(yaml.dump({"build_mode": "hierarchy"}))
        gc = load_global_config(config_path)
        assert gc.build_mode == "hierarchy"


class TestProjectConfigBuildMode:
    """build_mode in ProjectConfig."""

    def test_default_is_none(self):
        pc = ProjectConfig()
        assert pc.build_mode is None

    def test_load_from_yaml(self, tmp_path: Path):
        config_path = tmp_path / "pact.yaml"
        config_path.write_text(yaml.dump({"build_mode": "unary"}))
        pc = load_project_config(tmp_path)
        assert pc.build_mode == "unary"

    def test_load_missing_is_none(self, tmp_path: Path):
        config_path = tmp_path / "pact.yaml"
        config_path.write_text(yaml.dump({"budget": 5.0}))
        pc = load_project_config(tmp_path)
        assert pc.build_mode is None

    def test_load_hierarchy(self, tmp_path: Path):
        config_path = tmp_path / "pact.yaml"
        config_path.write_text(yaml.dump({"build_mode": "hierarchy"}))
        pc = load_project_config(tmp_path)
        assert pc.build_mode == "hierarchy"


class TestBuildModeEndToEnd:
    """End-to-end config loading and resolution."""

    def test_project_overrides_global(self, tmp_path: Path):
        global_path = tmp_path / "global.yaml"
        global_path.write_text(yaml.dump({"build_mode": "hierarchy"}))
        gc = load_global_config(global_path)

        project_dir = tmp_path / "proj"
        project_dir.mkdir()
        (project_dir / "pact.yaml").write_text(yaml.dump({"build_mode": "unary"}))
        pc = load_project_config(project_dir)

        assert resolve_build_mode(pc, gc) == BuildMode.UNARY

    def test_global_used_when_project_unset(self, tmp_path: Path):
        global_path = tmp_path / "global.yaml"
        global_path.write_text(yaml.dump({"build_mode": "hierarchy"}))
        gc = load_global_config(global_path)

        project_dir = tmp_path / "proj"
        project_dir.mkdir()
        (project_dir / "pact.yaml").write_text(yaml.dump({"budget": 5.0}))
        pc = load_project_config(project_dir)

        assert resolve_build_mode(pc, gc) == BuildMode.HIERARCHY

    def test_all_defaults(self, tmp_path: Path):
        gc = load_global_config(tmp_path / "nonexistent.yaml")
        pc = load_project_config(tmp_path / "nonexistent")
        assert resolve_build_mode(pc, gc) == BuildMode.AUTO

    def test_build_mode_is_string_compatible(self):
        """BuildMode values can be used as strings directly."""
        mode = BuildMode.UNARY
        assert mode == "unary"
        assert f"mode={mode}" == "mode=unary"

    def test_resolve_each_mode(self):
        """Each mode resolves correctly."""
        for mode_str in ("unary", "auto", "hierarchy"):
            pc = ProjectConfig(build_mode=mode_str)
            gc = GlobalConfig()
            result = resolve_build_mode(pc, gc)
            assert result.value == mode_str
