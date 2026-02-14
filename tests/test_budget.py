"""Tests for budget tracking."""

from __future__ import annotations

from pact.budget import BudgetExceeded, BudgetTracker, pricing_for_model


class TestPricingForModel:
    def test_exact_match(self):
        inp, out = pricing_for_model("claude-opus-4-6")
        assert inp == 15.00
        assert out == 75.00

    def test_haiku(self):
        inp, out = pricing_for_model("claude-haiku-4-5-20251001")
        assert inp == 0.80

    def test_unknown_defaults_to_haiku(self):
        inp, out = pricing_for_model("unknown-model-v99")
        assert inp == 0.80


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
        assert cost == 15.00

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
