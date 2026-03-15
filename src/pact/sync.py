"""Test sync — one-way copy of visible tests from audit repo to code repo.

Goodhart tests are NEVER synced. Only contract tests and their JSON metadata
are copied. The synced copy is read-only for the coding agent.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import shutil
from pathlib import Path

from pact.project import ProjectManager

logger = logging.getLogger(__name__)


def sync_visible_tests(project: ProjectManager) -> dict[str, str]:
    """Copy visible tests from audit repo to code repo.

    Returns dict of {cid: status} where status is "synced" or "skipped".
    Skips goodhart/ directories entirely — they never leave the audit repo.
    """
    if not project.has_audit_repo:
        return {}

    audit_tests = project._visible_tests_dir   # audit_dir/tests/
    synced_tests = project.synced_tests_dir     # project_dir/tests/

    if synced_tests is None or not audit_tests.exists():
        return {}

    results: dict[str, str] = {}

    for cid_dir in sorted(audit_tests.iterdir()):
        if not cid_dir.is_dir():
            continue
        cid = cid_dir.name
        dest = synced_tests / cid
        dest.mkdir(parents=True, exist_ok=True)

        synced_any = False
        for f in cid_dir.iterdir():
            # NEVER sync goodhart tests or any subdirectories
            if f.name == "goodhart" or f.is_dir():
                continue
            shutil.copy2(f, dest / f.name)
            synced_any = True

        results[cid] = "synced" if synced_any else "skipped"

    logger.info("Synced visible tests for %d components", len(results))
    return results


def compute_artifact_manifest(project: ProjectManager) -> dict[str, str]:
    """Compute SHA-256 hashes of all audit artifacts.

    Returns dict of {relative_path: sha256_hex}.
    """
    manifest: dict[str, str] = {}
    audit_root = project.audit_root

    for path in sorted(audit_root.rglob("*")):
        if path.is_file() and not path.name.startswith("."):
            rel = str(path.relative_to(audit_root))
            manifest[rel] = hashlib.sha256(path.read_bytes()).hexdigest()

    return manifest


async def clone_or_pull_audit_repo(project_dir: Path, audit_repo_url: str) -> Path:
    """Clone or pull the audit repo to .pact/audit_cache/.

    Returns the local path to the cached audit repo.
    """
    cache_dir = project_dir / ".pact" / "audit_cache"

    if (cache_dir / ".git").exists():
        proc = await asyncio.create_subprocess_exec(
            "git", "pull", "--ff-only",
            cwd=str(cache_dir),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await proc.communicate()
        if proc.returncode != 0:
            logger.warning("git pull failed in audit cache: %s", stderr.decode())
    else:
        cache_dir.parent.mkdir(parents=True, exist_ok=True)
        proc = await asyncio.create_subprocess_exec(
            "git", "clone", audit_repo_url, str(cache_dir),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await proc.communicate()
        if proc.returncode != 0:
            msg = f"git clone failed for audit repo: {stderr.decode()}"
            logger.error(msg)
            raise RuntimeError(msg)

    return cache_dir
