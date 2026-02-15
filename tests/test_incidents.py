"""Tests for incident lifecycle management and budget enforcement."""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from pact.incidents import IncidentManager
from pact.schemas_monitoring import Incident, MonitoringBudget, Signal


def _make_signal(text: str = "ERROR: test error") -> Signal:
    return Signal(
        source="log_file",
        raw_text=text,
        timestamp=datetime.now().isoformat(),
    )


class TestCreateIncident:
    def test_creates_with_detected_status(self, tmp_path: Path):
        mgr = IncidentManager(tmp_path, MonitoringBudget())
        signal = _make_signal()
        incident = mgr.create_incident(signal, "/tmp/proj")
        assert incident.status == "detected"
        assert incident.project_dir == "/tmp/proj"
        assert len(incident.signals) == 1
        assert incident.spend_usd == 0.0

    def test_generates_unique_ids(self, tmp_path: Path):
        mgr = IncidentManager(tmp_path, MonitoringBudget())
        inc1 = mgr.create_incident(_make_signal("err1"), "/tmp/proj")
        inc2 = mgr.create_incident(_make_signal("err2"), "/tmp/proj")
        assert inc1.id != inc2.id

    def test_with_component_id(self, tmp_path: Path):
        mgr = IncidentManager(tmp_path, MonitoringBudget())
        incident = mgr.create_incident(_make_signal(), "/tmp/proj", "pricing")
        assert incident.component_id == "pricing"


class TestIncidentLifecycle:
    def test_status_transitions(self, tmp_path: Path):
        mgr = IncidentManager(tmp_path, MonitoringBudget())
        incident = mgr.create_incident(_make_signal(), "/tmp/proj")

        mgr.update_status(incident.id, "triaging")
        assert mgr.get_incident(incident.id).status == "triaging"

        mgr.update_status(incident.id, "diagnosing")
        assert mgr.get_incident(incident.id).status == "diagnosing"

        mgr.update_status(incident.id, "remediating")
        assert mgr.get_incident(incident.id).status == "remediating"

        mgr.update_status(incident.id, "verifying")
        assert mgr.get_incident(incident.id).status == "verifying"

        mgr.update_status(incident.id, "resolved")
        assert mgr.get_incident(incident.id).status == "resolved"

    def test_get_active_excludes_resolved(self, tmp_path: Path):
        mgr = IncidentManager(tmp_path, MonitoringBudget())
        inc1 = mgr.create_incident(_make_signal("err1"), "/tmp/proj")
        inc2 = mgr.create_incident(_make_signal("err2"), "/tmp/proj")

        mgr.update_status(inc1.id, "resolved")
        active = mgr.get_active_incidents()
        assert len(active) == 1
        assert active[0].id == inc2.id

    def test_get_active_excludes_escalated(self, tmp_path: Path):
        mgr = IncidentManager(tmp_path, MonitoringBudget())
        inc = mgr.create_incident(_make_signal(), "/tmp/proj")
        mgr.update_status(inc.id, "escalated")
        assert len(mgr.get_active_incidents()) == 0


class TestBudgetPerIncident:
    def test_exceeds_per_incident_cap(self, tmp_path: Path):
        budget = MonitoringBudget(per_incident_cap=1.00)
        mgr = IncidentManager(tmp_path, budget)
        incident = mgr.create_incident(_make_signal(), "/tmp/proj")

        # Under cap — should be OK
        mgr.record_spend(incident.id, 0.50)
        assert mgr.check_budget(incident.id) is True

        # At cap — should fail
        mgr.record_spend(incident.id, 0.50)
        assert mgr.check_budget(incident.id) is False

    def test_zero_budget_always_fails(self, tmp_path: Path):
        budget = MonitoringBudget(per_incident_cap=0.0)
        mgr = IncidentManager(tmp_path, budget)
        incident = mgr.create_incident(_make_signal(), "/tmp/proj")
        assert mgr.check_budget(incident.id) is False


class TestBudgetHourly:
    def test_exceeds_hourly_cap(self, tmp_path: Path):
        budget = MonitoringBudget(
            per_incident_cap=100.0,
            hourly_cap=2.00,
        )
        mgr = IncidentManager(tmp_path, budget)

        # Create two incidents with total spend exceeding hourly cap
        inc1 = mgr.create_incident(_make_signal("err1"), "/tmp/proj")
        mgr.record_spend(inc1.id, 1.50)

        inc2 = mgr.create_incident(_make_signal("err2"), "/tmp/proj")
        mgr.record_spend(inc2.id, 0.60)

        # Hourly total is 2.10, exceeds 2.00 cap
        assert mgr.check_budget(inc2.id) is False


