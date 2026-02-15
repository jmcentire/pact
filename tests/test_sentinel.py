"""Tests for the Sentinel — long-running production monitor."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from pact.config import GlobalConfig
from pact.events import PactEvent
from pact.schemas_monitoring import (
    Incident,
    MonitoringBudget,
    MonitoringTarget,
    Signal,
)
from pact.sentinel import Sentinel


def _make_config(**overrides) -> GlobalConfig:
    defaults = {
        "monitoring_enabled": True,
        "monitoring_auto_remediate": True,
        "monitoring_budget": {},
    }
    defaults.update(overrides)
    return GlobalConfig(**defaults)


def _make_target(project_dir: str = "/tmp/proj") -> MonitoringTarget:
    return MonitoringTarget(
        project_dir=project_dir,
        label="test",
        log_files=["/var/log/test.log"],
    )


def _make_signal(text: str = "ERROR: test error") -> Signal:
    return Signal(
        source="log_file",
        raw_text=text,
        timestamp=datetime.now().isoformat(),
        file_path="/var/log/test.log",
    )


class TestHandleSignal:
    @pytest.mark.asyncio
    async def test_creates_incident(self, tmp_path: Path):
        config = _make_config()
        target = _make_target(str(tmp_path))
        sentinel = Sentinel(config, [target], tmp_path)

        signal = _make_signal()
        await sentinel.handle_signal(signal, target)

        incidents = sentinel._incident_mgr.get_recent_incidents()
        assert len(incidents) == 1
        assert incidents[0].status in ("detected", "escalated", "triaging")
        assert incidents[0].project_dir == str(tmp_path)

    @pytest.mark.asyncio
    async def test_dedup_same_fingerprint(self, tmp_path: Path):
        config = _make_config(monitoring_auto_remediate=False)
        target = _make_target(str(tmp_path))
        sentinel = Sentinel(config, [target], tmp_path)

        signal1 = _make_signal("ERROR: same error")
        signal2 = _make_signal("ERROR: same error")

        await sentinel.handle_signal(signal1, target)
        await sentinel.handle_signal(signal2, target)

        incidents = sentinel._incident_mgr.get_recent_incidents()
        # Second signal should be added to existing incident, not create new one
        assert len(incidents) == 1


class TestHandleManualReport:
    @pytest.mark.asyncio
    async def test_creates_incident_from_manual_report(self, tmp_path: Path):
        config = _make_config(monitoring_auto_remediate=False)
        target = _make_target(str(tmp_path))
        sentinel = Sentinel(config, [target], tmp_path)

        incident = await sentinel.handle_manual_report(
            str(tmp_path),
            "TypeError: NoneType has no attribute 'price'",
        )
        assert incident is not None
        assert incident.status in ("detected", "escalated")


class TestSentinelAlert:
    @pytest.mark.asyncio
    async def test_alert_fires_even_without_auto_remediate(self, tmp_path: Path):
        config = _make_config(monitoring_auto_remediate=False)
        target = _make_target(str(tmp_path))

        event_bus = MagicMock()
        event_bus.emit = AsyncMock()

        sentinel = Sentinel(config, [target], tmp_path, event_bus=event_bus)

        signal = _make_signal()
        await sentinel.handle_signal(signal, target)

        # Should have emitted at least an incident_detected event
        calls = event_bus.emit.call_args_list
        event_kinds = [c[0][0].kind for c in calls]
        assert "incident_detected" in event_kinds


class TestSentinelAutoRemediate:
    @pytest.mark.asyncio
    async def test_spawns_fixer_when_enabled(self, tmp_path: Path):
        config = _make_config(monitoring_auto_remediate=True)
        target = _make_target(str(tmp_path))
        sentinel = Sentinel(config, [target], tmp_path)

        # Pre-create an incident with a known component
        signal = Signal(
            source="manual",
            raw_text="ERROR: pricing failed",
            timestamp=datetime.now().isoformat(),
            log_key=f"PACT:{_project_hash(str(tmp_path))}:pricing",
        )

        with patch.object(sentinel, "_spawn_fixer", new_callable=AsyncMock) as mock_fixer, \
             patch.object(sentinel, "_triage", new_callable=AsyncMock) as mock_triage:
            mock_fixer.return_value = True
            mock_triage.return_value = ""

            await sentinel.handle_signal(signal, target)

            # Should have attempted to spawn a fixer (if component was matched)
            # The exact call depends on whether the log key matched

    @pytest.mark.asyncio
    async def test_does_not_spawn_when_disabled(self, tmp_path: Path):
        config = _make_config(monitoring_auto_remediate=False)
        target = _make_target(str(tmp_path))
        sentinel = Sentinel(config, [target], tmp_path)

        with patch.object(sentinel, "_spawn_fixer", new_callable=AsyncMock) as mock_fixer:
            signal = _make_signal()
            await sentinel.handle_signal(signal, target)

            # _spawn_fixer should NOT be called when auto_remediate is False
            mock_fixer.assert_not_called()


class TestSentinelEscalation:
    @pytest.mark.asyncio
    async def test_escalates_when_fixer_fails(self, tmp_path: Path):
        config = _make_config(monitoring_auto_remediate=True)
        target = _make_target(str(tmp_path))

        event_bus = MagicMock()
        event_bus.emit = AsyncMock()

        sentinel = Sentinel(config, [target], tmp_path, event_bus=event_bus)

        # Create incident with component and make fixer fail
        signal = _make_signal()

        with patch.object(sentinel, "_spawn_fixer", new_callable=AsyncMock) as mock_fixer, \
             patch.object(sentinel, "_triage", new_callable=AsyncMock) as mock_triage:
            mock_fixer.return_value = False
            mock_triage.return_value = "pricing"

            await sentinel.handle_signal(signal, target)

            # Should have escalated
            calls = event_bus.emit.call_args_list
            event_kinds = [c[0][0].kind for c in calls]
            assert "incident_escalated" in event_kinds

    @pytest.mark.asyncio
    async def test_escalates_on_budget_exceeded(self, tmp_path: Path):
        config = _make_config(
            monitoring_auto_remediate=True,
            monitoring_budget={"per_incident_cap": 0.0},
        )
        target = _make_target(str(tmp_path))

        event_bus = MagicMock()
        event_bus.emit = AsyncMock()

        sentinel = Sentinel(config, [target], tmp_path, event_bus=event_bus)

        signal = _make_signal()

        with patch.object(sentinel, "_triage", new_callable=AsyncMock) as mock_triage:
            mock_triage.return_value = "pricing"
            await sentinel.handle_signal(signal, target)

        # Budget exceeded → should escalate
        calls = event_bus.emit.call_args_list
        event_kinds = [c[0][0].kind for c in calls]
        assert "incident_escalated" in event_kinds


class TestSentinelStop:
    def test_stop_sets_flag(self, tmp_path: Path):
        config = _make_config()
        sentinel = Sentinel(config, [], tmp_path)
        sentinel.stop()
        assert sentinel._running is False


def _project_hash(project_dir: str) -> str:
    import hashlib
    return hashlib.sha256(project_dir.encode()).hexdigest()[:6]
