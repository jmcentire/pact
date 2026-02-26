"""Tests for dysmemic pressure detection — health monitoring.

Based on the five conditions from "Your Agentic AI Is Recreating the
Meetings It Was Supposed to Replace."
"""

from __future__ import annotations

import pytest

from pact.health import (
    HealthCondition,
    HealthMetrics,
    HealthStatus,
    Remedy,
    check_health,
    render_health_report,
    should_abort,
    suggest_remedies,
)


# ── HealthMetrics Properties ──────────────────────────────────────


class TestHealthMetrics:
    def test_output_planning_ratio_balanced(self):
        m = HealthMetrics(planning_tokens=1000, generation_tokens=2000)
        assert m.output_planning_ratio == 2.0

    def test_output_planning_ratio_no_planning(self):
        m = HealthMetrics(planning_tokens=0, generation_tokens=1000)
        assert m.output_planning_ratio == float("inf")

    def test_output_planning_ratio_no_tokens(self):
        m = HealthMetrics()
        assert m.output_planning_ratio == 0.0

    def test_rejection_rate(self):
        m = HealthMetrics(implementation_attempts=10, implementation_failures=3)
        assert m.rejection_rate == 0.3

    def test_rejection_rate_no_attempts(self):
        m = HealthMetrics()
        assert m.rejection_rate == 0.0

    def test_test_pass_rate(self):
        m = HealthMetrics(test_passes=8, test_failures=2)
        assert m.test_pass_rate == 0.8

    def test_budget_velocity(self):
        m = HealthMetrics(
            contracts_produced=3,
            tests_produced=3,
            implementations_produced=3,
            total_spend=2.0,
        )
        assert m.budget_velocity == 4.5  # 9 artifacts / $2.00

    def test_budget_velocity_no_spend(self):
        m = HealthMetrics(contracts_produced=5)
        assert m.budget_velocity == 0.0

    def test_record_planning(self):
        m = HealthMetrics()
        m.record_planning(100, 200)
        assert m.planning_tokens == 300
        assert m.planning_calls == 1

    def test_record_generation(self):
        m = HealthMetrics()
        m.record_generation(500, 1000)
        assert m.generation_tokens == 1500
        assert m.generation_calls == 1

    def test_record_phase_tokens(self):
        m = HealthMetrics()
        m.record_phase_tokens("interview", 100, 200)
        m.record_phase_tokens("interview", 50, 50)
        assert m.phase_tokens["interview"].total_tokens == 400
        assert m.phase_tokens["interview"].calls == 2


# ── Output-to-Planning Ratio ──────────────────────────────────────


class TestOutputPlanningRatio:
    def test_healthy_ratio(self):
        m = HealthMetrics(planning_tokens=1000, generation_tokens=3000)
        report = check_health(m)
        opr = next(f for f in report.findings if f.condition == HealthCondition.output_planning_ratio)
        assert opr.status == HealthStatus.healthy

    def test_warning_ratio(self):
        m = HealthMetrics(planning_tokens=3000, generation_tokens=1000)
        report = check_health(m)
        opr = next(f for f in report.findings if f.condition == HealthCondition.output_planning_ratio)
        assert opr.status == HealthStatus.warning

    def test_critical_ratio(self):
        """The $50-planning-zero-output pattern."""
        m = HealthMetrics(planning_tokens=10000, generation_tokens=500)
        report = check_health(m)
        opr = next(f for f in report.findings if f.condition == HealthCondition.output_planning_ratio)
        assert opr.status == HealthStatus.critical

    def test_insufficient_data(self):
        m = HealthMetrics(planning_tokens=100, generation_tokens=50)
        report = check_health(m)
        opr = next(f for f in report.findings if f.condition == HealthCondition.output_planning_ratio)
        assert opr.status == HealthStatus.healthy  # Too little data to judge


# ── Rejection Rate ─────────────────────────────────────────────────


