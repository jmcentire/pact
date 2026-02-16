"""Tests for cross-artifact analysis."""

from __future__ import annotations

import pytest

from pact.schemas import (
    ComponentContract,
    ContractTestSuite,
    DecompositionNode,
    DecompositionTree,
    ErrorCase,
    FieldSpec,
    FunctionContract,
    TestCase,
    TypeSpec,
)
from pact.schemas_tasks import FindingCategory, FindingSeverity
from pact.analyzer import (
    analyze_project,
    render_analysis_markdown,
)


# ── Fixtures ────────────────────────────────────────────────────────


def _tree(*component_ids: str, root_children: list[str] | None = None) -> DecompositionTree:
    """Build a simple tree with root + optional children."""
    nodes = {}
    children = root_children or list(component_ids[1:]) if len(component_ids) > 1 else []
    nodes["root"] = DecompositionNode(
        component_id="root", name="Root", description="Root component",
        depth=0, children=children,
    )
    for cid in component_ids:
        if cid != "root":
            nodes[cid] = DecompositionNode(
                component_id=cid, name=cid.title(), description=f"{cid} component",
                depth=1, parent_id="root",
            )
    return DecompositionTree(root_id="root", nodes=nodes)


def _contract(
    cid: str,
    description: str = "A well-described component that does many useful things for the system",
    functions: list[FunctionContract] | None = None,
    types: list[str] | None = None,
    deps: list[str] | None = None,
) -> ComponentContract:
    return ComponentContract(
        component_id=cid,
        name=cid.title(),
        description=description,
        functions=functions or [
            FunctionContract(
                name="do_work",
                description="Does the work correctly and completely",
                inputs=[FieldSpec(name="x", type_ref="str")],
                output_type="bool",
            ),
        ],
        types=[TypeSpec(name=t, kind="struct") for t in (types or [])],
        dependencies=deps or [],
    )


def _suite(cid: str, functions: list[str] | None = None, categories: list[str] | None = None) -> ContractTestSuite:
    fns = functions or ["do_work"]
    cats = categories or ["happy_path"]
    cases = []
    for i, (fn, cat) in enumerate(zip(fns, cats)):
        cases.append(TestCase(id=f"t{i}", description="Test", function=fn, category=cat))
    return ContractTestSuite(component_id=cid, contract_version=1, test_cases=cases)


# ── Coverage ────────────────────────────────────────────────────────


class TestCoverageChecks:
    def test_missing_contract(self):
        tree = _tree("root", "auth")
        report = analyze_project(tree, {}, {})
        coverage_gaps = [f for f in report.findings if f.category == FindingCategory.coverage_gap]
        assert any("no contract" in f.description for f in coverage_gaps)

    def test_missing_test_suite(self):
        tree = _tree("root")
        contracts = {"root": _contract("root")}
        report = analyze_project(tree, contracts, {})
        coverage_gaps = [f for f in report.findings if f.category == FindingCategory.coverage_gap]
        assert any("no test suite" in f.description for f in coverage_gaps)

    def test_function_without_test(self):
        tree = _tree("root")
        contracts = {"root": _contract("root", functions=[
            FunctionContract(name="fn1", description="First", inputs=[], output_type="str"),
            FunctionContract(name="fn2", description="Second", inputs=[], output_type="str"),
        ])}
        suites = {"root": _suite("root", functions=["fn1"], categories=["happy_path"])}
        report = analyze_project(tree, contracts, suites)
        coverage_gaps = [f for f in report.findings if f.category == FindingCategory.coverage_gap]
        assert any("fn2" in f.description for f in coverage_gaps)

    def test_all_functions_covered(self):
        tree = _tree("root")
        contracts = {"root": _contract("root")}
        suites = {"root": _suite("root")}
        report = analyze_project(tree, contracts, suites)
        coverage_gaps = [
            f for f in report.findings
            if f.category == FindingCategory.coverage_gap and f.component_id == "root"
        ]
        assert coverage_gaps == []

    def test_missing_contract_is_error(self):
        tree = _tree("root")
        report = analyze_project(tree, {}, {})
        errors = report.errors
        assert any("no contract" in f.description for f in errors)

    def test_missing_suite_is_error(self):
        tree = _tree("root")
        contracts = {"root": _contract("root")}
        report = analyze_project(tree, contracts, {})
        errors = report.errors
        assert any("no test suite" in f.description for f in errors)

    def test_function_gap_is_warning(self):
        tree = _tree("root")
        contracts = {"root": _contract("root", functions=[
            FunctionContract(name="fn1", description="d", inputs=[], output_type="str"),
            FunctionContract(name="fn2", description="d", inputs=[], output_type="str"),
        ])}
        suites = {"root": _suite("root", functions=["fn1"], categories=["happy_path"])}
        report = analyze_project(tree, contracts, suites)
        fn_warnings = [f for f in report.warnings if "fn2" in f.description]
        assert len(fn_warnings) == 1


