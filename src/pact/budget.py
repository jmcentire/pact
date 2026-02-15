"""Per-project + daily token/dollar tracking.

Reused from swarm with minimal adaptation. Dissipation boundary:
each project has a dollar cap. No retries on budget exceeded.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

# Built-in defaults — overridable via config.yaml model_pricing
# Format: model_id -> (input_cost_per_million, output_cost_per_million)
DEFAULT_MODEL_PRICING: dict[str, tuple[float, float]] = {
    # Anthropic
    "claude-haiku-4-5-20251001": (0.80, 4.00),
    "claude-sonnet-4-5-20250929": (3.00, 15.00),
    "claude-opus-4-6": (15.00, 75.00),
    # OpenAI
    "gpt-4o": (2.50, 10.00),
    "gpt-4o-mini": (0.15, 0.60),
    "gpt-4-turbo": (10.00, 30.00),
    "o3": (10.00, 40.00),
    "o3-mini": (1.10, 4.40),
    # Google Gemini
    "gemini-2.5-pro": (1.25, 10.00),
    "gemini-2.5-flash": (0.15, 0.60),
    "gemini-2.5-flash-lite": (0.075, 0.30),
    "gemini-3-pro-preview": (1.25, 10.00),
    "gemini-3-flash-preview": (0.15, 0.60),
}

# Active pricing table — starts as defaults, can be updated
_active_pricing: dict[str, tuple[float, float]] = dict(DEFAULT_MODEL_PRICING)


def set_model_pricing_table(overrides: dict[str, tuple[float, float]]) -> None:
    """Override the pricing table with user-configured values."""
    _active_pricing.update(overrides)


def get_model_pricing_table() -> dict[str, tuple[float, float]]:
    """Return the current active pricing table."""
    return dict(_active_pricing)


def pricing_for_model(model: str) -> tuple[float, float]:
    """Look up (input_cost, output_cost) per million tokens."""
    if model in _active_pricing:
        return _active_pricing[model]
    for key, pricing in _active_pricing.items():
        if key.startswith(model) or model.startswith(key.rsplit("-", 1)[0]):
            return pricing
    logger.warning("Unknown model %r — defaulting to Haiku rates", model)
    return _active_pricing.get(
        "claude-haiku-4-5-20251001",
        DEFAULT_MODEL_PRICING["claude-haiku-4-5-20251001"],
    )


class BudgetExceeded(Exception):
    """Raised when a budget cap is hit."""


@dataclass
class BudgetTracker:
    """Tracks per-project spend in dollars."""

    per_project_cap: float = 10.00
    daily_cap: float = 50.00
    input_cost_per_million: float = 0.0
    output_cost_per_million: float = 0.0

    _project_spend: float = 0.0
    _project_tokens_in: int = 0
    _project_tokens_out: int = 0
    _daily_spend: float = field(default=0.0)
    _day_start: float = field(default_factory=time.monotonic)

    def set_model_pricing(self, model: str) -> None:
        inp, out = pricing_for_model(model)
        self.input_cost_per_million = inp
        self.output_cost_per_million = out

    def tokens_to_dollars(self, input_tokens: int, output_tokens: int) -> float:
        return (
            input_tokens * self.input_cost_per_million / 1_000_000
            + output_tokens * self.output_cost_per_million / 1_000_000
        )

    def _maybe_reset_day(self) -> None:
        if time.monotonic() - self._day_start >= 86400:
            self._daily_spend = 0.0
            self._day_start = time.monotonic()

    def start_project(self) -> None:
        """Reset per-project tracking for a new run."""
        self._project_spend = 0.0
        self._project_tokens_in = 0
        self._project_tokens_out = 0

    def record_tokens(self, input_tokens: int, output_tokens: int) -> bool:
        """Record token usage. Returns False if budget exceeded."""
        self._maybe_reset_day()
        cost = self.tokens_to_dollars(input_tokens, output_tokens)
        self._project_spend += cost
        self._project_tokens_in += input_tokens
        self._project_tokens_out += output_tokens
        self._daily_spend += cost

        if self._project_spend > self.per_project_cap:
            logger.warning(
                "Per-project budget exceeded: $%.4f > $%.2f",
                self._project_spend, self.per_project_cap,
            )
            return False
        if self._daily_spend > self.daily_cap:
            logger.warning(
                "Daily budget exceeded: $%.4f > $%.2f",
                self._daily_spend, self.daily_cap,
            )
            return False
        return True

    def is_exceeded(self) -> bool:
        """Check if budget is exceeded without recording."""
        return self._project_spend > self.per_project_cap

    @property
    def project_spend(self) -> float:
        return self._project_spend

    @property
    def project_tokens(self) -> tuple[int, int]:
        return self._project_tokens_in, self._project_tokens_out

    @property
    def budget_remaining(self) -> float:
        """Remaining budget for the current project."""
        return max(0.0, self.per_project_cap - self._project_spend)

    @property
    def spend_percentage(self) -> float:
        """Percentage of project budget spent (0.0 - 100.0+)."""
        if self.per_project_cap <= 0:
            return 100.0
        return (self._project_spend / self.per_project_cap) * 100.0

    @property
    def daily_spend(self) -> float:
        self._maybe_reset_day()
        return self._daily_spend


class PhaseBudget(BaseModel):
    """Budget tracking broken down by pipeline phase."""
    phase_spend: dict[str, float] = Field(
        default_factory=dict,
        description="Spend per phase: {'interview': 0.50, 'decompose': 1.20, ...}",
    )
    phase_caps: dict[str, float] = Field(
        default_factory=dict,
        description="Max spend per phase as fraction of total: {'shaping': 0.15}",
    )

    def record_spend(self, phase: str, amount: float) -> None:
        """Record spending for a specific phase."""
        self.phase_spend[phase] = self.phase_spend.get(phase, 0.0) + amount

    def check_phase_budget(self, phase: str, total_budget: float) -> bool:
        """Check if phase has budget remaining under its cap.

        Returns True if:
          - phase has no cap (uncapped phases always pass)
          - phase_spend[phase] < phase_caps[phase] * total_budget
        Returns False if cap exceeded.
        """
        if phase not in self.phase_caps:
            return True  # Uncapped phase
        cap_amount = self.phase_caps[phase] * total_budget
        spent = self.phase_spend.get(phase, 0.0)
        return spent < cap_amount

    def phase_summary(self) -> dict[str, dict[str, float]]:
        """Return {phase: {spent, cap_fraction, remaining_fraction}} for tracked phases."""
        result = {}
        all_phases = set(self.phase_spend.keys()) | set(self.phase_caps.keys())
        for phase in sorted(all_phases):
            spent = self.phase_spend.get(phase, 0.0)
            cap = self.phase_caps.get(phase)
            entry: dict[str, float] = {"spent": spent}
            if cap is not None:
                entry["cap_fraction"] = cap
            result[phase] = entry
        return result

    @classmethod
    def from_config(cls, shaping_budget_pct: float = 0.15) -> "PhaseBudget":
        """Create PhaseBudget with standard caps from config.

        Backward compatible: maps shaping_budget_pct to phase_caps["shaping"].
        """
        caps: dict[str, float] = {}
        if shaping_budget_pct > 0:
            caps["shape"] = shaping_budget_pct
        return cls(phase_caps=caps)
