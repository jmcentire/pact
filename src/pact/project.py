"""Project directory lifecycle — init, load, save, resume.

The project directory is the unit of work:
  proj/
  ├── task.md
  ├── sops.md
  ├── pact.yaml
  ├── design.md
  └── .pact/
      ├── state.json
      ├── audit.jsonl
      ├── budget.json
      ├── decomposition/
      │   ├── tree.json
      │   ├── decisions.json
      │   └── interview.json
      ├── contracts/<component_id>/
      │   ├── interface.json
      │   ├── research.json
      │   ├── tests/contract_test.py
      │   └── history/<timestamp>.json
      ├── implementations/<component_id>/
      │   ├── src/
      │   ├── research.json
      │   ├── plan.json
      │   ├── metadata.json
      │   └── test_results.json
      ├── compositions/<parent_id>/
      │   ├── glue.py
      │   ├── composition_test.py
      │   └── test_results.json
      └── learnings/
          └── learnings.jsonl
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


class ProjectManager:
    """Manages project directory lifecycle."""

    def __init__(self, project_dir: str | Path) -> None:
        self.project_dir = Path(project_dir).resolve()
        self._pact_dir = self.project_dir / PACT_DIR
        self._decomp_dir = self._pact_dir / "decomposition"
        self._contracts_dir = self._pact_dir / "contracts"
        self._impl_dir = self._pact_dir / "implementations"
        self._comp_dir = self._pact_dir / "compositions"
        self._learnings_dir = self._pact_dir / "learnings"

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
        return self._pact_dir / "tasks.json"

    @property
    def tasks_md_path(self) -> Path:
        return self.project_dir / "TASKS.md"

    @property
    def analysis_path(self) -> Path:
        return self._pact_dir / "analysis.json"

    @property
    def checklist_path(self) -> Path:
        return self._pact_dir / "checklist.json"

    # ── Init ───────────────────────────────────────────────────────

    def init(self, budget: float = 10.00) -> None:
        """Scaffold a new project directory."""
        self.project_dir.mkdir(parents=True, exist_ok=True)
        self._pact_dir.mkdir(exist_ok=True)
        self._decomp_dir.mkdir(exist_ok=True)
        self._contracts_dir.mkdir(exist_ok=True)
        self._impl_dir.mkdir(exist_ok=True)
        self._comp_dir.mkdir(exist_ok=True)
        self._learnings_dir.mkdir(exist_ok=True)

        if not self.task_path.exists():
            self.task_path.write_text(
                "# Task\n\n"
                "Describe your task here.\n\n"
                "## Context\n\n"
                "Any relevant context, constraints, or requirements.\n"
            )

        if not self.sops_path.exists():
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

        if not self.config_path.exists():
            config = {
                "budget": budget,
            }
            with open(self.config_path, "w") as f:
                yaml.dump(config, f, default_flow_style=False, sort_keys=False)

        if not self.design_path.exists():
            self.design_path.write_text(
                "# Design Document\n\n"
                "*Auto-maintained by pact. Do not edit manually.*\n\n"
                "## Status: Not started\n"
            )

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

    def clear_state(self) -> None:
        """Remove all run state. Preserves task.md, sops.md, config."""
        if self._pact_dir.exists():
            shutil.rmtree(self._pact_dir)
        self._pact_dir.mkdir(exist_ok=True)
        self._decomp_dir.mkdir(exist_ok=True)
        self._contracts_dir.mkdir(exist_ok=True)
        self._impl_dir.mkdir(exist_ok=True)
        self._comp_dir.mkdir(exist_ok=True)
        self._learnings_dir.mkdir(exist_ok=True)

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

    # ── Contracts ──────────────────────────────────────────────────

    def contract_dir(self, component_id: str) -> Path:
        d = self._contracts_dir / component_id
        d.mkdir(parents=True, exist_ok=True)
        return d

    def save_contract(self, contract: ComponentContract) -> Path:
        d = self.contract_dir(contract.component_id)
        path = d / "interface.json"
        path.write_text(contract.model_dump_json(indent=2))
        # Save interface stub (the agent's mental model, code-shaped)
        from pact.interface_stub import render_stub
        stub_path = d / "interface.py"
        stub_path.write_text(render_stub(contract))
        # Save to history
        history = d / "history"
        history.mkdir(exist_ok=True)
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        (history / f"{ts}.json").write_text(contract.model_dump_json(indent=2))
        return path

    def load_contract(self, component_id: str) -> ComponentContract | None:
        path = self._contracts_dir / component_id / "interface.json"
        if not path.exists():
            return None
        return ComponentContract.model_validate_json(path.read_text())

    def load_all_contracts(self) -> dict[str, ComponentContract]:
        contracts = {}
        if not self._contracts_dir.exists():
            return contracts
        for d in self._contracts_dir.iterdir():
            if d.is_dir():
                c = self.load_contract(d.name)
                if c:
                    contracts[d.name] = c
        return contracts

    # ── Test Suites ────────────────────────────────────────────────

    def save_test_suite(self, suite: ContractTestSuite) -> Path:
        d = self.contract_dir(suite.component_id) / "tests"
        d.mkdir(exist_ok=True)
        json_path = d / "contract_test_suite.json"
        json_path.write_text(suite.model_dump_json(indent=2))
        if suite.generated_code:
            code_path = d / "contract_test.py"
            code_path.write_text(suite.generated_code)
        return json_path

    def load_test_suite(self, component_id: str) -> ContractTestSuite | None:
        path = self._contracts_dir / component_id / "tests" / "contract_test_suite.json"
        if not path.exists():
            return None
        return ContractTestSuite.model_validate_json(path.read_text())

    def load_all_test_suites(self) -> dict[str, ContractTestSuite]:
        suites = {}
        if not self._contracts_dir.exists():
            return suites
        for d in self._contracts_dir.iterdir():
            if d.is_dir():
                s = self.load_test_suite(d.name)
                if s:
                    suites[d.name] = s
        return suites

    def test_code_path(self, component_id: str) -> Path:
        return self._contracts_dir / component_id / "tests" / "contract_test.py"

    # ── Implementations ────────────────────────────────────────────

    def impl_dir(self, component_id: str) -> Path:
        d = self._impl_dir / component_id
        d.mkdir(parents=True, exist_ok=True)
        return d

    def impl_src_dir(self, component_id: str) -> Path:
        d = self.impl_dir(component_id) / "src"
        d.mkdir(exist_ok=True)
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
        main_src = self._impl_dir / component_id / "src"
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
            d = self.contract_dir(component_id)
        else:
            d = self.impl_dir(component_id)
        path = d / "research.json"
        if hasattr(research, "model_dump_json"):
            path.write_text(research.model_dump_json(indent=2))
        else:
            path.write_text(json.dumps(research, indent=2, default=str))

    # ── Task List ──────────────────────────────────────────────────

    def save_task_list(self, task_list: object) -> None:
        """Save a TaskList to .pact/tasks.json and TASKS.md."""
        self._pact_dir.mkdir(parents=True, exist_ok=True)
        if hasattr(task_list, "model_dump_json"):
            self.tasks_json_path.write_text(task_list.model_dump_json(indent=2))
        else:
            self.tasks_json_path.write_text(json.dumps(task_list, indent=2, default=str))

        # Also render markdown
        from pact.task_list import render_task_list_markdown
        md = render_task_list_markdown(task_list)
        self.tasks_md_path.write_text(md)

    def load_task_list(self) -> object | None:
        """Load a TaskList from .pact/tasks.json."""
        if not self.tasks_json_path.exists():
            return None
        from pact.schemas_tasks import TaskList
        return TaskList.model_validate_json(self.tasks_json_path.read_text())

    # ── Analysis ──────────────────────────────────────────────────

    def save_analysis(self, report: object) -> None:
        """Save an AnalysisReport to .pact/analysis.json."""
        self._pact_dir.mkdir(parents=True, exist_ok=True)
        if hasattr(report, "model_dump_json"):
            self.analysis_path.write_text(report.model_dump_json(indent=2))
        else:
            self.analysis_path.write_text(json.dumps(report, indent=2, default=str))

    def load_analysis(self) -> object | None:
        """Load an AnalysisReport from .pact/analysis.json."""
        if not self.analysis_path.exists():
            return None
        from pact.schemas_tasks import AnalysisReport
        return AnalysisReport.model_validate_json(self.analysis_path.read_text())

    # ── Checklist ─────────────────────────────────────────────────

    def save_checklist(self, checklist: object) -> None:
        """Save a RequirementsChecklist to .pact/checklist.json."""
        self._pact_dir.mkdir(parents=True, exist_ok=True)
        if hasattr(checklist, "model_dump_json"):
            self.checklist_path.write_text(checklist.model_dump_json(indent=2))
        else:
            self.checklist_path.write_text(json.dumps(checklist, indent=2, default=str))

    def load_checklist(self) -> object | None:
        """Load a RequirementsChecklist from .pact/checklist.json."""
        if not self.checklist_path.exists():
            return None
        from pact.schemas_tasks import RequirementsChecklist
        return RequirementsChecklist.model_validate_json(self.checklist_path.read_text())

    # ── Shaping Pitch ─────────────────────────────────────────────

    @property
    def pitch_path(self) -> Path:
        return self._decomp_dir / "pitch.json"

    def save_pitch(self, pitch: object) -> None:
        """Save a ShapingPitch to decomposition/pitch.json."""
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
        self._pact_dir.mkdir(parents=True, exist_ok=True)
        # Save structured version
        (self._pact_dir / "design.json").write_text(doc.model_dump_json(indent=2))

    def load_design_doc(self) -> DesignDocument | None:
        path = self._pact_dir / "design.json"
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
