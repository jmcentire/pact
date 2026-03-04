"""Tests for budget tracking."""

from __future__ import annotations

import logging

import pytest

from pact.budget import BudgetExceeded, BudgetTracker, estimate_tokens, pricing_for_model


class TestPricingForModel:
    def test_exact_match(self):
        inp, out = pricing_for_model("claude-opus-4-6")
        assert inp == 5.00
        assert out == 25.00

    def test_haiku(self):
        inp, out = pricing_for_model("claude-haiku-4-5-20251001")
        assert inp == 1.00

    def test_unknown_defaults_to_haiku(self):
        inp, out = pricing_for_model("unknown-model-v99")
        assert inp == 1.00


class TestBudgetTracker:
    def test_record_tokens(self):
        bt = BudgetTracker(per_project_cap=10.00)
        bt.set_model_pricing("claude-opus-4-6")
        ok = bt.record_tokens(1000, 500)
        assert ok is True
        assert bt.project_spend > 0

    def test_budget_exceeded(self):
        bt = BudgetTracker(per_project_cap=0.001)
        bt.set_model_pricing("claude-opus-4-6")
        ok = bt.record_tokens(100000, 50000)
        assert ok is False
        assert bt.is_exceeded()

    def test_start_project_resets(self):
        bt = BudgetTracker(per_project_cap=10.00)
        bt.set_model_pricing("claude-opus-4-6")
        bt.record_tokens(1000, 500)
        assert bt.project_spend > 0
        bt.start_project()
        assert bt.project_spend == 0.0

    def test_tokens_to_dollars(self):
        bt = BudgetTracker()
        bt.set_model_pricing("claude-opus-4-6")
        cost = bt.tokens_to_dollars(1_000_000, 0)
        assert cost == 5.00

    def test_project_tokens(self):
        bt = BudgetTracker(per_project_cap=100.00)
        bt.set_model_pricing("claude-haiku-4-5-20251001")
        bt.record_tokens(100, 200)
        in_tok, out_tok = bt.project_tokens
        assert in_tok == 100
        assert out_tok == 200

    def test_daily_spend(self):
        bt = BudgetTracker(per_project_cap=100.00)
        bt.set_model_pricing("claude-haiku-4-5-20251001")
        bt.record_tokens(100, 200)
        assert bt.daily_spend > 0


class TestEstimateTokens:
    def test_empty_returns_zero(self):
        assert estimate_tokens("") == 0

    def test_code_uses_lower_ratio(self):
        """Symbol-heavy text of equal length should produce a higher token count."""
        # Make both strings the same length so only chars_per_token differs
        base_len = 2000
        code = ("if (x > 0) { return a[i] + b[j]; } " * 100)[:base_len]
        prose = ("the quick brown fox jumps over the lazy dog " * 100)[:base_len]
        assert len(code) == len(prose)
        assert estimate_tokens(code) > estimate_tokens(prose)

    def test_prose_uses_higher_ratio(self):
        """English prose should produce fewer tokens per character."""
        prose = "The quick brown fox jumps over the lazy dog " * 100
        tokens = estimate_tokens(prose)
        # ~4.5 chars per token for prose
        expected_approx = len(prose) / 4.5
        assert abs(tokens - expected_approx) / expected_approx < 0.1

    def test_at_least_one(self):
        assert estimate_tokens("x") == 1

    def test_conservative_vs_old(self):
        """estimate_tokens should be >= len(text) // 5 for typical code."""
        code = "def foo(bar): return bar + 1\n" * 100
        assert estimate_tokens(code) >= len(code) // 5


