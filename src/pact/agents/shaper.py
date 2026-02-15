"""Shaper Agent — produces a ShapingPitch from task, SOPs, interview, and budget.

Supports three depth levels:
  - light: 1 LLM call, core fields only (problem, appetite, no-gos)
  - standard: 1 LLM call, adds breadboards and rabbit holes
  - thorough: 2 LLM calls, adds region maps and fit checks

Rigor levels control failure behavior:
  - relaxed: return minimal stub on failure
  - moderate: return partial result on failure
  - strict: raise ShapingLLMError on failure
"""

from __future__ import annotations

import logging

from pact.agents.base import AgentBase
from pact.schemas_shaping import (
    Appetite,
    Breadboard,
    FitCheck,
    RabbitHole,
    RegionMap,
    ShapingPitch,
    ShapingStatus,
)

logger = logging.getLogger(__name__)


class BudgetExceededError(Exception):
    """Raised when the shaping budget cap is exceeded."""

    def __init__(self, used: float, total: float, cap_pct: float):
        self.used = used
        self.total = total
        self.cap_pct = cap_pct
        super().__init__(
            f"Shaping budget exceeded: used={used:.2f}, total={total:.2f}, "
            f"ratio={used/total if total > 0 else 'N/A':.2%}, cap={cap_pct:.0%}"
        )


class ShapingLLMError(Exception):
    """Raised when an LLM call fails under strict rigor."""

    def __init__(self, depth: str, call_number: int, original_error: str):
        self.depth = depth
        self.call_number = call_number
        self.original_error = original_error
        super().__init__(
            f"LLM call {call_number} failed at depth '{depth}': {original_error}"
        )


