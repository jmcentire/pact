"""Tests for all Pydantic models in schemas.py."""

from __future__ import annotations

import pytest

from pact.schemas import (
    ComponentContract,
    ComponentTask,
    Contingency,
    ContractTestSuite,
    DecompositionNode,
    DecompositionTree,
    DesignDocument,
    EngineeringDecision,
    EnvironmentCheck,
    ErrorCase,
    FailureRecord,
    FieldSpec,
    FunctionContract,
    GateResult,
    InterviewResult,
    IOTrace,
    LearningEntry,
    PlanEvaluation,
    PreflightPlan,
    RedLine,
    ResearchFinding,
    ResearchReport,
    RunState,
    TestCase,
    TestFailure,
    TestResults,
    TraceDiagnosis,
    TypeSpec,
    ValidatorSpec,
)


class TestValidatorSpec:
    def test_basic(self):
        v = ValidatorSpec(kind="range", expression="0 < x < 100")
        assert v.kind == "range"
        assert v.expression == "0 < x < 100"

    def test_with_error_message(self):
        v = ValidatorSpec(kind="regex", expression=r"^\d+$", error_message="Must be numeric")
        assert v.error_message == "Must be numeric"


class TestFieldSpec:
    def test_required_field(self):
        f = FieldSpec(name="price", type_ref="float")
        assert f.required is True
        assert f.default == ""

    def test_optional_field(self):
        f = FieldSpec(name="note", type_ref="str", required=False, default="''")
        assert f.required is False


class TestTypeSpec:
    def test_struct(self):
        t = TypeSpec(
            name="PriceResult",
            kind="struct",
            fields=[FieldSpec(name="amount", type_ref="float")],
            description="Price calculation result",
        )
        assert t.name == "PriceResult"
        assert len(t.fields) == 1

    def test_enum(self):
        t = TypeSpec(name="Status", kind="enum", variants=["active", "inactive"])
        assert len(t.variants) == 2

    def test_list(self):
        t = TypeSpec(name="Prices", kind="list", item_type="float")
        assert t.item_type == "float"


class TestFunctionContract:
    def test_basic(self):
        f = FunctionContract(
            name="calculate_price",
            description="Calculate price for a unit",
            inputs=[FieldSpec(name="unit_id", type_ref="str")],
            output_type="PriceResult",
        )
        assert f.name == "calculate_price"
        assert f.output_type == "PriceResult"

    def test_with_errors(self):
        f = FunctionContract(
            name="get_unit",
            description="Get unit by ID",
            inputs=[FieldSpec(name="id", type_ref="str")],
            output_type="Unit",
            error_cases=[ErrorCase(name="not_found", condition="id not in db", error_type="NotFoundError")],
            preconditions=["id is non-empty"],
            postconditions=["result.id == id"],
        )
        assert len(f.error_cases) == 1
        assert f.preconditions == ["id is non-empty"]


class TestComponentContract:
    def test_minimal(self):
        c = ComponentContract(
            component_id="pricing",
            name="Pricing Engine",
            description="Calculates prices",
        )
        assert c.component_id == "pricing"
        assert c.version == 1

    def test_with_dependencies(self):
        c = ComponentContract(
            component_id="checkout",
            name="Checkout",
            description="Checkout flow",
            dependencies=["pricing", "inventory"],
        )
        assert len(c.dependencies) == 2


class TestTestModels:
    def test_test_case(self):
        tc = TestCase(
            id="test_1",
            description="Happy path",
            function="calculate_price",
            category="happy_path",
            assertions=["result > 0"],
        )
        assert tc.category == "happy_path"

    def test_test_suite(self):
        suite = ContractTestSuite(
            component_id="pricing",
            contract_version=1,
            test_cases=[
                TestCase(id="t1", description="test", function="f", category="happy_path"),
            ],
            generated_code="def test_example(): pass",
        )
        assert len(suite.test_cases) == 1

    def test_test_results_all_passed(self):
        r = TestResults(total=5, passed=5, failed=0, errors=0)
        assert r.all_passed is True

    def test_test_results_with_failures(self):
        r = TestResults(total=5, passed=3, failed=2, errors=0)
        assert r.all_passed is False

    def test_test_results_empty(self):
        # empty (all skipped) = no failures = passes vacuously
        r = TestResults()
        assert r.all_passed is True


class TestInterviewResult:
    def test_basic(self):
        i = InterviewResult(
            risks=["scope creep"],
            ambiguities=["auth method unclear"],
            questions=["OAuth or JWT?"],
            assumptions=["Will use JWT"],
        )
        assert len(i.questions) == 1
        assert not i.approved

    def test_approved(self):
        i = InterviewResult(approved=True)
        assert i.approved