# ── Ambiguity ───────────────────────────────────────────────────────


class TestAmbiguityChecks:
    def test_short_description(self):
        tree = _tree("root")
        contracts = {"root": _contract("root", description="Too short")}
        suites = {"root": _suite("root")}
        report = analyze_project(tree, contracts, suites)
        ambiguity = [f for f in report.findings if f.category == FindingCategory.ambiguity]
        assert any("too short" in f.description.lower() for f in ambiguity)

    def test_vague_words(self):
        tree = _tree("root")
        contracts = {"root": _contract(
            "root",
            description="This component does various things and handles stuff etc for the system"
        )}
        suites = {"root": _suite("root")}
        report = analyze_project(tree, contracts, suites)
        ambiguity = [f for f in report.findings if f.category == FindingCategory.ambiguity]
        assert any("vague" in f.description.lower() for f in ambiguity)

    def test_clean_description_no_ambiguity(self):
        tree = _tree("root")
        contracts = {"root": _contract(
            "root",
            description="This component validates user authentication tokens against the identity provider using OAuth2 protocol flow"
        )}
        suites = {"root": _suite("root")}
        report = analyze_project(tree, contracts, suites)
        ambiguity = [f for f in report.findings if f.category == FindingCategory.ambiguity and f.component_id == "root"]
        assert ambiguity == []

    def test_vague_in_function_description(self):
        tree = _tree("root")
        contracts = {"root": _contract("root", functions=[
            FunctionContract(
                name="do_stuff",
                description="Does various things somehow",
                inputs=[], output_type="str",
            ),
        ])}
        suites = {"root": _suite("root", functions=["do_stuff"])}
        report = analyze_project(tree, contracts, suites)
        fn_ambiguity = [f for f in report.findings if f.category == FindingCategory.ambiguity and "do_stuff" in f.description]
        assert len(fn_ambiguity) >= 1

    def test_ambiguity_is_warning(self):
        tree = _tree("root")
        contracts = {"root": _contract("root", description="Short")}
        suites = {"root": _suite("root")}
        report = analyze_project(tree, contracts, suites)
        ambiguity = [f for f in report.findings if f.category == FindingCategory.ambiguity]
        assert all(f.severity in (FindingSeverity.warning, FindingSeverity.info) for f in ambiguity)


# ── Duplication ─────────────────────────────────────────────────────


class TestDuplicationChecks:
    def test_duplicate_type_names(self):
        tree = _tree("root", "auth", "db")
        contracts = {
            "root": _contract("root"),
            "auth": _contract("auth", types=["UserModel"]),
            "db": _contract("db", types=["UserModel"]),
        }
        suites = {cid: _suite(cid) for cid in contracts}
        report = analyze_project(tree, contracts, suites)
        duplication = [f for f in report.findings if f.category == FindingCategory.duplication]
        assert any("UserModel" in f.description for f in duplication)

    def test_no_duplicate_types(self):
        tree = _tree("root", "auth")
        contracts = {
            "root": _contract("root", types=["Config"]),
            "auth": _contract("auth", types=["Token"]),
        }
        suites = {cid: _suite(cid) for cid in contracts}
        report = analyze_project(tree, contracts, suites)
        duplication = [f for f in report.findings if f.category == FindingCategory.duplication and "type" in f.description.lower()]
        # Only type duplication, not function signature duplication
        type_dupes = [f for f in duplication if "Type" in f.description]
        assert type_dupes == []

    def test_similar_function_signatures(self):
        tree = _tree("root", "auth", "db")
        contracts = {
            "root": _contract("root"),
            "auth": _contract("auth", functions=[
                FunctionContract(name="validate", description="Validates input", inputs=[FieldSpec(name="x", type_ref="str")], output_type="bool"),
            ]),
            "db": _contract("db", functions=[
                FunctionContract(name="check", description="Checks input", inputs=[FieldSpec(name="y", type_ref="str")], output_type="bool"),
            ]),
        }
        suites = {cid: _suite(cid, functions=[c.functions[0].name]) for cid, c in contracts.items()}
        report = analyze_project(tree, contracts, suites)
        sig_dupes = [f for f in report.findings if f.category == FindingCategory.duplication and "signature" in f.description.lower()]
        assert len(sig_dupes) >= 1

    def test_duplicate_has_artifacts(self):
        tree = _tree("root", "auth", "db")
        contracts = {
            "root": _contract("root"),
            "auth": _contract("auth", types=["SharedType"]),
            "db": _contract("db", types=["SharedType"]),
        }
        suites = {cid: _suite(cid) for cid in contracts}
        report = analyze_project(tree, contracts, suites)
        dupes = [f for f in report.findings if "SharedType" in f.description]
        assert dupes and len(dupes[0].artifacts) == 2


