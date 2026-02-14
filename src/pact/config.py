"""Configuration — GlobalConfig + ProjectConfig.

GlobalConfig: defaults from config.yaml.
ProjectConfig: per-project from pact.yaml.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import yaml


@dataclass
class GlobalConfig:
    """Global pact configuration."""
    model: str = "claude-opus-4-6"
    default_budget: float = 10.00
    check_interval: int = 300

    role_models: dict[str, str] = field(default_factory=lambda: {
        "decomposer": "claude-opus-4-6",
        "contract_author": "claude-opus-4-6",
        "test_author": "claude-sonnet-4-5-20250929",
        "code_author": "claude-opus-4-6",
        "trace_analyst": "claude-opus-4-6",
    })
    role_backends: dict[str, str] = field(default_factory=lambda: {
        "decomposer": "anthropic",
        "contract_author": "anthropic",
        "test_author": "claude_code",
        "code_author": "claude_code",
        "trace_analyst": "claude_code",
    })

    max_implementation_attempts: int = 3
    max_plan_revisions: int = 2
    autonomous_timeout: int = 600

    parallel_components: bool = False
    competitive_implementations: bool = False
    competitive_agents: int = 2
    max_concurrent_agents: int = 4
    plan_only: bool = False


@dataclass
class ProjectConfig:
    """Per-project configuration from pact.yaml."""
    budget: float = 10.00
    model: str = ""
    backend: str = "anthropic"
    check_interval: int = 0  # 0 = use global default
    max_implementation_attempts: int = 0  # 0 = use global default
    role_models: dict[str, str] = field(default_factory=dict)
    role_backends: dict[str, str] = field(default_factory=dict)

    parallel_components: bool | None = None  # None = use global default
    competitive_implementations: bool | None = None
    competitive_agents: int | None = None
    max_concurrent_agents: int | None = None
    plan_only: bool | None = None


def load_global_config(config_path: str | Path | None = None) -> GlobalConfig:
    """Load global config from config.yaml."""
    if config_path is None:
        config_path = Path(__file__).parent.parent.parent / "config.yaml"

    config_path = Path(config_path)
    if not config_path.exists():
        return GlobalConfig()

    with open(config_path) as f:
        raw = yaml.safe_load(f) or {}

    return GlobalConfig(
        model=raw.get("model", GlobalConfig.model),
        default_budget=raw.get("default_budget", GlobalConfig.default_budget),
        check_interval=raw.get("check_interval", GlobalConfig.check_interval),
        role_models=raw.get("role_models", GlobalConfig().role_models),
        role_backends=raw.get("role_backends", GlobalConfig().role_backends),
        max_implementation_attempts=raw.get(
            "max_implementation_attempts", GlobalConfig.max_implementation_attempts
        ),
        max_plan_revisions=raw.get("max_plan_revisions", GlobalConfig.max_plan_revisions),
        autonomous_timeout=raw.get("autonomous_timeout", GlobalConfig.autonomous_timeout),
        parallel_components=raw.get("parallel_components", False),
        competitive_implementations=raw.get("competitive_implementations", False),
        competitive_agents=raw.get("competitive_agents", 2),
        max_concurrent_agents=raw.get("max_concurrent_agents", 4),
        plan_only=raw.get("plan_only", False),
    )


def load_project_config(project_dir: str | Path) -> ProjectConfig:
    """Load per-project config from pact.yaml."""
    project_dir = Path(project_dir)
    config_path = project_dir / "pact.yaml"

    if not config_path.exists():
        return ProjectConfig()

    with open(config_path) as f:
        raw = yaml.safe_load(f) or {}

    return ProjectConfig(
        budget=raw.get("budget", 10.00),
        model=raw.get("model", ""),
        backend=raw.get("backend", "anthropic"),
        check_interval=raw.get("check_interval", 0),
        max_implementation_attempts=raw.get("max_implementation_attempts", 0),
        role_models=raw.get("role_models", {}),
        role_backends=raw.get("role_backends", {}),
        parallel_components=raw.get("parallel_components"),
        competitive_implementations=raw.get("competitive_implementations"),
        competitive_agents=raw.get("competitive_agents"),
        max_concurrent_agents=raw.get("max_concurrent_agents"),
        plan_only=raw.get("plan_only"),
    )


def resolve_model(role: str, project: ProjectConfig, global_cfg: GlobalConfig) -> str:
    """Resolve the model for a role: project override > global role > global default."""
    if role in project.role_models and project.role_models[role]:
        return project.role_models[role]
    if role in global_cfg.role_models:
        return global_cfg.role_models[role]
    return project.model or global_cfg.model


def resolve_backend(role: str, project: ProjectConfig, global_cfg: GlobalConfig) -> str:
    """Resolve the backend for a role: project override > global role > global default."""
    if role in project.role_backends and project.role_backends[role]:
        return project.role_backends[role]
    if role in global_cfg.role_backends:
        return global_cfg.role_backends[role]
    return project.backend or "anthropic"


@dataclass
class ParallelConfig:
    """Resolved parallel execution configuration."""
    parallel: bool = False
    competitive: bool = False
    agent_count: int = 2
    max_concurrent: int = 4
    plan_only: bool = False


def resolve_parallel_config(
    project: ProjectConfig, global_cfg: GlobalConfig,
) -> ParallelConfig:
    """Resolve parallel/competitive config: project override > global default."""
    return ParallelConfig(
        parallel=project.parallel_components if project.parallel_components is not None
            else global_cfg.parallel_components,
        competitive=project.competitive_implementations if project.competitive_implementations is not None
            else global_cfg.competitive_implementations,
        agent_count=project.competitive_agents if project.competitive_agents is not None
            else global_cfg.competitive_agents,
        max_concurrent=project.max_concurrent_agents if project.max_concurrent_agents is not None
            else global_cfg.max_concurrent_agents,
        plan_only=project.plan_only if project.plan_only is not None
            else global_cfg.plan_only,
    )
