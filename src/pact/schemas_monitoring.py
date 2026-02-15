"""Monitoring data models — production observability for Pact-generated code.

All models for the monitoring subsystem: signal ingestion, incident lifecycle,
budget enforcement, and diagnostic reporting.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class MonitoringTarget(BaseModel):
    """A project Pact is monitoring."""
    project_dir: str
    label: str = ""
    log_files: list[str] = []
    process_patterns: list[str] = []
    webhook_port: int = 0
    error_patterns: list[str] = Field(
        default_factory=lambda: ["ERROR", "CRITICAL", "Traceback"],
    )


class LogKey(BaseModel):
    """Embedded identifier in Pact-generated code."""
    project_id: str
    component_id: str
    format: str = "PACT:{project_id}:{component_id}"


class Signal(BaseModel):
    """A raw incoming error signal."""
    source: Literal["log_file", "process", "webhook", "manual"]
    raw_text: str
    timestamp: str
    file_path: str = ""
    process_name: str = ""
    log_key: str = ""


class SignalFingerprint(BaseModel):
    """Deduplicated signal identity."""
    hash: str
    first_seen: str
    last_seen: str
    count: int = 1
    representative: Signal


class Incident(BaseModel):
    """A tracked error incident with lifecycle."""
    id: str
    status: Literal[
        "detected", "triaging", "diagnosing", "remediating",
        "verifying", "resolved", "escalated",
    ] = "detected"
    project_dir: str
    component_id: str = ""
    signals: list[Signal] = []
    fingerprint: SignalFingerprint | None = None
    created_at: str
    updated_at: str
    spend_usd: float = 0.0
    resolution: str = ""
    diagnostic_report: str = ""
    remediation_attempts: int = 0


class MonitoringBudget(BaseModel):
    """Multi-window budget for monitoring operations."""
    per_incident_cap: float = 5.00
    hourly_cap: float = 10.00
    daily_cap: float = 25.00
    weekly_cap: float = 100.00
    monthly_cap: float = 300.00


class DiagnosticReport(BaseModel):
    """Structured escalation report."""
    incident_id: str
    summary: str
    error_analysis: str
    component_context: str
    attempted_fixes: list[str] = []
    recommended_direction: str
    severity: Literal["low", "medium", "high", "critical"]
    confidence: float = Field(ge=0.0, le=1.0)
