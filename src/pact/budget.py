"""Per-project + daily token/dollar tracking.

Reused from swarm with minimal adaptation. Dissipation boundary:
each project has a dollar cap. No retries on budget exceeded.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

MODEL_PRICING: dict[str, tuple[float, float]] = {
    "claude-haiku-4-5-20251001": (0.80, 4.00),
    "claude-sonnet-4-5-20250929": (3.00, 15.00),
    "claude-opus-4-6": (15.00, 75.00),
}


def pricing_for_model(model: str) -> tuple[float, float]:
    """Look up (input_cost, output_cost) per million tokens."""
    if model in MODEL_PRICING:
        return MODEL_PRICING[model]
    for key, pricing in MODEL_PRICING.items():
        if key.startswith(model) or model.startswith(key.rsplit("-", 1)[0]):
            return pricing
    logger.warning("Unknown model %r — defaulting to Haiku rates", model)
    return MODEL_PRICING["claude-haiku-4-5-20251001"]


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
    def daily_spend(self) -> float:
        self._maybe_reset_day()
        return self._daily_spend
