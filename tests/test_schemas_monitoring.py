"""Tests for monitoring data models."""

from __future__ import annotations

import pytest

from pact.schemas_monitoring import (
    DiagnosticReport,
    Incident,
    LogKey,
    MonitoringBudget,
    MonitoringTarget,
    Signal,
    SignalFingerprint,
)


class TestMonitoringTarget:
    def test_defaults(self):
        t = MonitoringTarget(project_dir="/tmp/proj")
        assert t.project_dir == "/tmp/proj"
        assert t.label == ""
        assert t.log_files == []
        assert t.process_patterns == []
        assert t.webhook_port == 0
        assert "ERROR" in t.error_patterns
        assert "CRITICAL" in t.error_patterns
        assert "Traceback" in t.error_patterns

    def test_custom_fields(self):
        t = MonitoringTarget(
            project_dir="/tmp/proj",
            label="My Project",
            log_files=["/var/log/app.log"],
            process_patterns=["myapp"],
            webhook_port=9876,
            error_patterns=["FATAL"],
        )
        assert t.label == "My Project"
        assert t.log_files == ["/var/log/app.log"]
        assert t.webhook_port == 9876
        assert t.error_patterns == ["FATAL"]

    def test_serialization_roundtrip(self):
        t = MonitoringTarget(
            project_dir="/tmp/proj",
            log_files=["/a.log", "/b.log"],
        )
        data = t.model_dump()
        t2 = MonitoringTarget.model_validate(data)
        assert t2.project_dir == t.project_dir
        assert t2.log_files == t.log_files


class TestLogKey:
    def test_construction(self):
        k = LogKey(project_id="abc123", component_id="pricing")
        assert k.project_id == "abc123"
        assert k.component_id == "pricing"

    def test_format_rendering(self):
        k = LogKey(project_id="abc123", component_id="pricing")
        rendered = k.format.format(
            project_id=k.project_id,
            component_id=k.component_id,
        )
        assert rendered == "PACT:abc123:pricing"

    def test_default_format(self):
        k = LogKey(project_id="x", component_id="y")
        assert k.format == "PACT:{project_id}:{component_id}"


class TestSignal:
    def test_log_file_signal(self):
        s = Signal(
            source="log_file",
            raw_text="ERROR: something failed",
            timestamp="2024-01-01T00:00:00",
            file_path="/var/log/app.log",
        )
        assert s.source == "log_file"
        assert s.file_path == "/var/log/app.log"
        assert s.log_key == ""

    def test_manual_signal(self):
        s = Signal(
            source="manual",
            raw_text="TypeError: NoneType",
            timestamp="2024-01-01T00:00:00",
        )
        assert s.source == "manual"
        assert s.process_name == ""

    def test_webhook_signal_with_key(self):
        s = Signal(
            source="webhook",
            raw_text="Error in pricing",
            timestamp="2024-01-01T00:00:00",
            log_key="PACT:abc123:pricing",
        )
        assert s.log_key == "PACT:abc123:pricing"


class TestSignalFingerprint:
    def test_construction(self):
        signal = Signal(
            source="log_file",
            raw_text="Error",
            timestamp="2024-01-01T00:00:00",
        )
        fp = SignalFingerprint(
            hash="abcdef1234567890",
            first_seen="2024-01-01T00:00:00",
            last_seen="2024-01-01T00:00:00",
            representative=signal,
        )
        assert fp.hash == "abcdef1234567890"
        assert fp.count == 1

    def test_count_increments(self):
        signal = Signal(
            source="log_file",
            raw_text="Error",
            timestamp="2024-01-01T00:00:00",
        )
        fp = SignalFingerprint(
            hash="abc",
            first_seen="2024-01-01T00:00:00",
            last_seen="2024-01-01T00:00:00",
            count=5,
            representative=signal,
        )
        assert fp.count == 5


class TestIncident:
    def test_defaults(self):
        inc = Incident(
            id="abc123def456",
            project_dir="/tmp/proj",
            created_at="2024-01-01T00:00:00",
            updated_at="2024-01-01T00:00:00",
        )
        assert inc.status == "detected"
        assert inc.component_id == ""
        assert inc.signals == []
        assert inc.spend_usd == 0.0
        assert inc.resolution == ""
        assert inc.diagnostic_report == ""
        assert inc.remediation_attempts == 0

    def test_status_transitions(self):
        inc = Incident(
            id="test",
            project_dir="/tmp",
            created_at="2024-01-01",
            updated_at="2024-01-01",
        )
        assert inc.status == "detected"

        inc.status = "triaging"
        assert inc.status == "triaging"

        inc.status = "remediating"
        assert inc.status == "remediating"

        inc.status = "resolved"
        assert inc.status == "resolved"

    def test_serialization(self):
        inc = Incident(
            id="abc",
            project_dir="/tmp",
            component_id="pricing",
            created_at="2024-01-01",
            updated_at="2024-01-01",
            spend_usd=1.23,
        )
        data = inc.model_dump()
        inc2 = Incident.model_validate(data)
        assert inc2.id == "abc"
        assert inc2.component_id == "pricing"
        assert inc2.spend_usd == 1.23


class TestMonitoringBudget:
    def test_defaults(self):
        b = MonitoringBudget()
        assert b.per_incident_cap == 5.00
        assert b.hourly_cap == 10.00
        assert b.daily_cap == 25.00
        assert b.weekly_cap == 100.00
        assert b.monthly_cap == 300.00

    def test_custom_budget(self):
        b = MonitoringBudget(
            per_incident_cap=1.00,
            hourly_cap=5.00,
            daily_cap=10.00,
        )
        assert b.per_incident_cap == 1.00
        assert b.hourly_cap == 5.00

    def test_serialization(self):
        b = MonitoringBudget(per_incident_cap=2.50)
        data = b.model_dump()
        b2 = MonitoringBudget.model_validate(data)
        assert b2.per_incident_cap == 2.50


class TestDiagnosticReport:
    def test_construction(self):
        r = DiagnosticReport(
            incident_id="abc",
            summary="NoneType error in pricing",
            error_analysis="Missing null check on price lookup",
            component_context="pricing component handles price calculation",
            recommended_direction="Add null check before accessing price attribute",
            severity="high",
            confidence=0.85,
        )
        assert r.incident_id == "abc"
        assert r.severity == "high"
        assert r.confidence == 0.85
        assert r.attempted_fixes == []

    def test_with_attempted_fixes(self):
        r = DiagnosticReport(
            incident_id="abc",
            summary="test",
            error_analysis="test",
            component_context="test",
            attempted_fixes=["Added null check", "Refactored lookup"],
            recommended_direction="test",
            severity="medium",
            confidence=0.5,
        )
        assert len(r.attempted_fixes) == 2

    def test_confidence_bounds(self):
        with pytest.raises(Exception):
            DiagnosticReport(
                incident_id="abc",
                summary="test",
                error_analysis="test",
                component_context="test",
                recommended_direction="test",
                severity="low",
                confidence=1.5,  # Out of bounds
            )