# ── Consistency ─────────────────────────────────────────────────────


class TestConsistencyChecks:
    def test_missing_child_dependency(self):
        tree = _tree("root", "auth", "db", root_children=["auth", "db"])
        contracts = {
            "root": _contract("root", deps=[]),  # Missing deps on children
            "auth": _contract("auth"),
            "db": _contract("db"),
        }
        suites = {cid: _suite(cid) for cid in contracts}
        report = analyze_project(tree, contracts, suites)
        consistency = [f for f in report.findings if f.category == FindingCategory.consistency]
        assert any("missing dependencies" in f.description.lower() for f in consistency)

    def test_correct_dependencies(self):
        tree = _tree("root", "auth", "db", root_children=["auth", "db"])
        contracts = {
            "root": _contract("root", deps=["auth", "db"]),
            "auth": _contract("auth"),
            "db": _contract("db"),
        }
        suites = {cid: _suite(cid) for cid in contracts}
        report = analyze_project(tree, contracts, suites)
        consistency = [f for f in report.findings if f.category == FindingCategory.consistency and f.component_id == "root"]
        assert consistency == []

    def test_cross_boundary_type_ref_unknown(self):
        tree = _tree("root")
        contracts = {"root": _contract("root", functions=[
            FunctionContract(
                name="process",
                description="Processes input from unknown component reference",
                inputs=[FieldSpec(name="data", type_ref="unknown_comp.DataType")],
                output_type="bool",
            ),
        ])}
        suites = {"root": _suite("root", functions=["process"])}
        report = analyze_project(tree, contracts, suites)
        consistency = [f for f in report.findings if f.category == FindingCategory.consistency]
        assert any("unknown component" in f.description.lower() for f in consistency)

    def test_dependency_mismatch_is_error(self):
        tree = _tree("root", "auth", root_children=["auth"])
        contracts = {
            "root": _contract("root", deps=[]),
            "auth": _contract("auth"),
        }
        suites = {cid: _suite(cid) for cid in contracts}
        report = analyze_project(tree, contracts, suites)
        errors = report.errors
        assert any("missing dependencies" in f.description.lower() for f in errors)


# ── Clean Project ───────────────────────────────────────────────────


class TestCleanProject:
    def test_no_findings(self):
        tree = _tree("root")
        contracts = {"root": _contract("root")}
        suites = {"root": _suite("root")}
        report = analyze_project(tree, contracts, suites)
        assert report.errors == []
        # May still have warnings (e.g., function signature duplication)

    def test_summary_format(self):
        tree = _tree("root")
        contracts = {"root": _contract("root")}
        suites = {"root": _suite("root")}
        report = analyze_project(tree, contracts, suites)
        assert "error(s)" in report.summary
        assert "warning(s)" in report.summary
        assert "info(s)" in report.summary


# ── render_analysis_markdown ────────────────────────────────────────


class TestRenderAnalysisMarkdown:
    def test_header(self):
        tree = _tree("root")
        contracts = {"root": _contract("root")}
        suites = {"root": _suite("root")}
        report = analyze_project(tree, contracts, suites)
        md = render_analysis_markdown(report)
        assert "# Cross-Artifact Analysis" in md

    def test_empty_report(self):
        from pact.schemas_tasks import AnalysisReport
        report = AnalysisReport(project_id="test", summary="0 findings")
        md = render_analysis_markdown(report)
        assert "No findings" in md

    def test_findings_grouped_by_severity(self):
        tree = _tree("root", "auth")
        contracts = {"root": _contract("root", description="Short")}  # Will trigger ambiguity + coverage
        report = analyze_project(tree, contracts, {})
        md = render_analysis_markdown(report)
        # Should have at least ERRORS section (missing test suite)
        assert "ERRORS" in md or "WARNINGS" in md

    def test_finding_ids_shown(self):
        tree = _tree("root")
        report = analyze_project(tree, {}, {})
        md = render_analysis_markdown(report)
        assert "F001" in md

    def test_suggestions_shown(self):
        tree = _tree("root")
        report = analyze_project(tree, {}, {})
        md = render_analysis_markdown(report)
        assert "Suggestion:" in md
