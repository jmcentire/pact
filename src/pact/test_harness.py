"""Functional test execution against implementations.

Runs contract-generated tests against black-box implementations.
Parses pytest output to produce TestResults.
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
from pathlib import Path

from pact.schemas import TestFailure, TestResults

logger = logging.getLogger(__name__)


async def run_contract_tests(
    test_file: Path,
    impl_dir: Path,
    timeout: int = 120,
    environment: "EnvironmentSpec | None" = None,
    extra_paths: list[Path] | None = None,
    language: str = "python",
    project_dir: Path | None = None,
) -> TestResults:
    """Run tests on a contract test file against an implementation.

    Args:
        test_file: Path to the contract test file (.py or .ts).
        impl_dir: Path to the implementation source directory.
        timeout: Max seconds to wait for tests.
        extra_paths: Additional directories to add to the module path
            (PYTHONPATH for Python, NODE_PATH for TypeScript).
        language: Test language — "python" (default) or "typescript".
        project_dir: Project root directory (where package.json / vitest.config.ts
            live). Used as cwd for TypeScript tests. If None, discovered by
            walking up from impl_dir looking for pact.yaml.

    Returns:
        TestResults with pass/fail counts and failure details.
    """
    if language == "typescript":
        return await run_typescript_tests(
            test_file, impl_dir, extra_paths=extra_paths, timeout=timeout,
            project_dir=project_dir,
        )

    if not test_file.exists():
        return TestResults(
            total=0, passed=0, failed=0, errors=1,
            failure_details=[TestFailure(
                test_id="setup",
                error_message=f"Test file not found: {test_file}",
            )],
        )

    parts = [str(impl_dir), str(impl_dir.parent)]
    if extra_paths:
        parts.extend(str(p) for p in extra_paths)
    env_path = ":".join(parts)

    if environment:
        env = environment.build_env(env_path)
    else:
        # Default: inherit parent PATH (fixes the 0/0 test failure root cause)
        env = {
            "PYTHONPATH": env_path,
            "PATH": os.environ.get("PATH", "/usr/bin:/usr/local/bin"),
        }

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
            env=env,
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


# ── TypeScript / Vitest support ──────────────────────────────────────


async def run_typescript_tests(
    test_file: Path,
    src_dir: Path,
    extra_paths: list[Path] | None = None,
    timeout: int = 120,
    project_dir: Path | None = None,
) -> TestResults:
    """Run vitest (or fall back to jest) on a TypeScript contract test file.

    Args:
        test_file: Path to the contract test .ts file.
        src_dir: Path to the implementation source directory (added to NODE_PATH).
        extra_paths: Additional directories to add to NODE_PATH.
        timeout: Max seconds to wait for tests.
        project_dir: Project root directory (where package.json / vitest.config.ts
            live). Used as cwd for vitest. If None, discovered by walking up
            from src_dir looking for pact.yaml.

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

    # Resolve project root: walk up from src_dir looking for pact.yaml
    if project_dir is None:
        candidate = src_dir
        while candidate != candidate.parent:
            if (candidate / "pact.yaml").exists():
                project_dir = candidate
                break
            candidate = candidate.parent
        if project_dir is None:
            # Fallback: use src_dir.parent (legacy behaviour)
            project_dir = src_dir.parent

    # Build NODE_PATH
    node_parts = [str(src_dir), str(src_dir.parent)]
    if extra_paths:
        node_parts.extend(str(p) for p in extra_paths)
    node_path = ":".join(node_parts)

    env = {
        "NODE_PATH": node_path,
        "NODE_NO_WARNINGS": "1",
        "PATH": os.environ.get("PATH", "/usr/bin:/usr/local/bin"),
        # Inherit HOME so npx can locate global caches
        "HOME": os.environ.get("HOME", ""),
    }

    # Prefer vitest; fall back to jest if vitest is unavailable
    cmd = [
        "npx", "vitest", "run", str(test_file),
        "--reporter=verbose", "--no-color",
    ]

    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env,
            cwd=str(project_dir),
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

    # If vitest was not found, retry with jest
    if proc.returncode != 0 and "vitest" in stderr_text.lower() and "not found" in stderr_text.lower():
        logger.info("vitest not found, falling back to jest")
        cmd = ["npx", "jest", str(test_file), "--verbose"]
        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=env,
                cwd=str(project_dir),
            )
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(), timeout=timeout,
            )
        except asyncio.TimeoutError:
            return TestResults(
                total=0, passed=0, failed=0, errors=1,
                failure_details=[TestFailure(
                    test_id="timeout",
                    error_message=f"Tests timed out after {timeout}s (jest fallback)",
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

    return parse_vitest_output(stdout_text, stderr_text)


def parse_vitest_output(stdout: str, stderr: str) -> TestResults:
    """Parse vitest verbose output into TestResults.

    Vitest verbose format emits lines like:
        ✓ test name (5ms)
        × test name

    And a summary line:
        Tests  42 passed | 1 failed
    """
    total = 0
    passed = 0
    failed = 0
    errors = 0
    failures: list[TestFailure] = []

    combined = stdout + "\n" + stderr

    # Parse individual test result lines
    for line in combined.splitlines():
        stripped = line.strip()

        # Passed: ✓ test name  or  √ test name  or  ✓ test name (5ms)
        if re.match(r"[✓√]\s+", stripped):
            total += 1
            passed += 1

        # Failed: × test name  or  ✕ test name  or  x test name (vitest uses ×)
        elif re.match(r"[×✕x]\s+", stripped):
            total += 1
            failed += 1
            # Extract test name (strip the marker and optional timing)
            test_name = re.sub(r"^[×✕x]\s+", "", stripped)
            test_name = re.sub(r"\s+\(\d+\s*m?s\)\s*$", "", test_name)
            failures.append(TestFailure(
                test_id=test_name,
                error_message="FAILED",
                stdout=stdout,
                stderr=stderr,
            ))

    # Fallback: parse the summary line "Tests  N passed | M failed"
    if total == 0:
        summary = re.search(
            r"Tests\s+(?:(\d+)\s+passed)?(?:\s*\|\s*)?(?:(\d+)\s+failed)?",
            combined,
        )
        if summary:
            passed = int(summary.group(1) or 0)
            failed = int(summary.group(2) or 0)
            total = passed + failed

    # Also try jest-style summary: "Tests: N passed, M failed, K total"
    if total == 0:
        jest_summary = re.search(
            r"Tests:\s+(?:(\d+)\s+passed)?(?:,\s*)?(?:(\d+)\s+failed)?(?:,\s*)?(?:(\d+)\s+total)?",
            combined,
        )
        if jest_summary:
            passed = int(jest_summary.group(1) or 0)
            failed = int(jest_summary.group(2) or 0)
            total_parsed = int(jest_summary.group(3) or 0)
            total = total_parsed if total_parsed else passed + failed

    # If still nothing parsed, check for errors
    if total == 0 and ("Error" in combined or "error" in combined or "ERR" in combined):
        errors = 1
        total = 1
        failures.append(TestFailure(
            test_id="collection",
            error_message="Failed to collect or run tests",
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
