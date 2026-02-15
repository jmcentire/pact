"""Sentinel — long-running production monitor and auto-remediation coordinator.

The Sentinel watches configured signal sources (log files, processes, webhooks)
and dispatches knowledge-flashed fixer agents when errors are detected.

Lifecycle:
1. Load all monitoring targets from config
2. Start signal ingestion (log tailers, process watchers, webhook receivers)
3. On new signal:
   a. Fingerprint and deduplicate
   b. Match to project/component (log key or LLM triage)
   c. Create incident
   d. Alert via integrations (always)
   e. Check budget
   f. If budget OK and auto_remediate: spawn fixer
   g. If fixer succeeds: close incident as "auto_fixed", notify
   h. If fixer fails or budget exceeded: escalate with diagnostic report
4. Persist state between restarts
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from pathlib import Path

from pact.config import GlobalConfig
from pact.events import EventBus, PactEvent
from pact.incidents import IncidentManager
from pact.schemas_monitoring import (
    Incident,
    MonitoringBudget,
    MonitoringTarget,
    Signal,
)
from pact.signals import (
    SignalIngester,
    extract_log_key,
    fingerprint_signal,
    match_signal_to_project,
)

logger = logging.getLogger(__name__)


class Sentinel:
    """Long-running process that watches for errors and dispatches fixers."""

    def __init__(
        self,
        config: GlobalConfig,
        targets: list[MonitoringTarget],
        state_dir: Path,
        event_bus: EventBus | None = None,
    ) -> None:
        self._config = config
        self._targets = targets
        self._state_dir = state_dir
        self._event_bus = event_bus
        self._running = False

        # Parse budget from config
        budget_dict = config.monitoring_budget or {}
        self._budget = MonitoringBudget(**budget_dict) if budget_dict else MonitoringBudget()

        # Auto-remediate flag
        self._auto_remediate = config.monitoring_auto_remediate

        # Incident manager
        self._incident_mgr = IncidentManager(state_dir, self._budget)

        # Signal ingester
        self._ingester = SignalIngester(targets)

        # Project dir -> target mapping for quick lookup
        self._target_map: dict[str, MonitoringTarget] = {
            t.project_dir: t for t in targets
        }

    async def run(self) -> None:
        """Main loop — run until stopped."""
        self._running = True
        logger.info("Sentinel starting: watching %d projects", len(self._targets))

        await self._ingester.start()

        try:
            async for signal, target in self._ingester.watch():
                if not self._running:
                    break
                try:
                    await self.handle_signal(signal, target)
                except Exception as e:
                    logger.debug("Error handling signal: %s", e)
        except asyncio.CancelledError:
            pass
        finally:
            self._ingester.stop()
            logger.info("Sentinel stopped")

    async def handle_signal(
        self,
        signal: Signal,
        target: MonitoringTarget | None,
    ) -> None:
        """Process a single signal through the full pipeline."""
        # Step 1: Match to project/component
        match = match_signal_to_project(signal, self._targets)
        if not match and target:
            project_dir = target.project_dir
            component_id = ""
        elif match:
            project_dir, component_id = match
        else:
            logger.debug("Signal could not be matched to any project: %s", signal.raw_text[:100])
            return

        # Step 2: Check for existing incident with same fingerprint (any status)
        fp_hash = fingerprint_signal(signal)
        existing = self._find_incident_by_fingerprint(fp_hash)
        if existing:
            self._incident_mgr.add_signal(existing.id, signal)
            return

        # Step 3: Create incident
        incident = self._incident_mgr.create_incident(
            signal, project_dir, component_id,
        )
        incident.fingerprint = _make_fingerprint(signal, fp_hash)
        self._incident_mgr.save_state()

        # Step 4: Alert (always, regardless of auto_remediate)
        await self._alert(incident)

        # Step 5: If no component identified and we have an agent, try LLM triage
        if not component_id:
            component_id = await self._triage(incident, project_dir)
            if component_id:
                incident.component_id = component_id
                self._incident_mgr.save_state()

        # Step 6: Check budget and auto-remediate
        if self._auto_remediate and component_id:
            if self._incident_mgr.check_budget(incident.id):
                success = await self._spawn_fixer(incident)
                if success:
                    self._incident_mgr.close_incident(
                        incident.id, "auto_fixed",
                        f"Auto-fixed by Sentinel at {datetime.now().isoformat()}",
                    )
                    await self._notify_resolved(incident)
                else:
                    await self._escalate(incident)
            else:
                incident.resolution = "budget_exceeded"
                await self._escalate(incident)
        elif not self._auto_remediate:
            # Alert-only mode: escalate immediately with diagnostics
            await self._escalate(incident)

    async def handle_manual_report(
        self,
        project_dir: str,
        error_text: str,
    ) -> Incident:
        """Handle a manually reported error (from CLI or webhook)."""
        signal = Signal(
            source="manual",
            raw_text=error_text,
            timestamp=datetime.now().isoformat(),
        )
        await self.handle_signal(
            signal,
            self._target_map.get(project_dir),
        )
        # Return the most recently created incident
        recent = self._incident_mgr.get_recent_incidents(1)
        return recent[0] if recent else self._incident_mgr.create_incident(
            signal, project_dir,
        )

    async def _spawn_fixer(self, incident: Incident) -> bool:
        """Flash knowledge and attempt remediation. Returns success."""
        from pact.project import ProjectManager
        from pact.remediator import remediate_incident

        self._incident_mgr.update_status(incident.id, "remediating")

        # Notify that remediation is in progress
        if self._event_bus:
            await self._event_bus.emit(PactEvent(
                kind="incident_remediating",
                project_name=Path(incident.project_dir).name,
                component_id=incident.component_id,
                detail=f"Incident {incident.id}: auto-remediating {incident.component_id}",
            ))

        project = ProjectManager(incident.project_dir)

        # Create a fresh agent for the fixer
        from pact.agents.base import AgentBase
        from pact.budget import BudgetTracker

        budget = BudgetTracker(per_project_cap=self._budget.per_incident_cap)
        agent = AgentBase(budget=budget, model=self._config.model)

        try:
            success, summary = await remediate_incident(
                incident=incident,
                project=project,
                agent_or_factory=agent,
                event_bus=self._event_bus,
            )

            # Record spend
            self._incident_mgr.record_spend(incident.id, budget.project_spend)

            return success
        except Exception as e:
            logger.debug("Fixer failed for incident %s: %s", incident.id, e)
            return False
        finally:
            try:
                await agent.close()
            except Exception:
                pass

    async def _triage(self, incident: Incident, project_dir: str) -> str:
        """Attempt LLM-based triage to identify the component."""
        try:
            from pact.agents.base import AgentBase
            from pact.agents.triage import triage_signal
            from pact.budget import BudgetTracker
            from pact.project import ProjectManager

            self._incident_mgr.update_status(incident.id, "triaging")

            project = ProjectManager(project_dir)
            tree = project.load_tree()
            contracts = project.load_all_contracts()

            if not tree or not contracts:
                return ""

            budget = BudgetTracker(per_project_cap=1.00)
            agent = AgentBase(budget=budget, model=self._config.model)

            try:
                signal = incident.signals[0] if incident.signals else Signal(
                    source="manual", raw_text="", timestamp="",
                )
                result = await triage_signal(agent, signal, project, tree, contracts)
                self._incident_mgr.record_spend(incident.id, budget.project_spend)
                return result or ""
            finally:
                try:
                    await agent.close()
                except Exception:
                    pass
        except Exception as e:
            logger.debug("Triage failed: %s", e)
            return ""

    async def _alert(self, incident: Incident) -> None:
        """Send alert notification for a new incident."""
        if self._event_bus:
            await self._event_bus.emit(PactEvent(
                kind="incident_detected",
                project_name=Path(incident.project_dir).name,
                component_id=incident.component_id,
                detail=(
                    f"Incident {incident.id}: "
                    f"{incident.signals[0].raw_text[:100] if incident.signals else 'unknown error'}"
                ),
            ))

    async def _notify_resolved(self, incident: Incident) -> None:
        """Notify that an incident was auto-resolved."""
        if self._event_bus:
            await self._event_bus.emit(PactEvent(
                kind="incident_resolved",
                project_name=Path(incident.project_dir).name,
                component_id=incident.component_id,
                detail=f"Incident {incident.id}: auto-fixed (${incident.spend_usd:.2f})",
            ))

    async def _escalate(self, incident: Incident) -> None:
        """Escalate an incident with a diagnostic report."""
        self._incident_mgr.update_status(incident.id, "escalated")

        # Generate diagnostic report
        report = (
            f"# Diagnostic Report: Incident {incident.id}\n\n"
            f"**Status:** Escalated\n"
            f"**Component:** {incident.component_id or 'unknown'}\n"
            f"**Spend:** ${incident.spend_usd:.2f}\n"
            f"**Attempts:** {incident.remediation_attempts}\n\n"
            f"## Error Signals\n"
        )
        for s in incident.signals[:5]:
            report += f"- [{s.source}] {s.raw_text[:200]}\n"
        report += f"\n## Resolution\n{incident.resolution or 'Escalated for manual review'}\n"

        self._incident_mgr.close_incident(incident.id, "escalated", report)

        if self._event_bus:
            await self._event_bus.emit(PactEvent(
                kind="incident_escalated",
                project_name=Path(incident.project_dir).name,
                component_id=incident.component_id,
                detail=f"Incident {incident.id}: escalated — {incident.resolution or 'needs human review'}",
            ))

    def _find_incident_by_fingerprint(self, fp_hash: str) -> Incident | None:
        """Find any incident (active or closed) matching a fingerprint hash."""
        for incident in self._incident_mgr.get_recent_incidents(100):
            if incident.fingerprint and incident.fingerprint.hash == fp_hash:
                return incident
        return None

    def stop(self) -> None:
        """Signal graceful shutdown."""
        self._running = False
        self._ingester.stop()


def _make_fingerprint(signal: Signal, fp_hash: str) -> "SignalFingerprint":
    """Create a fingerprint from a signal."""
    from pact.schemas_monitoring import SignalFingerprint

    now = datetime.now().isoformat()
    return SignalFingerprint(
        hash=fp_hash,
        first_seen=now,
        last_seen=now,
        count=1,
        representative=signal,
    )