class Shaper:
    """Shaper agent that produces a ShapingPitch.

    Not a direct subclass of AgentBase — wraps one for LLM calls while
    adding shaping-specific logic (depth, rigor, budget cap).
    """

    def __init__(
        self,
        agent: AgentBase,
        shaping_depth: str = "standard",
        shaping_rigor: str = "moderate",
        shaping_budget_pct: float = 0.15,
        appetite_threshold: float = 0.5,
    ) -> None:
        self._agent = agent
        self.shaping_depth = shaping_depth
        self.shaping_rigor = shaping_rigor
        self.shaping_budget_pct = shaping_budget_pct
        self.appetite_threshold = appetite_threshold

    async def shape(
        self,
        task: str,
        sops: str,
        interview_context: str = "",
        budget_used: float = 0.0,
        budget_total: float = 100.0,
    ) -> ShapingPitch:
        """Produce a ShapingPitch from task description and context.

        Args:
            task: Task description (from task.md).
            sops: Standard operating procedures.
            interview_context: Formatted interview results.
            budget_used: Current project spend in USD.
            budget_total: Total project budget in USD.

        Returns:
            ShapingPitch with depth-appropriate fields populated.

        Raises:
            ValueError: Empty task or invalid budget.
            BudgetExceededError: Shaping budget cap exceeded.
            ShapingLLMError: LLM failure under strict rigor.
        """
        if not task.strip():
            raise ValueError("task must be a non-empty string")
        if budget_total <= 0:
            raise ValueError("budget_total must be > 0")

        appetite = self._map_appetite(budget_used, budget_total)
        self._check_budget(budget_used, budget_total)

        depth = self.shaping_depth
        logger.info("Shaping at depth='%s' with appetite='%s'", depth, appetite)

        if depth == "light":
            return await self._shape_light(task, sops, interview_context, appetite)
        elif depth == "thorough":
            return await self._shape_thorough(
                task, sops, interview_context, appetite,
                budget_used, budget_total,
            )
        else:
            return await self._shape_standard(task, sops, interview_context, appetite)

    async def _shape_light(
        self, task: str, sops: str, interview: str, appetite: Appetite,
    ) -> ShapingPitch:
        """1 LLM call: problem, appetite, no-gos only."""
        prompt = self._build_prompt(task, sops, interview, appetite, "light")
        try:
            result, _, _ = await self._agent.assess(ShapingPitch, prompt, SYSTEM_PROMPT)
        except Exception as e:
            return self._handle_failure(e, task, appetite, "light", 1)

        return ShapingPitch(
            problem=result.problem,
            appetite=appetite,
            no_gos=result.no_gos,
            status=ShapingStatus.shaped,
        )

    async def _shape_standard(
        self, task: str, sops: str, interview: str, appetite: Appetite,
    ) -> ShapingPitch:
        """1 LLM call: problem, appetite, no-gos, breadboard, rabbit holes."""
        prompt = self._build_prompt(task, sops, interview, appetite, "standard")
        try:
            result, _, _ = await self._agent.assess(ShapingPitch, prompt, SYSTEM_PROMPT)
        except Exception as e:
            return self._handle_failure(e, task, appetite, "standard", 1)

        return ShapingPitch(
            problem=result.problem,
            appetite=appetite,
            no_gos=result.no_gos,
            solution_breadboard=result.solution_breadboard,
            rabbit_holes=result.rabbit_holes,
            status=ShapingStatus.shaped,
        )

    async def _shape_thorough(
        self, task: str, sops: str, interview: str, appetite: Appetite,
        budget_used: float, budget_total: float,
    ) -> ShapingPitch:
        """2 LLM calls: full pitch with region maps and fit checks."""
        prompt1 = self._build_prompt(task, sops, interview, appetite, "thorough")
        try:
            call1, _, _ = await self._agent.assess(ShapingPitch, prompt1, SYSTEM_PROMPT)
        except Exception as e:
            return self._handle_failure(e, task, appetite, "thorough", 1)

        # Check budget before second call
        if not self._budget_ok(budget_used, budget_total):
            logger.warning("Budget exhausted after call 1; returning partial.")
            return ShapingPitch(
                problem=call1.problem,
                appetite=appetite,
                no_gos=call1.no_gos,
                solution_breadboard=call1.solution_breadboard,
                rabbit_holes=call1.rabbit_holes,
                status=ShapingStatus.raw,
            )

        prompt2 = self._build_enrichment_prompt(task, call1)
        try:
            call2, _, _ = await self._agent.assess(
                ShapingPitch, prompt2, SYSTEM_PROMPT_ENRICHMENT,
            )
        except Exception as e:
            return self._handle_failure(e, task, appetite, "thorough", 2, partial=call1)

        return ShapingPitch(
            problem=call2.problem or call1.problem,
            appetite=appetite,
            no_gos=call2.no_gos or call1.no_gos,
            solution_breadboard=call2.solution_breadboard or call1.solution_breadboard,
            rabbit_holes=call2.rabbit_holes or call1.rabbit_holes,
            solution_region_map=call2.solution_region_map,
            fit_check=call2.fit_check,
            status=ShapingStatus.shaped,
        )

    def _handle_failure(
        self,
        error: Exception,
        task: str,
        appetite: Appetite,
        depth: str,
        call_number: int,
        partial: ShapingPitch | None = None,
    ) -> ShapingPitch:
        """Handle LLM failure according to rigor level."""
        if self.shaping_rigor == "strict":
            raise ShapingLLMError(depth, call_number, str(error)) from error

        logger.warning(
            "LLM call %d failed at depth '%s' (%s rigor): %s",
            call_number, depth, self.shaping_rigor, error,
        )

        if partial is not None:
            return ShapingPitch(
                problem=partial.problem,
                appetite=appetite,
                no_gos=partial.no_gos,
                solution_breadboard=partial.solution_breadboard,
                rabbit_holes=partial.rabbit_holes,
                status=ShapingStatus.raw,
            )

        return ShapingPitch(
            problem=task[:500],
            appetite=appetite,
            status=ShapingStatus.raw,
        )

    def _map_appetite(self, used: float, total: float) -> Appetite:
        """Map budget state to appetite level."""
        remaining = (total - used) / total if total > 0 else 0.0
        return Appetite.big if remaining > self.appetite_threshold else Appetite.small

    def _check_budget(self, used: float, total: float) -> None:
        """Raise BudgetExceededError if shaping cap exceeded."""
        if not self._budget_ok(used, total):
            raise BudgetExceededError(used, total, self.shaping_budget_pct)

    def _budget_ok(self, used: float, total: float) -> bool:
        """Check if budget allows another shaping call."""
        if total <= 0:
            return False
        return (used / total) < self.shaping_budget_pct

    def _build_prompt(
        self, task: str, sops: str, interview: str, appetite: Appetite, depth: str,
    ) -> str:
        """Construct the shaping prompt."""
        sections = [
            f"## Task Description\n{task}",
            f"## Standard Operating Procedures\n{sops}",
        ]
        if interview:
            sections.append(f"## Interview Results\n{interview}")
        sections.append(f"## Appetite\n{appetite}")

        if depth == "light":
            sections.append(
                "\n## Instructions\n"
                "Produce a shaping pitch with ONLY:\n"
                "- problem: clear problem statement\n"
                "- appetite: small or big (use the value above)\n"
                "- no_gos: explicit exclusions from interview context"
            )
        else:
            sections.append(
                "\n## Instructions\n"
                "Produce a shaping pitch with:\n"
                "- problem: clear problem statement\n"
                "- appetite: small or big\n"
                "- no_gos: explicit exclusions\n"
                "- solution_breadboard: places, affordances, connections\n"
                "- rabbit_holes: identified risks with status and mitigation"
            )
        return "\n".join(sections)

    def _build_enrichment_prompt(self, task: str, prior: ShapingPitch) -> str:
        """Construct the thorough-depth enrichment prompt (call 2)."""
        data = prior.model_dump(mode="json")
        return (
            f"## Prior Shaping Result\n{data}\n\n"
            f"## Task\n{task}\n\n"
            "## Instructions\n"
            "Using the prior shaping result, enrich it with:\n"
            "- solution_region_map: architectural regions with responsibilities\n"
            "- fit_check: whether the solution fits the appetite\n"
            "Include all prior fields."
        )

    async def close(self) -> None:
        await self._agent.close()


SYSTEM_PROMPT = "You are a product shaper following Shape Up methodology."

SYSTEM_PROMPT_ENRICHMENT = (
    "You are a product shaper performing deep analysis. "
    "Enrich the prior shaping result with region maps and fit checks."
)
