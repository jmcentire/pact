"""Tests for the architectural assessment engine."""

from __future__ import annotations

from pathlib import Path

import pytest

from pact.assessor import (
    DEFAULT_THRESHOLDS,
    _build_import_graph,
    _check_hub_dependencies,
    _check_shallow_modules,
    _check_test_gaps,
    _check_tight_coupling,
    _check_scattered_logic,
    _compute_fan_metrics,
    _count_loc,
    _discover_modules,
    _find_sccs,
    _parse_module,
    assess_codebase,
    render_assessment_markdown,
)
from pact.schemas_assess import (
    AssessmentCategory,
    AssessmentFinding,
    AssessmentReport,
    ModuleMetrics,
)
from pact.schemas_tasks import FindingSeverity


# ── Helpers ────────────────────────────────────────────────────────


def _make_py(tmp_path: Path, name: str, content: str) -> Path:
    """Create a Python file in tmp_path and return its path."""
    f = tmp_path / name
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(content)
    return f


# ── Module Discovery ───────────────────────────────────────────────


class TestDiscoverModules:
    def test_finds_python_files(self, tmp_path: Path):
        _make_py(tmp_path, "foo.py", "x = 1")
        _make_py(tmp_path, "bar.py", "y = 2")
        result = _discover_modules(tmp_path)
        names = {p.name for p in result}
        assert "foo.py" in names
        assert "bar.py" in names

    def test_skips_pycache(self, tmp_path: Path):
        _make_py(tmp_path, "good.py", "x = 1")
        _make_py(tmp_path, "__pycache__/bad.py", "y = 2")
        result = _discover_modules(tmp_path)
        names = {p.name for p in result}
        assert "good.py" in names
        assert "bad.py" not in names

    def test_skips_empty_init(self, tmp_path: Path):
        _make_py(tmp_path, "__init__.py", "")
        _make_py(tmp_path, "real.py", "x = 1")
        result = _discover_modules(tmp_path)
        names = {p.name for p in result}
        assert "real.py" in names
        assert "__init__.py" not in names

    def test_keeps_substantial_init(self, tmp_path: Path):
        _make_py(tmp_path, "__init__.py", "# big init\n" * 10 + "x = 1\n")
        result = _discover_modules(tmp_path)
        names = {p.name for p in result}
        assert "__init__.py" in names


# ── Module Parsing ─────────────────────────────────────────────────


class TestParseModule:
    def test_counts_public_functions(self, tmp_path: Path):
        f = _make_py(tmp_path, "mod.py", "def foo(): pass\ndef bar(): pass\ndef _private(): pass\n")
        m = _parse_module(f, tmp_path)
        assert m.public_functions == 2

    def test_counts_async_functions(self, tmp_path: Path):
        f = _make_py(tmp_path, "mod.py", "async def fetch(): pass\nasync def _internal(): pass\n")
        m = _parse_module(f, tmp_path)
        assert m.public_functions == 1

    def test_counts_classes(self, tmp_path: Path):
        f = _make_py(tmp_path, "mod.py", "class Foo: pass\nclass _Bar: pass\n")
        m = _parse_module(f, tmp_path)
        assert m.public_classes == 1

    def test_computes_depth_ratio(self, tmp_path: Path):
        # 10 lines, 2 public names -> depth 5.0
        code = "\n".join(f"x{i} = {i}" for i in range(10))
        code += "\ndef foo(): pass\ndef bar(): pass\n"
        f = _make_py(tmp_path, "mod.py", code)
        m = _parse_module(f, tmp_path)
        assert m.interface_size == 2
        assert m.depth_ratio == m.loc / 2

    def test_handles_empty_file(self, tmp_path: Path):
        f = _make_py(tmp_path, "empty.py", "")
        m = _parse_module(f, tmp_path)
        assert m.loc == 0
        assert m.interface_size == 0

    def test_handles_syntax_error(self, tmp_path: Path):
        f = _make_py(tmp_path, "bad.py", "def broken(:\n")
        m = _parse_module(f, tmp_path)
        assert m.loc > 0  # LOC still counted
        assert m.interface_size == 0  # No AST parsed


