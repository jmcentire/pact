"""Configuration — GlobalConfig + ProjectConfig.

GlobalConfig: defaults from config.yaml.
ProjectConfig: per-project from pact.yaml.
"""

from __future__ import annotations

import os
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

    # Per-million-token pricing: {"model_id": [input_cost, output_cost]}
    model_pricing: dict[str, list[float]] = field(default_factory=dict)

    # Integrations (all optional — empty string = disabled)
    slack_webhook: str = ""           # or CF_SLACK_WEBHOOK env var
    linear_api_key: str = ""          # or LINEAR_API_KEY env var
    linear_team_id: str = ""
    git_auto_commit: bool = False     # Auto-commit after each phase
    git_auto_branch: bool = False     # Branch per component

    # Bidirectional integration (read-side)
    slack_bot_token: str = ""         # or PACT_SLACK_BOT_TOKEN env var
    slack_channel: str = ""           # Channel ID for project threads
    poll_integrations: bool = False   # Poll integrations when daemon pauses
    poll_interval: int = 60           # Seconds between polls
    max_poll_attempts: int = 10       # Max polls before giving up
    context_max_chars: int = 4000     # Max external context in prompts

    # Shaping (Shape Up methodology — off by default)
    shaping: bool = False             # Master toggle for shaping phase
    shaping_depth: str = "standard"   # light | standard | thorough
    shaping_rigor: str = "moderate"   # relaxed | moderate | strict
    shaping_budget_pct: float = 0.15  # Max fraction of budget for shaping

    # Environment
    environment: dict = field(default_factory=dict)  # Raw YAML dict for EnvironmentSpec


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

    # Integrations (all optional — empty string = disabled)
    slack_webhook: str = ""
    linear_api_key: str = ""
    linear_team_id: str = ""
    git_auto_commit: bool | None = None
    git_auto_branch: bool | None = None

    # Bidirectional integration (read-side)
    slack_bot_token: str = ""
    slack_channel: str = ""
    poll_integrations: bool | None = None
    poll_interval: int | None = None
    max_poll_attempts: int | None = None
    context_max_chars: int | None = None

    # Shaping (Shape Up methodology)
    shaping: bool | None = None           # None = use global default
    shaping_depth: str | None = None      # light | standard | thorough
    shaping_rigor: str | None = None      # relaxed | moderate | strict
    shaping_budget_pct: float | None = None

    # Environment
    environment: dict | None = None


def load_global_config(config_path: str | Path | None = None) -> GlobalConfig:
    """Load global config from config.yaml."""
    if config_path is None:
        config_path = Path(__file__).parent.parent.parent / "config.yaml"

    config_path = Path(config_path)
    if not config_path.exists():
        return GlobalConfig()

    with open(config_path) as f:
        raw = yaml.safe_load(f) or {}

    config = GlobalConfig(
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
        model_pricing=raw.get("model_pricing", {}),
        slack_webhook=raw.get("slack_webhook", ""),
        linear_api_key=raw.get("linear_api_key", ""),
        linear_team_id=raw.get("linear_team_id", ""),
        git_auto_commit=raw.get("git_auto_commit", False),
        git_auto_branch=raw.get("git_auto_branch", False),
        slack_bot_token=raw.get("slack_bot_token", ""),
        slack_channel=raw.get("slack_channel", ""),
        poll_integrations=raw.get("poll_integrations", False),
        poll_interval=raw.get("poll_interval", 60),
        max_poll_attempts=raw.get("max_poll_attempts", 10),
        context_max_chars=raw.get("context_max_chars", 4000),
        shaping=raw.get("shaping", False),
        shaping_depth=raw.get("shaping_depth", "standard"),
        shaping_rigor=raw.get("shaping_rigor", "moderate"),
        shaping_budget_pct=raw.get("shaping_budget_pct", 0.15),
        environment=raw.get("environment", {}),
    )

    # Apply pricing overrides if configured
    if config.model_pricing:
        from pact.budget import set_model_pricing_table
        overrides = {
            model_id: (costs[0], costs[1])
            for model_id, costs in config.model_pricing.items()
            if isinstance(costs, list) and len(costs) == 2
        }
        if overrides:
            set_model_pricing_table(overrides)

    return config


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
        slack_webhook=raw.get("slack_webhook", ""),
        linear_api_key=raw.get("linear_api_key", ""),
        linear_team_id=raw.get("linear_team_id", ""),
        git_auto_commit=raw.get("git_auto_commit"),
        git_auto_branch=raw.get("git_auto_branch"),
        slack_bot_token=raw.get("slack_bot_token", ""),
        slack_channel=raw.get("slack_channel", ""),
        poll_integrations=raw.get("poll_integrations"),
        poll_interval=raw.get("poll_interval"),
        max_poll_attempts=raw.get("max_poll_attempts"),
        context_max_chars=raw.get("context_max_chars"),
        shaping=raw.get("shaping"),
        shaping_depth=raw.get("shaping_depth"),
        shaping_rigor=raw.get("shaping_rigor"),
        shaping_budget_pct=raw.get("shaping_budget_pct"),
        environment=raw.get("environment"),
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


@dataclass
class EnvironmentSpec:
    """Standardized execution environment for test harness and agents."""
    python_path: str = "python3"
    inherit_path: bool = True
    extra_path_dirs: list[str] = field(default_factory=list)
    required_tools: list[str] = field(default_factory=lambda: ["pytest"])
    env_vars: dict[str, str] = field(default_factory=dict)

    def build_env(self, pythonpath: str) -> dict[str, str]:
        """Build the subprocess environment dict.

        Returns a dict suitable for passing as env= to subprocess calls.
        """
        env: dict[str, str] = {}

        # PATH construction
        path_parts: list[str] = []
        if self.inherit_path:
            parent_path = os.environ.get("PATH", "")
            if parent_path:
                path_parts.append(parent_path)
        if self.extra_path_dirs:
            path_parts.extend(self.extra_path_dirs)
        if not path_parts:
            path_parts.append("/usr/bin:/usr/local/bin")
        env["PATH"] = ":".join(path_parts)

        # PYTHONPATH
        env["PYTHONPATH"] = pythonpath

        # Additional env vars
        env.update(self.env_vars)

        return env

    def validate_environment(self) -> list[str]:
        """Check that all required tools are available.

        Returns list of missing tools (empty = all present).
        """
        import shutil
        missing = []
        for tool in self.required_tools:
            if shutil.which(tool) is None:
                missing.append(tool)
        return missing


def resolve_environment(project: ProjectConfig, global_cfg: GlobalConfig) -> EnvironmentSpec:
    """Resolve environment spec from project or global config."""
    raw = project.environment if project.environment else global_cfg.environment
    if not raw:
        return EnvironmentSpec()
    return EnvironmentSpec(
        python_path=raw.get("python_path", "python3"),
        inherit_path=raw.get("inherit_path", True),
        extra_path_dirs=raw.get("extra_path_dirs", []),
        required_tools=raw.get("required_tools", ["pytest"]),
        env_vars=raw.get("env_vars", {}),
    )
