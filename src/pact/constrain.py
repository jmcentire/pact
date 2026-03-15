"""Constrain integration — load and validate Constrain output artifacts.

When --constrain-dir is provided, Pact uses Constrain artifacts to seed
decomposition: prompt.md augments task.md, constraints.yaml adds contract
constraints, component_map.yaml seeds the component list, and trust_policy.yaml
is passed through to access_graph.json.
"""

from __future__ import annotations

import logging
from pathlib import Path

import yaml

from pact.schemas import ConstrainContext

logger = logging.getLogger(__name__)


def load_constrain_artifacts(constrain_dir: str | Path) -> ConstrainContext:
    """Load all Constrain artifacts from a directory.

    Expected files (all optional):
    - prompt.md: replaces or augments task.md
    - constraints.yaml: additional contract constraints
    - component_map.yaml: seeds component list
    - trust_policy.yaml: passed through to access_graph.json
    """
    d = Path(constrain_dir).resolve()
    ctx = ConstrainContext()

    prompt_path = d / "prompt.md"
    if prompt_path.exists():
        ctx.prompt = prompt_path.read_text()
        logger.info("Loaded constrain prompt.md (%d chars)", len(ctx.prompt))

    for name, attr in [
        ("constraints.yaml", "constraints"),
        ("component_map.yaml", "component_map"),
        ("trust_policy.yaml", "trust_policy"),
    ]:
        path = d / name
        if path.exists():
            with open(path) as f:
                data = yaml.safe_load(f) or {}
            setattr(ctx, attr, data)
            logger.info("Loaded constrain %s", name)

    return ctx


def validate_constrain_artifacts(ctx: ConstrainContext) -> list[str]:
    """Validate loaded Constrain artifacts.

    Returns list of error messages (empty = valid).
    """
    errors: list[str] = []

    if ctx.component_map:
        components = ctx.component_map.get("components", [])
        if not isinstance(components, list):
            errors.append("component_map.yaml: 'components' must be a list")
        for i, comp in enumerate(components):
            if isinstance(comp, dict) and not comp.get("id"):
                errors.append(f"component_map.yaml: component[{i}] missing 'id'")

    if ctx.constraints:
        if not isinstance(ctx.constraints, dict):
            errors.append("constraints.yaml: must be a YAML mapping")

    return errors


def merge_constrain_into_task(task_text: str, ctx: ConstrainContext) -> str:
    """Merge Constrain prompt with existing task.md text.

    If constrain prompt exists, it replaces the task description but preserves
    any user-added context sections.
    """
    if not ctx.prompt:
        return task_text

    # Use constrain prompt as primary, append original task as context
    return f"""{ctx.prompt}

---
## Original Task Context

{task_text}
"""


def get_seeded_component_names(ctx: ConstrainContext) -> list[str]:
    """Extract component names from component_map.yaml.

    These names are fixed — the decomposition agent may add new components
    but must not rename or remove these.
    """
    if not ctx.component_map:
        return []
    components = ctx.component_map.get("components", [])
    return [c.get("id", "") for c in components if isinstance(c, dict) and c.get("id")]
