"""Tests for EnvironmentSpec."""
import os
from pact.config import EnvironmentSpec, resolve_environment, GlobalConfig, ProjectConfig


class TestEnvironmentSpec:
    def test_default_inherits_path(self):
        spec = EnvironmentSpec()
        assert spec.inherit_path is True
        assert spec.python_path == "python3"
        assert spec.required_tools == ["pytest"]

    def test_build_env_inherits_parent_path(self):
        spec = EnvironmentSpec(inherit_path=True)
        env = spec.build_env("src:lib")
        assert env["PYTHONPATH"] == "src:lib"
        # Should contain parent PATH
        parent_path = os.environ.get("PATH", "")
        if parent_path:
            assert parent_path in env["PATH"]

    def test_build_env_no_inherit(self):
        spec = EnvironmentSpec(inherit_path=False, extra_path_dirs=["/custom/bin"])
        env = spec.build_env("src")
        assert "/custom/bin" in env["PATH"]
        parent_path = os.environ.get("PATH", "")
        # Should NOT contain full parent PATH
        if parent_path and "/custom/bin" not in parent_path:
            assert parent_path not in env["PATH"]

    def test_build_env_extra_path_dirs(self):
        spec = EnvironmentSpec(
            inherit_path=True,
            extra_path_dirs=["/opt/homebrew/bin", "/custom/bin"],
        )
        env = spec.build_env("src")
        assert "/opt/homebrew/bin" in env["PATH"]
        assert "/custom/bin" in env["PATH"]

    def test_build_env_includes_env_vars(self):
        spec = EnvironmentSpec(env_vars={"MY_VAR": "hello", "DEBUG": "1"})
        env = spec.build_env("src")
        assert env["MY_VAR"] == "hello"
        assert env["DEBUG"] == "1"

    def test_build_env_fallback_minimal_path(self):
        spec = EnvironmentSpec(inherit_path=False, extra_path_dirs=[])
        env = spec.build_env("src")
        assert "/usr/bin" in env["PATH"]

    def test_validate_finds_pytest(self):
        spec = EnvironmentSpec(required_tools=["pytest"])
        missing = spec.validate_environment()
        # pytest should be installed in our test environment
        assert "pytest" not in missing

    def test_validate_finds_missing_tool(self):
        spec = EnvironmentSpec(required_tools=["nonexistent_tool_xyz_123"])
        missing = spec.validate_environment()
        assert "nonexistent_tool_xyz_123" in missing

    def test_validate_empty_tools(self):
        spec = EnvironmentSpec(required_tools=[])
        missing = spec.validate_environment()
        assert missing == []


class TestResolveEnvironment:
    def test_default_when_no_config(self):
        spec = resolve_environment(ProjectConfig(), GlobalConfig())
        assert spec.inherit_path is True
        assert spec.python_path == "python3"

    def test_global_config(self):
        gc = GlobalConfig(environment={
            "python_path": "python3.12",
            "extra_path_dirs": ["/opt/homebrew/bin"],
        })
        spec = resolve_environment(ProjectConfig(), gc)
        assert spec.python_path == "python3.12"
        assert "/opt/homebrew/bin" in spec.extra_path_dirs

    def test_project_overrides_global(self):
        gc = GlobalConfig(environment={"python_path": "python3.12"})
        pc = ProjectConfig(environment={"python_path": "python3.13"})
        spec = resolve_environment(pc, gc)
        assert spec.python_path == "python3.13"
