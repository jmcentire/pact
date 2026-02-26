"""Tests for test-gen pipeline schemas."""

from __future__ import annotations

import json

from pact.schemas_testgen import (
    CodebaseAnalysis,
    CoverageEntry,
    CoverageMap,
    ExtractedFunction,
    ExtractedParameter,
    SecurityAuditReport,
    SecurityFinding,
    SecurityRiskLevel,
    SourceFile,
    TestFile,
    TestGenPlan,
    TestGenPlanEntry,
    TestGenResult,
)


# ── ExtractedParameter ─────────────────────────────────────────────


class TestExtractedParameter:
    def test_defaults(self):
        p = ExtractedParameter(name="x")
        assert p.name == "x"
        assert p.type_annotation == ""
        assert p.default == ""

    def test_all_fields(self):
        p = ExtractedParameter(name="count", type_annotation="int", default="0")
        assert p.type_annotation == "int"
        assert p.default == "0"

    def test_json_roundtrip(self):
        p = ExtractedParameter(name="x", type_annotation="str", default="''")
        data = json.loads(p.model_dump_json())
        p2 = ExtractedParameter.model_validate(data)
        assert p2.name == "x"
        assert p2.type_annotation == "str"


# ── ExtractedFunction ──────────────────────────────────────────────


class TestExtractedFunction:
    def test_defaults(self):
        f = ExtractedFunction(name="foo")
        assert f.name == "foo"
        assert f.params == []
        assert f.return_type == ""
        assert f.complexity == 1
        assert f.line_number == 0
        assert f.is_async is False
        assert f.is_method is False
        assert f.class_name == ""
        assert f.decorators == []
        assert f.docstring == ""

    def test_all_fields(self):
        f = ExtractedFunction(
            name="process",
            params=[ExtractedParameter(name="data", type_annotation="dict")],
            return_type="bool",
            complexity=5,
            body_source="def process(data): ...",
            line_number=42,
            is_async=True,
            is_method=True,
            class_name="Handler",
            decorators=["staticmethod"],
            docstring="Process data.",
        )
        assert f.complexity == 5
        assert f.is_async is True
        assert len(f.params) == 1

    def test_json_roundtrip(self):
        f = ExtractedFunction(name="foo", complexity=3, line_number=10)
        data = json.loads(f.model_dump_json())
        f2 = ExtractedFunction.model_validate(data)
        assert f2.complexity == 3


# ── SourceFile ─────────────────────────────────────────────────────


class TestSourceFile:
    def test_defaults(self):
        sf = SourceFile(path="src/auth.py")
        assert sf.path == "src/auth.py"
        assert sf.functions == []
        assert sf.imports == []
        assert sf.classes == []

    def test_with_functions(self):
        sf = SourceFile(
            path="src/auth.py",
            functions=[ExtractedFunction(name="login")],
            imports=["os", "sys"],
            classes=["AuthHandler"],
        )
        assert len(sf.functions) == 1
        assert len(sf.imports) == 2


# ── TestFile ───────────────────────────────────────────────────────


class TestTestFile:
    def test_defaults(self):
        tf = TestFile(path="tests/test_auth.py")
        assert tf.path == "tests/test_auth.py"
        assert tf.test_functions == []
        assert tf.imported_modules == []

    def test_with_data(self):
        tf = TestFile(
            path="tests/test_auth.py",
            test_functions=["test_login", "test_logout"],
            imported_modules=["src.auth"],
            referenced_names=["login", "logout"],
        )
        assert len(tf.test_functions) == 2


# ── CoverageMap ────────────────────────────────────────────────────


class TestCoverageMap:
    def test_empty(self):
        cm = CoverageMap()
        assert cm.coverage_ratio == 0.0
        assert cm.total_functions == 0
        assert cm.covered_count == 0
        assert cm.uncovered_count == 0

    def test_partial_coverage(self):
        cm = CoverageMap(entries=[
            CoverageEntry(function_name="a", file_path="f.py", covered=True, test_count=1),
            CoverageEntry(function_name="b", file_path="f.py", covered=False),
            CoverageEntry(function_name="c", file_path="f.py", covered=True, test_count=2),
            CoverageEntry(function_name="d", file_path="f.py", covered=False),
        ])
        assert cm.coverage_ratio == 0.5
        assert cm.total_functions == 4
        assert cm.covered_count == 2
        assert cm.uncovered_count == 2

    def test_full_coverage(self):
        cm = CoverageMap(entries=[
            CoverageEntry(function_name="a", file_path="f.py", covered=True, test_count=1),
        ])
        assert cm.coverage_ratio == 1.0

    def test_json_roundtrip(self):
        cm = CoverageMap(entries=[
            CoverageEntry(function_name="a", file_path="f.py", complexity=3, covered=True, test_count=1),
        ])
        data = json.loads(cm.model_dump_json())
        cm2 = CoverageMap.model_validate(data)
        assert cm2.entries[0].function_name == "a"
        assert cm2.entries[0].complexity == 3


