"""Tests for per-phase budget tracking."""
from pact.budget import PhaseBudget


class TestPhaseBudget:
    def test_record_spend(self):
        pb = PhaseBudget()
        pb.record_spend("interview", 0.50)
        pb.record_spend("interview", 0.25)
        assert pb.phase_spend["interview"] == 0.75

    def test_phase_under_cap_passes(self):
        pb = PhaseBudget(phase_caps={"shape": 0.15})
        pb.record_spend("shape", 2.00)
        assert pb.check_phase_budget("shape", 100.0) is True  # 2 < 15

    def test_phase_over_cap_fails(self):
        pb = PhaseBudget(phase_caps={"shape": 0.15})
        pb.record_spend("shape", 20.00)
        assert pb.check_phase_budget("shape", 100.0) is False  # 20 > 15

    def test_uncapped_phase_always_passes(self):
        pb = PhaseBudget()
        pb.record_spend("implement", 40.00)
        assert pb.check_phase_budget("implement", 50.0) is True

    def test_phase_summary(self):
        pb = PhaseBudget(phase_caps={"shape": 0.15})
        pb.record_spend("shape", 5.00)
        pb.record_spend("implement", 10.00)
        summary = pb.phase_summary()
        assert "shape" in summary
        assert summary["shape"]["spent"] == 5.00
        assert summary["shape"]["cap_fraction"] == 0.15
        assert "implement" in summary
        assert "cap_fraction" not in summary["implement"]

    def test_from_config_backward_compat(self):
        pb = PhaseBudget.from_config(shaping_budget_pct=0.15)
        assert pb.phase_caps["shape"] == 0.15

    def test_from_config_zero_pct(self):
        pb = PhaseBudget.from_config(shaping_budget_pct=0.0)
        assert "shape" not in pb.phase_caps

    def test_multiple_phases(self):
        pb = PhaseBudget(phase_caps={"shape": 0.15, "interview": 0.10})
        pb.record_spend("shape", 5.0)
        pb.record_spend("interview", 8.0)
        assert pb.check_phase_budget("shape", 100.0) is True
        assert pb.check_phase_budget("interview", 100.0) is True
        pb.record_spend("interview", 5.0)
        assert pb.check_phase_budget("interview", 100.0) is False  # 13 > 10

    def test_empty_phase_budget(self):
        pb = PhaseBudget()
        assert pb.phase_summary() == {}
        assert pb.check_phase_budget("anything", 100.0) is True

    def test_record_spend_new_phase(self):
        pb = PhaseBudget()
        pb.record_spend("decompose", 1.0)
        assert pb.phase_spend["decompose"] == 1.0
