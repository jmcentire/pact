"""Tests for external vs internal dependency validation."""
from pathlib import Path
from pact.contracts import validate_all_contracts, validate_external_dependencies
from pact.schemas import (
    ComponentContract, ContractTestSuite, DecompositionTree,
    DecompositionNode, FunctionContract, FieldSpec, TestCase,
)


def _make_contract(cid, deps=None, requires=None):
    return ComponentContract(
        component_id=cid,
        name=cid.title(),
        description=f"Test contract for {cid}",
        functions=[FunctionContract(
            name="do_thing",
            description="Does a thing",
            inputs=[FieldSpec(name="x", type_ref="str")],
            output_type="str",
        )],
        dependencies=deps or [],
        requires=requires or [],
    )


def _make_suite(cid):
    return ContractTestSuite(
        component_id=cid,
        contract_version=1,
        test_cases=[TestCase(
            id=f"test_{cid}_1",
            description="Basic test",
            function="do_thing",
            category="happy_path",
        )],
        generated_code=f"def test_{cid}(): pass",
    )


def _make_tree(*cids, root_id=None):
    root_id = root_id or cids[0]
    nodes = {}
    for cid in cids:
        nodes[cid] = DecompositionNode(
            component_id=cid,
            name=cid.title(),
            description=f"Node {cid}",
        )
    # Make first node the parent of the rest
    if len(cids) > 1:
        nodes[root_id].children = list(cids[1:])
        for cid in cids[1:]:
            nodes[cid].parent_id = root_id
    return DecompositionTree(root_id=root_id, nodes=nodes)


class TestExternalDependencyValidation:
    def test_external_dep_on_existing_module_passes(self):
        """Contract depends on module not in tree -> should NOT error."""
        tree = _make_tree("root", "comp_a", "comp_b")
        contracts = {
            "root": _make_contract("root", deps=["comp_a", "comp_b"]),
            "comp_a": _make_contract("comp_a", deps=["agents.base"]),  # external!
            "comp_b": _make_contract("comp_b"),
        }
        suites = {cid: _make_suite(cid) for cid in contracts}

        gate = validate_all_contracts(tree, contracts, suites)
        # Should pass — agents.base is external, not in tree
        assert gate.passed, f"Expected pass but got: {gate.details}"

    def test_internal_dep_without_contract_fails(self):
        """Contract depends on sibling in tree but sibling has no contract -> error."""
        tree = _make_tree("root", "comp_a", "comp_b")
        contracts = {
            "root": _make_contract("root", deps=["comp_a", "comp_b"]),
            "comp_a": _make_contract("comp_a", deps=["comp_b"]),
            # comp_b has no contract!
        }
        suites = {cid: _make_suite(cid) for cid in contracts}

        gate = validate_all_contracts(tree, contracts, suites)
        assert not gate.passed
        assert any("comp_b" in d for d in gate.details)

    def test_mixed_internal_external_deps(self):
        """Both internal and external deps -> only internal checked."""
        tree = _make_tree("root", "comp_a", "comp_b")
        contracts = {
            "root": _make_contract("root", deps=["comp_a", "comp_b"]),
            "comp_a": _make_contract("comp_a", deps=["comp_b", "schemas", "config"]),
            "comp_b": _make_contract("comp_b"),
        }
        suites = {cid: _make_suite(cid) for cid in contracts}

        gate = validate_all_contracts(tree, contracts, suites)
        assert gate.passed, f"Expected pass but got: {gate.details}"

    def test_internal_dep_with_contract_passes(self):
        """Contract depends on sibling that has a contract -> pass."""
        tree = _make_tree("root", "comp_a", "comp_b")
        contracts = {
            "root": _make_contract("root", deps=["comp_a", "comp_b"]),
            "comp_a": _make_contract("comp_a", deps=["comp_b"]),
            "comp_b": _make_contract("comp_b"),
        }
        suites = {cid: _make_suite(cid) for cid in contracts}

        gate = validate_all_contracts(tree, contracts, suites)
        assert gate.passed

    def test_completely_external_deps_pass(self):
        """All deps are external (not in tree) -> pass."""
        tree = _make_tree("root")
        contracts = {
            "root": _make_contract("root", deps=["pydantic", "yaml", "agents.base"]),
        }
        suites = {"root": _make_suite("root")}

        gate = validate_all_contracts(tree, contracts, suites)
        assert gate.passed, f"Expected pass but got: {gate.details}"


class TestValidateExternalDependencies:
    def test_no_source_tree_returns_empty(self):
        contract = _make_contract("x", requires=["agents.base"])
        warnings = validate_external_dependencies(contract, source_tree=None)
        assert warnings == []

    def test_no_requires_returns_empty(self):
        contract = _make_contract("x")
        warnings = validate_external_dependencies(contract, source_tree=Path("/tmp"))
        assert warnings == []

    def test_existing_module_no_warning(self, tmp_path):
        # Create a fake module
        mod_dir = tmp_path / "agents"
        mod_dir.mkdir()
        (mod_dir / "base.py").write_text("# base module")

        contract = _make_contract("x", requires=["agents.base"])
        warnings = validate_external_dependencies(contract, source_tree=tmp_path)
        assert warnings == []

    def test_missing_module_warns(self, tmp_path):
        contract = _make_contract("x", requires=["nonexistent_module"])
        warnings = validate_external_dependencies(contract, source_tree=tmp_path)
        assert len(warnings) == 1
        assert "nonexistent_module" in warnings[0]

    def test_package_with_init(self, tmp_path):
        # Create a package with __init__.py
        pkg = tmp_path / "mypackage"
        pkg.mkdir()
        (pkg / "__init__.py").write_text("")

        contract = _make_contract("x", requires=["mypackage"])
        warnings = validate_external_dependencies(contract, source_tree=tmp_path)
        assert warnings == []