class TestBudgetAcrossWindows:
    def test_per_incident_ok_but_daily_exceeded(self, tmp_path: Path):
        budget = MonitoringBudget(
            per_incident_cap=10.0,
            hourly_cap=100.0,
            daily_cap=2.00,
        )
        mgr = IncidentManager(tmp_path, budget)

        inc1 = mgr.create_incident(_make_signal("err1"), "/tmp/proj")
        mgr.record_spend(inc1.id, 1.50)

        inc2 = mgr.create_incident(_make_signal("err2"), "/tmp/proj")
        mgr.record_spend(inc2.id, 0.60)

        # Per-incident is fine (0.60 < 10.0), hourly fine (2.10 < 100.0),
        # but daily is exceeded (2.10 > 2.00)
        assert mgr.check_budget(inc2.id) is False


class TestPersistence:
    def test_save_and_reload(self, tmp_path: Path):
        budget = MonitoringBudget()
        mgr = IncidentManager(tmp_path, budget)
        signal = _make_signal("ERROR: persistence test")
        incident = mgr.create_incident(signal, "/tmp/proj", "pricing")
        mgr.record_spend(incident.id, 1.23)

        # Create new manager from same directory
        mgr2 = IncidentManager(tmp_path, budget)
        loaded = mgr2.get_incident(incident.id)
        assert loaded is not None
        assert loaded.component_id == "pricing"
        assert loaded.spend_usd == 1.23
        assert len(loaded.signals) == 1

    def test_persistence_file_exists(self, tmp_path: Path):
        mgr = IncidentManager(tmp_path, MonitoringBudget())
        mgr.create_incident(_make_signal(), "/tmp/proj")
        assert (tmp_path / "monitoring" / "incidents.json").exists()
        assert (tmp_path / "monitoring" / "budget.json").exists()


class TestCloseWithReport:
    def test_close_as_auto_fixed(self, tmp_path: Path):
        mgr = IncidentManager(tmp_path, MonitoringBudget())
        incident = mgr.create_incident(_make_signal(), "/tmp/proj")
        mgr.close_incident(incident.id, "auto_fixed", "# Fix Report\nFixed by adding null check")

        closed = mgr.get_incident(incident.id)
        assert closed.status == "resolved"
        assert closed.resolution == "auto_fixed"
        assert "Fix Report" in closed.diagnostic_report

        # Report file written
        report_path = tmp_path / "monitoring" / "reports" / f"{incident.id}.md"
        assert report_path.exists()
        assert "Fix Report" in report_path.read_text()

    def test_close_as_escalated(self, tmp_path: Path):
        mgr = IncidentManager(tmp_path, MonitoringBudget())
        incident = mgr.create_incident(_make_signal(), "/tmp/proj")
        mgr.close_incident(incident.id, "escalated", "# Escalation\nNeeds human review")

        closed = mgr.get_incident(incident.id)
        assert closed.status == "escalated"
        assert closed.resolution == "escalated"


class TestFindByFingerprint:
    def test_finds_matching_incident(self, tmp_path: Path):
        from pact.schemas_monitoring import SignalFingerprint

        mgr = IncidentManager(tmp_path, MonitoringBudget())
        signal = _make_signal()
        incident = mgr.create_incident(signal, "/tmp/proj")

        # Set fingerprint
        incident.fingerprint = SignalFingerprint(
            hash="abc123",
            first_seen=datetime.now().isoformat(),
            last_seen=datetime.now().isoformat(),
            representative=signal,
        )
        mgr.save_state()

        found = mgr.find_by_fingerprint("abc123")
        assert found is not None
        assert found.id == incident.id

    def test_returns_none_for_unknown(self, tmp_path: Path):
        mgr = IncidentManager(tmp_path, MonitoringBudget())
        mgr.create_incident(_make_signal(), "/tmp/proj")
        assert mgr.find_by_fingerprint("nonexistent") is None

    def test_does_not_find_resolved(self, tmp_path: Path):
        from pact.schemas_monitoring import SignalFingerprint

        mgr = IncidentManager(tmp_path, MonitoringBudget())
        signal = _make_signal()
        incident = mgr.create_incident(signal, "/tmp/proj")
        incident.fingerprint = SignalFingerprint(
            hash="abc123",
            first_seen=datetime.now().isoformat(),
            last_seen=datetime.now().isoformat(),
            representative=signal,
        )
        mgr.update_status(incident.id, "resolved")

        assert mgr.find_by_fingerprint("abc123") is None


class TestRecentIncidents:
    def test_returns_newest_first(self, tmp_path: Path):
        mgr = IncidentManager(tmp_path, MonitoringBudget())
        inc1 = mgr.create_incident(_make_signal("err1"), "/tmp/proj")
        inc2 = mgr.create_incident(_make_signal("err2"), "/tmp/proj")

        recent = mgr.get_recent_incidents(10)
        assert len(recent) == 2
        # Newest first
        assert recent[0].id == inc2.id

    def test_respects_limit(self, tmp_path: Path):
        mgr = IncidentManager(tmp_path, MonitoringBudget())
        for i in range(5):
            mgr.create_incident(_make_signal(f"err{i}"), "/tmp/proj")

        recent = mgr.get_recent_incidents(3)
        assert len(recent) == 3
