"""Tests for diagnoser module — error recovery logic."""

from __future__ import annotations

from pact.diagnoser import determine_recovery_action
from pact.schemas import TraceDiagnosis


class TestDetermineRecoveryAction:
    def test_implementation_bug(self):
        d = TraceDiagnosis(
            failing_test="t1",
            root_cause="implementation_bug",
            component_id="pricing",
            explanation="Wrong calculation",
        )
        assert determine_recovery_action(d) == "reimplement"

    def test_glue_bug(self):
        d = TraceDiagnosis(
            failing_test="t1",
            root_cause="glue_bug",
            component_id="root",
            explanation="Wrong wiring",
        )
        assert determine_recovery_action(d) == "reglue"

    def test_contract_bug(self):
        d = TraceDiagnosis(
            failing_test="t1",
            root_cause="contract_bug",
            component_id="pricing",
            explanation="Missing error case",
        )
        assert determine_recovery_action(d) == "update_contract"

    def test_design_bug(self):
        d = TraceDiagnosis(
            failing_test="t1",
            root_cause="design_bug",
            component_id="root",
            explanation="Wrong decomposition",
        )
        assert determine_recovery_action(d) == "redesign"
