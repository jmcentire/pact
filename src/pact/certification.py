"""Certification — tamper-evident proof that implementations satisfy all contracts.

The certify() function runs all tests (visible + Goodhart) from the audit repo
against implementations in the code repo, hashes every artifact, and produces a
CertificationArtifact with a self-integrity hash.
"""

from __future__ import annotations

import hashlib
import json
import logging
from datetime import datetime
from pathlib import Path

from pact.project import ProjectManager
from pact.schemas import CertificationArtifact
from pact.test_harness import run_contract_tests

logger = logging.getLogger(__name__)


def _hash_file(path: Path) -> str:
    """SHA-256 hex digest of a file's contents."""
    if not path.exists():
        return ""
    return hashlib.sha256(path.read_bytes()).hexdigest()


def compute_self_hash(cert: CertificationArtifact) -> str:
    """Compute self-integrity hash.

    Serializes the artifact with self_hash="", hashes the JSON, returns hex digest.
    """
    data = cert.model_dump()
    data["self_hash"] = ""
    canonical = json.dumps(data, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()


def verify_certification(cert: CertificationArtifact) -> tuple[bool, list[str]]:
    """Verify a certification artifact's self-hash.

    Returns (valid, [discrepancies]).
    """
    issues: list[str] = []

    if not cert.self_hash:
        issues.append("No self_hash present — certification was not finalized")
        return False, issues

    expected = compute_self_hash(cert)
    if cert.self_hash != expected:
        issues.append(
            f"Self-hash mismatch: recorded {cert.self_hash[:16]}... "
            f"vs computed {expected[:16]}..."
        )
        return False, issues

    return True, []


def verify_artifact_hashes(
    cert: CertificationArtifact,
    project: ProjectManager,
) -> list[str]:
    """Check that current artifacts match the hashes recorded in the certification.

    Returns list of mismatch descriptions (empty = all match).
    """
    mismatches: list[str] = []

    # Tree
    if cert.tree_hash:
        actual = _hash_file(project.tree_path)
        if actual != cert.tree_hash:
            mismatches.append(f"tree.json: expected {cert.tree_hash[:16]}..., got {actual[:16]}...")

    # Contracts
    for cid, expected_hash in cert.contract_hashes.items():
        actual = _hash_file(project.contract_dir(cid) / "interface.json")
        if actual != expected_hash:
            mismatches.append(f"contracts/{cid}/interface.json: hash mismatch")

    # Visible tests
    for cid, expected_hash in cert.test_hashes.items():
        test_dir = project._visible_tests_dir / cid
        actual = _hash_file(test_dir / "contract_test_suite.json")
        if actual != expected_hash:
            mismatches.append(f"tests/{cid}/contract_test_suite.json: hash mismatch")

    # Goodhart tests
    for cid, expected_hash in cert.goodhart_hashes.items():
        goodhart_dir = project._visible_tests_dir / cid / "goodhart"
        actual = _hash_file(goodhart_dir / "goodhart_test_suite.json")
        if actual != expected_hash:
            mismatches.append(f"tests/{cid}/goodhart/goodhart_test_suite.json: hash mismatch")

    return mismatches


async def certify(project: ProjectManager) -> CertificationArtifact:
    """Run full certification: all tests from audit repo against code repo.

    1. Load decomposition tree and all contracts from audit repo
    2. Run visible tests against implementations
    3. Run Goodhart tests against implementations
    4. Hash all artifacts
    5. Compute verdict and self-hash
    """
    cert = CertificationArtifact(
        project_id=project.project_dir.name,
        timestamp=datetime.now().isoformat(),
    )

    # Load tree
    tree = project.load_tree()
    if tree is None:
        cert.summary = "No decomposition tree found"
        cert.self_hash = compute_self_hash(cert)
        return cert

    cert.tree_hash = _hash_file(project.tree_path)
    cert.components = [n.component_id for n in tree.nodes]

    # Load contracts and compute hashes
    contracts = project.load_all_contracts()
    for cid, contract in contracts.items():
        contract_path = project.contract_dir(cid) / "interface.json"
        cert.contract_hashes[cid] = _hash_file(contract_path)

    # Hash test suites
    test_suites = project.load_all_test_suites()
    for cid in test_suites:
        test_json = project._visible_tests_dir / cid / "contract_test_suite.json"
        cert.test_hashes[cid] = _hash_file(test_json)

    goodhart_suites = project.load_all_goodhart_suites()
    for cid in goodhart_suites:
        gh_json = project._visible_tests_dir / cid / "goodhart" / "goodhart_test_suite.json"
        cert.goodhart_hashes[cid] = _hash_file(gh_json)

    language = project.language

    # Run visible tests
    all_visible_pass = True
    for cid, suite in test_suites.items():
        test_file = project.test_code_path(cid)
        impl_dir = project.impl_src_dir(cid)
        if not test_file.exists() or not impl_dir.exists():
            cert.visible_results[cid] = {"total": 0, "passed": 0, "failed": 0, "skipped": True}
            all_visible_pass = False
            continue
        try:
            results = await run_contract_tests(
                test_file, impl_dir, language=language,
                project_dir=project.project_dir,
            )
            cert.visible_results[cid] = {
                "total": results.total,
                "passed": results.passed,
                "failed": results.failed,
            }
            if not results.all_passed:
                all_visible_pass = False
        except Exception as e:
            logger.error("Visible test error for %s: %s", cid, e)
            cert.visible_results[cid] = {"total": 0, "passed": 0, "failed": 1, "error": str(e)}
            all_visible_pass = False

    # Run Goodhart tests
    all_goodhart_pass = True
    for cid, suite in goodhart_suites.items():
        test_file = project.goodhart_test_code_path(cid)
        impl_dir = project.impl_src_dir(cid)
        if not test_file.exists() or not impl_dir.exists():
            cert.goodhart_results[cid] = {"total": 0, "passed": 0, "failed": 0, "skipped": True}
            all_goodhart_pass = False
            continue
        try:
            results = await run_contract_tests(
                test_file, impl_dir, language=language,
                project_dir=project.project_dir,
            )
            cert.goodhart_results[cid] = {
                "total": results.total,
                "passed": results.passed,
                "failed": results.failed,
            }
            if not results.all_passed:
                all_goodhart_pass = False
        except Exception as e:
            logger.error("Goodhart test error for %s: %s", cid, e)
            cert.goodhart_results[cid] = {"total": 0, "passed": 0, "failed": 1, "error": str(e)}
            all_goodhart_pass = False

    # Determine verdict
    if all_visible_pass and all_goodhart_pass:
        cert.verdict = "pass"
        cert.summary = "All visible and Goodhart tests pass"
    elif all_visible_pass:
        cert.verdict = "partial"
        cert.summary = "Visible tests pass but Goodhart tests have failures"
    else:
        cert.verdict = "fail"
        cert.summary = "Test failures detected"

    # Compute self-hash last
    cert.self_hash = compute_self_hash(cert)

    return cert