class TestRejectionRate:
    def test_healthy(self):
        m = HealthMetrics(implementation_attempts=10, implementation_failures=2)
        report = check_health(m)
        rr = next(f for f in report.findings if f.condition == HealthCondition.rejection_rate)
        assert rr.status == HealthStatus.healthy

    def test_warning(self):
        m = HealthMetrics(implementation_attempts=10, implementation_failures=6)
        report = check_health(m)
        rr = next(f for f in report.findings if f.condition == HealthCondition.rejection_rate)
        assert rr.status == HealthStatus.warning

    def test_critical_87_percent(self):
        """The article's pipeline rejected 87% of submissions."""
        m = HealthMetrics(implementation_attempts=100, implementation_failures=87)
        report = check_health(m)
        rr = next(f for f in report.findings if f.condition == HealthCondition.rejection_rate)
        assert rr.status == HealthStatus.critical


# ── Budget Velocity ────────────────────────────────────────────────


class TestBudgetVelocity:
    def test_healthy(self):
        m = HealthMetrics(
            contracts_produced=5, tests_produced=5,
            implementations_produced=5, total_spend=1.0,
        )
        report = check_health(m)
        bv = next(f for f in report.findings if f.condition == HealthCondition.budget_velocity)
        assert bv.status == HealthStatus.healthy

    def test_critical_zero_output(self):
        """Spent money, produced nothing."""
        m = HealthMetrics(total_spend=5.0)
        report = check_health(m)
        bv = next(f for f in report.findings if f.condition == HealthCondition.budget_velocity)
        assert bv.status == HealthStatus.critical


# ── Phase Balance ──────────────────────────────────────────────────


class TestPhaseBalance:
    def test_balanced(self):
        m = HealthMetrics()
        m.record_phase_tokens("interview", 500, 500)
        m.record_phase_tokens("implement", 500, 500)
        report = check_health(m)
        pb = next(f for f in report.findings if f.condition == HealthCondition.phase_balance)
        assert pb.status == HealthStatus.healthy

    def test_critical_single_phase_dominates(self):
        m = HealthMetrics()
        m.record_phase_tokens("interview", 5000, 5000)
        m.record_phase_tokens("implement", 100, 100)
        report = check_health(m)
        pb = next(f for f in report.findings if f.condition == HealthCondition.phase_balance)
        assert pb.status == HealthStatus.critical
        assert "interview" in pb.message


# ── Graceful Degradation ──────────────────────────────────────────


class TestGracefulDegradation:
    def test_no_cascades(self):
        m = HealthMetrics()
        report = check_health(m)
        gd = next(f for f in report.findings if f.condition == HealthCondition.graceful_degradation)
        assert gd.status == HealthStatus.healthy

    def test_cascade_warning(self):
        m = HealthMetrics(cascade_events=3)
        report = check_health(m)
        gd = next(f for f in report.findings if f.condition == HealthCondition.graceful_degradation)
        assert gd.status == HealthStatus.warning

    def test_cascade_critical(self):
        m = HealthMetrics(cascade_events=6)
        report = check_health(m)
        gd = next(f for f in report.findings if f.condition == HealthCondition.graceful_degradation)
        assert gd.status == HealthStatus.critical

    def test_repeated_component_failure(self):
        m = HealthMetrics()
        for _ in range(4):
            m.record_component_failure("auth")
        report = check_health(m)
        gd = next(f for f in report.findings if f.condition == HealthCondition.graceful_degradation)
        assert gd.status == HealthStatus.warning
        assert "auth" in gd.message


# ── Five Conditions ────────────────────────────────────────────────


class TestFiveConditions:
    def test_gain_outweighs_cost_critical(self):
        """Spent $5, produced nothing — the meeting pattern."""
        m = HealthMetrics(total_spend=5.0)
        report = check_health(m)
        goc = next(f for f in report.findings if f.condition == HealthCondition.gain_outweighs_cost)
        assert goc.status == HealthStatus.critical

    def test_variance_reaches_target(self):
        """All tokens in planning, none in generation."""
        m = HealthMetrics(planning_tokens=8000, generation_tokens=500)
        report = check_health(m)
        vrt = next(f for f in report.findings if f.condition == HealthCondition.variance_reaches_target)
        assert vrt.status == HealthStatus.critical


# ── Report & Abort ─────────────────────────────────────────────────