class TestCountLoc:
    def test_counts_code_lines(self):
        assert _count_loc("x = 1\ny = 2\n") == 2

    def test_ignores_comments(self):
        assert _count_loc("# comment\nx = 1\n") == 1

    def test_ignores_blank_lines(self):
        assert _count_loc("x = 1\n\n\ny = 2\n") == 2


# ── Import Graph ───────────────────────────────────────────────────


class TestImportGraph:
    def test_detects_intra_project_imports(self, tmp_path: Path):
        pkg = tmp_path.name  # Use the actual tmp dir name as package
        _make_py(tmp_path, "alpha.py", f"from {pkg}.beta import x\n")
        _make_py(tmp_path, "beta.py", "x = 1\n")
        modules = _discover_modules(tmp_path)
        metrics = {str(p.relative_to(tmp_path)): _parse_module(p, tmp_path) for p in modules}
        graph = _build_import_graph(tmp_path, modules, metrics)
        # alpha.py should import beta.py
        assert "beta.py" in graph.get("alpha.py", set())

    def test_ignores_external_imports(self, tmp_path: Path):
        _make_py(tmp_path, "mod.py", "import os\nfrom pathlib import Path\n")
        modules = _discover_modules(tmp_path)
        metrics = {str(p.relative_to(tmp_path)): _parse_module(p, tmp_path) for p in modules}
        graph = _build_import_graph(tmp_path, modules, metrics)
        assert graph.get("mod.py", set()) == set()


# ── Fan Metrics ────────────────────────────────────────────────────


class TestFanMetrics:
    def test_computes_fan_in_out(self, tmp_path: Path):
        m_a = ModuleMetrics(path="a.py", loc=10)
        m_b = ModuleMetrics(path="b.py", loc=10)
        m_c = ModuleMetrics(path="c.py", loc=10)
        metrics = {"a.py": m_a, "b.py": m_b, "c.py": m_c}
        graph = {"a.py": {"b.py", "c.py"}, "b.py": {"c.py"}, "c.py": set()}

        _compute_fan_metrics(graph, metrics)

        assert m_a.fan_out == 2
        assert m_a.fan_in == 0
        assert m_b.fan_out == 1
        assert m_b.fan_in == 1
        assert m_c.fan_out == 0
        assert m_c.fan_in == 2


# ── Check: Shallow Modules ────────────────────────────────────────


class TestCheckShallowModules:
    def test_flags_shallow_module(self):
        metrics = {
            "thin.py": ModuleMetrics(
                path="thin.py", loc=15, public_functions=5,
                public_classes=0, interface_size=5, depth_ratio=3.0,
            ),
        }
        counter = [0]
        findings = _check_shallow_modules(metrics, counter, DEFAULT_THRESHOLDS)
        assert len(findings) == 1
        assert findings[0].category == AssessmentCategory.shallow_module
        assert findings[0].severity == FindingSeverity.warning

    def test_does_not_flag_deep_module(self):
        metrics = {
            "deep.py": ModuleMetrics(
                path="deep.py", loc=500, public_functions=3,
                public_classes=0, interface_size=3, depth_ratio=166.7,
            ),
        }
        counter = [0]
        findings = _check_shallow_modules(metrics, counter, DEFAULT_THRESHOLDS)
        assert len(findings) == 0

    def test_ignores_tiny_interface(self):
        metrics = {
            "small.py": ModuleMetrics(
                path="small.py", loc=5, public_functions=1,
                public_classes=0, interface_size=1, depth_ratio=5.0,
            ),
        }
        counter = [0]
        findings = _check_shallow_modules(metrics, counter, DEFAULT_THRESHOLDS)
        assert len(findings) == 0

    def test_respects_threshold_override(self):
        metrics = {
            "mod.py": ModuleMetrics(
                path="mod.py", loc=20, public_functions=4,
                public_classes=0, interface_size=4, depth_ratio=5.0,
            ),
        }
        counter = [0]
        # Default threshold is 5.0, so depth_ratio=5.0 is NOT flagged
        assert len(_check_shallow_modules(metrics, counter, DEFAULT_THRESHOLDS)) == 0
        # But with threshold 6.0, it IS flagged
        counter = [0]
        assert len(_check_shallow_modules(
            metrics, counter, {**DEFAULT_THRESHOLDS, "shallow_depth_ratio": 6.0},
        )) == 1


