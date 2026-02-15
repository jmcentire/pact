"""Drift detection and staleness tracking for Pact artifacts.

Captures hash baselines after successful builds and detects when
artifacts have been modified without updating related artifacts.

Storage: .pact/baselines/{component_id}.json
"""

from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


class ArtifactBaseline(BaseModel):
    """Hash baseline for drift detection."""
    component_id: str
    contract_hash: str = Field(default="", description="SHA256 of interface.json")
    test_hash: str = Field(default="", description="SHA256 of contract_test.py")
    impl_hash: str = Field(default="", description="SHA256 of implementation files concatenated")
    captured_at: str = Field(default="", description="ISO 8601 timestamp")
    test_passed: bool = Field(default=False, description="Whether tests passed at capture time")

    @classmethod
    def from_component(cls, component_id: str, project_dir: Path) -> "ArtifactBaseline":
        """Capture current hashes for a component's artifacts."""
        pact_dir = project_dir / ".pact"
        
        contract_hash = _hash_file(pact_dir / "contracts" / component_id / "interface.json")
        test_hash = _hash_file(pact_dir / "contracts" / component_id / "tests" / "contract_test.py")
        
        # Hash all implementation files concatenated
        impl_dir = pact_dir / "implementations" / component_id / "src"
        impl_hash = _hash_directory(impl_dir) if impl_dir.exists() else ""
        
        return cls(
            component_id=component_id,
            contract_hash=contract_hash,
            test_hash=test_hash,
            impl_hash=impl_hash,
            captured_at=datetime.now(timezone.utc).isoformat(),
        )


def _hash_file(path: Path) -> str:
    """SHA256 hash of a file. Returns empty string if file doesn't exist."""
    if not path.exists():
        return ""
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _hash_directory(directory: Path) -> str:
    """SHA256 hash of all files in a directory, sorted by name."""
    if not directory.exists():
        return ""
    hasher = hashlib.sha256()
    for path in sorted(directory.rglob("*")):
        if path.is_file():
            hasher.update(path.name.encode())
            hasher.update(path.read_bytes())
    return hasher.hexdigest()


def capture_baseline(component_id: str, project_dir: Path, test_passed: bool = True) -> ArtifactBaseline:
    """Capture and save current hashes for a component's artifacts.
    
    Saves to .pact/baselines/{component_id}.json
    """
    baseline = ArtifactBaseline.from_component(component_id, project_dir)
    baseline.test_passed = test_passed
    
    baselines_dir = project_dir / ".pact" / "baselines"
    baselines_dir.mkdir(parents=True, exist_ok=True)
    
    path = baselines_dir / f"{component_id}.json"
    path.write_text(baseline.model_dump_json(indent=2))
    
    return baseline


def load_baseline(component_id: str, project_dir: Path) -> ArtifactBaseline | None:
    """Load a saved baseline. Returns None if not found."""
    path = project_dir / ".pact" / "baselines" / f"{component_id}.json"
    if not path.exists():
        return None
    try:
        return ArtifactBaseline.model_validate_json(path.read_text())
    except Exception:
        return None


def detect_drift(
    baseline: ArtifactBaseline,
    project_dir: Path,
) -> list[str]:
    """Compare current file hashes against baseline.

    Returns:
      List of drift descriptions, e.g.:
        ["implementation changed (hash mismatch) but contract version unchanged"]
    """
    current = ArtifactBaseline.from_component(baseline.component_id, project_dir)
    drifts = []

    # Contract changed
    contract_changed = (
        baseline.contract_hash and current.contract_hash and
        baseline.contract_hash != current.contract_hash
    )
    
    # Test changed
    test_changed = (
        baseline.test_hash and current.test_hash and
        baseline.test_hash != current.test_hash
    )
    
    # Implementation changed
    impl_changed = (
        baseline.impl_hash and current.impl_hash and
        baseline.impl_hash != current.impl_hash
    )

    if contract_changed and not test_changed:
        drifts.append(
            f"Contract for '{baseline.component_id}' changed but tests were not updated"
        )
    
    if impl_changed and not contract_changed:
        drifts.append(
            f"Implementation of '{baseline.component_id}' changed but contract version unchanged"
        )
    
    if contract_changed and not impl_changed:
        drifts.append(
            f"Contract for '{baseline.component_id}' changed but implementation not updated"
        )

    return drifts


class StalenessCheck(BaseModel):
    """Result of checking a component's staleness."""
    component_id: str
    status: Literal["fresh", "aging", "stale"] = "fresh"
    reason: str = ""
    days_since_verification: int = 0
    dependency_updates_since: int = 0


def check_staleness(
    component_id: str,
    baseline: ArtifactBaseline,
    dependency_baselines: dict[str, ArtifactBaseline] | None = None,
    staleness_window_days: int = 90,
) -> StalenessCheck:
    """Determine if a component's contract is stale.

    Rules:
      - fresh: verified within staleness_window, no dependency changes
      - aging: verified within staleness_window, but dependencies have changed
      - stale: not verified within staleness_window OR dependencies changed + not re-verified
    """
    now = datetime.now(timezone.utc)
    
    # Parse captured_at timestamp
    try:
        captured = datetime.fromisoformat(baseline.captured_at.replace("Z", "+00:00"))
        if captured.tzinfo is None:
            captured = captured.replace(tzinfo=timezone.utc)
        days_since = (now - captured).days
    except (ValueError, AttributeError):
        days_since = staleness_window_days + 1  # Treat unparseable as stale
    
    # Count dependency updates since baseline
    dep_updates = 0
    if dependency_baselines:
        for dep_id, dep_baseline in dependency_baselines.items():
            try:
                dep_captured = datetime.fromisoformat(
                    dep_baseline.captured_at.replace("Z", "+00:00")
                )
                if dep_captured.tzinfo is None:
                    dep_captured = dep_captured.replace(tzinfo=timezone.utc)
                baseline_captured = datetime.fromisoformat(
                    baseline.captured_at.replace("Z", "+00:00")
                )
                if baseline_captured.tzinfo is None:
                    baseline_captured = baseline_captured.replace(tzinfo=timezone.utc)
                if dep_captured > baseline_captured:
                    dep_updates += 1
            except (ValueError, AttributeError):
                continue
    
    # Determine status
    if days_since > staleness_window_days:
        return StalenessCheck(
            component_id=component_id,
            status="stale",
            reason=f"Not verified in {days_since} days (window: {staleness_window_days})",
            days_since_verification=days_since,
            dependency_updates_since=dep_updates,
        )
    
    if dep_updates > 0:
        return StalenessCheck(
            component_id=component_id,
            status="aging",
            reason=f"{dep_updates} dependencies updated since last verification",
            days_since_verification=days_since,
            dependency_updates_since=dep_updates,
        )
    
    return StalenessCheck(
        component_id=component_id,
        status="fresh",
        reason=f"Verified {days_since} days ago, no dependency changes",
        days_since_verification=days_since,
        dependency_updates_since=0,
    )