class TestResearchModels:
    def test_finding(self):
        f = ResearchFinding(
            topic="Error handling",
            finding="Use Result types",
            source="domain knowledge",
            relevance="Prevents exception propagation",
            confidence=0.9,
        )
        assert f.confidence == 0.9

    def test_report(self):
        r = ResearchReport(
            task_summary="Build pricing engine",
            findings=[
                ResearchFinding(
                    topic="t", finding="f", source="s",
                    relevance="r", confidence=0.8,
                ),
            ],
            recommended_approach="Use strategy pattern",
        )
        assert len(r.findings) == 1

    def test_plan_evaluation(self):
        p = PlanEvaluation(
            plan_summary="Implement with strategy pattern",
            decision="proceed",
        )
        assert p.decision == "proceed"

    def test_plan_evaluation_revise(self):
        p = PlanEvaluation(
            plan_summary="Initial plan",
            decision="revise",
            revision_notes="Need to handle edge case",
        )
        assert p.revision_notes != ""


class TestDecompositionModels:
    def test_node(self):
        n = DecompositionNode(
            component_id="pricing",
            name="Pricing Engine",
            description="Calculates prices",
        )
        assert n.implementation_status == "pending"
        assert n.depth == 0

    def test_tree_leaves(self):
        tree = DecompositionTree(
            root_id="root",
            nodes={
                "root": DecompositionNode(
                    component_id="root", name="Root",
                    description="Root", children=["a", "b"],
                ),
                "a": DecompositionNode(
                    component_id="a", name="A",
                    description="A", parent_id="root",
                ),
                "b": DecompositionNode(
                    component_id="b", name="B",
                    description="B", parent_id="root",
                ),
            },
        )
        leaves = tree.leaves()
        assert len(leaves) == 2
        assert {l.component_id for l in leaves} == {"a", "b"}

    def test_tree_topological_order(self):
        tree = DecompositionTree(
            root_id="root",
            nodes={
                "root": DecompositionNode(
                    component_id="root", name="Root",
                    description="Root", children=["a", "b"],
                ),
                "a": DecompositionNode(
                    component_id="a", name="A",
                    description="A", parent_id="root",
                ),
                "b": DecompositionNode(
                    component_id="b", name="B",
                    description="B", parent_id="root",
                ),
            },
        )
        order = tree.topological_order()
        assert order[-1] == "root"
        assert set(order) == {"root", "a", "b"}

    def test_tree_children_of(self):
        tree = DecompositionTree(
            root_id="root",
            nodes={
                "root": DecompositionNode(
                    component_id="root", name="Root",
                    description="Root", children=["a"],
                ),
                "a": DecompositionNode(
                    component_id="a", name="A",
                    description="A", parent_id="root",
                ),
            },
        )
        children = tree.children_of("root")
        assert len(children) == 1
        assert children[0].component_id == "a"

    def test_tree_parent_of(self):
        tree = DecompositionTree(
            root_id="root",
            nodes={
                "root": DecompositionNode(
                    component_id="root", name="Root",
                    description="Root", children=["a"],
                ),
                "a": DecompositionNode(
                    component_id="a", name="A",
                    description="A", parent_id="root",
                ),
            },
        )
        parent = tree.parent_of("a")
        assert parent is not None
        assert parent.component_id == "root"
        assert tree.parent_of("root") is None


class TestIOTrace:
    def test_basic(self):
        t = IOTrace(
            component_id="pricing",
            function="calculate",
            inputs={"unit_id": "123"},
            output="100.0",
        )
        assert t.duration_ms == 0.0

    def test_nested(self):
        t = IOTrace(
            component_id="checkout",
            function="process",
            sub_traces=[
                IOTrace(component_id="pricing", function="calc"),
                IOTrace(component_id="inventory", function="check"),
            ],
        )
        assert len(t.sub_traces) == 2


class TestRunState:
    def test_create(self):
        s = RunState(id="abc123", project_dir="/tmp/test")
        assert s.status == "active"
        assert s.phase == "interview"

    def test_pause(self):
        s = RunState(id="abc123", project_dir="/tmp/test")
        s.pause("waiting for user")
        assert s.status == "paused"

    def test_fail(self):
        s = RunState(id="abc123", project_dir="/tmp/test")
        s.fail("unrecoverable")
        assert s.status == "failed"
        assert s.completed_at != ""

    def test_complete(self):
        s = RunState(id="abc123", project_dir="/tmp/test")
        s.complete()
        assert s.status == "completed"

    def test_record_tokens(self):
        s = RunState(id="abc123", project_dir="/tmp/test")
        s.record_tokens(1000, 500, 0.05)
        assert s.total_tokens == 1500
        assert s.total_cost_usd == 0.05


