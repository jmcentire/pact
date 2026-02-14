"""Functional test execution against implementations.

Runs contract-generated tests against black-box implementations.
Parses pytest output to produce TestResults.
"""

from __future__ import annotations

import asyncio
import logging
import re
from pathlib import Path

from pact.schemas import TestFailure, TestResults

logger = logging.getLogger(__name__)


async def run_contract_tests(
    test_file: Path,
    impl_dir: Path,
    timeout: int = 120,
) -> TestResults:
    """Run pytest on a contract test file against an implementation.

    Args:
        test_file: Path to the contract_test.py file.
        impl_dir: Path to the implementation source directory (added to PYTHONPATH).
        timeout: Max seconds to wait for tests.

    Returns:
        TestResults with pass/fail counts and failure details.
    """
    if not test_file.exists():
        return TestResults(
            total=0, passed=0, failed=0, errors=1,
            failure_details=[TestFailure(
                test_id="setup",
                error_message=f"Test file not found: {test_file}",
            )],
        )

    env_path = f"{impl_dir}:{impl_dir.parent}"
    cmd = [
        "python3", "-m", "pytest",
        str(test_file),
        "-v", "--tb=short", "--no-header",
        f"--rootdir={impl_dir.parent}",
    ]

    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env={"PYTHONPATH": env_path, "PATH": "/usr/bin:/usr/local/bin"},
            cwd=str(impl_dir.parent),
        )
        stdout, stderr = await asyncio.wait_for(
            proc.communicate(), timeout=timeout,
        )
    except asyncio.TimeoutError:
        return TestResults(
            total=0, passed=0, failed=0, errors=1,
            failure_details=[TestFailure(
                test_id="timeout",
                error_message=f"Tests timed out after {timeout}s",
            )],
        )
    except Exception as e:
        return TestResults(
            total=0, passed=0, failed=0, errors=1,
            failure_details=[TestFailure(
                test_id="execution",
                error_message=str(e),
            )],
        )

    stdout_text = stdout.decode(errors="replace")
    stderr_text = stderr.decode(errors="replace")

    return parse_pytest_output(stdout_text, stderr_text)


def parse_pytest_output(stdout: str, stderr: str) -> TestResults:
    """Parse pytest verbose output into TestResults."""
    total = 0
    passed = 0
    failed = 0
    errors = 0
    failures: list[TestFailure] = []

    # Parse individual test lines (pytest -v format)
    for line in stdout.splitlines():
        if " PASSED" in line:
            total += 1
            passed += 1
        elif " FAILED" in line:
            total += 1
            failed += 1
            test_name = line.split(" FAILED")[0].strip()
            failures.append(TestFailure(
                test_id=test_name,
                error_message="FAILED",
                stdout=stdout,
                stderr=stderr,
            ))
        elif " ERROR" in line:
            total += 1
            errors += 1
            test_name = line.split(" ERROR")[0].strip()
            failures.append(TestFailure(
                test_id=test_name,
                error_message="ERROR",
                stdout=stdout,
                stderr=stderr,
            ))

    # Fallback: parse summary line "X passed, Y failed, Z errors"
    if total == 0:
        summary = re.search(
            r"(\d+) passed(?:.*?(\d+) failed)?(?:.*?(\d+) error)?",
            stdout,
        )
        if summary:
            passed = int(summary.group(1))
            failed = int(summary.group(2) or 0)
            errors = int(summary.group(3) or 0)
            total = passed + failed + errors

    # If still nothing parsed, check for collection errors
    combined = stdout + stderr
    if total == 0 and ("ERROR" in combined or "error" in combined):
        errors = 1
        total = 1
        failures.append(TestFailure(
            test_id="collection",
            error_message="Failed to collect tests",
            stdout=stdout,
            stderr=stderr,
        ))

    from datetime import datetime
    return TestResults(
        total=total,
        passed=passed,
        failed=failed,
        errors=errors,
        failure_details=failures,
        timestamp=datetime.now().isoformat(),
    )
