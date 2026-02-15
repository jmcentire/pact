"""Tests for research cache persistence (P1-3)."""
import pytest
from pathlib import Path

from pact.research_cache import (
    cache_key,
    context_hash,
    save_research,
    load_research,
    invalidate,
)
from pact.schemas import ResearchReport, ResearchFinding


def _make_report() -> ResearchReport:
    return ResearchReport(
        task_summary="Build a pricing engine",
        findings=[
            ResearchFinding(
                topic="testing",
                finding="Use pytest",
                source="best practices",
                relevance="high",
                confidence=0.9,
            ),
            ResearchFinding(
                topic="patterns",
                finding="Use factory pattern",
                source="design patterns",
                relevance="medium",
                confidence=0.8,
            ),
        ],
        recommended_approach="Start with interfaces",
    )


class TestCacheKey:
    def test_deterministic(self):
        k1 = cache_key("comp_a", "contract_author", "abc123def456")
        k2 = cache_key("comp_a", "contract_author", "abc123def456")
        assert k1 == k2

    def test_different_role_different_key(self):
        k1 = cache_key("comp_a", "contract_author", "abc123")
        k2 = cache_key("comp_a", "test_author", "abc123")
        assert k1 != k2

    def test_different_hash_different_key(self):
        k1 = cache_key("comp_a", "contract_author", "abc123")
        k2 = cache_key("comp_a", "contract_author", "def456")
        assert k1 != k2

    def test_truncates_hash(self):
        long_hash = "a" * 64
        key = cache_key("comp_a", "role", long_hash)
        assert len(key.split("__")[2]) == 16


class TestContextHash:
    def test_deterministic(self):
        h1 = context_hash("Build pricing engine", ["dep_a"], "Use TDD")
        h2 = context_hash("Build pricing engine", ["dep_a"], "Use TDD")
        assert h1 == h2

    def test_different_desc_different_hash(self):
        h1 = context_hash("Build pricing engine", ["dep_a"], "Use TDD")
        h2 = context_hash("Build payment engine", ["dep_a"], "Use TDD")
        assert h1 != h2

    def test_different_deps_different_hash(self):
        h1 = context_hash("Build it", ["dep_a"], "")
        h2 = context_hash("Build it", ["dep_b"], "")
        assert h1 != h2

    def test_different_sops_different_hash(self):
        h1 = context_hash("Build it", [], "Use TDD")
        h2 = context_hash("Build it", [], "Use BDD")
        assert h1 != h2

    def test_dep_order_independent(self):
        h1 = context_hash("Build it", ["dep_b", "dep_a"], "")
        h2 = context_hash("Build it", ["dep_a", "dep_b"], "")
        assert h1 == h2

    def test_returns_hex_string(self):
        h = context_hash("desc", [], "")
        assert all(c in "0123456789abcdef" for c in h)


class TestSaveLoadRoundtrip:
    def test_save_and_load(self, tmp_path):
        report = _make_report()
        key = "comp_a__contract_author__abc123"
        save_research(tmp_path, key, report)
        loaded = load_research(tmp_path, key)
        assert loaded is not None
        assert loaded.recommended_approach == report.recommended_approach
        assert len(loaded.findings) == 2

    def test_load_missing_returns_none(self, tmp_path):
        result = load_research(tmp_path, "nonexistent_key")
        assert result is None

    def test_load_corrupt_returns_none(self, tmp_path):
        research_dir = tmp_path / ".pact" / "research"
        research_dir.mkdir(parents=True)
        (research_dir / "bad_key.json").write_text("not valid json{{{")
        result = load_research(tmp_path, "bad_key")
        assert result is None

    def test_save_creates_directory(self, tmp_path):
        report = _make_report()
        save_research(tmp_path, "key", report)
        assert (tmp_path / ".pact" / "research" / "key.json").exists()


class TestInvalidate:
    def test_invalidate_removes_entries(self, tmp_path):
        report = _make_report()
        save_research(tmp_path, "comp_a__contract__abc", report)
        save_research(tmp_path, "comp_a__test__def", report)
        save_research(tmp_path, "comp_b__contract__abc", report)
        removed = invalidate(tmp_path, "comp_a")
        assert removed == 2
        assert load_research(tmp_path, "comp_a__contract__abc") is None
        assert load_research(tmp_path, "comp_b__contract__abc") is not None

    def test_invalidate_no_directory(self, tmp_path):
        removed = invalidate(tmp_path, "comp_a")
        assert removed == 0

    def test_invalidate_no_matches(self, tmp_path):
        report = _make_report()
        save_research(tmp_path, "comp_b__contract__abc", report)
        removed = invalidate(tmp_path, "comp_a")
        assert removed == 0
