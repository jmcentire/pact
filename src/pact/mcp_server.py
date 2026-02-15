"""MCP server resources and tools for Pact.

Provides structured read access to Pact project state for external tools.
Resources are read-only. Tools may modify state with confirmation.

MCP resources:
  pact://status          -> RunState summary
  pact://contracts       -> list of contracts with summaries
  pact://contract/{id}   -> full contract for a component
  pact://budget          -> budget summary with phase breakdown
  pact://retrospective   -> latest retrospective

MCP tools:
  pact_validate          -> run validation, return errors
  pact_resume            -> resume failed/paused run
  pact_status            -> detailed status with staleness
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


class PactMCPServer:
    """MCP-compatible server for Pact project introspection.
    
    Provides resource handlers and tool handlers that can be
    wired to any MCP transport layer.
    """

    def __init__(self, project_dir: str | Path | None = None):
        self._project_dir = Path(project_dir) if project_dir else None
        self._project = None

    @property
    def project_dir(self) -> Path | None:
        return self._project_dir

    def _ensure_project(self) -> bool:
        """Ensure project directory is valid and has .pact/."""
        if not self._project_dir:
            return False
        pact_dir = self._project_dir / ".pact"
        return pact_dir.exists()

    def _get_project_manager(self):
        """Lazy-load ProjectManager."""
        if self._project is None and self._project_dir:
            from pact.project import ProjectManager
            self._project = ProjectManager(self._project_dir)
        return self._project

    # ── Resources (read-only) ─────────────────────────────────────

    def resource_status(self) -> dict[str, Any]:
        """Get current run status.
        
        Returns dict with: id, status, phase, cost, tokens, components summary.
        Returns error dict if no project found.
        """
        if not self._ensure_project():
            return {"error": "No Pact project found", "hint": "Run 'pact init <dir>' first"}

        pm = self._get_project_manager()
        if not pm:
            return {"error": "Could not load project"}

        try:
            state = pm.load_state()
            result = {
                "id": state.id,
                "status": state.status,
                "phase": state.phase,
                "total_cost_usd": state.total_cost_usd,
                "total_tokens": state.total_tokens,
                "pause_reason": state.pause_reason,
            }
            if state.component_tasks:
                completed = sum(1 for t in state.component_tasks if t.status == "completed")
                failed = sum(1 for t in state.component_tasks if t.status == "failed")
                result["components"] = {
                    "total": len(state.component_tasks),
                    "completed": completed,
                    "failed": failed,
                }
            return result
        except FileNotFoundError:
            return {"error": "No run state found", "hint": "Run 'pact run <dir>' first"}
        except Exception as e:
            return {"error": f"Failed to load state: {e}"}

    def resource_contracts(self) -> dict[str, Any]:
        """List all contracts with summaries.
        
        Returns dict with contract_id -> {name, description, function_count, type_count}.
        """
        if not self._ensure_project():
            return {"error": "No Pact project found"}

        pm = self._get_project_manager()
        if not pm:
            return {"error": "Could not load project"}

        try:
            contracts = pm.load_all_contracts()
            result = {}
            for cid, contract in contracts.items():
                result[cid] = {
                    "name": contract.name,
                    "description": contract.description[:200],
                    "version": contract.version,
                    "function_count": len(contract.functions),
                    "type_count": len(contract.types),
                    "dependencies": contract.dependencies,
                }
            return {"contracts": result, "count": len(result)}
        except Exception as e:
            return {"error": f"Failed to load contracts: {e}"}

    def resource_contract(self, component_id: str) -> dict[str, Any]:
        """Get full contract for a specific component."""
        if not self._ensure_project():
            return {"error": "No Pact project found"}

        pm = self._get_project_manager()
        if not pm:
            return {"error": "Could not load project"}

        try:
            contract = pm.load_contract(component_id)
            if not contract:
                return {"error": f"Contract not found: {component_id}"}
            return json.loads(contract.model_dump_json())
        except Exception as e:
            return {"error": f"Failed to load contract: {e}"}

    def resource_budget(self) -> dict[str, Any]:
        """Get budget summary."""
        if not self._ensure_project():
            return {"error": "No Pact project found"}

        pm = self._get_project_manager()
        if not pm:
            return {"error": "Could not load project"}

        try:
            state = pm.load_state()
            config = pm.load_config()
            budget_cap = config.budget
            spent = state.total_cost_usd
            remaining = max(0, budget_cap - spent)
            pct = (spent / budget_cap * 100) if budget_cap > 0 else 0

            return {
                "budget": budget_cap,
                "spent": spent,
                "remaining": remaining,
                "percentage_used": round(pct, 1),
                "tokens": state.total_tokens,
            }
        except FileNotFoundError:
            return {"error": "No run state found"}
        except Exception as e:
            return {"error": f"Failed to load budget: {e}"}

    def resource_retrospective(self) -> dict[str, Any]:
        """Get latest retrospective."""
        if not self._ensure_project():
            return {"error": "No Pact project found"}

        retro_dir = self._project_dir / ".pact" / "retrospectives"
        if not retro_dir.exists():
            return {"error": "No retrospectives found"}

        try:
            files = sorted(retro_dir.glob("*.json"))
            if not files:
                return {"error": "No retrospectives found"}
            latest = json.loads(files[-1].read_text())
            return latest
        except Exception as e:
            return {"error": f"Failed to load retrospective: {e}"}

    # ── Tools (may modify state) ──────────────────────────────────

    def tool_validate(self) -> dict[str, Any]:
        """Run contract validation and return results."""
        if not self._ensure_project():
            return {"error": "No Pact project found"}

        pm = self._get_project_manager()
        if not pm:
            return {"error": "Could not load project"}

        try:
            from pact.contracts import validate_all_contracts

            tree = pm.load_tree()
            if not tree:
                return {"error": "No decomposition tree found"}

            contracts = pm.load_all_contracts()
            test_suites = pm.load_all_test_suites()

            gate = validate_all_contracts(tree, contracts, test_suites)
            return {
                "passed": gate.passed,
                "reason": gate.reason,
                "errors": gate.details,
                "error_count": len(gate.details),
            }
        except Exception as e:
            return {"error": f"Validation failed: {e}"}

    def tool_status(self) -> dict[str, Any]:
        """Detailed status with component breakdown."""
        status = self.resource_status()
        if "error" in status:
            return status

        pm = self._get_project_manager()
        if not pm:
            return status

        try:
            state = pm.load_state()
            if state.component_tasks:
                components = []
                for task in state.component_tasks:
                    components.append({
                        "id": task.component_id,
                        "status": task.status,
                        "attempts": task.attempts,
                        "last_error": task.last_error[:100] if task.last_error else "",
                    })
                status["component_details"] = components
            return status
        except Exception as e:
            status["component_error"] = str(e)
            return status

    def list_resources(self) -> list[dict[str, str]]:
        """List available MCP resources."""
        return [
            {"uri": "pact://status", "name": "Run Status", "description": "Current run state summary"},
            {"uri": "pact://contracts", "name": "Contracts", "description": "List all component contracts"},
            {"uri": "pact://contract/{id}", "name": "Contract Detail", "description": "Full contract for a component"},
            {"uri": "pact://budget", "name": "Budget", "description": "Budget and spend summary"},
            {"uri": "pact://retrospective", "name": "Retrospective", "description": "Latest run retrospective"},
        ]

    def list_tools(self) -> list[dict[str, str]]:
        """List available MCP tools."""
        return [
            {"name": "pact_validate", "description": "Run contract validation"},
            {"name": "pact_status", "description": "Detailed status with component breakdown"},
        ]
