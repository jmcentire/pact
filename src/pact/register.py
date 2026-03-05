"""Register drift detection — runtime consistency checking.

Papers 35-39 established that processing register (cognitive mode) is the
representational hub that domain anchors to. When an agent drifts from its
established register mid-task, coordination failure follows — but register
drift is detectable before it surfaces as wrong output.

This module provides:
1. A lightweight LLM check that compares agent output against expected register
2. A sampling function that probabilistically checks recent artifacts
"""

from __future__ import annotations

import logging
import random
from pathlib import Path

from pydantic import BaseModel, Field

from pact.agents.base import AgentBase

logger = logging.getLogger(__name__)


# ── Drift Check ──────────────────────────────────────────────────────


DRIFT_CHECK_SYSTEM = """You are a processing-mode classifier. Given a code artifact
and an expected processing register (cognitive mode), determine whether the artifact
is consistent with that register.

Register meanings:
- rigorous-analytical: exhaustive error handling, defensive coding, formal validation, thorough edge cases
- exploratory-generative: creative approaches, rapid prototyping, experimental patterns, novel solutions
- systematic-verification: methodical coverage, compliance checks, structured validation, checklist-driven
- pragmatic-implementation: practical trade-offs, minimal viable approach, ship-focused, direct solutions

Assess style and approach, not correctness. A rigorous-analytical artifact has
dense validation and edge-case handling. A pragmatic-implementation artifact has
minimal-but-working code. Drift means the artifact's character doesn't match
the expected mode."""


class _DriftCheckResponse(BaseModel):
    """LLM output for register drift assessment."""
    consistent: bool = Field(description="True if artifact matches expected register")
    confidence: float = Field(
        ge=0.0, le=1.0,
        description="Confidence in assessment (0.0-1.0)",
    )


async def assess_register_consistency(
    agent: AgentBase,
    artifact_text: str,
    expected_register: str,
    max_sample_chars: int = 2000,
) -> tuple[bool, float]:
    """Lightweight check: does artifact match expected processing register?

    Uses a fast-tier model call with truncated input to minimize cost.
    Typically ~500 input tokens + ~20 output tokens.

    Args:
        agent: LLM agent (should use fast-tier model).
        artifact_text: Code or contract text to assess.
        expected_register: Expected register descriptor.
        max_sample_chars: Max chars to send (truncates for cost control).

    Returns:
        (consistent, confidence) — True if no drift detected.
    """
    # Truncate to control cost — register is about style, not exhaustive review
    sample = artifact_text[:max_sample_chars]
    if len(artifact_text) > max_sample_chars:
        sample += "\n... (truncated)"

    prompt = f"""Expected processing register: {expected_register}

Artifact sample:
```
{sample}
```

Is this artifact consistent with the expected processing register?"""

    try:
        result, _, _ = await agent.assess(
            _DriftCheckResponse, prompt, DRIFT_CHECK_SYSTEM,
        )
        return result.consistent, result.confidence
    except Exception as e:
        # Drift checking must never block the pipeline
        logger.debug("Register drift check failed (non-fatal): %s", e)
        return True, 0.0  # Assume consistent on failure


async def check_artifacts_for_drift(
    agent: AgentBase,
    project_dir: Path,
    expected_register: str,
    component_ids: list[str],
    check_rate: float = 0.1,
) -> list[tuple[str, bool, float]]:
    """Sample and check recent implementation artifacts for register drift.

    Probabilistically selects components to check based on check_rate.
    For each selected component, reads the implementation source and
    runs a lightweight register consistency check.

    Args:
        agent: LLM agent for drift assessment.
        project_dir: Project directory path.
        expected_register: The established processing register.
        component_ids: Components with implementations to check.
        check_rate: Probability of checking each component (0.0-1.0).

    Returns:
        List of (component_id, consistent, confidence) for checked components.
    """
    if not expected_register or not component_ids:
        return []

    results: list[tuple[str, bool, float]] = []
    impl_dir = project_dir / ".pact" / "implementations"

    for cid in component_ids:
        # Probabilistic sampling
        if random.random() >= check_rate:
            continue

        # Read implementation source
        src_dir = impl_dir / cid / "src"
        if not src_dir.exists():
            continue

        # Concatenate source files (typically 1-2 files per component)
        source_parts = []
        for src_file in sorted(src_dir.iterdir()):
            if src_file.is_file() and src_file.suffix in (".py", ".ts", ".js"):
                try:
                    source_parts.append(src_file.read_text())
                except Exception:
                    continue

        if not source_parts:
            continue

        artifact_text = "\n\n".join(source_parts)
        consistent, confidence = await assess_register_consistency(
            agent, artifact_text, expected_register,
        )

        results.append((cid, consistent, confidence))
        logger.info(
            "Register drift check: %s — %s (confidence=%.2f)",
            cid,
            "consistent" if consistent else "DRIFT DETECTED",
            confidence,
        )

    return results