# ── Check: Hub Dependencies ───────────────────────────────────────


class TestCheckHubDependencies:
    def test_flags_high_fan_in_as_warning(self):
        metrics = {
            "hub.py": ModuleMetrics(path="hub.py", loc=100, fan_in=9),
        }
        counter = [0]
        findings = _check_hub_dependencies(metrics, counter, DEFAULT_THRESHOLDS)
        assert len(findings) == 1
        assert findings[0].severity == FindingSeverity.warning

    def test_flags_very_high_fan_in_as_error(self):
        metrics = {
            "hub.py": ModuleMetrics(path="hub.py", loc=100, fan_in=16),
        }
        counter = [0]
        findings = _check_hub_dependencies(metrics, counter, DEFAULT_THRESHOLDS)
        assert len(findings) == 1
        assert findings[0].severity == FindingSeverity.error

    def test_no_finding_below_threshold(self):
        metrics = {
            "leaf.py": ModuleMetrics(path="leaf.py", loc=100, fan_in=3),
        }
        counter = [0]
        findings = _check_hub_dependencies(metrics, counter, DEFAULT_THRESHOLDS)
        assert len(findings) == 0


# ── Check: Tight Coupling ─────────────────────────────────────────


class TestCheckTightCoupling:
    def test_detects_mutual_imports(self):
        graph = {"a.py": {"b.py"}, "b.py": {"a.py"}}
        counter = [0]
        findings = _check_tight_coupling(graph, counter, DEFAULT_THRESHOLDS)
        assert len(findings) == 1
        assert findings[0].category == AssessmentCategory.tight_coupling
        assert "a.py" in findings[0].module_path or "b.py" in findings[0].module_path

    def test_no_false_positive_for_one_way(self):
        graph = {"a.py": {"b.py"}, "b.py": set()}
        counter = [0]
        findings = _check_tight_coupling(graph, counter, DEFAULT_THRESHOLDS)
        assert len(findings) == 0


class TestFindSCCs:
    def test_finds_cycle_of_three(self):
        graph = {"a": {"b"}, "b": {"c"}, "c": {"a"}}
        sccs = _find_sccs(graph, {"a", "b", "c"})
        assert len(sccs) == 1
        assert set(sccs[0]) == {"a", "b", "c"}

    def test_no_scc_in_dag(self):
        graph = {"a": {"b"}, "b": {"c"}, "c": set()}
        sccs = _find_sccs(graph, {"a", "b", "c"})
        assert len(sccs) == 0

    def test_multiple_sccs(self):
        graph = {"a": {"b"}, "b": {"a"}, "c": {"d"}, "d": {"c"}}
        sccs = _find_sccs(graph, {"a", "b", "c", "d"})
        assert len(sccs) == 2


# ── Check: Test Gaps ──────────────────────────────────────────────


class TestCheckTestGaps:
    def test_flags_missing_test(self, tmp_path: Path):
        src = tmp_path / "src"
        src.mkdir()
        f = _make_py(src, "mymod.py", "x = 1\n")
        modules = [f]
        counter = [0]
        findings = _check_test_gaps(src, modules, counter, DEFAULT_THRESHOLDS)
        assert len(findings) == 1
        assert findings[0].category == AssessmentCategory.test_gap

    def test_recognizes_existing_test(self, tmp_path: Path):
        src = tmp_path / "src"
        src.mkdir()
        tests = tmp_path / "tests"
        tests.mkdir()
        f = _make_py(src, "mymod.py", "x = 1\n")
        _make_py(tests, "test_mymod.py", "def test_x(): pass\n")
        modules = [f]
        counter = [0]
        findings = _check_test_gaps(src, modules, counter, DEFAULT_THRESHOLDS)
        assert len(findings) == 0


# ── Check: Scattered Logic ────────────────────────────────────────