# ── SecurityRiskLevel ──────────────────────────────────────────────


class TestSecurityRiskLevel:
    def test_all_values(self):
        assert SecurityRiskLevel.critical == "critical"
        assert SecurityRiskLevel.high == "high"
        assert SecurityRiskLevel.medium == "medium"
        assert SecurityRiskLevel.low == "low"
        assert SecurityRiskLevel.info == "info"

    def test_is_str(self):
        assert isinstance(SecurityRiskLevel.critical, str)


# ── SecurityFinding ────────────────────────────────────────────────


class TestSecurityFinding:
    def test_defaults(self):
        f = SecurityFinding(function_name="check_auth", file_path="auth.py")
        assert f.risk_level == SecurityRiskLevel.medium
        assert f.pattern_matched == ""
        assert f.suggestion == ""
        assert f.covered is False

    def test_all_fields(self):
        f = SecurityFinding(
            function_name="grant_access",
            file_path="auth.py",
            line_number=42,
            complexity=8,
            risk_level=SecurityRiskLevel.critical,
            pattern_matched="variable: is_admin",
            suggestion="Test both admin and non-admin paths",
            covered=False,
        )
        assert f.risk_level == SecurityRiskLevel.critical
        assert f.complexity == 8


# ── SecurityAuditReport ───────────────────────────────────────────


class TestSecurityAuditReport:
    def test_empty(self):
        r = SecurityAuditReport()
        assert r.critical_count == 0
        assert r.high_count == 0
        assert r.medium_count == 0
        assert r.low_count == 0
        assert r.info_count == 0

    def test_counts(self):
        r = SecurityAuditReport(findings=[
            SecurityFinding(function_name="a", file_path="f.py", risk_level=SecurityRiskLevel.critical),
            SecurityFinding(function_name="b", file_path="f.py", risk_level=SecurityRiskLevel.critical),
            SecurityFinding(function_name="c", file_path="f.py", risk_level=SecurityRiskLevel.high),
            SecurityFinding(function_name="d", file_path="f.py", risk_level=SecurityRiskLevel.low),
        ])
        assert r.critical_count == 2
        assert r.high_count == 1
        assert r.low_count == 1
        assert r.medium_count == 0

    def test_analyzed_at_auto(self):
        r = SecurityAuditReport()
        assert r.analyzed_at  # Non-empty


# ── CodebaseAnalysis ───────────────────────────────────────────────


class TestCodebaseAnalysis:
    def test_defaults(self):
        a = CodebaseAnalysis(root_path="/tmp/test")
        assert a.root_path == "/tmp/test"
        assert a.language == "python"
        assert a.total_functions == 0
        assert a.total_source_files == 0
        assert a.total_test_files == 0

    def test_with_data(self):
        a = CodebaseAnalysis(
            root_path="/tmp/test",
            source_files=[
                SourceFile(path="a.py", functions=[
                    ExtractedFunction(name="foo"),
                    ExtractedFunction(name="bar"),
                ]),
                SourceFile(path="b.py", functions=[
                    ExtractedFunction(name="baz"),
                ]),
            ],
            test_files=[TestFile(path="test_a.py")],
        )
        assert a.total_functions == 3
        assert a.total_source_files == 2
        assert a.total_test_files == 1


# ── TestGenPlan ────────────────────────────────────────────────────


class TestTestGenPlan:
    def test_empty(self):
        p = TestGenPlan()
        assert p.total == 0
        assert p.security_sensitive_count == 0

    def test_with_entries(self):
        p = TestGenPlan(entries=[
            TestGenPlanEntry(function_name="login", file_path="auth.py", security_sensitive=True, priority=1),
            TestGenPlanEntry(function_name="render", file_path="ui.py", priority=2),
            TestGenPlanEntry(function_name="grant_admin", file_path="auth.py", security_sensitive=True, priority=0),
        ])
        assert p.total == 3
        assert p.security_sensitive_count == 2

    def test_generated_at_auto(self):
        p = TestGenPlan()
        assert p.generated_at


# ── TestGenResult ──────────────────────────────────────────────────


class TestTestGenResult:
    def test_defaults(self):
        r = TestGenResult()
        assert r.contracts_generated == 0
        assert r.tests_generated == 0
        assert r.security_findings == 0
        assert r.coverage_before == 0.0
        assert r.output_path == ""
        assert r.total_cost_usd == 0.0
        assert r.dry_run is False

    def test_json_roundtrip(self):
        r = TestGenResult(
            contracts_generated=3,
            tests_generated=3,
            security_findings=1,
            coverage_before=0.45,
            output_path=".pact/test-gen/",
            total_cost_usd=1.23,
        )
        data = json.loads(r.model_dump_json())
        r2 = TestGenResult.model_validate(data)
        assert r2.contracts_generated == 3
        assert r2.total_cost_usd == 1.23