class TestHealthSnapshot:
    def test_default_empty_dict(self):
        s = RunState(id="test", project_dir="/tmp/t")
        assert s.health_snapshot == {}

    def test_roundtrip_json(self):
        s = RunState(
            id="test", project_dir="/tmp/t",
            health_snapshot={
                "planning_tokens": 1000,
                "generation_tokens": 2000,
                "cascade_events": 1,
            },
        )
        data = s.model_dump_json()
        restored = RunState.model_validate_json(data)
        assert restored.health_snapshot["planning_tokens"] == 1000
        assert restored.health_snapshot["cascade_events"] == 1

    def test_empty_snapshot_roundtrip(self):
        s = RunState(id="test", project_dir="/tmp/t", health_snapshot={})
        data = s.model_dump_json()
        restored = RunState.model_validate_json(data)
        assert restored.health_snapshot == {}


class TestGateResult:
    def test_passed(self):
        g = GateResult(passed=True, reason="All good")
        assert g.passed

    def test_failed_with_details(self):
        g = GateResult(passed=False, reason="Errors", details=["err1", "err2"])
        assert not g.passed
        assert len(g.details) == 2


class TestDesignDocument:
    def test_basic(self):
        d = DesignDocument(
            project_id="test",
            title="Test Design",
        )
        assert d.version == 1

    def test_with_history(self):
        d = DesignDocument(
            project_id="test",
            title="Test Design",
            failure_history=[
                FailureRecord(
                    component_id="a",
                    failure_type="implementation_bug",
                    description="Failed tests",
                ),
            ],
        )
        assert len(d.failure_history) == 1


class TestPreflightModels:
    def test_red_line_basic(self):
        r = RedLine(rule="Do not delete tests")
        assert r.rule == "Do not delete tests"
        assert r.action_on_violation == "stop_and_report"

    def test_red_line_with_rationale(self):
        r = RedLine(
            rule="No eval()",
            rationale="Security risk",
            action_on_violation="use_registry_pattern",
        )
        assert r.rationale == "Security risk"

    def test_contingency_basic(self):
        c = Contingency(
            trigger="Tests fail after 3 iterations",
            response="Stop and report",
        )
        assert c.trigger.startswith("Tests fail")
        assert c.learned_from == ""

    def test_contingency_from_kindex(self):
        c = Contingency(
            trigger="Import resolution failure",
            response="Check conftest.py wiring",
            learned_from="kindex",
        )
        assert c.learned_from == "kindex"

    def test_environment_check(self):
        e = EnvironmentCheck(
            check="src dir writable",
            passed=True,
        )
        assert e.passed is True

    def test_environment_check_failed(self):
        e = EnvironmentCheck(
            check="src dir writable",
            passed=False,
            detail="Permission denied",
        )
        assert e.passed is False
        assert "Permission" in e.detail

    def test_preflight_plan_basic(self):
        plan = PreflightPlan(
            component_id="schemas",
            red_lines=[RedLine(rule="No test deletion")],
            contingencies=[Contingency(trigger="fail", response="stop")],
            environment_checks=[EnvironmentCheck(check="writable", passed=True)],
        )
        assert plan.component_id == "schemas"
        assert len(plan.red_lines) == 1
        assert len(plan.contingencies) == 1

    def test_preflight_plan_empty(self):
        plan = PreflightPlan(component_id="empty")
        assert plan.red_lines == []
        assert plan.contingencies == []
        assert plan.kindex_lessons == []

    def test_preflight_plan_with_kindex_lessons(self):
        plan = PreflightPlan(
            component_id="circuit",
            kindex_lessons=[
                "Previous run: subprocess spawning failed in Claude Code context",
                "Use registry dict instead of eval() for dynamic dispatch",
            ],
        )
        assert len(plan.kindex_lessons) == 2

    def test_preflight_plan_serialization_roundtrip(self):
        plan = PreflightPlan(
            component_id="test",
            red_lines=[RedLine(rule="r1"), RedLine(rule="r2")],
            contingencies=[Contingency(trigger="t", response="r")],
            environment_checks=[EnvironmentCheck(check="c", passed=True)],
            kindex_lessons=["lesson1"],
            created_at="2026-03-22T12:00:00",
        )
        json_str = plan.model_dump_json()
        restored = PreflightPlan.model_validate_json(json_str)
        assert restored.component_id == "test"
        assert len(restored.red_lines) == 2
        assert restored.kindex_lessons == ["lesson1"]
