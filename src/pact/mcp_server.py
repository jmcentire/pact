"""MCP server resources and tools for Pact.

Provides structured read access to Pact project state for external tools.
Resources are read-only. Tools may modify state with confirmation.

Run with: pact-mcp (stdio transport, for Claude Code integration)
Or:       pact mcp-server [--project-dir <dir>]

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
import os
import sys
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

    def tool_resume(self, from_phase: str = "") -> dict[str, Any]:
        """Resume a failed or paused run."""
        if not self._ensure_project():
            return {"error": "No Pact project found", "hint": "Run 'pact init <dir>' first"}

        pm = self._get_project_manager()
        if not pm:
            return {"error": "Could not load project"}

        try:
            from pact.lifecycle import compute_resume_strategy, execute_resume
            state = pm.load_state()
            strategy = compute_resume_strategy(state, from_phase=from_phase or None)
            execute_resume(state, strategy)
            pm.save_state(state)
            return {
                "resumed": True,
                "phase": state.phase,
                "status": state.status,
                "completed_components": strategy.completed_components,
            }
        except ValueError as e:
            return {"error": str(e)}
        except Exception as e:
            return {"error": f"Resume failed: {e}"}

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
            {"name": "pact_resume", "description": "Resume a failed/paused run"},
        ]


# ── FastMCP transport layer ──────────────────────────────────────────

def _find_project_dir() -> Path | None:
    """Find a Pact project directory.

    Checks PACT_PROJECT_DIR env var first, then walks up from CWD
    looking for pact.yaml or .pact/.
    """
    env = os.environ.get("PACT_PROJECT_DIR")
    if env:
        p = Path(env).resolve()
        if p.exists():
            return p

    cwd = Path.cwd()
    for d in [cwd, *cwd.parents]:
        if (d / "pact.yaml").exists() or (d / ".pact").exists():
            return d
    return None


def _json_str(obj: Any) -> str:
    """JSON-serialize with safe defaults."""
    return json.dumps(obj, indent=2, default=str)


def _create_mcp_app():
    """Create and configure the FastMCP application.

    Separated from module-level code so import doesn't fail
    when the mcp package isn't installed (tests only need PactMCPServer).
    """
    from mcp.server.fastmcp import FastMCP

    app = FastMCP(
        "pact",
        instructions=(
            "Pact is a contract-first multi-agent software engineering framework. "
            "Use these tools to inspect project state, validate contracts, and manage runs.\n\n"
            "## Available tools\n"
            "- `pact_status`: Get run status with component breakdown\n"
            "- `pact_contracts`: List all component contracts\n"
            "- `pact_contract`: Get full contract for a specific component\n"
            "- `pact_budget`: Get budget and spend summary\n"
            "- `pact_retrospective`: Get the latest run retrospective\n"
            "- `pact_validate`: Run contract validation gate\n"
            "- `pact_resume`: Resume a failed or paused run\n\n"
            "## Project detection\n"
            "Set PACT_PROJECT_DIR env var, or the server auto-detects from CWD.\n"
            "Pass project_dir to any tool to override."
        ),
    )

    def _server(project_dir: str | None = None) -> PactMCPServer:
        """Resolve project dir and return a PactMCPServer instance."""
        if project_dir:
            return PactMCPServer(project_dir)
        found = _find_project_dir()
        return PactMCPServer(found)

    # ── Tools ────────────────────────────────────────────────────

    @app.tool()
    def pact_status(project_dir: str | None = None) -> str:
        """Get detailed run status with component breakdown.

        Shows run ID, phase, cost, tokens, and per-component status.
        """
        s = _server(project_dir)
        return _json_str(s.tool_status())

    @app.tool()
    def pact_contracts(project_dir: str | None = None) -> str:
        """List all component contracts with summaries.

        Returns contract names, descriptions, function/type counts, and dependencies.
        """
        s = _server(project_dir)
        return _json_str(s.resource_contracts())

    @app.tool()
    def pact_contract(component_id: str, project_dir: str | None = None) -> str:
        """Get the full contract for a specific component.

        Args:
            component_id: The component ID to look up.
        """
        s = _server(project_dir)
        return _json_str(s.resource_contract(component_id))

    @app.tool()
    def pact_budget(project_dir: str | None = None) -> str:
        """Get budget and spend summary.

        Shows budget cap, amount spent, remaining, percentage used, and token count.
        """
        s = _server(project_dir)
        return _json_str(s.resource_budget())

    @app.tool()
    def pact_retrospective(project_dir: str | None = None) -> str:
        """Get the latest run retrospective.

        Returns the most recent retrospective analysis from the project.
        """
        s = _server(project_dir)
        return _json_str(s.resource_retrospective())

    @app.tool()
    def pact_validate(project_dir: str | None = None) -> str:
        """Run contract validation gate.

        Checks all contracts for structural correctness: refs resolve,
        no cycles, test code parses, cross-component interfaces match.
        """
        s = _server(project_dir)
        return _json_str(s.tool_validate())

    @app.tool()
    def pact_resume(from_phase: str = "", project_dir: str | None = None) -> str:
        """Resume a failed or paused pipeline run.

        Args:
            from_phase: Optional phase to resume from (e.g. 'implement', 'integrate').
                        If empty, resumes from where it left off.
        """
        s = _server(project_dir)
        return _json_str(s.tool_resume(from_phase=from_phase))

    # ── Resources ────────────────────────────────────────────────

    @app.resource("pact://status")
    def resource_status() -> str:
        """Current run state summary."""
        s = _server()
        return _json_str(s.resource_status())

    @app.resource("pact://contracts")
    def resource_contracts() -> str:
        """List all component contracts."""
        s = _server()
        return _json_str(s.resource_contracts())

    @app.resource("pact://contract/{component_id}")
    def resource_contract(component_id: str) -> str:
        """Full contract for a specific component."""
        s = _server()
        return _json_str(s.resource_contract(component_id))

    @app.resource("pact://budget")
    def resource_budget() -> str:
        """Budget and spend summary."""
        s = _server()
        return _json_str(s.resource_budget())

    @app.resource("pact://retrospective")
    def resource_retrospective() -> str:
        """Latest run retrospective."""
        s = _server()
        return _json_str(s.resource_retrospective())

    return app


# ── Entry point ──────────────────────────────────────────────────────


def main():
    """Run the Pact MCP server (stdio transport)."""
    try:
        app = _create_mcp_app()
    except ImportError:
        print(
            "Error: the 'mcp' package is not installed.\n"
            "Install with: pip install pact-agents[mcp]\n",
            file=sys.stderr,
        )
        sys.exit(1)
    app.run()
