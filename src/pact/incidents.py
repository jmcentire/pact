"""Incident lifecycle management and budget enforcement.

Tracks error incidents from detection through resolution or escalation.
Enforces multi-window spending budgets (per-incident, hourly, daily,
weekly, monthly) to prevent runaway monitoring costs.
"""

from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timedelta
from pathlib import Path

from pact.schemas_monitoring import (
    Incident,
    MonitoringBudget,
    Signal,
    SignalFingerprint,
)

logger = logging.getLogger(__name__)


class IncidentManager:
    """Manages incident lifecycle and budget enforcement.

    State is persisted to disk so incidents survive process restarts.
    Budget totals are maintained per time window (hour/day/week/month).
    """

    def __init__(self, state_dir: Path, budget: MonitoringBudget) -> None:
        self._state_dir = state_dir
        self._budget = budget
        self._incidents: dict[str, Incident] = {}
        self._spend_log: list[dict] = []  # [{timestamp, incident_id, amount}]

        # Ensure monitoring directory exists
        self._monitoring_dir = state_dir / "monitoring"
        self._monitoring_dir.mkdir(parents=True, exist_ok=True)
        (self._monitoring_dir / "reports").mkdir(exist_ok=True)

        self.load_state()

    def create_incident(
        self,
        signal: Signal,
        project_dir: str,
        component_id: str = "",
    ) -> Incident:
        """Create a new incident from a signal. Status starts at 'detected'."""
        now = datetime.now().isoformat()
        incident = Incident(
            id=uuid.uuid4().hex[:12],
            status="detected",
            project_dir=project_dir,
            component_id=component_id,
            signals=[signal],
            created_at=now,
            updated_at=now,
        )
        self._incidents[incident.id] = incident
        self.save_state()
        return incident

    def get_incident(self, incident_id: str) -> Incident | None:
        """Get an incident by ID."""
        return self._incidents.get(incident_id)

    def get_active_incidents(self) -> list[Incident]:
        """Get all non-resolved, non-escalated incidents."""
        return [
            i for i in self._incidents.values()
            if i.status not in ("resolved", "escalated")
        ]

    def update_status(self, incident_id: str, status: str) -> None:
        """Update an incident's status."""
        incident = self._incidents.get(incident_id)
        if incident:
            incident.status = status  # type: ignore[assignment]
            incident.updated_at = datetime.now().isoformat()
            self.save_state()

    def record_spend(self, incident_id: str, amount: float) -> None:
        """Record spending against an incident."""
        incident = self._incidents.get(incident_id)
        if incident:
            incident.spend_usd += amount
            incident.updated_at = datetime.now().isoformat()
            self._spend_log.append({
                "timestamp": datetime.now().isoformat(),
                "incident_id": incident_id,
                "amount": amount,
            })
            self.save_state()

    def check_budget(self, incident_id: str) -> bool:
        """Check if spending is within all budget windows.

        Returns False if ANY window is exceeded.
        """
        incident = self._incidents.get(incident_id)
        if not incident:
            return False

        # Per-incident cap
        if incident.spend_usd >= self._budget.per_incident_cap:
            return False

        now = datetime.now()

        # Aggregate spend by window
        hourly_spend = self._spend_in_window(now - timedelta(hours=1), now)
        if hourly_spend >= self._budget.hourly_cap:
            return False

        daily_spend = self._spend_in_window(now - timedelta(days=1), now)
        if daily_spend >= self._budget.daily_cap:
            return False

        weekly_spend = self._spend_in_window(now - timedelta(weeks=1), now)
        if weekly_spend >= self._budget.weekly_cap:
            return False

        monthly_spend = self._spend_in_window(now - timedelta(days=30), now)
        if monthly_spend >= self._budget.monthly_cap:
            return False

        return True

    def _spend_in_window(self, start: datetime, end: datetime) -> float:
        """Sum all spending within a time window."""
        total = 0.0
        for entry in self._spend_log:
            ts = datetime.fromisoformat(entry["timestamp"])
            if start <= ts <= end:
                total += entry["amount"]
        return total

    def close_incident(
        self,
        incident_id: str,
        resolution: str,
        report: str,
    ) -> None:
        """Close an incident with a resolution and diagnostic report."""
        incident = self._incidents.get(incident_id)
        if not incident:
            return

        incident.resolution = resolution
        incident.diagnostic_report = report
        incident.status = "resolved" if resolution == "auto_fixed" else "escalated"
        incident.updated_at = datetime.now().isoformat()

        # Write report to file
        report_path = self._monitoring_dir / "reports" / f"{incident_id}.md"
        report_path.write_text(report)

        self.save_state()

    def add_signal(self, incident_id: str, signal: Signal) -> None:
        """Add a signal to an existing incident."""
        incident = self._incidents.get(incident_id)
        if incident:
            incident.signals.append(signal)
            incident.updated_at = datetime.now().isoformat()
            self.save_state()

    def find_by_fingerprint(self, fp_hash: str) -> Incident | None:
        """Find an active incident matching a fingerprint hash."""
        for incident in self.get_active_incidents():
            if incident.fingerprint and incident.fingerprint.hash == fp_hash:
                return incident
        return None

    def load_state(self) -> dict:
        """Load all incidents from disk."""
        incidents_path = self._monitoring_dir / "incidents.json"
        budget_path = self._monitoring_dir / "budget.json"

        if incidents_path.exists():
            try:
                data = json.loads(incidents_path.read_text())
                self._incidents = {
                    k: Incident.model_validate(v)
                    for k, v in data.get("incidents", {}).items()
                }
            except (json.JSONDecodeError, Exception) as e:
                logger.debug("Failed to load incidents: %s", e)

        if budget_path.exists():
            try:
                data = json.loads(budget_path.read_text())
                self._spend_log = data.get("spend_log", [])
            except (json.JSONDecodeError, Exception) as e:
                logger.debug("Failed to load budget state: %s", e)

        return {"incidents": len(self._incidents), "spend_entries": len(self._spend_log)}

    def save_state(self) -> None:
        """Persist all state to disk."""
        incidents_path = self._monitoring_dir / "incidents.json"
        budget_path = self._monitoring_dir / "budget.json"

        incidents_data = {
            "incidents": {
                k: v.model_dump() for k, v in self._incidents.items()
            },
        }
        incidents_path.write_text(json.dumps(incidents_data, indent=2, default=str))

        budget_data = {
            "budget": self._budget.model_dump(),
            "spend_log": self._spend_log,
        }
        budget_path.write_text(json.dumps(budget_data, indent=2, default=str))

    def get_recent_incidents(self, limit: int = 20) -> list[Incident]:
        """Get recent incidents sorted by creation time (newest first)."""
        incidents = sorted(
            self._incidents.values(),
            key=lambda i: i.created_at,
            reverse=True,
        )
        return incidents[:limit]