class TestReportAndAbort:
    def test_overall_status_healthy(self):
        m = HealthMetrics()
        report = check_health(m)
        assert report.overall_status == HealthStatus.healthy

    def test_overall_status_critical(self):
        m = HealthMetrics(total_spend=10.0)  # Zero output
        report = check_health(m)
        assert report.overall_status == HealthStatus.critical

    def test_should_abort_on_zero_output(self):
        m = HealthMetrics(total_spend=5.0)
        report = check_health(m)
        assert should_abort(report) is True

    def test_should_not_abort_healthy(self):
        m = HealthMetrics(
            contracts_produced=5, tests_produced=5,
            total_spend=1.0,
        )
        report = check_health(m)
        assert should_abort(report) is False

    def test_render_report(self):
        m = HealthMetrics(total_spend=10.0)
        report = check_health(m)
        text = render_health_report(report)
        assert "CRITICAL" in text

    def test_render_healthy_report(self):
        m = HealthMetrics()
        report = check_health(m)
        text = render_health_report(report)
        assert "HEALTHY" in text


# ── Serialization ─────────────────────────────────────────────────


class TestHealthSerialization:
    def test_roundtrip(self):
        """to_dict -> from_dict should preserve all fields."""
        m = HealthMetrics(
            planning_tokens=1000,
            generation_tokens=2000,
            planning_calls=5,
            generation_calls=10,
            plan_revisions=2,
            implementation_attempts=8,
            implementation_failures=3,
            test_failures=4,
            test_passes=12,
            contracts_produced=3,
            tests_produced=3,
            implementations_produced=2,
            cascade_events=1,
            total_spend=1.50,
            budget_cap=10.0,
        )
        m.record_component_failure("auth")
        m.record_component_failure("auth")
        m.record_phase_tokens("interview", 100, 200)
        m.record_phase_tokens("implement", 500, 800)

        d = m.to_dict()
        restored = HealthMetrics.from_dict(d)

        assert restored.planning_tokens == 1000
        assert restored.generation_tokens == 2000
        assert restored.planning_calls == 5
        assert restored.generation_calls == 10
        assert restored.plan_revisions == 2
        assert restored.implementation_attempts == 8
        assert restored.implementation_failures == 3
        assert restored.test_failures == 4
        assert restored.test_passes == 12
        assert restored.contracts_produced == 3
        assert restored.tests_produced == 3
        assert restored.implementations_produced == 2
        assert restored.cascade_events == 1
        assert restored.total_spend == 1.50
        assert restored.budget_cap == 10.0
        assert restored.component_failures == {"auth": 2}
        assert restored.phase_tokens["interview"].total_tokens == 300
        assert restored.phase_tokens["implement"].total_tokens == 1300
        assert restored.phase_tokens["implement"].calls == 1

    def test_from_empty_dict(self):
        """from_dict({}) should return fresh HealthMetrics."""
        m = HealthMetrics.from_dict({})
        assert m.planning_tokens == 0
        assert m.total_spend == 0.0
        assert m.phase_tokens == {}

    def test_from_partial_dict(self):
        """from_dict with partial keys should fill defaults."""
        d = {"planning_tokens": 500, "total_spend": 2.0}
        m = HealthMetrics.from_dict(d)
        assert m.planning_tokens == 500
        assert m.total_spend == 2.0
        assert m.generation_tokens == 0
        assert m.cascade_events == 0


# ── Remedies ──────────────────────────────────────────────────────


