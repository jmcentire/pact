"""Tests for variable timeout configuration."""
from pact.config import (
    ImpatienceLevel, TimeoutConfig,
    GlobalConfig, ProjectConfig,
    resolve_timeout_config,
)


class TestImpatienceLevel:
    def test_patient_value(self):
        assert ImpatienceLevel.PATIENT == "patient"

    def test_normal_value(self):
        assert ImpatienceLevel.NORMAL == "normal"

    def test_impatient_value(self):
        assert ImpatienceLevel.IMPATIENT == "impatient"


class TestTimeoutConfig:
    def test_patient_doubles_timeout(self):
        tc = TimeoutConfig(impatience=ImpatienceLevel.PATIENT)
        assert tc.get_timeout("code_author") == 600  # 300 * 2

    def test_normal_keeps_timeout(self):
        tc = TimeoutConfig(impatience=ImpatienceLevel.NORMAL)
        assert tc.get_timeout("code_author") == 300

    def test_impatient_halves_timeout(self):
        tc = TimeoutConfig(impatience=ImpatienceLevel.IMPATIENT)
        assert tc.get_timeout("code_author") == 150  # 300 * 0.5

    def test_floor_at_30(self):
        tc = TimeoutConfig(
            impatience=ImpatienceLevel.IMPATIENT,
            role_timeouts={"tiny_role": 20},
        )
        assert tc.get_timeout("tiny_role") == 30  # Floor, not 10

    def test_role_override(self):
        tc = TimeoutConfig(role_timeouts={"test_author": 450})
        assert tc.get_timeout("test_author") == 450

    def test_role_override_with_impatience(self):
        tc = TimeoutConfig(
            impatience=ImpatienceLevel.PATIENT,
            role_timeouts={"test_author": 450},
        )
        assert tc.get_timeout("test_author") == 900  # 450 * 2

    def test_unknown_role_uses_default(self):
        tc = TimeoutConfig()
        assert tc.get_timeout("unknown_agent") == 300  # Default

    def test_unknown_role_with_impatience(self):
        tc = TimeoutConfig(impatience=ImpatienceLevel.IMPATIENT)
        assert tc.get_timeout("unknown_agent") == 150  # 300 * 0.5

    def test_trace_analyst_default(self):
        tc = TimeoutConfig()
        assert tc.get_timeout("trace_analyst") == 180

    def test_all_defaults_present(self):
        tc = TimeoutConfig()
        for role in ["decomposer", "contract_author", "test_author", "code_author", "trace_analyst"]:
            assert tc.get_timeout(role) > 0


class TestResolveTimeoutConfig:
    def test_defaults(self):
        tc = resolve_timeout_config(ProjectConfig(), GlobalConfig())
        assert tc.impatience == ImpatienceLevel.NORMAL
        assert tc.get_timeout("code_author") == 300

    def test_global_override(self):
        gc = GlobalConfig(impatience="patient")
        tc = resolve_timeout_config(ProjectConfig(), gc)
        assert tc.impatience == ImpatienceLevel.PATIENT

    def test_project_overrides_global(self):
        gc = GlobalConfig(impatience="patient")
        pc = ProjectConfig(impatience="impatient")
        tc = resolve_timeout_config(pc, gc)
        assert tc.impatience == ImpatienceLevel.IMPATIENT

    def test_invalid_impatience_defaults_normal(self):
        gc = GlobalConfig(impatience="invalid_value")
        tc = resolve_timeout_config(ProjectConfig(), gc)
        assert tc.impatience == ImpatienceLevel.NORMAL

    def test_role_timeout_merge(self):
        gc = GlobalConfig(role_timeouts={"code_author": 500})
        pc = ProjectConfig(role_timeouts={"test_author": 600})
        tc = resolve_timeout_config(pc, gc)
        assert tc.get_timeout("code_author") == 500
        assert tc.get_timeout("test_author") == 600
