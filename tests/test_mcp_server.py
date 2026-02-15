"""Tests for MCP server resources and tools."""
import json
from pathlib import Path

from pact.mcp_server import PactMCPServer


def _setup_project(tmp_path: Path, run_id: str = "test123"):
    """Create minimal project structure."""
    pact_dir = tmp_path / ".pact"
    pact_dir.mkdir()
    
    # State
    state = {
        "id": run_id,
        "project_dir": str(tmp_path),
        "status": "active",
        "phase": "implement",
        "total_cost_usd": 5.50,
        "total_tokens": 50000,
        "pause_reason": "",
        "component_tasks": [
            {"component_id": "comp_a", "status": "completed", "attempts": 1, "last_error": ""},
            {"component_id": "comp_b", "status": "implementing", "attempts": 0, "last_error": ""},
        ],
        "created_at": "2024-01-01T00:00:00",
    }
    (pact_dir / "state.json").write_text(json.dumps(state))
    
    # Config
    (tmp_path / "pact.yaml").write_text("budget: 25.00\n")
    (tmp_path / "task.md").write_text("# Test Task\n")
    
    return pact_dir


def _add_contract(pact_dir: Path, component_id: str):
    """Add a contract to the project."""
    contract_dir = pact_dir / "contracts" / component_id
    contract_dir.mkdir(parents=True)
    contract = {
        "component_id": component_id,
        "name": f"Component {component_id}",
        "description": f"Description of {component_id}",
        "version": 1,
        "types": [],
        "functions": [
            {"name": "do_thing", "description": "Does thing", "inputs": [], "output_type": "str"},
        ],
        "dependencies": [],
        "invariants": [],
    }
    (contract_dir / "interface.json").write_text(json.dumps(contract))


class TestMCPServerNoProject:
    def test_status_no_project(self):
        server = PactMCPServer(None)
        result = server.resource_status()
        assert "error" in result

    def test_contracts_no_project(self):
        server = PactMCPServer(None)
        result = server.resource_contracts()
        assert "error" in result

    def test_budget_no_project(self):
        server = PactMCPServer(None)
        result = server.resource_budget()
        assert "error" in result

    def test_validate_no_project(self):
        server = PactMCPServer(None)
        result = server.tool_validate()
        assert "error" in result

    def test_starts_without_project(self):
        """Server instantiates cleanly without project dir."""
        server = PactMCPServer()
        assert server.project_dir is None


class TestMCPServerResources:
    def test_status_returns_json(self, tmp_path):
        _setup_project(tmp_path)
        server = PactMCPServer(tmp_path)
        result = server.resource_status()
        assert result["id"] == "test123"
        assert result["status"] == "active"
        assert result["phase"] == "implement"
        assert result["total_cost_usd"] == 5.50
        assert "components" in result

    def test_status_component_counts(self, tmp_path):
        _setup_project(tmp_path)
        server = PactMCPServer(tmp_path)
        result = server.resource_status()
        assert result["components"]["total"] == 2
        assert result["components"]["completed"] == 1

    def test_contracts_list(self, tmp_path):
        pact_dir = _setup_project(tmp_path)
        _add_contract(pact_dir, "comp_a")
        _add_contract(pact_dir, "comp_b")
        server = PactMCPServer(tmp_path)
        result = server.resource_contracts()
        assert result["count"] == 2
        assert "comp_a" in result["contracts"]
        assert "comp_b" in result["contracts"]

    def test_contract_detail(self, tmp_path):
        pact_dir = _setup_project(tmp_path)
        _add_contract(pact_dir, "comp_a")
        server = PactMCPServer(tmp_path)
        result = server.resource_contract("comp_a")
        assert result["component_id"] == "comp_a"
        assert result["name"] == "Component comp_a"

    def test_contract_not_found(self, tmp_path):
        _setup_project(tmp_path)
        server = PactMCPServer(tmp_path)
        result = server.resource_contract("nonexistent")
        assert "error" in result

    def test_budget_summary(self, tmp_path):
        _setup_project(tmp_path)
        server = PactMCPServer(tmp_path)
        result = server.resource_budget()
        assert result["budget"] == 25.0
        assert result["spent"] == 5.50
        assert result["remaining"] == 19.50
        assert result["percentage_used"] == 22.0

    def test_retrospective_no_retros(self, tmp_path):
        _setup_project(tmp_path)
        server = PactMCPServer(tmp_path)
        result = server.resource_retrospective()
        assert "error" in result


class TestMCPServerTools:
    def test_detailed_status(self, tmp_path):
        _setup_project(tmp_path)
        server = PactMCPServer(tmp_path)
        result = server.tool_status()
        assert "component_details" in result
        assert len(result["component_details"]) == 2

    def test_validate_no_tree(self, tmp_path):
        _setup_project(tmp_path)
        server = PactMCPServer(tmp_path)
        result = server.tool_validate()
        assert "error" in result

    def test_list_resources(self):
        server = PactMCPServer()
        resources = server.list_resources()
        assert len(resources) >= 4
        uris = [r["uri"] for r in resources]
        assert "pact://status" in uris

    def test_list_tools(self):
        server = PactMCPServer()
        tools = server.list_tools()
        assert len(tools) >= 2
        names = [t["name"] for t in tools]
        assert "pact_validate" in names