class TestRemedies:
    def test_healthy_returns_empty(self):
        m = HealthMetrics()
        report = check_health(m)
        remedies = suggest_remedies(report)
        assert remedies == []

    def test_rejection_rate_warning_no_remedy(self):
        """WARNING-level rejection rate should NOT trigger config changes."""
        m = HealthMetrics(implementation_attempts=10, implementation_failures=6)
        report = check_health(m)
        remedies = suggest_remedies(report)
        kinds = [r.kind for r in remedies]
        assert "max_plan_revisions" not in kinds

    def test_rejection_rate_critical_suggests_remedy(self):
        """CRITICAL-level rejection rate should suggest reducing revisions."""
        m = HealthMetrics(implementation_attempts=10, implementation_failures=9)
        report = check_health(m)
        remedies = suggest_remedies(report)
        kinds = [r.kind for r in remedies]
        assert "max_plan_revisions" in kinds

    def test_planning_heavy_suggests_shaping_disable(self):
        """CRITICAL planning ratio (< 0.25) should suggest disabling shaping."""
        m = HealthMetrics(planning_tokens=10000, generation_tokens=500)
        report = check_health(m)
        remedies = suggest_remedies(report)
        kinds = [r.kind for r in remedies]
        assert "shaping" in kinds

    def test_planning_warning_no_shaping_remedy(self):
        """WARNING-level planning ratio should NOT disable shaping."""
        m = HealthMetrics(planning_tokens=3000, generation_tokens=1000)
        report = check_health(m)
        remedies = suggest_remedies(report)
        kinds = [r.kind for r in remedies]
        assert "shaping" not in kinds

    def test_cascade_critical_suggests_skip(self):
        m = HealthMetrics(cascade_events=6)
        report = check_health(m)
        remedies = suggest_remedies(report)
        kinds = [r.kind for r in remedies]
        assert "skip_cascaded" in kinds

    def test_low_velocity_is_informational(self):
        m = HealthMetrics(total_spend=5.0)  # Zero artifacts
        report = check_health(m)
        remedies = suggest_remedies(report, m)
        kinds = [r.kind for r in remedies]
        assert "informational" in kinds

    def test_config_remedies_are_not_auto(self):
        """max_plan_revisions and shaping remedies should require user approval."""
        m = HealthMetrics(
            implementation_attempts=10, implementation_failures=9,
            planning_tokens=10000, generation_tokens=500,
        )
        report = check_health(m)
        remedies = suggest_remedies(report, m)
        for r in remedies:
            if r.kind in ("max_plan_revisions", "shaping"):
                assert r.auto is False
                assert r.fifo_hint != ""

    def test_skip_cascaded_is_proposed(self):
        """skip_cascaded is proposed, not auto-applied.

        Auto-skipping removes redundancy (Brittleness Trap / C5) — the
        contract boundary may allow independent success despite parent
        failure. User decides via FIFO.
        """
        m = HealthMetrics(cascade_events=6)
        report = check_health(m)
        remedies = suggest_remedies(report, m)
        for r in remedies:
            if r.kind == "skip_cascaded":
                assert r.auto is False
                assert r.fifo_hint != ""

    def test_remedies_include_concrete_numbers(self):
        """Remedy descriptions should include actual spend/token numbers."""
        m = HealthMetrics(
            planning_tokens=10000, generation_tokens=500,
            total_spend=5.0,
        )
        report = check_health(m)
        remedies = suggest_remedies(report, m)
        shaping_remedy = next((r for r in remedies if r.kind == "shaping"), None)
        assert shaping_remedy is not None
        assert "10,000" in shaping_remedy.description
        assert "500" in shaping_remedy.description


# ── Threshold Overrides ───────────────────────────────────────────


class TestThresholdOverrides:
    def test_default_thresholds_unchanged(self):
        """check_health with no overrides uses module defaults."""
        m = HealthMetrics(planning_tokens=3000, generation_tokens=1000)
        report = check_health(m)
        opr = next(f for f in report.findings if f.condition == HealthCondition.output_planning_ratio)
        assert opr.status == HealthStatus.warning

    def test_relaxed_threshold_avoids_warning(self):
        """Project can relax thresholds to avoid false positives."""
        m = HealthMetrics(planning_tokens=3000, generation_tokens=1000)
        # With a lower warning threshold, 0.33 ratio is now healthy
        report = check_health(m, thresholds={"output_planning_ratio_warning": 0.2})
        opr = next(f for f in report.findings if f.condition == HealthCondition.output_planning_ratio)
        assert opr.status == HealthStatus.healthy

    def test_strict_threshold_triggers_critical(self):
        """Project can tighten thresholds for strict monitoring."""
        m = HealthMetrics(implementation_attempts=10, implementation_failures=6)
        # Default critical is 0.8, override to 0.5
        report = check_health(m, thresholds={"rejection_rate_critical": 0.5})
        rr = next(f for f in report.findings if f.condition == HealthCondition.rejection_rate)
        assert rr.status == HealthStatus.critical

    def test_cascade_threshold_override(self):
        """Project can raise cascade thresholds for complex projects."""
        m = HealthMetrics(cascade_events=4)
        # Default warning is 2, critical is 5. Override warning to 5.
        report = check_health(m, thresholds={"cascade_warning": 5})
        gd = next(f for f in report.findings if f.condition == HealthCondition.graceful_degradation)
        assert gd.status == HealthStatus.healthy
