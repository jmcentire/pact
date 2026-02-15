"""Tests for daemon ActivityTracker."""
import time
from pact.daemon import ActivityTracker


class TestActivityTracker:
    def test_initial_state(self):
        tracker = ActivityTracker()
        assert tracker.idle_seconds() < 1.0
        assert not tracker.is_idle(10)
        assert tracker.last_activity_type == "init"

    def test_record_activity_resets_idle(self):
        tracker = ActivityTracker()
        # Simulate some time passing
        tracker._last_activity = time.monotonic() - 100
        assert tracker.idle_seconds() >= 99

        tracker.record_activity("api_call")
        assert tracker.idle_seconds() < 1.0
        assert tracker.last_activity_type == "api_call"

    def test_is_idle_after_timeout(self):
        tracker = ActivityTracker()
        tracker._last_activity = time.monotonic() - 700
        assert tracker.is_idle(600)

    def test_not_idle_with_recent_activity(self):
        tracker = ActivityTracker()
        tracker.record_activity("state_transition")
        assert not tracker.is_idle(600)

    def test_activity_types(self):
        tracker = ActivityTracker()
        for activity_type in ["api_call", "state_transition", "audit_entry", "fifo_signal", "phase_complete"]:
            tracker.record_activity(activity_type)
            assert tracker.last_activity_type == activity_type
            assert tracker.idle_seconds() < 1.0

    def test_multiple_activities_use_latest(self):
        tracker = ActivityTracker()
        tracker._last_activity = time.monotonic() - 50
        tracker.record_activity("first")
        tracker._last_activity = time.monotonic() - 10
        tracker.record_activity("second")
        assert tracker.idle_seconds() < 1.0
        assert tracker.last_activity_type == "second"
