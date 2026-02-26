"""Dysmemic pressure detection — self-monitoring for agentic pipelines.

Implements the five conditions from "Your Agentic AI Is Recreating the
Meetings It Was Supposed to Replace" as runtime checks. Prevents pact
from becoming the pipeline that spent $50 on planning and shipped nothing.

Key metrics:
- Output-to-planning ratio: are we generating or just coordinating?
- Rejection rate: are agents optimizing for each other's approval?
- Budget velocity: useful output per dollar spent
- Phase balance: is any phase consuming disproportionate budget?
- Graceful degradation: do component failures cascade or contain?
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from enum import StrEnum

logger = logging.getLogger(__name__)


# ── Health Status ──────────────────────────────────────────────────


class HealthStatus(StrEnum):
    """Overall health assessment."""
    healthy = "healthy"
    warning = "warning"
    critical = "critical"


class HealthCondition(StrEnum):
    """The five conditions from the article, plus operational checks."""
    # Article's five conditions
    room_to_improve = "room_to_improve"
    nonlinear_integration = "nonlinear_integration"
    variance_reaches_target = "variance_reaches_target"
    gain_outweighs_cost = "gain_outweighs_cost"
    graceful_degradation = "graceful_degradation"
    # Operational checks
    output_planning_ratio = "output_planning_ratio"
    rejection_rate = "rejection_rate"
    budget_velocity = "budget_velocity"
    phase_balance = "phase_balance"


# ── Metrics Tracking ───────────────────────────────────────────────


@dataclass
class PhaseTokens:
    """Token usage for a single phase."""
    input_tokens: int = 0
    output_tokens: int = 0
    calls: int = 0

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens


@dataclass
class HealthMetrics:
    """Running metrics for dysmemic pressure detection.

    Tracks token spend by category (planning vs generation),
    rejection/revision counts, and per-phase budget allocation.
    """

    # Token spend by category
    planning_tokens: int = 0     # Research, planning, evaluation
    generation_tokens: int = 0   # Contract authoring, test authoring, code authoring

    # Call counts by category
    planning_calls: int = 0
    generation_calls: int = 0

    # Rejection/revision tracking
    plan_revisions: int = 0
    implementation_attempts: int = 0
    implementation_failures: int = 0
    test_failures: int = 0
    test_passes: int = 0

    # Artifact counts
    contracts_produced: int = 0
    tests_produced: int = 0
    implementations_produced: int = 0

    # Component-level failure tracking
    component_failures: dict[str, int] = field(default_factory=dict)
    cascade_events: int = 0  # Times one failure triggered another

    # Per-phase token tracking
    phase_tokens: dict[str, PhaseTokens] = field(default_factory=dict)

    # Dollar spend
    total_spend: float = 0.0
    budget_cap: float = 10.0

    def record_planning(self, input_tokens: int, output_tokens: int) -> None:
        """Record tokens spent on planning/research/evaluation."""
        self.planning_tokens += input_tokens + output_tokens
        self.planning_calls += 1

    def record_generation(self, input_tokens: int, output_tokens: int) -> None:
        """Record tokens spent on actual artifact generation."""
        self.generation_tokens += input_tokens + output_tokens
        self.generation_calls += 1

    def record_phase_tokens(
        self, phase: str, input_tokens: int, output_tokens: int,
    ) -> None:
        """Record tokens for a specific phase."""
        if phase not in self.phase_tokens:
            self.phase_tokens[phase] = PhaseTokens()
        pt = self.phase_tokens[phase]
        pt.input_tokens += input_tokens
        pt.output_tokens += output_tokens
        pt.calls += 1

    def record_revision(self) -> None:
        self.plan_revisions += 1

    def record_attempt(self, success: bool) -> None:
        self.implementation_attempts += 1
        if not success:
            self.implementation_failures += 1

    def record_test_run(self, passed: int, failed: int) -> None:
        self.test_passes += passed
        self.test_failures += failed

    def record_component_failure(self, component_id: str) -> None:
        self.component_failures[component_id] = (
            self.component_failures.get(component_id, 0) + 1
        )

    def record_cascade(self) -> None:
        self.cascade_events += 1

    def to_dict(self) -> dict:
        """Serialize to a JSON-safe dict for persistence in RunState."""
        return {
            "planning_tokens": self.planning_tokens,
            "generation_tokens": self.generation_tokens,
            "planning_calls": self.planning_calls,
            "generation_calls": self.generation_calls,
            "plan_revisions": self.plan_revisions,
            "implementation_attempts": self.implementation_attempts,
            "implementation_failures": self.implementation_failures,
            "test_failures": self.test_failures,
            "test_passes": self.test_passes,
            "contracts_produced": self.contracts_produced,
            "tests_produced": self.tests_produced,
            "implementations_produced": self.implementations_produced,
            "component_failures": dict(self.component_failures),
            "cascade_events": self.cascade_events,
            "phase_tokens": {
                phase: {
                    "input_tokens": pt.input_tokens,
                    "output_tokens": pt.output_tokens,
                    "calls": pt.calls,
                }
                for phase, pt in self.phase_tokens.items()
            },
            "total_spend": self.total_spend,
            "budget_cap": self.budget_cap,
        }

    @classmethod
    def from_dict(cls, data: dict) -> HealthMetrics:
        """Deserialize from a dict. Tolerant of missing keys."""
        if not data:
            return cls()
        phase_tokens = {}
        for phase, pt_data in data.get("phase_tokens", {}).items():
            phase_tokens[phase] = PhaseTokens(
                input_tokens=pt_data.get("input_tokens", 0),
                output_tokens=pt_data.get("output_tokens", 0),
                calls=pt_data.get("calls", 0),
            )
        return cls(
            planning_tokens=data.get("planning_tokens", 0),
            generation_tokens=data.get("generation_tokens", 0),
            planning_calls=data.get("planning_calls", 0),
            generation_calls=data.get("generation_calls", 0),
            plan_revisions=data.get("plan_revisions", 0),
            implementation_attempts=data.get("implementation_attempts", 0),
            implementation_failures=data.get("implementation_failures", 0),
            test_failures=data.get("test_failures", 0),
            test_passes=data.get("test_passes", 0),
            contracts_produced=data.get("contracts_produced", 0),
            tests_produced=data.get("tests_produced", 0),
            implementations_produced=data.get("implementations_produced", 0),
            component_failures=data.get("component_failures", {}),
            cascade_events=data.get("cascade_events", 0),
            phase_tokens=phase_tokens,
            total_spend=data.get("total_spend", 0.0),
            budget_cap=data.get("budget_cap", 10.0),
        )

    @property
    def output_planning_ratio(self) -> float:
        """Ratio of generation tokens to planning tokens.

        > 1.0 means more generation than planning (healthy).
        < 1.0 means more planning than generation (warning).
        Infinite if no planning (healthy edge case).
        """
        if self.planning_tokens == 0:
            return float("inf") if self.generation_tokens > 0 else 0.0
        return self.generation_tokens / self.planning_tokens

    @property
    def rejection_rate(self) -> float:
        """Fraction of attempts that failed (0.0 - 1.0)."""
        total = self.implementation_attempts
        if total == 0:
            return 0.0
        return self.implementation_failures / total

    @property
    def test_pass_rate(self) -> float:
        """Fraction of tests that passed (0.0 - 1.0)."""
        total = self.test_passes + self.test_failures
        if total == 0:
            return 0.0
        return self.test_passes / total

    @property
    def budget_velocity(self) -> float:
        """Useful artifacts per dollar spent."""
        if self.total_spend <= 0:
            return 0.0
        useful = self.contracts_produced + self.tests_produced + self.implementations_produced
        return useful / self.total_spend

    @property
    def artifacts_produced(self) -> int:
        return self.contracts_produced + self.tests_produced + self.implementations_produced

    @property
    def total_tokens(self) -> int:
        return self.planning_tokens + self.generation_tokens


# ── Health Findings ────────────────────────────────────────────────


@dataclass
class HealthFinding:
    """A single health check result."""
    condition: HealthCondition
    status: HealthStatus
    message: str
    metric_value: float = 0.0
    threshold: float = 0.0


@dataclass
class HealthReport:
    """Complete health assessment."""
    findings: list[HealthFinding] = field(default_factory=list)

    @property
    def overall_status(self) -> HealthStatus:
        if any(f.status == HealthStatus.critical for f in self.findings):
            return HealthStatus.critical
        if any(f.status == HealthStatus.warning for f in self.findings):
            return HealthStatus.warning
        return HealthStatus.healthy

    @property
    def critical_findings(self) -> list[HealthFinding]:
        return [f for f in self.findings if f.status == HealthStatus.critical]

    @property
    def warning_findings(self) -> list[HealthFinding]:
        return [f for f in self.findings if f.status == HealthStatus.warning]


# ── Health Checks ──────────────────────────────────────────────────

# Thresholds — configurable, sane defaults
OUTPUT_PLANNING_RATIO_WARNING = 0.5    # Below this = more planning than generation
OUTPUT_PLANNING_RATIO_CRITICAL = 0.25  # Below this = 4x more planning than generation
REJECTION_RATE_WARNING = 0.5           # 50%+ rejection rate
REJECTION_RATE_CRITICAL = 0.8          # 80%+ rejection rate
BUDGET_VELOCITY_WARNING = 1.0          # Less than 1 artifact per dollar
BUDGET_VELOCITY_CRITICAL = 0.25        # Less than 0.25 artifacts per dollar
PHASE_BALANCE_WARNING = 0.4            # Single phase consuming 40%+ of tokens
PHASE_BALANCE_CRITICAL = 0.6           # Single phase consuming 60%+ of tokens
CASCADE_WARNING = 2                    # 2+ cascade events
CASCADE_CRITICAL = 5                   # 5+ cascade events
COMPONENT_FAILURE_THRESHOLD = 3        # Same component failing 3+ times


def check_health(
    metrics: HealthMetrics,
    thresholds: dict[str, float] | None = None,
) -> HealthReport:
    """Run all health checks against current metrics.

    Args:
        metrics: Current health metrics.
        thresholds: Optional per-project threshold overrides. Keys match
            the module-level constant names (lowercase), e.g.:
            {"output_planning_ratio_warning": 0.3, "rejection_rate_critical": 0.9}

    Returns a HealthReport with findings for each condition.
    This is pact's immune system — it detects the organizational
    dysfunction patterns the article describes and flags them before
    they consume the budget.
    """
    t = thresholds or {}
    findings: list[HealthFinding] = []

    findings.append(_check_output_planning_ratio(metrics, t))
    findings.append(_check_rejection_rate(metrics, t))
    findings.append(_check_budget_velocity(metrics, t))
    findings.append(_check_phase_balance(metrics, t))
    findings.append(_check_graceful_degradation(metrics, t))
    findings.extend(_check_five_conditions(metrics))

    report = HealthReport(findings=findings)

    # Log warnings
    for f in report.critical_findings:
        logger.warning("HEALTH CRITICAL: [%s] %s", f.condition, f.message)
    for f in report.warning_findings:
        logger.info("HEALTH WARNING: [%s] %s", f.condition, f.message)

    return report


def _check_output_planning_ratio(metrics: HealthMetrics, t: dict[str, float] | None = None) -> HealthFinding:
    """Check if we're generating more than we're planning.

    The article's pipeline spent $50 on planning and produced nothing.
    This catches that pattern early.
    """
    t = t or {}
    ratio = metrics.output_planning_ratio
    warn = t.get("output_planning_ratio_warning", OUTPUT_PLANNING_RATIO_WARNING)
    crit = t.get("output_planning_ratio_critical", OUTPUT_PLANNING_RATIO_CRITICAL)

    # Not enough data yet
    if metrics.total_tokens < 1000:
        return HealthFinding(
            condition=HealthCondition.output_planning_ratio,
            status=HealthStatus.healthy,
            message="Insufficient data for output/planning ratio",
            metric_value=ratio,
        )

    if ratio < crit:
        return HealthFinding(
            condition=HealthCondition.output_planning_ratio,
            status=HealthStatus.critical,
            message=f"Planning dominates generation {ratio:.2f}x — "
                    f"spending {metrics.planning_tokens} tokens planning vs "
                    f"{metrics.generation_tokens} generating. "
                    f"This is the $50-planning-zero-output pattern.",
            metric_value=ratio,
            threshold=crit,
        )

    if ratio < warn:
        return HealthFinding(
            condition=HealthCondition.output_planning_ratio,
            status=HealthStatus.warning,
            message=f"Planning heavy: {ratio:.2f}x generation/planning ratio. "
                    f"Consider reducing plan revisions.",
            metric_value=ratio,
            threshold=warn,
        )

    return HealthFinding(
        condition=HealthCondition.output_planning_ratio,
        status=HealthStatus.healthy,
        message=f"Output/planning ratio: {ratio:.2f}x",
        metric_value=ratio,
    )


def _check_rejection_rate(metrics: HealthMetrics, t: dict[str, float] | None = None) -> HealthFinding:
    """Check if agents are rejecting too much of each other's work.

    The article's pipeline rejected 87% of submissions. Selection pressure
    was optimizing for inter-agent approval, not outcomes.
    """
    t = t or {}
    rate = metrics.rejection_rate
    warn = t.get("rejection_rate_warning", REJECTION_RATE_WARNING)
    crit = t.get("rejection_rate_critical", REJECTION_RATE_CRITICAL)

    if metrics.implementation_attempts < 2:
        return HealthFinding(
            condition=HealthCondition.rejection_rate,
            status=HealthStatus.healthy,
            message="Insufficient attempts for rejection rate",
            metric_value=rate,
        )

    if rate >= crit:
        return HealthFinding(
            condition=HealthCondition.rejection_rate,
            status=HealthStatus.critical,
            message=f"Rejection rate {rate:.0%} — agents rejecting {metrics.implementation_failures}/"
                    f"{metrics.implementation_attempts} attempts. "
                    f"Selection pressure is on process compliance, not outcomes.",
            metric_value=rate,
            threshold=crit,
        )

    if rate >= warn:
        return HealthFinding(
            condition=HealthCondition.rejection_rate,
            status=HealthStatus.warning,
            message=f"Rejection rate {rate:.0%} — review if contracts are overly strict.",
            metric_value=rate,
            threshold=warn,
        )

    return HealthFinding(
        condition=HealthCondition.rejection_rate,
        status=HealthStatus.healthy,
        message=f"Rejection rate: {rate:.0%}",
        metric_value=rate,
    )


def _check_budget_velocity(metrics: HealthMetrics, t: dict[str, float] | None = None) -> HealthFinding:
    """Check useful output per dollar.

    If velocity drops below threshold, the system is churning without
    producing value — coordination cost exceeds execution value.
    """
    t = t or {}
    velocity = metrics.budget_velocity
    warn = t.get("budget_velocity_warning", BUDGET_VELOCITY_WARNING)
    crit = t.get("budget_velocity_critical", BUDGET_VELOCITY_CRITICAL)

    if metrics.total_spend < 0.10:
        return HealthFinding(
            condition=HealthCondition.budget_velocity,
            status=HealthStatus.healthy,
            message="Insufficient spend for velocity check",
            metric_value=velocity,
        )

    if velocity < crit:
        return HealthFinding(
            condition=HealthCondition.budget_velocity,
            status=HealthStatus.critical,
            message=f"Budget velocity {velocity:.2f} artifacts/$ — "
                    f"spent ${metrics.total_spend:.2f} for {metrics.artifacts_produced} artifacts. "
                    f"Coordination complexity exceeds execution value.",
            metric_value=velocity,
            threshold=crit,
        )

    if velocity < warn:
        return HealthFinding(
            condition=HealthCondition.budget_velocity,
            status=HealthStatus.warning,
            message=f"Budget velocity {velocity:.2f} artifacts/$ — below target.",
            metric_value=velocity,
            threshold=warn,
        )

    return HealthFinding(
        condition=HealthCondition.budget_velocity,
        status=HealthStatus.healthy,
        message=f"Budget velocity: {velocity:.2f} artifacts/$",
        metric_value=velocity,
    )


def _check_phase_balance(metrics: HealthMetrics, t: dict[str, float] | None = None) -> HealthFinding:
    """Check if any single phase dominates token consumption.

    If interview/decompose phases consume 60%+ of tokens, the architecture
    is inverted — coordination layer is heavier than execution layer.
    """
    t = t or {}
    if not metrics.phase_tokens:
        return HealthFinding(
            condition=HealthCondition.phase_balance,
            status=HealthStatus.healthy,
            message="Insufficient data for phase balance check",
        )

    # Use phase-level token totals (these may differ from planning/generation tokens)
    total = sum(pt.total_tokens for pt in metrics.phase_tokens.values())
    if total < 1000:
        return HealthFinding(
            condition=HealthCondition.phase_balance,
            status=HealthStatus.healthy,
            message="Insufficient data for phase balance check",
        )
    worst_phase = ""
    worst_ratio = 0.0

    num_phases = len(metrics.phase_tokens)
    # Fair share: with N phases, each should get ~1/N of tokens.
    # Only flag when a phase significantly exceeds its fair share.
    fair_share = 1.0 / num_phases if num_phases > 1 else 1.0

    for phase, pt in metrics.phase_tokens.items():
        ratio = pt.total_tokens / total if total > 0 else 0
        if ratio > worst_ratio:
            worst_ratio = ratio
            worst_phase = phase

    # Excess over fair share matters more than raw ratio
    excess = worst_ratio - fair_share if num_phases > 1 else 0.0

    pb_crit = t.get("phase_balance_critical", PHASE_BALANCE_CRITICAL)
    pb_warn = t.get("phase_balance_warning", PHASE_BALANCE_WARNING)

    if worst_ratio >= pb_crit and excess > 0.1:
        return HealthFinding(
            condition=HealthCondition.phase_balance,
            status=HealthStatus.critical,
            message=f"Phase '{worst_phase}' consuming {worst_ratio:.0%} of all tokens. "
                    f"Architecture is inverted — simplify coordination.",
            metric_value=worst_ratio,
            threshold=pb_crit,
        )

    if worst_ratio >= pb_warn and excess > 0.1:
        return HealthFinding(
            condition=HealthCondition.phase_balance,
            status=HealthStatus.warning,
            message=f"Phase '{worst_phase}' consuming {worst_ratio:.0%} of tokens.",
            metric_value=worst_ratio,
            threshold=pb_warn,
        )

    return HealthFinding(
        condition=HealthCondition.phase_balance,
        status=HealthStatus.healthy,
        message=f"Phase balance OK (max: {worst_phase} at {worst_ratio:.0%})",
        metric_value=worst_ratio,
    )


def _check_graceful_degradation(metrics: HealthMetrics, t: dict[str, float] | None = None) -> HealthFinding:
    """Check condition 5: failures must not cascade.

    If one component's failure triggers failures in others, the system
    has brittle handoffs — the fifth condition is violated.
    """
    t = t or {}
    casc_crit = int(t.get("cascade_critical", CASCADE_CRITICAL))
    casc_warn = int(t.get("cascade_warning", CASCADE_WARNING))
    comp_fail_thresh = int(t.get("component_failure_threshold", COMPONENT_FAILURE_THRESHOLD))

    if metrics.cascade_events >= casc_crit:
        return HealthFinding(
            condition=HealthCondition.graceful_degradation,
            status=HealthStatus.critical,
            message=f"{metrics.cascade_events} cascade events — failures are propagating. "
                    f"Add isolation between components.",
            metric_value=float(metrics.cascade_events),
            threshold=float(casc_crit),
        )

    if metrics.cascade_events >= casc_warn:
        return HealthFinding(
            condition=HealthCondition.graceful_degradation,
            status=HealthStatus.warning,
            message=f"{metrics.cascade_events} cascade events detected.",
            metric_value=float(metrics.cascade_events),
            threshold=float(casc_warn),
        )

    # Check for repeated single-component failures
    repeat_failures = {
        cid: count for cid, count in metrics.component_failures.items()
        if count >= comp_fail_thresh
    }
    if repeat_failures:
        return HealthFinding(
            condition=HealthCondition.graceful_degradation,
            status=HealthStatus.warning,
            message=f"Repeated failures: {repeat_failures}. "
                    f"Consider simplifying or skipping these components.",
            metric_value=float(max(repeat_failures.values())),
        )

    return HealthFinding(
        condition=HealthCondition.graceful_degradation,
        status=HealthStatus.healthy,
        message="No cascade events or repeated failures",
    )


def _check_five_conditions(metrics: HealthMetrics) -> list[HealthFinding]:
    """Check the article's five formal conditions for calibrated variance.

    1. Room to improve — are there uncovered functions or failing tests?
    2. Nonlinear integration — is the integration function using contracts, not pass-through?
    3. Variance reaches target — is budget reaching the right phases?
    4. Gain outweighs cost — are we producing more value than we're consuming?
    5. Graceful degradation — already checked above, included for completeness.
    """
    findings = []

    # Condition 1: Room to improve
    # If everything is passing and covered, no benefit to variance
    if metrics.artifacts_produced > 0 and metrics.test_pass_rate == 1.0 and metrics.rejection_rate == 0.0:
        findings.append(HealthFinding(
            condition=HealthCondition.room_to_improve,
            status=HealthStatus.healthy,
            message="All tests passing, no rejections — system is stable. "
                    "Variance benefits diminish in stable systems.",
            metric_value=1.0,
        ))
    else:
        findings.append(HealthFinding(
            condition=HealthCondition.room_to_improve,
            status=HealthStatus.healthy,
            message=f"Room to improve: {metrics.test_failures} test failures, "
                    f"{metrics.implementation_failures} impl failures — variance can help.",
            metric_value=0.0,
        ))

    # Condition 3: Variance reaches target
    # If all budget is going to planning, variance never touches the actual code
    generation_pct = (
        metrics.generation_tokens / metrics.total_tokens
        if metrics.total_tokens > 0 else 0.5
    )
    if generation_pct < 0.2 and metrics.total_tokens > 5000:
        findings.append(HealthFinding(
            condition=HealthCondition.variance_reaches_target,
            status=HealthStatus.critical,
            message=f"Only {generation_pct:.0%} of tokens reach generation. "
                    f"Variance is trapped in the planning layer.",
            metric_value=generation_pct,
            threshold=0.2,
        ))
    else:
        findings.append(HealthFinding(
            condition=HealthCondition.variance_reaches_target,
            status=HealthStatus.healthy,
            message=f"Generation receives {generation_pct:.0%} of tokens.",
            metric_value=generation_pct,
        ))

    # Condition 4: Gain outweighs cost
    if metrics.total_spend > 1.0 and metrics.artifacts_produced == 0:
        findings.append(HealthFinding(
            condition=HealthCondition.gain_outweighs_cost,
            status=HealthStatus.critical,
            message=f"Spent ${metrics.total_spend:.2f} with zero artifacts produced. "
                    f"This is the meeting-that-replaces-work pattern.",
            metric_value=0.0,
            threshold=1.0,
        ))
    elif metrics.total_spend > 0 and metrics.budget_velocity < 0.5:
        findings.append(HealthFinding(
            condition=HealthCondition.gain_outweighs_cost,
            status=HealthStatus.warning,
            message=f"Cost/benefit ratio marginal: {metrics.budget_velocity:.2f} artifacts/$.",
            metric_value=metrics.budget_velocity,
            threshold=0.5,
        ))
    else:
        findings.append(HealthFinding(
            condition=HealthCondition.gain_outweighs_cost,
            status=HealthStatus.healthy,
            message="Gain outweighs cost" + (
                f": {metrics.budget_velocity:.1f} artifacts/$"
                if metrics.total_spend > 0 else ""
            ),
        ))

    return findings


# ── Render ─────────────────────────────────────────────────────────


def render_health_report(report: HealthReport) -> str:
    """Render health report as human-readable text."""
    lines = []
    status = report.overall_status
    lines.append(f"Health: {status.value.upper()}")
    lines.append("")

    if report.critical_findings:
        lines.append("CRITICAL:")
        for f in report.critical_findings:
            lines.append(f"  [{f.condition}] {f.message}")
        lines.append("")

    if report.warning_findings:
        lines.append("WARNING:")
        for f in report.warning_findings:
            lines.append(f"  [{f.condition}] {f.message}")
        lines.append("")

    healthy = [f for f in report.findings if f.status == HealthStatus.healthy]
    if healthy and not report.critical_findings and not report.warning_findings:
        lines.append("All checks passed.")

    return "\n".join(lines)


@dataclass
class Remedy:
    """A corrective action suggested by health analysis.

    auto=True means safe to apply without user confirmation
    (e.g. skip_cascaded protects the pipeline, informational is read-only).

    auto=False means the remedy changes user-configured behavior and
    should be proposed, not applied. The user accepts via FIFO directive:
      pact signal <project> --directive '{"type":"apply_remedy","remedy":"<kind>"}'
    """
    kind: str           # "max_plan_revisions", "shaping", "skip_cascaded", "informational"
    description: str
    auto: bool = True   # Safe to auto-apply?
    fifo_hint: str = "" # Example FIFO command for user (empty if auto)
    component_ids: list[str] = field(default_factory=list)


def suggest_remedies(report: HealthReport, metrics: HealthMetrics | None = None) -> list[Remedy]:
    """Map health findings to corrective actions.

    Returns a list of Remedy objects. Remedies with auto=True are applied
    immediately by the scheduler. Remedies with auto=False are surfaced
    as proposals in the pause message for user acceptance via FIFO.
    """
    remedies: list[Remedy] = []

    for f in report.findings:
        if f.condition == HealthCondition.rejection_rate and f.status == HealthStatus.critical:
            remedies.append(Remedy(
                kind="max_plan_revisions",
                description=f"High rejection rate ({f.metric_value:.0%}) — reduce max_plan_revisions to 1",
                auto=False,
                fifo_hint='{"type":"apply_remedy","remedy":"max_plan_revisions","value":1}',
            ))

        if f.condition == HealthCondition.output_planning_ratio and f.status == HealthStatus.critical:
            plan_tok = metrics.planning_tokens if metrics else 0
            gen_tok = metrics.generation_tokens if metrics else 0
            remedies.append(Remedy(
                kind="shaping",
                description=(
                    f"Planning-heavy ratio ({f.metric_value:.2f}x) — "
                    f"{plan_tok:,} planning vs {gen_tok:,} generation tokens. "
                    f"Disable shaping to redirect budget toward generation."
                ),
                auto=False,
                fifo_hint='{"type":"apply_remedy","remedy":"shaping"}',
            ))

        if f.condition == HealthCondition.graceful_degradation and f.status == HealthStatus.critical:
            remedies.append(Remedy(
                kind="skip_cascaded",
                description="Cascade failures detected — skip pending downstream components",
                auto=False,
                fifo_hint='{"type":"apply_remedy","remedy":"skip_cascaded"}',
            ))

        if f.condition == HealthCondition.budget_velocity and f.status == HealthStatus.critical:
            spend = metrics.total_spend if metrics else 0
            artifacts = metrics.artifacts_produced if metrics else 0
            remedies.append(Remedy(
                kind="informational",
                description=(
                    f"Spent ${spend:.2f} for {artifacts} artifacts "
                    f"({f.metric_value:.2f}/$ velocity). Consider reducing scope."
                ),
                auto=True,
            ))

    return remedies


def should_abort(report: HealthReport) -> bool:
    """Check if the health report indicates the run should abort.

    Returns True if there are critical findings that indicate
    the system is in the $50-planning-zero-output failure mode.
    """
    for f in report.critical_findings:
        if f.condition == HealthCondition.gain_outweighs_cost:
            return True
        if f.condition == HealthCondition.output_planning_ratio:
            return True
        if f.condition == HealthCondition.variance_reaches_target:
            return True
    return False
