"""Tests for optional kindex integration — graceful degradation."""

import pytest
from pathlib import Path
from unittest.mock import patch, MagicMock


# ============================================================================
# Availability and graceful degradation
# ============================================================================

class TestAvailability:
    def test_fetch_context_returns_empty_when_unavailable(self):
        from pact import kindex_integration as ki
        ki.close()
        with patch.object(ki, "is_available", return_value=False):
            assert ki.fetch_context("test topic") == ""

    def test_search_returns_empty_when_unavailable(self):
        from pact import kindex_integration as ki
        ki.close()
        with patch.object(ki, "is_available", return_value=False):
            assert ki.search("test") == []

    def test_publish_node_returns_none_when_unavailable(self):
        from pact import kindex_integration as ki
        ki.close()
        with patch.object(ki, "is_available", return_value=False):
            assert ki.publish_node("title", "content") is None

    def test_publish_task_returns_none_when_unavailable(self):
        from pact import kindex_integration as ki
        ki.close()
        with patch.object(ki, "is_available", return_value=False):
            assert ki.publish_task("title", "content") is None

    def test_publish_decision_returns_none_when_unavailable(self):
        from pact import kindex_integration as ki
        ki.close()
        with patch.object(ki, "is_available", return_value=False):
            assert ki.publish_decision("title", "rationale") is None

    def test_publish_contract_returns_none_when_unavailable(self):
        from pact import kindex_integration as ki
        ki.close()
        with patch.object(ki, "is_available", return_value=False):
            assert ki.publish_contract("comp", "{}") is None

    def test_publish_decomposition_returns_zero_when_unavailable(self):
        from pact import kindex_integration as ki
        ki.close()
        with patch.object(ki, "is_available", return_value=False):
            assert ki.publish_decomposition('{"id": "root"}') == 0

    def test_learn_text_returns_zero_when_unavailable(self):
        from pact import kindex_integration as ki
        ki.close()
        with patch.object(ki, "is_available", return_value=False):
            assert ki.learn_text("some text") == 0

    def test_index_codebase_returns_false_when_unavailable(self):
        from pact import kindex_integration as ki
        ki.close()
        with patch.object(ki, "_cli_available", return_value=False):
            with patch.object(ki, "is_available", return_value=False):
                assert ki.index_codebase(Path(".")) is False

    def test_close_is_safe_when_not_initialized(self):
        from pact import kindex_integration as ki
        ki._store = None
        ki._checked = False
        ki.close()


# ============================================================================
# .kin/config management
# ============================================================================

class TestKinConfig:
    def test_read_missing_config(self, tmp_path):
        from pact.kindex_integration import read_kin_config
        assert read_kin_config(tmp_path) == {}

    def test_write_and_read_config(self, tmp_path):
        from pact.kindex_integration import read_kin_config, write_kin_config
        write_kin_config(tmp_path, {"auto_index": True, "name": "test-project"})
        config = read_kin_config(tmp_path)
        assert config["auto_index"] is True
        assert config["name"] == "test-project"

    def test_should_auto_index_unset(self, tmp_path):
        from pact.kindex_integration import should_auto_index
        assert should_auto_index(tmp_path) is None

    def test_should_auto_index_true(self, tmp_path):
        from pact.kindex_integration import write_kin_config, should_auto_index
        write_kin_config(tmp_path, {"auto_index": True})
        assert should_auto_index(tmp_path) is True


# ============================================================================
# Decomposition publishing (with mocked store)
# ============================================================================

class TestPublishDecomposition:
    def test_publishes_tree_nodes(self):
        from pact import kindex_integration as ki
        mock_store = MagicMock()
        mock_store.add_node.return_value = "node-123"
        ki._store = mock_store
        ki._checked = True

        tree_json = '{"id": "root", "description": "top", "children": [{"id": "child-a", "description": "a"}, {"id": "child-b", "description": "b"}]}'
        count = ki.publish_decomposition(tree_json, tags=["test"])
        assert count == 3  # root + 2 children
        ki.close()

    def test_empty_tree(self):
        from pact import kindex_integration as ki
        ki._store = MagicMock()
        ki._checked = True
        assert ki.publish_decomposition("") == 0
        ki.close()

    def test_invalid_json(self):
        from pact import kindex_integration as ki
        ki._store = MagicMock()
        ki._checked = True
        assert ki.publish_decomposition("{not valid json") == 0
        ki.close()


# ============================================================================
# CLI integration (mocked)
# ============================================================================

class TestCLIKindexPrompt:
    def test_skips_when_unavailable(self):
        from pact.cli import _kindex_prompt_and_index
        with patch("pact.kindex_integration.is_available", return_value=False):
            _kindex_prompt_and_index("/tmp/test")
            # Should not raise

    def test_fetch_context_returns_none_when_unavailable(self):
        from pact.cli import _kindex_fetch_context
        with patch("pact.kindex_integration.is_available", return_value=False):
            assert _kindex_fetch_context("/tmp/test") is None

    def test_publish_project_noop_when_unavailable(self, tmp_path):
        from pact.cli import _kindex_publish_project
        with patch("pact.kindex_integration.is_available", return_value=False):
            _kindex_publish_project(str(tmp_path))
            # Should not raise