class TestRecordTokensValidated:
    def test_uses_reported_when_higher(self):
        bt = BudgetTracker(per_project_cap=100.00)
        bt.set_model_pricing("claude-opus-4-6")
        # Report high tokens, no text to estimate from
        bt.record_tokens_validated(10000, 5000)
        in_tok, out_tok = bt.project_tokens
        assert in_tok == 10000
        assert out_tok == 5000

    def test_uses_estimated_when_higher(self):
        bt = BudgetTracker(per_project_cap=100.00)
        bt.set_model_pricing("claude-opus-4-6")
        # Report 0 tokens but provide text — estimation should kick in
        text = "x" * 1000
        bt.record_tokens_validated(0, 0, prompt_text=text, response_text=text)
        in_tok, out_tok = bt.project_tokens
        assert in_tok > 0
        assert out_tok > 0

    def test_logs_discrepancy(self, caplog):
        bt = BudgetTracker(per_project_cap=100.00)
        bt.set_model_pricing("claude-opus-4-6")
        # Reported is 10 tokens but text is 1000 chars (~250 tokens) → 25x ratio
        with caplog.at_level(logging.WARNING):
            bt.record_tokens_validated(
                10, 10,
                prompt_text="a" * 1000,
                response_text="b" * 1000,
            )
        assert "Token discrepancy" in caplog.text

    def test_no_estimation_without_text(self):
        bt = BudgetTracker(per_project_cap=100.00)
        bt.set_model_pricing("claude-opus-4-6")
        bt.record_tokens_validated(500, 200, prompt_text="", response_text="")
        in_tok, out_tok = bt.project_tokens
        assert in_tok == 500
        assert out_tok == 200

    def test_budget_exceeded_returns_false(self):
        bt = BudgetTracker(per_project_cap=0.0001)
        bt.set_model_pricing("claude-opus-4-6")
        text = "x" * 100000
        result = bt.record_tokens_validated(0, 0, prompt_text=text, response_text=text)
        assert result is False


class TestBudgetSummary:
    def test_summary_keys(self):
        bt = BudgetTracker(per_project_cap=10.00)
        bt.set_model_pricing("claude-opus-4-6")
        bt.record_tokens(1000, 500)
        s = bt.summary()
        expected_keys = {
            "project_spend", "project_cap", "budget_remaining", "spend_percentage",
            "daily_spend", "tokens_in", "tokens_out",
            "cache_creation_tokens", "cache_read_tokens", "cache_hit_rate",
        }
        assert set(s.keys()) == expected_keys

    def test_summary_values(self):
        bt = BudgetTracker(per_project_cap=10.00)
        bt.set_model_pricing("claude-opus-4-6")
        bt.record_tokens(1000, 500)
        bt.record_cache_tokens(creation=100, read=300)
        s = bt.summary()
        assert s["project_spend"] > 0
        assert s["project_cap"] == 10.00
        assert s["tokens_in"] == 1000
        assert s["tokens_out"] == 500
        assert s["cache_creation_tokens"] == 100
        assert s["cache_read_tokens"] == 300
        assert s["cache_hit_rate"] == 0.75

    def test_as_dict_alias(self):
        bt = BudgetTracker(per_project_cap=10.00)
        assert bt.summary() == bt.as_dict()


class TestPricingFile:
    def test_save_load_roundtrip(self, tmp_path):
        from pact.budget import load_pricing_file, save_pricing_file
        pricing = {"test-model": (1.00, 2.00), "other-model": (3.00, 6.00)}
        path = tmp_path / "pricing.json"
        save_pricing_file(path, pricing)
        loaded = load_pricing_file(path)
        assert loaded == pricing

    def test_save_defaults(self, tmp_path):
        from pact.budget import get_model_pricing_table, load_pricing_file, save_pricing_file
        path = tmp_path / "pricing.json"
        save_pricing_file(path)
        loaded = load_pricing_file(path)
        current = get_model_pricing_table()
        assert loaded == current

    def test_creates_parent_dirs(self, tmp_path):
        from pact.budget import save_pricing_file
        path = tmp_path / "nested" / "dir" / "pricing.json"
        save_pricing_file(path, {"m": (1.0, 2.0)})
        assert path.exists()
