"""Tests for drift detection and staleness tracking."""
import json
from datetime import datetime, timezone, timedelta
from pathlib import Path

from pact.drift import (
    ArtifactBaseline,
    capture_baseline,
    load_baseline,
    detect_drift,
    StalenessCheck,
    check_staleness,
    _hash_file,
    _hash_directory,
)


def _setup_component(tmp_path: Path, component_id: str = "comp_a"):
    """Create minimal component file structure."""
    pact = tmp_path / ".pact"
    contracts = pact / "contracts" / component_id
    tests = contracts / "tests"
    impls = pact / "implementations" / component_id / "src"
    baselines = pact / "baselines"
    
    for d in [contracts, tests, impls, baselines]:
        d.mkdir(parents=True, exist_ok=True)
    
    (contracts / "interface.json").write_text('{"component_id": "comp_a"}')
    (tests / "contract_test.py").write_text("def test_one(): pass")
    (impls / "main.py").write_text("def main(): return 42")
    
    return pact


class TestHashFile:
    def test_existing_file(self, tmp_path):
        f = tmp_path / "test.txt"
        f.write_text("hello")
        h = _hash_file(f)
        assert len(h) == 64  # SHA256 hex

    def test_nonexistent_file(self, tmp_path):
        h = _hash_file(tmp_path / "nope.txt")
        assert h == ""

    def test_same_content_same_hash(self, tmp_path):
        f1 = tmp_path / "a.txt"
        f2 = tmp_path / "b.txt"
        f1.write_text("same")
        f2.write_text("same")
        assert _hash_file(f1) == _hash_file(f2)

    def test_different_content_different_hash(self, tmp_path):
        f1 = tmp_path / "a.txt"
        f2 = tmp_path / "b.txt"
        f1.write_text("hello")
        f2.write_text("world")
        assert _hash_file(f1) != _hash_file(f2)


class TestArtifactBaseline:
    def test_from_component(self, tmp_path):
        _setup_component(tmp_path)
        baseline = ArtifactBaseline.from_component("comp_a", tmp_path)
        assert baseline.component_id == "comp_a"
        assert baseline.contract_hash != ""
        assert baseline.test_hash != ""
        assert baseline.impl_hash != ""
        assert baseline.captured_at != ""

    def test_from_component_missing_files(self, tmp_path):
        (tmp_path / ".pact" / "contracts" / "comp_x").mkdir(parents=True)
        baseline = ArtifactBaseline.from_component("comp_x", tmp_path)
        assert baseline.contract_hash == ""
        assert baseline.test_hash == ""
        assert baseline.impl_hash == ""


class TestCaptureAndLoad:
    def test_capture_saves_file(self, tmp_path):
        _setup_component(tmp_path)
        baseline = capture_baseline("comp_a", tmp_path)
        path = tmp_path / ".pact" / "baselines" / "comp_a.json"
        assert path.exists()
        assert baseline.test_passed is True

    def test_load_roundtrip(self, tmp_path):
        _setup_component(tmp_path)
        original = capture_baseline("comp_a", tmp_path)
        loaded = load_baseline("comp_a", tmp_path)
        assert loaded is not None
        assert loaded.contract_hash == original.contract_hash
        assert loaded.test_hash == original.test_hash
        assert loaded.impl_hash == original.impl_hash

    def test_load_nonexistent(self, tmp_path):
        result = load_baseline("nope", tmp_path)
        assert result is None


class TestDetectDrift:
    def test_no_drift_clean(self, tmp_path):
        _setup_component(tmp_path)
        baseline = capture_baseline("comp_a", tmp_path)
        drifts = detect_drift(baseline, tmp_path)
        assert drifts == []

    def test_impl_drift_detected(self, tmp_path):
        _setup_component(tmp_path)
        baseline = capture_baseline("comp_a", tmp_path)
        # Modify implementation
        impl = tmp_path / ".pact" / "implementations" / "comp_a" / "src" / "main.py"
        impl.write_text("def main(): return 99")
        drifts = detect_drift(baseline, tmp_path)
        assert len(drifts) >= 1
        assert any("implementation" in d.lower() for d in drifts)

    def test_contract_drift_without_test_update(self, tmp_path):
        _setup_component(tmp_path)
        baseline = capture_baseline("comp_a", tmp_path)
        # Modify contract only
        contract = tmp_path / ".pact" / "contracts" / "comp_a" / "interface.json"
        contract.write_text('{"component_id": "comp_a", "version": 2}')
        drifts = detect_drift(baseline, tmp_path)
        assert len(drifts) >= 1
        assert any("contract" in d.lower() and "test" in d.lower() for d in drifts)

    def test_contract_and_test_updated_no_impl_drift(self, tmp_path):
        """Updating both contract and tests is expected — no drift for that pair."""
        _setup_component(tmp_path)
        baseline = capture_baseline("comp_a", tmp_path)
        # Modify both contract and tests
        contract = tmp_path / ".pact" / "contracts" / "comp_a" / "interface.json"
        contract.write_text('{"component_id": "comp_a", "version": 2}')
        test = tmp_path / ".pact" / "contracts" / "comp_a" / "tests" / "contract_test.py"
        test.write_text("def test_updated(): pass")
        drifts = detect_drift(baseline, tmp_path)
        # Should report that impl needs updating since contract changed
        assert any("implementation" in d.lower() for d in drifts)


class TestStaleness:
    def test_fresh_within_window(self):
        baseline = ArtifactBaseline(
            component_id="comp_a",
            captured_at=(datetime.now(timezone.utc) - timedelta(days=30)).isoformat(),
        )
        result = check_staleness("comp_a", baseline)
        assert result.status == "fresh"
        assert result.days_since_verification <= 31

    def test_stale_past_window(self):
        baseline = ArtifactBaseline(
            component_id="comp_a",
            captured_at=(datetime.now(timezone.utc) - timedelta(days=100)).isoformat(),
        )
        result = check_staleness("comp_a", baseline, staleness_window_days=90)
        assert result.status == "stale"
        assert result.days_since_verification >= 99

    def test_aging_dep_changed(self):
        baseline = ArtifactBaseline(
            component_id="comp_a",
            captured_at=(datetime.now(timezone.utc) - timedelta(days=30)).isoformat(),
        )
        dep_baseline = ArtifactBaseline(
            component_id="comp_b",
            captured_at=(datetime.now(timezone.utc) - timedelta(days=10)).isoformat(),
        )
        result = check_staleness(
            "comp_a", baseline,
            dependency_baselines={"comp_b": dep_baseline},
        )
        assert result.status == "aging"
        assert result.dependency_updates_since == 1

    def test_fresh_no_deps(self):
        baseline = ArtifactBaseline(
            component_id="comp_a",
            captured_at=datetime.now(timezone.utc).isoformat(),
        )
        result = check_staleness("comp_a", baseline)
        assert result.status == "fresh"
        assert result.dependency_updates_since == 0

    def test_custom_staleness_window(self):
        baseline = ArtifactBaseline(
            component_id="comp_a",
            captured_at=(datetime.now(timezone.utc) - timedelta(days=15)).isoformat(),
        )
        # With 10-day window, should be stale
        result = check_staleness("comp_a", baseline, staleness_window_days=10)
        assert result.status == "stale"
        # With 30-day window, should be fresh
        result = check_staleness("comp_a", baseline, staleness_window_days=30)
        assert result.status == "fresh"

    def test_unparseable_timestamp_is_stale(self):
        baseline = ArtifactBaseline(
            component_id="comp_a",
            captured_at="not-a-date",
        )
        result = check_staleness("comp_a", baseline)
        assert result.status == "stale"
