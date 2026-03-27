"""Project directory lifecycle — init, load, save, resume.

The project directory is the unit of work. All project knowledge is visible
in the project tree. Only ephemeral per-run state lives in .pact/:

  proj/
  ├── task.md
  ├── sops.md
  ├── pact.yaml
  ├── design.md
  ├── design.json                        # Structured design document
  ├── standards.json                     # Global standards
  ├── tasks.json                         # Task list
  ├── analysis.json                      # Cross-artifact analysis
  ├── checklist.json                     # Requirements checklist
  ├── TASKS.md                           # Rendered task list
  ├── decomposition/                     # Decomposition artifacts
  │   ├── tree.json
  │   ├── decisions.json
  │   ├── interview.json
  │   └── pitch.json
  ├── contracts/<component_id>/          # Interface specs + history
  │   ├── interface.json
  │   ├── interface.py (or .ts)
  │   └── history/<timestamp>.json
  ├── src/<component_id>/                # Implementations + glue
  │   └── <component_id>.py (or .ts)
  ├── tests/<component_id>/              # Contract tests + Goodhart tests
  │   ├── contract_test.py (or .test.ts)
  │   ├── contract_test_suite.json
  │   └── goodhart/
  │       ├── goodhart_test_suite.json
  │       └── goodhart_test.py (or .test.ts)
  ├── learnings/                         # Accumulated learnings
  │   └── learnings.jsonl
  └── .pact/                             # Ephemeral run state only
      ├── state.json
      ├── audit.jsonl
      ├── budget.json
      ├── contracts/<component_id>/
      │   └── research.json
      ├── implementations/<component_id>/
      │   ├── research.json
      │   ├── plan.json
      │   ├── metadata.json
      │   ├── test_results.json
      │   └── attempts/
      └── compositions/<parent_id>/
          └── test_results.json
"""

from __future__ import annotations

import json
import logging
import shutil
from datetime import datetime
from pathlib import Path
from uuid import uuid4

import yaml

from pact.config import ProjectConfig, load_project_config
from pact.schemas import (
    CertificationArtifact,
    ComponentContract,
    ContractTestSuite,
    DecompositionTree,
    DesignDocument,
    InterviewResult,
    RunState,
)

logger = logging.getLogger(__name__)

PACT_DIR = ".pact"
STATE_FILE = "state.json"
AUDIT_FILE = "audit.jsonl"

_GITATTRIBUTES_CONTENT = """\
# Pact-generated artifacts — collapsed in GitHub PRs
# Human inputs: task.md, sops.md, pact.yaml, design.md
# Human deliverables: src/**

# Decomposition artifacts
decomposition/*.json linguist-generated=true

# Contracts and interface stubs
contracts/**/interface.json linguist-generated=true
contracts/**/interface.py linguist-generated=true
contracts/**/interface.ts linguist-generated=true
contracts/**/history/*.json linguist-generated=true

# Test suites (generated from contracts)
tests/**/contract_test_suite.json linguist-generated=true
tests/**/contract_test.py linguist-generated=true
tests/**/contract_test.test.ts linguist-generated=true
tests/**/goodhart/goodhart_test_suite.json linguist-generated=true
tests/**/goodhart/goodhart_test.py linguist-generated=true
tests/**/goodhart/goodhart_test.test.ts linguist-generated=true
tests/smoke/test_*.py linguist-generated=true

# Project metadata (auto-generated after decomposition)
standards.json linguist-generated=true
tasks.json linguist-generated=true
TASKS.md linguist-generated=true
design.json linguist-generated=true
analysis.json linguist-generated=true
checklist.json linguist-generated=true
"""


