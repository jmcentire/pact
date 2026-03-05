"""Tests for processing register — establishment, propagation, drift detection.

Papers 35-39 established that register (processing mode) is the representational
hub that domain anchors to. These tests verify the register field propagates
through the pact pipeline and that drift detection works correctly.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from pact.health import (
    HealthCondition,
    HealthMetrics,
    HealthStatus,
    _check_register_drift,
    check_health,
)
from pact.schemas import ComponentContract, InterviewResult, RunState


# ── Schema Tests ────────────────────────────────────────────────────


class TestRegisterOnSchemas:
    """processing_register field exists and defaults correctly."""

    def test_interview_result_default(self):
        result = InterviewResult()
        assert result.processing_register == ""

    def test_interview_result_set(self):
        result = InterviewResult(processing_register="rigorous-analytical")
        assert result.processing_register == "rigorous-analytical"

    def test_component_contract_default(self):
        contract = ComponentContract(
            component_id="test", name="Test", description="Test component",
        )
        assert contract.processing_register == ""

    def test_component_contract_set(self):
        contract = ComponentContract(
            component_id="test", name="Test", description="Test component",
            processing_register="systematic-verification",
        )
        assert contract.processing_register == "systematic-verification"

    def test_run_state_default(self):
        state = RunState(id="abc", project_dir="/tmp/test")
        assert state.processing_register == ""

    def test_run_state_set(self):
        state = RunState(
            id="abc", project_dir="/tmp/test",
            processing_register="exploratory-generative",
        )
        assert state.processing_register == "exploratory-generative"

    def test_interview_result_serialization(self):
        result = InterviewResult(processing_register="pragmatic-implementation")
        data = result.model_dump()
        assert data["processing_register"] == "pragmatic-implementation"
        restored = InterviewResult.model_validate(data)
        assert restored.processing_register == "pragmatic-implementation"

    def test_contract_serialization(self):
        contract = ComponentContract(
            component_id="c1", name="C1", description="desc",
            processing_register="rigorous-analytical",
        )
        data = contract.model_dump()
        assert data["processing_register"] == "rigorous-analytical"
        restored = ComponentContract.model_validate(data)
        assert restored.processing_register == "rigorous-analytical"


# ── Config Tests ────────────────────────────────────────────────────


class TestRegisterConfig:
    """processing_register loads from pact.yaml."""

    def test_project_config_default(self):
        from pact.config import ProjectConfig
        cfg = ProjectConfig()
        assert cfg.processing_register == ""

    def test_project_config_from_yaml(self, tmp_path):
        from pact.config import load_project_config
        yaml_content = "processing_register: rigorous-analytical\n"
        (tmp_path / "pact.yaml").write_text(yaml_content)
        cfg = load_project_config(tmp_path)
        assert cfg.processing_register == "rigorous-analytical"

    def test_project_config_missing_register(self, tmp_path):
        from pact.config import load_project_config
        yaml_content = "budget: 5.0\n"
        (tmp_path / "pact.yaml").write_text(yaml_content)
        cfg = load_project_config(tmp_path)
        assert cfg.processing_register == ""


# ── Register Establishment Tests ────────────────────────────────────


class TestRegisterEstablishment:
    """run_register_establishment() determines processing register."""

    @pytest.mark.asyncio
    async def test_register_establishment(self):
        from pact.decomposer import run_register_establishment, _RegisterResponse

        mock_agent = AsyncMock()
        mock_agent.assess = AsyncMock(return_value=(
            _RegisterResponse(processing_register="rigorous-analytical"),
            100, 20,
        ))

        register = await run_register_establishment(mock_agent, "Build a parser")
        assert register == "rigorous-analytical"
        mock_agent.assess.assert_called_once()

    @pytest.mark.asyncio
    async def test_register_normalizes_case(self):
        from pact.decomposer import run_register_establishment, _RegisterResponse

        mock_agent = AsyncMock()
        mock_agent.assess = AsyncMock(return_value=(
            _RegisterResponse(processing_register="  Rigorous-Analytical  "),
            100, 20,
        ))

        register = await run_register_establishment(mock_agent, "Build a parser")
        assert register == "rigorous-analytical"

    @pytest.mark.asyncio
    async def test_interview_uses_provided_register(self):
        from pact.decomposer import run_interview

        mock_agent = AsyncMock()
        mock_agent.assess = AsyncMock(return_value=(
            InterviewResult(risks=["test risk"]),
            100, 20,
        ))

        result = await run_interview(
            mock_agent, "Build a thing", processing_register="pragmatic-implementation",
        )
        assert result.processing_register == "pragmatic-implementation"
        # Should only call assess once (interview), not register establishment
        assert mock_agent.assess.call_count == 1

    @pytest.mark.asyncio
    async def test_interview_establishes_register_if_missing(self):
        from pact.decomposer import run_interview, _RegisterResponse

        mock_agent = AsyncMock()
        # First call: register establishment, second: interview
        mock_agent.assess = AsyncMock(side_effect=[
            (_RegisterResponse(processing_register="systematic-verification"), 50, 10),
            (InterviewResult(risks=["test risk"]), 100, 20),
        ])

        result = await run_interview(mock_agent, "Build a thing")
        assert result.processing_register == "systematic-verification"
        assert mock_agent.assess.call_count == 2


# ── Handoff Brief Tests ─────────────────────────────────────────────


class TestRegisterInHandoffBrief:
    """render_handoff_brief includes register priming in correct position."""

    def test_register_priming_present(self):
        from pact.interface_stub import render_handoff_brief

        contract = ComponentContract(
            component_id="c1", name="Parser", description="Parse input",
            processing_register="rigorous-analytical",
        )
        brief = render_handoff_brief(
            component_id="c1",
            contract=contract,
            contracts={"c1": contract},
            processing_register="rigorous-analytical",
        )
        assert "Processing register: rigorous-analytical" in brief
        assert "Maintain this cognitive mode" in brief

    def test_register_priming_absent_when_empty(self):
        from pact.interface_stub import render_handoff_brief

        contract = ComponentContract(
            component_id="c1", name="Parser", description="Parse input",
        )
        brief = render_handoff_brief(
            component_id="c1",
            contract=contract,
            contracts={"c1": contract},
        )
        assert "Processing register:" not in brief

    def test_register_before_domain(self):
        """Register priming must come after reset and before domain content."""
        from pact.interface_stub import render_handoff_brief

        contract = ComponentContract(
            component_id="c1", name="Parser", description="Parse input",
            processing_register="rigorous-analytical",
        )
        brief = render_handoff_brief(
            component_id="c1",
            contract=contract,
            contracts={"c1": contract},
            processing_register="rigorous-analytical",
            strategic_context="Project: Test. This component parses input.",
        )

        # Find positions
        reset_pos = brief.find("no prior conversation context")
        register_pos = brief.find("Processing register:")
        strategic_pos = brief.find("Project: Test")
        interface_pos = brief.find("YOUR INTERFACE CONTRACT")

        # Order: reset → register → strategic → interface
        assert reset_pos < register_pos < strategic_pos < interface_pos


# ── Register Drift Health Tests ─────────────────────────────────────


class TestRegisterDriftCheck:
    """_check_register_drift detects register inconsistency."""

    def test_insufficient_data(self):
        metrics = HealthMetrics()
        finding = _check_register_drift(metrics)
        assert finding.status == HealthStatus.healthy
        assert "Insufficient" in finding.message

    def test_healthy_no_drift(self):
        metrics = HealthMetrics(register_checks=10, register_drift_events=0)
        finding = _check_register_drift(metrics)
        assert finding.status == HealthStatus.healthy
        assert "100%" in finding.message

    def test_warning_moderate_drift(self):
        metrics = HealthMetrics(register_checks=10, register_drift_events=3)
        finding = _check_register_drift(metrics)
        assert finding.status == HealthStatus.warning
        assert finding.condition == HealthCondition.register_drift

    def test_critical_high_drift(self):
        metrics = HealthMetrics(register_checks=10, register_drift_events=6)
        finding = _check_register_drift(metrics)
        assert finding.status == HealthStatus.critical
        assert "Coordination failure" in finding.message

    def test_custom_thresholds(self):
        metrics = HealthMetrics(register_checks=10, register_drift_events=2)
        # Default threshold is 0.2 (20%), so 20% should trigger warning
        finding = _check_register_drift(metrics)
        assert finding.status == HealthStatus.warning

        # With raised threshold, same rate is healthy
        finding = _check_register_drift(
            metrics, {"register_drift_warning": 0.3},
        )
        assert finding.status == HealthStatus.healthy

    def test_register_drift_in_full_health_check(self):
        """Register drift appears in full health check."""
        metrics = HealthMetrics(register_checks=10, register_drift_events=6)
        report = check_health(metrics)
        drift_findings = [
            f for f in report.findings
            if f.condition == HealthCondition.register_drift
        ]
        assert len(drift_findings) == 1
        assert drift_findings[0].status == HealthStatus.critical


class TestHealthMetricsRegister:
    """HealthMetrics register tracking methods."""

    def test_record_register_check_no_drift(self):
        m = HealthMetrics()
        m.record_register_check(drifted=False)
        assert m.register_checks == 1
        assert m.register_drift_events == 0

    def test_record_register_check_with_drift(self):
        m = HealthMetrics()
        m.record_register_check(drifted=True)
        assert m.register_checks == 1
        assert m.register_drift_events == 1

    def test_register_drift_rate(self):
        m = HealthMetrics(register_checks=4, register_drift_events=1)
        assert m.register_drift_rate == 0.25

    def test_register_drift_rate_zero_checks(self):
        m = HealthMetrics()
        assert m.register_drift_rate == 0.0

    def test_serialization_roundtrip(self):
        m = HealthMetrics(register_checks=5, register_drift_events=2)
        d = m.to_dict()
        assert d["register_checks"] == 5
        assert d["register_drift_events"] == 2
        restored = HealthMetrics.from_dict(d)
        assert restored.register_checks == 5
        assert restored.register_drift_events == 2

    def test_from_dict_missing_register_fields(self):
        """Backward compatibility: old dicts without register fields."""
        old_data = {"planning_tokens": 100, "generation_tokens": 200}
        m = HealthMetrics.from_dict(old_data)
        assert m.register_checks == 0
        assert m.register_drift_events == 0


# ── Contract Author Register Propagation ────────────────────────────


class TestContractAuthorRegister:
    """author_contract() propagates processing_register to contract."""

    @pytest.mark.asyncio
    async def test_register_propagation(self):
        from pact.agents.contract_author import author_contract
        from pact.schemas import PlanEvaluation, ResearchReport

        mock_agent = AsyncMock()
        mock_agent._model = "test-model"
        mock_agent.set_model = MagicMock()
        # Research + plan + contract all use assess_cached
        mock_agent.assess_cached = AsyncMock(side_effect=[
            (ResearchReport(task_summary="test"), 100, 20),
            (PlanEvaluation(plan_summary="test", decision="proceed"), 100, 20),
            (ComponentContract(
                component_id="c1", name="C1", description="test",
            ), 200, 50),
        ])

        with patch("pact.quality.audit_contract_specificity", return_value=[]):
            contract, _, _ = await author_contract(
                mock_agent,
                component_id="c1",
                component_name="C1",
                component_description="Test component",
                processing_register="rigorous-analytical",
            )
        assert contract.processing_register == "rigorous-analytical"


# ── Runtime Drift Detection Tests ───────────────────────────────────


class TestAssessRegisterConsistency:
    """assess_register_consistency() lightweight LLM check."""

    @pytest.mark.asyncio
    async def test_consistent_artifact(self):
        from pact.register import _DriftCheckResponse, assess_register_consistency

        mock_agent = AsyncMock()
        mock_agent.assess = AsyncMock(return_value=(
            _DriftCheckResponse(consistent=True, confidence=0.9),
            200, 10,
        ))

        consistent, confidence = await assess_register_consistency(
            mock_agent, "def parse(x):\n    return x.strip()", "rigorous-analytical",
        )
        assert consistent is True
        assert confidence == 0.9

    @pytest.mark.asyncio
    async def test_drifted_artifact(self):
        from pact.register import _DriftCheckResponse, assess_register_consistency

        mock_agent = AsyncMock()
        mock_agent.assess = AsyncMock(return_value=(
            _DriftCheckResponse(consistent=False, confidence=0.85),
            200, 10,
        ))

        consistent, confidence = await assess_register_consistency(
            mock_agent, "# quick hack\nresult = input()", "rigorous-analytical",
        )
        assert consistent is False
        assert confidence == 0.85

    @pytest.mark.asyncio
    async def test_truncation(self):
        from pact.register import _DriftCheckResponse, assess_register_consistency

        mock_agent = AsyncMock()
        mock_agent.assess = AsyncMock(return_value=(
            _DriftCheckResponse(consistent=True, confidence=0.7),
            200, 10,
        ))

        long_code = "x = 1\n" * 5000  # Way over 2000 chars
        await assess_register_consistency(
            mock_agent, long_code, "pragmatic-implementation",
            max_sample_chars=100,
        )

        # Check that the prompt was truncated
        call_args = mock_agent.assess.call_args
        prompt = call_args[0][1]  # Second positional arg
        assert "truncated" in prompt

    @pytest.mark.asyncio
    async def test_failure_returns_consistent(self):
        """On LLM failure, assume consistent (never block pipeline)."""
        from pact.register import assess_register_consistency

        mock_agent = AsyncMock()
        mock_agent.assess = AsyncMock(side_effect=RuntimeError("API down"))

        consistent, confidence = await assess_register_consistency(
            mock_agent, "some code", "rigorous-analytical",
        )
        assert consistent is True
        assert confidence == 0.0


class TestCheckArtifactsForDrift:
    """check_artifacts_for_drift() sampling and file-reading."""

    @pytest.mark.asyncio
    async def test_empty_register_skips(self):
        from pact.register import check_artifacts_for_drift

        mock_agent = AsyncMock()
        results = await check_artifacts_for_drift(
            mock_agent, Path("/tmp"), "", ["c1"], check_rate=1.0,
        )
        assert results == []

    @pytest.mark.asyncio
    async def test_empty_components_skips(self):
        from pact.register import check_artifacts_for_drift

        mock_agent = AsyncMock()
        results = await check_artifacts_for_drift(
            mock_agent, Path("/tmp"), "rigorous-analytical", [], check_rate=1.0,
        )
        assert results == []

    @pytest.mark.asyncio
    async def test_reads_and_checks_implementations(self, tmp_path):
        from pact.register import _DriftCheckResponse, check_artifacts_for_drift

        # Set up fake implementation files
        src_dir = tmp_path / ".pact" / "implementations" / "parser" / "src"
        src_dir.mkdir(parents=True)
        (src_dir / "module.py").write_text("def parse(x):\n    return x.strip()\n")

        mock_agent = AsyncMock()
        mock_agent.assess = AsyncMock(return_value=(
            _DriftCheckResponse(consistent=True, confidence=0.92),
            200, 10,
        ))

        results = await check_artifacts_for_drift(
            mock_agent, tmp_path, "rigorous-analytical",
            ["parser"], check_rate=1.0,  # Always check
        )

        assert len(results) == 1
        cid, consistent, conf = results[0]
        assert cid == "parser"
        assert consistent is True
        assert conf == 0.92

    @pytest.mark.asyncio
    async def test_probabilistic_sampling(self, tmp_path):
        """With check_rate=0.0, nothing gets checked."""
        from pact.register import check_artifacts_for_drift

        src_dir = tmp_path / ".pact" / "implementations" / "c1" / "src"
        src_dir.mkdir(parents=True)
        (src_dir / "module.py").write_text("x = 1\n")

        mock_agent = AsyncMock()
        results = await check_artifacts_for_drift(
            mock_agent, tmp_path, "rigorous-analytical",
            ["c1"], check_rate=0.0,
        )
        assert results == []
        mock_agent.assess.assert_not_called()

    @pytest.mark.asyncio
    async def test_drift_detected(self, tmp_path):
        from pact.register import _DriftCheckResponse, check_artifacts_for_drift

        src_dir = tmp_path / ".pact" / "implementations" / "c1" / "src"
        src_dir.mkdir(parents=True)
        (src_dir / "module.py").write_text("# hacky prototype\npass\n")

        mock_agent = AsyncMock()
        mock_agent.assess = AsyncMock(return_value=(
            _DriftCheckResponse(consistent=False, confidence=0.88),
            200, 10,
        ))

        results = await check_artifacts_for_drift(
            mock_agent, tmp_path, "rigorous-analytical",
            ["c1"], check_rate=1.0,
        )

        assert len(results) == 1
        assert results[0][1] is False  # drifted

    @pytest.mark.asyncio
    async def test_skips_missing_src_dir(self, tmp_path):
        """Components without implementations are silently skipped."""
        from pact.register import check_artifacts_for_drift

        mock_agent = AsyncMock()
        results = await check_artifacts_for_drift(
            mock_agent, tmp_path, "rigorous-analytical",
            ["nonexistent"], check_rate=1.0,
        )
        assert results == []


class TestRegisterCheckRateConfig:
    """register_check_rate loads from pact.yaml."""

    def test_default_rate(self):
        from pact.config import ProjectConfig
        cfg = ProjectConfig()
        assert cfg.register_check_rate == 0.1

    def test_custom_rate_from_yaml(self, tmp_path):
        from pact.config import load_project_config
        (tmp_path / "pact.yaml").write_text("register_check_rate: 0.25\n")
        cfg = load_project_config(tmp_path)
        assert cfg.register_check_rate == 0.25

    def test_zero_rate_disables(self, tmp_path):
        from pact.config import load_project_config
        (tmp_path / "pact.yaml").write_text("register_check_rate: 0.0\n")
        cfg = load_project_config(tmp_path)
        assert cfg.register_check_rate == 0.0
