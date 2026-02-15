"""Tests for signal ingestion, fingerprinting, and project matching."""

from __future__ import annotations

import asyncio
from datetime import datetime

import pytest

from pact.schemas_monitoring import MonitoringTarget, Signal
from pact.signals import (
    SignalIngester,
    _project_id_hash,
    extract_log_key,
    fingerprint_signal,
    match_signal_to_project,
)


class TestExtractLogKey:
    def test_valid_key(self):
        key = extract_log_key("[PACT:abc123:pricing_engine] ERROR: price is None")
        assert key is not None
        assert key.project_id == "abc123"
        assert key.component_id == "pricing_engine"

    def test_key_at_start(self):
        key = extract_log_key("PACT:abc123:comp_id some text")
        assert key is not None
        assert key.project_id == "abc123"

    def test_no_key_returns_none(self):
        key = extract_log_key("ERROR: something went wrong")
        assert key is None

    def test_partial_key_no_match(self):
        key = extract_log_key("PACT:abc123")
        assert key is None

    def test_empty_string(self):
        key = extract_log_key("")
        assert key is None

    def test_key_with_underscores(self):
        key = extract_log_key("PACT:a1b2c3:my_component_name INFO: ok")
        assert key is not None
        assert key.component_id == "my_component_name"

    def test_key_with_numbers(self):
        key = extract_log_key("PACT:123456:comp123")
        assert key is not None
        assert key.project_id == "123456"
        assert key.component_id == "comp123"


class TestFingerprintSignal:
    def test_same_error_different_timestamps(self):
        s1 = Signal(
            source="log_file",
            raw_text="2024-01-01T12:00:00 ERROR: division by zero at line 42",
            timestamp="2024-01-01T12:00:00",
        )
        s2 = Signal(
            source="log_file",
            raw_text="2024-06-15T08:30:00 ERROR: division by zero at line 42",
            timestamp="2024-06-15T08:30:00",
        )
        assert fingerprint_signal(s1) == fingerprint_signal(s2)

    def test_different_errors_different_hashes(self):
        s1 = Signal(
            source="log_file",
            raw_text="ERROR: division by zero",
            timestamp="2024-01-01T00:00:00",
        )
        s2 = Signal(
            source="log_file",
            raw_text="ERROR: index out of range",
            timestamp="2024-01-01T00:00:00",
        )
        assert fingerprint_signal(s1) != fingerprint_signal(s2)

    def test_strips_line_numbers(self):
        s1 = Signal(
            source="log_file",
            raw_text="ERROR: failure at file.py:42",
            timestamp="2024-01-01T00:00:00",
        )
        s2 = Signal(
            source="log_file",
            raw_text="ERROR: failure at file.py:99",
            timestamp="2024-01-01T00:00:00",
        )
        assert fingerprint_signal(s1) == fingerprint_signal(s2)

    def test_strips_memory_addresses(self):
        s1 = Signal(
            source="log_file",
            raw_text="ERROR: object at 0x7fff12345678 is None",
            timestamp="2024-01-01T00:00:00",
        )
        s2 = Signal(
            source="log_file",
            raw_text="ERROR: object at 0xdeadbeef0000 is None",
            timestamp="2024-01-01T00:00:00",
        )
        assert fingerprint_signal(s1) == fingerprint_signal(s2)

    def test_hash_is_deterministic(self):
        s = Signal(
            source="log_file",
            raw_text="ERROR: something",
            timestamp="2024-01-01T00:00:00",
        )
        assert fingerprint_signal(s) == fingerprint_signal(s)

    def test_hash_is_16_chars(self):
        s = Signal(
            source="log_file",
            raw_text="ERROR: test",
            timestamp="2024-01-01T00:00:00",
        )
        assert len(fingerprint_signal(s)) == 16


class TestMatchSignalToProject:
    def _make_targets(self) -> list[MonitoringTarget]:
        return [
            MonitoringTarget(
                project_dir="/tmp/project_a",
                log_files=["/var/log/a.log"],
                process_patterns=["app_a"],
            ),
            MonitoringTarget(
                project_dir="/tmp/project_b",
                log_files=["/var/log/b.log"],
                process_patterns=["app_b"],
            ),
        ]

    def test_match_by_log_key(self):
        targets = self._make_targets()
        pid = _project_id_hash("/tmp/project_a")
        signal = Signal(
            source="log_file",
            raw_text=f"PACT:{pid}:pricing ERROR: failed",
            timestamp="2024-01-01T00:00:00",
            log_key=f"PACT:{pid}:pricing",
        )
        result = match_signal_to_project(signal, targets)
        assert result is not None
        assert result[0] == "/tmp/project_a"
        assert result[1] == "pricing"

    def test_match_by_file_path(self):
        targets = self._make_targets()
        signal = Signal(
            source="log_file",
            raw_text="ERROR: something",
            timestamp="2024-01-01T00:00:00",
            file_path="/var/log/b.log",
        )
        result = match_signal_to_project(signal, targets)
        assert result is not None
        assert result[0] == "/tmp/project_b"
        assert result[1] == ""

    def test_match_by_process_name(self):
        targets = self._make_targets()
        signal = Signal(
            source="process",
            raw_text="Process crashed",
            timestamp="2024-01-01T00:00:00",
            process_name="app_a_worker",
        )
        result = match_signal_to_project(signal, targets)
        assert result is not None
        assert result[0] == "/tmp/project_a"

    def test_no_match_returns_none(self):
        targets = self._make_targets()
        signal = Signal(
            source="log_file",
            raw_text="ERROR: unknown",
            timestamp="2024-01-01T00:00:00",
        )
        result = match_signal_to_project(signal, targets)
        assert result is None

    def test_log_key_in_raw_text(self):
        targets = self._make_targets()
        pid = _project_id_hash("/tmp/project_b")
        signal = Signal(
            source="log_file",
            raw_text=f"[PACT:{pid}:sync] ERROR: sync failed",
            timestamp="2024-01-01T00:00:00",
        )
        result = match_signal_to_project(signal, targets)
        assert result is not None
        assert result[0] == "/tmp/project_b"
        assert result[1] == "sync"


class TestSignalIngesterDedup:
    def test_same_signal_deduped(self):
        ingester = SignalIngester([], dedup_window_seconds=300)
        s1 = Signal(
            source="log_file",
            raw_text="ERROR: test error",
            timestamp="2024-01-01T00:00:00",
        )
        s2 = Signal(
            source="log_file",
            raw_text="ERROR: test error",
            timestamp="2024-01-01T00:00:01",
        )
        # First signal should pass dedup
        assert ingester._deduplicate(s1) is True
        # Same signal within window should be deduped
        assert ingester._deduplicate(s2) is False

    def test_different_signals_not_deduped(self):
        ingester = SignalIngester([], dedup_window_seconds=300)
        s1 = Signal(
            source="log_file",
            raw_text="ERROR: first error",
            timestamp="2024-01-01T00:00:00",
        )
        s2 = Signal(
            source="log_file",
            raw_text="ERROR: second completely different error",
            timestamp="2024-01-01T00:00:01",
        )
        assert ingester._deduplicate(s1) is True
        assert ingester._deduplicate(s2) is True


class TestProjectIdHash:
    def test_deterministic(self):
        assert _project_id_hash("/tmp/proj") == _project_id_hash("/tmp/proj")

    def test_different_paths_different_hashes(self):
        assert _project_id_hash("/tmp/a") != _project_id_hash("/tmp/b")

    def test_six_chars(self):
        assert len(_project_id_hash("/tmp/proj")) == 6