class ProjectManager:
    """Manages project directory lifecycle."""

    def __init__(self, project_dir: str | Path, audit_dir: str | Path | None = None) -> None:
        self.project_dir = Path(project_dir).resolve()
        self._audit_dir = Path(audit_dir).resolve() if audit_dir else None

        # Audit-owned artifacts redirect to audit_dir when set
        audit_root = self._audit_dir or self.project_dir
        self._visible_contracts_dir = audit_root / "contracts"
        self._visible_tests_dir = audit_root / "tests"
        self._decomp_dir = audit_root / "decomposition"

        # Code-owned artifacts — always in project_dir
        self._visible_src_dir = self.project_dir / "src"
        self._learnings_dir = self.project_dir / "learnings"

        # Synced tests: read-only copy of visible tests in code repo
        self._synced_tests_dir = self.project_dir / "tests" if self._audit_dir else None

        # Ephemeral run state — always in project_dir
        self._pact_dir = self.project_dir / PACT_DIR
        self._contracts_dir = self._pact_dir / "contracts"
        self._impl_dir = self._pact_dir / "implementations"
        self._comp_dir = self._pact_dir / "compositions"

    # ── Language ───────────────────────────────────────────────────

    @property
    def language(self) -> str:
        """Project language from pact.yaml config. Defaults to 'python'."""
        cfg = self.load_config()
        return cfg.language

    # ── Audit Separation ──────────────────────────────────────────

    @property
    def audit_root(self) -> Path:
        """Root for audit-owned artifacts. Falls back to project_dir."""
        return self._audit_dir or self.project_dir

    @property
    def has_audit_repo(self) -> bool:
        """Whether this project uses a separate audit repo."""
        return self._audit_dir is not None

    @property
    def synced_tests_dir(self) -> Path | None:
        """Read-only test copy in code repo. None if no audit separation."""
        return self._synced_tests_dir

    def dev_test_code_path(self, component_id: str) -> Path:
        """Test path for development use by coding agent.

        In audit-separated mode: returns synced copy in code repo.
        In single-repo mode: returns the canonical test path.
        """
        if self._synced_tests_dir:
            ext = ".test.ts" if self.language in ("typescript", "javascript") else ".py"
            return self._synced_tests_dir / component_id / f"contract_test{ext}"
        return self.test_code_path(component_id)

    @property
    def certification_dir(self) -> Path:
        d = self.audit_root / "certification"
        d.mkdir(parents=True, exist_ok=True)
        return d

    def save_certification(self, cert: CertificationArtifact) -> Path:
        path = self.certification_dir / "certification.json"
        path.write_text(cert.model_dump_json(indent=2))
        return path

    def load_certification(self) -> CertificationArtifact | None:
        path = self.certification_dir / "certification.json"
        if not path.exists():
            return None
        return CertificationArtifact.model_validate_json(path.read_text())

    # ── Paths ──────────────────────────────────────────────────────

    @property
    def task_path(self) -> Path:
        return self.project_dir / "task.md"

    @property
    def sops_path(self) -> Path:
        return self.project_dir / "sops.md"

    @property
    def config_path(self) -> Path:
        return self.project_dir / "pact.yaml"

    @property
    def design_path(self) -> Path:
        return self.project_dir / "design.md"

    @property
    def state_path(self) -> Path:
        return self._pact_dir / STATE_FILE

    @property
    def audit_path(self) -> Path:
        return self._pact_dir / AUDIT_FILE

    @property
    def tree_path(self) -> Path:
        return self._decomp_dir / "tree.json"

    @property
    def interview_path(self) -> Path:
        return self._decomp_dir / "interview.json"

    @property
    def tasks_json_path(self) -> Path:
        return self.project_dir / "tasks.json"

    @property
    def tasks_md_path(self) -> Path:
        return self.project_dir / "TASKS.md"

    @property
    def analysis_path(self) -> Path:
        return self.audit_root / "analysis.json"

    @property
    def checklist_path(self) -> Path:
        return self.audit_root / "checklist.json"

    @property
    def standards_path(self) -> Path:
        return self.audit_root / "standards.json"

    # ── Archive ────────────────────────────────────────────────────

    # Files that pact writes during init (human inputs + generated metadata).
    _ARCHIVABLE_FILES = [
        "task.md", "sops.md", "pact.yaml", "design.md",
        "design.json", "tasks.json", "TASKS.md",
        "analysis.json", "checklist.json", "standards.json",
    ]

    @property
    def archive_dir(self) -> Path:
        """Archive directory: .pact/archive/ under the project directory."""
        return self._pact_dir / "archive"

    def archive_existing(self) -> list[tuple[Path, Path]]:
        """Archive existing artifacts into .pact/archive/<slug>/.

        Returns list of (original, archived) path pairs.
        """
        from pact.archive import archive_artifacts

        subdir, archived = archive_artifacts(
            self.project_dir,
            self._ARCHIVABLE_FILES,
            archive_base=self.archive_dir,
            slug_source_priority=["task.md", "pact.yaml"],
        )
        if archived:
            logger.info(
                "Archived %d artifact(s) to %s/",
                len(archived), subdir.name if subdir else "?",
            )
            for orig, dest in archived:
                logger.info("  %s", orig.name)
        return archived

    def load_previous_context(self) -> dict[str, str]:
        """Load artifact contents from the most recent archived session.

        Returns dict mapping filename to content, or empty dict if none.
        """
        from pact.archive import load_archived_artifacts

        return load_archived_artifacts(self.archive_dir)

    # ── Init ───────────────────────────────────────────────────────

    def init(self, budget: float = 10.00) -> None:
        """Scaffold a new project directory.

        If artifacts from a previous session exist, they are archived
        into ``.pact/archive/<slug>/`` before fresh templates are written.
        """
        self.project_dir.mkdir(parents=True, exist_ok=True)

        # Archive existing artifacts before scaffolding fresh templates
        self.archive_existing()

        # Audit-owned directories (in audit_dir when separated, else project_dir)
        self._visible_contracts_dir.mkdir(parents=True, exist_ok=True)
        self._visible_tests_dir.mkdir(parents=True, exist_ok=True)
        self._decomp_dir.mkdir(parents=True, exist_ok=True)

        # Code-owned directories — always in project_dir
        self._visible_src_dir.mkdir(exist_ok=True)
        self._learnings_dir.mkdir(exist_ok=True)

        # Synced tests directory in code repo (when audit-separated)
        if self._synced_tests_dir:
            self._synced_tests_dir.mkdir(exist_ok=True)

        # Certification directory (in audit root)
        if self._audit_dir:
            (self._audit_dir / "certification").mkdir(parents=True, exist_ok=True)

        # Ephemeral run state directories
        self._pact_dir.mkdir(exist_ok=True)
        self._contracts_dir.mkdir(exist_ok=True)
        self._impl_dir.mkdir(exist_ok=True)
        self._comp_dir.mkdir(exist_ok=True)

        # Write fresh templates (files were archived above if they existed)
        self.task_path.write_text(
            "# Task\n\n"
            "Describe your task here.\n\n"
            "## Context\n\n"
            "Any relevant context, constraints, or requirements.\n"
        )

        self.sops_path.write_text(
            "# Operating Procedures\n\n"
            "## Tech Stack\n"
            "- Language: Python 3.12+\n"
            "- Testing: pytest\n\n"
            "## Standards\n"
            "- Type annotations on all public functions\n"
            "- Prefer composition over inheritance\n\n"
            "## Verification\n"
            "- All functions must have at least one test\n"
            "- Tests must be runnable without external services\n"
            "- No task is done until its contract tests pass\n\n"
            "## Preferences\n"
            "- Prefer stdlib over third-party libraries\n"
            "- Keep files under 300 lines\n"
        )

        config = {
            "budget": budget,
        }
        with open(self.config_path, "w") as f:
            yaml.dump(config, f, default_flow_style=False, sort_keys=False)

        self.design_path.write_text(
            "# Design Document\n\n"
            "*Auto-maintained by pact. Do not edit manually.*\n\n"
            "## Status: Not started\n"
        )

        gitattributes = self.project_dir / ".gitattributes"
        if not gitattributes.exists():
            gitattributes.write_text(_GITATTRIBUTES_CONTENT)

        logger.info("Initialized project: %s", self.project_dir)

    # ── Task & Config ──────────────────────────────────────────────

    def load_task(self) -> str:
        if not self.task_path.exists():
            raise FileNotFoundError(f"No task.md found in {self.project_dir}")
        return self.task_path.read_text()

    def load_sops(self) -> str:
        if not self.sops_path.exists():
            return ""
        return self.sops_path.read_text()

    def load_config(self) -> ProjectConfig:
        return load_project_config(self.project_dir)

    # ── Run State ──────────────────────────────────────────────────

    def has_state(self) -> bool:
        return self.state_path.exists()

    def load_state(self) -> RunState:
        if not self.state_path.exists():
            raise FileNotFoundError(f"No state file: {self.state_path}")
        return RunState.model_validate_json(self.state_path.read_text())

    def save_state(self, state: RunState) -> None:
        self._pact_dir.mkdir(parents=True, exist_ok=True)
        self.state_path.write_text(state.model_dump_json(indent=2))

    def create_run(self) -> RunState:
        return RunState(
            id=uuid4().hex[:12],
            project_dir=str(self.project_dir),
            status="active",
            phase="interview",
            created_at=datetime.now().isoformat(),
        )

    def clear_state(self, include_deliverables: bool = False) -> None:
        """Remove all run state. Preserves task.md, sops.md, config.

        Args:
            include_deliverables: If True, also remove all project knowledge
                (contracts/, src/, tests/, decomposition/, learnings/,
                standards.json, tasks.json, analysis.json, checklist.json,
                design.json). Default False.
        """
        if self._pact_dir.exists():
            shutil.rmtree(self._pact_dir)
        self._pact_dir.mkdir(exist_ok=True)
        self._contracts_dir.mkdir(exist_ok=True)
        self._impl_dir.mkdir(exist_ok=True)
        self._comp_dir.mkdir(exist_ok=True)

        if include_deliverables:
            for d in (
                self._visible_contracts_dir, self._visible_src_dir,
                self._visible_tests_dir, self._decomp_dir, self._learnings_dir,
            ):
                if d.exists():
                    shutil.rmtree(d)
                d.mkdir(exist_ok=True)
            # Remove visible JSON files
            for f in (
                self.tasks_json_path, self.analysis_path, self.checklist_path,
                self.standards_path, self.project_dir / "design.json",
            ):
                if f.exists():
                    f.unlink()

    # ── Audit ──────────────────────────────────────────────────────

    def append_audit(self, action: str, detail: str = "", **kwargs: str) -> None:
        self._pact_dir.mkdir(parents=True, exist_ok=True)
        entry = {
            "timestamp": datetime.now().isoformat(),
            "action": action,
            "detail": detail,
            **kwargs,
        }
        with open(self.audit_path, "a") as f:
            f.write(json.dumps(entry) + "\n")

    def load_audit(self) -> list[dict]:
        if not self.audit_path.exists():
            return []
        entries = []
        with open(self.audit_path) as f:
            for line in f:
                line = line.strip()
                if line:
                    entries.append(json.loads(line))
        return entries

    # ── Decomposition ──────────────────────────────────────────────

    def save_tree(self, tree: DecompositionTree) -> None:
        self._decomp_dir.mkdir(parents=True, exist_ok=True)
        self.tree_path.write_text(tree.model_dump_json(indent=2))

    def load_tree(self) -> DecompositionTree | None:
        if not self.tree_path.exists():
            return None
        return DecompositionTree.model_validate_json(self.tree_path.read_text())

    def save_interview(self, result: InterviewResult) -> None:
        self._decomp_dir.mkdir(parents=True, exist_ok=True)
        self.interview_path.write_text(result.model_dump_json(indent=2))

    def load_interview(self) -> InterviewResult | None:
        if not self.interview_path.exists():
            return None
        return InterviewResult.model_validate_json(self.interview_path.read_text())

    def save_decisions(self, decisions: list[dict]) -> None:
        path = self._decomp_dir / "decisions.json"
        path.write_text(json.dumps(decisions, indent=2))

    def save_type_registry(self, registry) -> None:
        path = self._decomp_dir / "type_registry.json"
        path.write_text(registry.model_dump_json(indent=2))

    def load_type_registry(self):
        from pact.schemas import TypeRegistry
        path = self._decomp_dir / "type_registry.json"
        if not path.exists():
            return None
        return TypeRegistry.model_validate_json(path.read_text())

    # ── Contracts ──────────────────────────────────────────────────

    def contract_dir(self, component_id: str) -> Path:
        """Visible contract directory: contracts/<component_id>/."""
        d = self._visible_contracts_dir / component_id
        d.mkdir(parents=True, exist_ok=True)
        return d

    def _internal_contract_dir(self, component_id: str) -> Path:
        """Ephemeral contract research: .pact/contracts/<component_id>/."""
        d = self._contracts_dir / component_id
        d.mkdir(parents=True, exist_ok=True)
        return d

    def save_contract(self, contract: ComponentContract) -> Path:
        d = self.contract_dir(contract.component_id)
        path = d / "interface.json"
        path.write_text(contract.model_dump_json(indent=2))
        from pact.interface_stub import render_stub
        stub_ext = ".ts" if self.language == "typescript" else ".py"
        stub_path = d / f"interface{stub_ext}"
        stub_path.write_text(render_stub(contract))
        # History alongside contract
        history = d / "history"
        history.mkdir(exist_ok=True)
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        (history / f"{ts}.json").write_text(contract.model_dump_json(indent=2))
        return path

    def load_contract(self, component_id: str) -> ComponentContract | None:
        path = self._visible_contracts_dir / component_id / "interface.json"
        if not path.exists():
            return None
        return ComponentContract.model_validate_json(path.read_text())

    def load_all_contracts(self) -> dict[str, ComponentContract]:
        contracts = {}
        if not self._visible_contracts_dir.exists():
            return contracts
        for d in self._visible_contracts_dir.iterdir():
            if d.is_dir():
                c = self.load_contract(d.name)
                if c:
                    contracts[d.name] = c
        return contracts

    # ── Test Suites ────────────────────────────────────────────────

    def save_test_suite(self, suite: ContractTestSuite) -> Path:
        visible_test_dir = self._visible_tests_dir / suite.component_id
        visible_test_dir.mkdir(parents=True, exist_ok=True)
        # JSON metadata alongside test code
        json_path = visible_test_dir / "contract_test_suite.json"
        json_path.write_text(suite.model_dump_json(indent=2))
        # Test code
        if suite.generated_code:
            test_ext = ".test.ts" if self.language == "typescript" else ".py"
            test_filename = f"contract_test{test_ext}"
            code_path = visible_test_dir / test_filename
            code_path.write_text(suite.generated_code)
        return json_path

    def load_test_suite(self, component_id: str) -> ContractTestSuite | None:
        path = self._visible_tests_dir / component_id / "contract_test_suite.json"
        if not path.exists():
            return None
        return ContractTestSuite.model_validate_json(path.read_text())

    def load_all_test_suites(self) -> dict[str, ContractTestSuite]:
        suites = {}
        if not self._visible_tests_dir.exists():
            return suites
        for d in self._visible_tests_dir.iterdir():
            if d.is_dir():
                s = self.load_test_suite(d.name)
                if s:
                    suites[d.name] = s
        return suites

    def test_code_path(self, component_id: str) -> Path:
        test_ext = ".test.ts" if self.language == "typescript" else ".py"
        return self._visible_tests_dir / component_id / f"contract_test{test_ext}"

    # ── Goodhart (Hidden) Test Suites ─────────────────────────────

    def save_goodhart_suite(self, suite: ContractTestSuite) -> Path:
        d = self._visible_tests_dir / suite.component_id / "goodhart"
        d.mkdir(parents=True, exist_ok=True)
        json_path = d / "goodhart_test_suite.json"
        json_path.write_text(suite.model_dump_json(indent=2))
        if suite.generated_code:
            test_ext = ".test.ts" if self.language == "typescript" else ".py"
            code_path = d / f"goodhart_test{test_ext}"
            code_path.write_text(suite.generated_code)
        return json_path

    def load_goodhart_suite(self, component_id: str) -> ContractTestSuite | None:
        path = self._visible_tests_dir / component_id / "goodhart" / "goodhart_test_suite.json"
        if not path.exists():
            return None
        return ContractTestSuite.model_validate_json(path.read_text())

    def load_all_goodhart_suites(self) -> dict[str, ContractTestSuite]:
        suites = {}
        if not self._visible_tests_dir.exists():
            return suites
        for d in self._visible_tests_dir.iterdir():
            if d.is_dir():
                s = self.load_goodhart_suite(d.name)
                if s:
                    suites[d.name] = s
        return suites

    def goodhart_test_code_path(self, component_id: str) -> Path:
        test_ext = ".test.ts" if self.language == "typescript" else ".py"
        return self._visible_tests_dir / component_id / "goodhart" / f"goodhart_test{test_ext}"

    # ── Emission Compliance Tests ─────────────────────────────────

    def save_emission_test(self, component_id: str, code: str) -> Path:
        """Save a generated emission compliance test for a component."""
        d = self._visible_tests_dir / component_id
        d.mkdir(parents=True, exist_ok=True)
        test_ext = ".test.ts" if self.language == "typescript" else ".py"
        path = d / f"emission_test{test_ext}"
        path.write_text(code)
        return path

    def emission_test_path(self, component_id: str) -> Path:
        test_ext = ".test.ts" if self.language == "typescript" else ".py"
        return self._visible_tests_dir / component_id / f"emission_test{test_ext}"

    # ── Implementations ────────────────────────────────────────────

    def impl_dir(self, component_id: str) -> Path:
        d = self._impl_dir / component_id
        d.mkdir(parents=True, exist_ok=True)
        return d

    def impl_src_dir(self, component_id: str) -> Path:
        """Visible implementation source: src/<component_id>/."""
        d = self._visible_src_dir / component_id
        d.mkdir(parents=True, exist_ok=True)
        return d

    def save_impl_metadata(self, component_id: str, metadata: dict) -> None:
        path = self.impl_dir(component_id) / "metadata.json"
        path.write_text(json.dumps(metadata, indent=2, default=str))

    def save_impl_research(self, component_id: str, research: object) -> None:
        path = self.impl_dir(component_id) / "research.json"
        if hasattr(research, "model_dump_json"):
            path.write_text(research.model_dump_json(indent=2))
        else:
            path.write_text(json.dumps(research, indent=2, default=str))

    def save_impl_plan(self, component_id: str, plan: object) -> None:
        path = self.impl_dir(component_id) / "plan.json"
        if hasattr(plan, "model_dump_json"):
            path.write_text(plan.model_dump_json(indent=2))
        else:
            path.write_text(json.dumps(plan, indent=2, default=str))

    def save_test_results(self, component_id: str, results: object) -> None:
        path = self.impl_dir(component_id) / "test_results.json"
        if hasattr(results, "model_dump_json"):
            path.write_text(results.model_dump_json(indent=2))
        else:
            path.write_text(json.dumps(results, indent=2, default=str))

    # ── Attempts (Competitive Mode) ──────────────────────────────

    def attempt_dir(self, component_id: str, attempt_id: str) -> Path:
        """Directory for a competitive attempt."""
        d = self._impl_dir / component_id / "attempts" / attempt_id
        d.mkdir(parents=True, exist_ok=True)
        return d

    def attempt_src_dir(self, component_id: str, attempt_id: str) -> Path:
        """Source directory within a competitive attempt."""
        d = self.attempt_dir(component_id, attempt_id) / "src"
        d.mkdir(exist_ok=True)
        return d

    def save_attempt_metadata(
        self, component_id: str, attempt_id: str, metadata: dict,
    ) -> None:
        """Save metadata for a competitive attempt."""
        path = self.attempt_dir(component_id, attempt_id) / "metadata.json"
        path.write_text(json.dumps(metadata, indent=2, default=str))

    def save_attempt_test_results(
        self, component_id: str, attempt_id: str, results: object,
    ) -> None:
        """Save test results for a competitive attempt."""
        path = self.attempt_dir(component_id, attempt_id) / "test_results.json"
        if hasattr(results, "model_dump_json"):
            path.write_text(results.model_dump_json(indent=2))
        else:
            path.write_text(json.dumps(results, indent=2, default=str))

    def promote_attempt(self, component_id: str, attempt_id: str) -> None:
        """Copy winning attempt to the main src/ directory."""
        attempt_src = self.attempt_dir(component_id, attempt_id) / "src"
        if not attempt_src.exists():
            return

        main_src = self.impl_src_dir(component_id)
        # Clear existing main src
        if main_src.exists():
            shutil.rmtree(main_src)
        main_src.mkdir(parents=True, exist_ok=True)

        # Copy attempt files to main
        for item in attempt_src.iterdir():
            dest = main_src / item.name
            if item.is_dir():
                shutil.copytree(item, dest)
            else:
                shutil.copy2(item, dest)

        # Copy attempt metadata/results to main impl dir
        attempt_meta = self.attempt_dir(component_id, attempt_id) / "metadata.json"
        if attempt_meta.exists():
            shutil.copy2(attempt_meta, self.impl_dir(component_id) / "metadata.json")
        attempt_results = self.attempt_dir(component_id, attempt_id) / "test_results.json"
        if attempt_results.exists():
            shutil.copy2(attempt_results, self.impl_dir(component_id) / "test_results.json")

    def archive_current_impl(self, component_id: str, reason: str) -> str | None:
        """Archive the current implementation as informational context.

        Used when cf build rebuilds a component — the old impl becomes
        context for the new agent.

        Returns the archive attempt_id, or None if no impl exists.
        """
        main_src = self.impl_src_dir(component_id)
        if not main_src.exists() or not any(main_src.iterdir()):
            return None

        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        archive_id = f"archived_{ts}"
        archive_dir = self.attempt_dir(component_id, archive_id)

        # Move src to archive
        archive_src = archive_dir / "src"
        if archive_src.exists():
            shutil.rmtree(archive_src)
        shutil.copytree(main_src, archive_src)

        # Save archive metadata
        self.save_attempt_metadata(component_id, archive_id, {
            "archived_at": datetime.now().isoformat(),
            "reason": reason,
            "type": "archived",
        })

        # Copy existing metadata/results if present
        for fname in ("metadata.json", "test_results.json"):
            existing = self._impl_dir / component_id / fname
            if existing.exists():
                shutil.copy2(existing, archive_dir / f"original_{fname}")

        # Clear main src
        shutil.rmtree(main_src)
        main_src.mkdir(exist_ok=True)

        return archive_id

    def list_attempts(self, component_id: str) -> list[dict]:
        """List all attempts for a component (competitive + archived)."""
        attempts_dir = self._impl_dir / component_id / "attempts"
        if not attempts_dir.exists():
            return []

        results = []
        for d in sorted(attempts_dir.iterdir()):
            if not d.is_dir():
                continue
            meta_path = d / "metadata.json"
            meta = {}
            if meta_path.exists():
                meta = json.loads(meta_path.read_text())
            results.append({
                "attempt_id": d.name,
                "path": str(d),
                **meta,
            })
        return results

    # ── Compositions ───────────────────────────────────────────────

    def composition_dir(self, parent_id: str) -> Path:
        """Visible composition source: src/<parent_id>/."""
        d = self._visible_src_dir / parent_id
        d.mkdir(parents=True, exist_ok=True)
        return d

    def _internal_composition_dir(self, parent_id: str) -> Path:
        """Internal composition metadata: .pact/compositions/<parent_id>/."""
        d = self._comp_dir / parent_id
        d.mkdir(parents=True, exist_ok=True)
        return d

    # ── Learnings ──────────────────────────────────────────────────

    def append_learning(self, entry: dict) -> None:
        path = self._learnings_dir / "learnings.jsonl"
        self._learnings_dir.mkdir(parents=True, exist_ok=True)
        with open(path, "a") as f:
            f.write(json.dumps(entry, default=str) + "\n")

    def load_learnings(self) -> list[dict]:
        path = self._learnings_dir / "learnings.jsonl"
        if not path.exists():
            return []
        entries = []
        with open(path) as f:
            for line in f:
                line = line.strip()
                if line:
                    entries.append(json.loads(line))
        return entries

    # ── Research ───────────────────────────────────────────────────

    def save_research(self, component_id: str, phase: str, research: object) -> None:
        """Save research for a contract or implementation phase."""
        if phase == "contract":
            d = self._internal_contract_dir(component_id)
        else:
            d = self.impl_dir(component_id)
        path = d / "research.json"
        if hasattr(research, "model_dump_json"):
            path.write_text(research.model_dump_json(indent=2))
        else:
            path.write_text(json.dumps(research, indent=2, default=str))

    # ── Task List ──────────────────────────────────────────────────

    def save_task_list(self, task_list: object) -> None:
        """Save a TaskList to tasks.json and TASKS.md."""
        if hasattr(task_list, "model_dump_json"):
            self.tasks_json_path.write_text(task_list.model_dump_json(indent=2))
        else:
            self.tasks_json_path.write_text(json.dumps(task_list, indent=2, default=str))

        # Also render markdown
        from pact.task_list import render_task_list_markdown
        md = render_task_list_markdown(task_list)
        self.tasks_md_path.write_text(md)

    def load_task_list(self) -> object | None:
        """Load a TaskList from tasks.json."""
        if not self.tasks_json_path.exists():
            return None
        from pact.schemas_tasks import TaskList
        return TaskList.model_validate_json(self.tasks_json_path.read_text())

    # ── Analysis ──────────────────────────────────────────────────

    def save_analysis(self, report: object) -> None:
        """Save an AnalysisReport to analysis.json."""
        if hasattr(report, "model_dump_json"):
            self.analysis_path.write_text(report.model_dump_json(indent=2))
        else:
            self.analysis_path.write_text(json.dumps(report, indent=2, default=str))

    def load_analysis(self) -> object | None:
        """Load an AnalysisReport from analysis.json."""
        if not self.analysis_path.exists():
            return None
        from pact.schemas_tasks import AnalysisReport
        return AnalysisReport.model_validate_json(self.analysis_path.read_text())

    # ── Checklist ─────────────────────────────────────────────────

    def save_checklist(self, checklist: object) -> None:
        """Save a RequirementsChecklist to checklist.json."""
        if hasattr(checklist, "model_dump_json"):
            self.checklist_path.write_text(checklist.model_dump_json(indent=2))
        else:
            self.checklist_path.write_text(json.dumps(checklist, indent=2, default=str))

    def load_checklist(self) -> object | None:
        """Load a RequirementsChecklist from checklist.json."""
        if not self.checklist_path.exists():
            return None
        from pact.schemas_tasks import RequirementsChecklist
        return RequirementsChecklist.model_validate_json(self.checklist_path.read_text())

    # ── Shaping Pitch ─────────────────────────────────────────────

    @property
    def pitch_path(self) -> Path:
        return self._decomp_dir / "pitch.json"

    def save_pitch(self, pitch: object) -> None:
        """Save a ShapingPitch."""
        self._decomp_dir.mkdir(parents=True, exist_ok=True)
        if hasattr(pitch, "model_dump_json"):
            self.pitch_path.write_text(pitch.model_dump_json(indent=2))
        else:
            import json
            self.pitch_path.write_text(json.dumps(pitch, indent=2, default=str))

    def load_pitch(self) -> object | None:
        """Load a ShapingPitch from decomposition/pitch.json."""
        if not self.pitch_path.exists():
            return None
        try:
            from pact.schemas_shaping import ShapingPitch
            return ShapingPitch.model_validate_json(self.pitch_path.read_text())
        except Exception:
            return None

    # ── Design Document ────────────────────────────────────────────

    def save_design_doc(self, doc: DesignDocument) -> None:
        path = self.audit_root / "design.json"
        path.write_text(doc.model_dump_json(indent=2))

    def load_design_doc(self) -> DesignDocument | None:
        path = self.audit_root / "design.json"
        if not path.exists():
            return None
        return DesignDocument.model_validate_json(path.read_text())


def write_artifact_metadata(
    artifact_path: Path,
    metadata: "ArtifactMetadata",
) -> None:
    """Write sidecar metadata file alongside generated artifact.

    Sidecar path: artifact_path.with_suffix(artifact_path.suffix + '.meta.json')
    e.g. contract.json -> contract.json.meta.json

    Postconditions:
      - .meta.json exists alongside the artifact
      - Metadata is valid JSON matching ArtifactMetadata schema
    """
    meta_path = Path(str(artifact_path) + ".meta.json")
    meta_path.parent.mkdir(parents=True, exist_ok=True)
    meta_path.write_text(metadata.model_dump_json(indent=2))


def read_artifact_metadata(artifact_path: Path) -> "ArtifactMetadata | None":
    """Read sidecar metadata for an artifact. Returns None if no metadata."""
    from pact.schemas import ArtifactMetadata

    meta_path = Path(str(artifact_path) + ".meta.json")
    if not meta_path.exists():
        return None
    try:
        return ArtifactMetadata.model_validate_json(meta_path.read_text())
    except Exception:
        return None
