"""Tests for prompt caching (P0-1) and cache metrics (P0-3)."""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from pact.budget import BudgetTracker


class TestBudgetCacheTracking:
    """P0-3: Cache metrics in BudgetTracker."""

    def test_initial_cache_tokens_zero(self):
        bt = BudgetTracker()
        assert bt.cache_creation_tokens == 0
        assert bt.cache_read_tokens == 0

    def test_record_cache_tokens(self):
        bt = BudgetTracker()
        bt.record_cache_tokens(1000, 500)
        assert bt.cache_creation_tokens == 1000
        assert bt.cache_read_tokens == 500

    def test_record_cache_tokens_accumulates(self):
        bt = BudgetTracker()
        bt.record_cache_tokens(1000, 0)
        bt.record_cache_tokens(0, 1000)
        assert bt.cache_creation_tokens == 1000
        assert bt.cache_read_tokens == 1000

    def test_cache_hit_rate_no_tokens(self):
        bt = BudgetTracker()
        assert bt.cache_hit_rate == 0.0

    def test_cache_hit_rate_all_creation(self):
        bt = BudgetTracker()
        bt.record_cache_tokens(1000, 0)
        assert bt.cache_hit_rate == 0.0

    def test_cache_hit_rate_all_reads(self):
        bt = BudgetTracker()
        bt.record_cache_tokens(0, 1000)
        assert bt.cache_hit_rate == 1.0

    def test_cache_hit_rate_mixed(self):
        bt = BudgetTracker()
        bt.record_cache_tokens(200, 800)
        assert bt.cache_hit_rate == 0.8


class TestAssessWithCache:
    """P0-1: Prompt caching in AnthropicBackend."""

    def test_assess_with_cache_exists(self):
        """Method exists on AnthropicBackend."""
        from pact.backends.anthropic import AnthropicBackend
        assert hasattr(AnthropicBackend, 'assess_with_cache')

    def test_cache_system_as_blocks(self):
        """System string converted to content block list with cache_control."""
        from pact.backends.anthropic import AnthropicBackend
        backend = AnthropicBackend.__new__(AnthropicBackend)
        blocks = backend._build_system_blocks("You are helpful.", cache=True)
        assert isinstance(blocks, list)
        assert blocks[0]["type"] == "text"
        assert blocks[0]["text"] == "You are helpful."
        assert blocks[0]["cache_control"] == {"type": "ephemeral"}

    def test_cache_system_short_no_cache(self):
        """Short system prompts skip cache_control."""
        from pact.backends.anthropic import AnthropicBackend
        backend = AnthropicBackend.__new__(AnthropicBackend)
        blocks = backend._build_system_blocks("Hi", cache=True)
        assert isinstance(blocks, list)
        assert "cache_control" not in blocks[0]

    def test_cache_user_blocks_with_prefix(self):
        """Cache prefix sent as separate block with cache_control."""
        from pact.backends.anthropic import AnthropicBackend
        backend = AnthropicBackend.__new__(AnthropicBackend)
        long_prefix = "x" * 400
        blocks = backend._build_user_blocks(long_prefix, "Do something.")
        assert len(blocks) == 2
        assert blocks[0]["cache_control"] == {"type": "ephemeral"}
        assert blocks[1]["text"] == "Do something."
        assert "cache_control" not in blocks[1]

    def test_cache_user_blocks_no_prefix(self):
        """Without prefix, returns plain string."""
        from pact.backends.anthropic import AnthropicBackend
        backend = AnthropicBackend.__new__(AnthropicBackend)
        result = backend._build_user_blocks("", "Do something.")
        assert result == "Do something."

    def test_cache_user_blocks_short_prefix_no_cache(self):
        """Short prefix skips cache_control."""
        from pact.backends.anthropic import AnthropicBackend
        backend = AnthropicBackend.__new__(AnthropicBackend)
        blocks = backend._build_user_blocks("short", "Do something.")
        # Short prefix: still sends as blocks but without cache_control
        assert isinstance(blocks, list)
        assert "cache_control" not in blocks[0]

    def test_cache_preserves_tool_choice(self):
        """assess_with_cache should still use tool_choice enforcement."""
        from pact.backends.anthropic import AnthropicBackend
        # Just verify the method signature accepts the same schema type
        import inspect
        sig = inspect.signature(AnthropicBackend.assess_with_cache)
        params = list(sig.parameters.keys())
        assert 'schema' in params
        assert 'prompt' in params
        assert 'system' in params
        assert 'cache_prefix' in params


class TestCallLlmCached:
    """Tests for _call_llm_cached internals."""

    def test_call_llm_cached_exists(self):
        from pact.backends.anthropic import AnthropicBackend
        assert hasattr(AnthropicBackend, '_call_llm_cached')