class TestCheckScatteredLogic:
    def test_flags_widely_imported_name(self, tmp_path: Path):
        # Create 6 files all importing from the same intra-project module
        _make_py(tmp_path, "core.py", "x = 1\n")
        for i in range(6):
            _make_py(tmp_path, f"mod{i}.py", f"from {tmp_path.name}.core import x\n")
        modules = _discover_modules(tmp_path)
        known = {tmp_path.name}
        counter = [0]
        findings = _check_scattered_logic(tmp_path, modules, known, counter, DEFAULT_THRESHOLDS)
        # Should find at least one scattered_logic finding
        scattered = [f for f in findings if f.category == AssessmentCategory.scattered_logic]
        assert len(scattered) >= 1

    def test_ignores_below_threshold(self, tmp_path: Path):
        _make_py(tmp_path, "core.py", "x = 1\n")
        _make_py(tmp_path, "mod1.py", f"from {tmp_path.name}.core import x\n")
        _make_py(tmp_path, "mod2.py", f"from {tmp_path.name}.core import x\n")
        modules = _discover_modules(tmp_path)
        known = {tmp_path.name}
        counter = [0]
        findings = _check_scattered_logic(tmp_path, modules, known, counter, DEFAULT_THRESHOLDS)
        assert len(findings) == 0


# ── Integration ────────────────────────────────────────────────────


class TestAssessCodebase:
    def test_assess_synthetic_project(self, tmp_path: Path):
        """Build a mini project with known friction and verify detection."""
        src = tmp_path / "myproject"
        src.mkdir()

        # Hub module imported by many
        _make_py(src, "hub.py", "class Hub:\n    pass\n")
        for i in range(10):
            _make_py(src, f"user{i}.py", f"from {src.name}.hub import Hub\nx{i} = Hub()\n")

        # Mutual import pair
        _make_py(src, "alpha.py", f"from {src.name}.beta import B\nclass A: pass\n")
        _make_py(src, "beta.py", f"from {src.name}.alpha import A\nclass B: pass\n")

        report = assess_codebase(src)
        assert report.root_path == str(src.resolve())
        assert len(report.module_metrics) > 0
        assert len(report.findings) > 0

        categories = {f.category for f in report.findings}
        assert AssessmentCategory.hub_dependency in categories
        assert AssessmentCategory.tight_coupling in categories

    def test_assess_empty_dir(self, tmp_path: Path):
        report = assess_codebase(tmp_path)
        assert "No Python modules found" in report.summary

    def test_assess_pact_itself(self):
        """Dogfood: run assess on pact's own source tree."""
        pact_src = Path(__file__).parent.parent / "src" / "pact"
        if not pact_src.exists():
            pytest.skip("Pact source not found at expected path")

        report = assess_codebase(pact_src)
        assert report.summary
        assert len(report.module_metrics) > 30
        # schemas.py should be flagged as a hub
        hub_findings = [
            f for f in report.findings
            if f.category == AssessmentCategory.hub_dependency
            and "schemas.py" in f.module_path
        ]
        assert len(hub_findings) >= 1


# ── Rendering ──────────────────────────────────────────────────────


class TestRenderMarkdown:
    def test_header_format(self):
        report = AssessmentReport(root_path="/tmp/test", summary="0 modules.")
        md = render_assessment_markdown(report)
        assert "# Architectural Assessment:" in md

    def test_empty_report(self):
        report = AssessmentReport(root_path="/tmp/test", summary="0 modules.")
        md = render_assessment_markdown(report)
        assert "No architectural findings" in md

    def test_findings_grouped_by_severity(self):
        report = AssessmentReport(
            root_path="/tmp/test",
            summary="test",
            findings=[
                AssessmentFinding(
                    id="A001", severity=FindingSeverity.error,
                    category=AssessmentCategory.hub_dependency,
                    description="Error finding",
                ),
                AssessmentFinding(
                    id="A002", severity=FindingSeverity.warning,
                    category=AssessmentCategory.shallow_module,
                    description="Warning finding",
                ),
            ],
        )
        md = render_assessment_markdown(report)
        assert "## Errors (1)" in md
        assert "## Warnings (1)" in md
        # Error should appear before warning
        error_pos = md.index("Error finding")
        warn_pos = md.index("Warning finding")
        assert error_pos < warn_pos
