"""Functional test execution against implementations.

Runs contract-generated tests against black-box implementations.
Parses pytest output to produce TestResults.

Supports tiered evaluation:
  - smoke: import checks only (near-instant, no execution)
  - standard: contract tests (default, visible test suite)
  - exhaustive: contract + Goodhart + emission compliance tests
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
from enum import StrEnum
from pathlib import Path

from pact.schemas import TestFailure, TestResults

logger = logging.getLogger(__name__)


class EvalTier(StrEnum):
    """Evaluation cost tiers — controls which tests run."""
    SMOKE = "smoke"
    STANDARD = "standard"
    EXHAUSTIVE = "exhaustive"


def select_test_files(
    component_id: str,
    project_dir: Path,
    tier: EvalTier = EvalTier.STANDARD,
    language: str = "python",
) -> list[Path]:
    """Select test files to run based on evaluation tier.

    Args:
        component_id: The component to evaluate.
        project_dir: Project root directory.
        tier: Which evaluation tier to use.
        language: Test language for file extension selection.

    Returns:
        List of test file paths to execute, in order.

    Tier behavior:
        smoke: Only smoke tests (tests/smoke/ if they exist)
        standard: Contract test suite only
        exhaustive: Contract + Goodhart + emission compliance
    """
    ext_map = {"typescript": ".test.ts", "rust": ".rs", "python": ".py"}
    ext = ext_map.get(language, ".py")
    tests_dir = project_dir / "tests" / component_id
    files: list[Path] = []

    if tier == EvalTier.SMOKE:
        # Just check that imports work — use the smoke test if available
        smoke_dir = project_dir / "tests" / "smoke"
        smoke_file = smoke_dir / f"test_{component_id}{ext}"
        if smoke_file.exists():
            files.append(smoke_file)
        # Fall back to contract test with -x (stop at first failure)
        elif (tests_dir / f"contract_test{ext}").exists():
            files.append(tests_dir / f"contract_test{ext}")
        return files

    if tier == EvalTier.STANDARD:
        contract_test = tests_dir / f"contract_test{ext}"
        if contract_test.exists():
            files.append(contract_test)
        return files

    # EXHAUSTIVE: contract + goodhart + emission
    contract_test = tests_dir / f"contract_test{ext}"
    if contract_test.exists():
        files.append(contract_test)

    goodhart_dir = tests_dir / "goodhart"
    goodhart_test = goodhart_dir / f"goodhart_test{ext}"
    if goodhart_test.exists():
        files.append(goodhart_test)

    emission_test = tests_dir / f"emission_test{ext}"
    if emission_test.exists():
        files.append(emission_test)

    return files


# ── Shared subprocess execution ────────────────────────────────────


class TestSubprocessError(Exception):
    """Raised when a test subprocess fails to execute."""
    def __init__(self, test_id: str, message: str):
        self.test_id = test_id
        self.message = message
        super().__init__(message)


async def _run_test_subprocess(
    cmd: list[str],
    env: dict[str, str],
    cwd: str,
    timeout: int,
) -> tuple[str, str]:
    """Run a test subprocess with timeout and error handling.

    Returns (stdout, stderr) as decoded strings.
    Raises TestSubprocessError on timeout or execution failure.
    """
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env,
            cwd=cwd,
        )
        stdout, stderr = await asyncio.wait_for(
            proc.communicate(), timeout=timeout,
        )
    except asyncio.TimeoutError:
        raise TestSubprocessError("timeout", f"Tests timed out after {timeout}s")
    except Exception as e:
        raise TestSubprocessError("execution", str(e))

    return stdout.decode(errors="replace"), stderr.decode(errors="replace")


def _error_results(test_id: str, message: str) -> TestResults:
    """Create a TestResults for a subprocess error."""
    return TestResults(
        total=0, passed=0, failed=0, errors=1,
        failure_details=[TestFailure(test_id=test_id, error_message=message)],
    )


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

    if language == "rust":
        return await run_rust_tests(
            impl_dir, timeout=timeout, project_dir=project_dir,
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
    # Include pact's own site-packages so anyio and other pact deps are available
    import sysconfig as _sysconfig
    _pact_site = _sysconfig.get_path("purelib")
    if _pact_site and _pact_site not in parts:
        parts.append(_pact_site)
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
        stdout_text, stderr_text = await _run_test_subprocess(
            cmd, env, str(impl_dir.parent), timeout,
        )
    except TestSubprocessError as e:
        return _error_results(e.test_id, e.message)

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
        stdout_text, stderr_text = await _run_test_subprocess(
            cmd, env, str(project_dir), timeout,
        )
    except TestSubprocessError as e:
        return _error_results(e.test_id, e.message)

    # If vitest was not found, retry with jest
    if "vitest" in stderr_text.lower() and "not found" in stderr_text.lower():
        logger.info("vitest not found, falling back to jest")
        cmd = ["npx", "jest", str(test_file), "--verbose"]
        try:
            stdout_text, stderr_text = await _run_test_subprocess(
                cmd, env, str(project_dir), timeout,
            )
        except TestSubprocessError as e:
            return _error_results(e.test_id, f"{e.message} (jest fallback)")

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


# ── Rust / cargo test support ───────────────────────────────────────


async def run_rust_tests(
    impl_dir: Path,
    timeout: int = 120,
    project_dir: Path | None = None,
) -> TestResults:
    """Run cargo test on a Rust project.

    Args:
        impl_dir: Path to the implementation source directory.
        timeout: Max seconds to wait for tests.
        project_dir: Project root directory (where Cargo.toml lives).
            If None, discovered by walking up from impl_dir.

    Returns:
        TestResults with pass/fail counts and failure details.
    """
    # Resolve project root: walk up from impl_dir looking for Cargo.toml
    if project_dir is None:
        candidate = impl_dir
        while candidate != candidate.parent:
            if (candidate / "Cargo.toml").exists():
                project_dir = candidate
                break
            candidate = candidate.parent
        if project_dir is None:
            project_dir = impl_dir

    if not (project_dir / "Cargo.toml").exists():
        return TestResults(
            total=0, passed=0, failed=0, errors=1,
            failure_details=[TestFailure(
                test_id="setup",
                error_message=f"Cargo.toml not found in {project_dir}",
            )],
        )

    env = {
        "PATH": os.environ.get("PATH", "/usr/bin:/usr/local/bin"),
        "HOME": os.environ.get("HOME", ""),
        "CARGO_TERM_COLOR": "never",
    }

    cmd = ["cargo", "test", "--", "--format=terse"]

    try:
        stdout_text, stderr_text = await _run_test_subprocess(
            cmd, env, str(project_dir), timeout,
        )
    except TestSubprocessError as e:
        return _error_results(e.test_id, e.message)

    return parse_cargo_test_output(stdout_text, stderr_text)


def parse_cargo_test_output(stdout: str, stderr: str) -> TestResults:
    """Parse cargo test output into TestResults.

    Cargo test output formats:
        test module::test_name ... ok
        test module::test_name ... FAILED

    Summary line:
        test result: ok. 42 passed; 0 failed; 0 ignored; 0 measured; 0 filtered out
        test result: FAILED. 40 passed; 2 failed; 0 ignored; 0 measured; 0 filtered out
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

        # Match: test some::path::test_name ... ok
        match = re.match(r"^test\s+(.+?)\s+\.\.\.\s+ok$", stripped)
        if match:
            total += 1
            passed += 1
            continue

        # Match: test some::path::test_name ... FAILED
        match = re.match(r"^test\s+(.+?)\s+\.\.\.\s+FAILED$", stripped)
        if match:
            total += 1
            failed += 1
            test_name = match.group(1)
            failures.append(TestFailure(
                test_id=test_name,
                error_message="FAILED",
                stdout=stdout,
                stderr=stderr,
            ))
            continue

        # Match: test some::path::test_name ... ignored
        match = re.match(r"^test\s+(.+?)\s+\.\.\.\s+ignored$", stripped)
        if match:
            # Ignored tests don't count toward pass/fail
            continue

    # Fallback: parse the summary line
    if total == 0:
        summary = re.search(
            r"test result:.*?(\d+)\s+passed;\s*(\d+)\s+failed",
            combined,
        )
        if summary:
            passed = int(summary.group(1))
            failed = int(summary.group(2))
            total = passed + failed

    # Check for compilation errors (cargo won't run tests if build fails)
    if total == 0 and ("error[E" in combined or "could not compile" in combined.lower()):
        errors = 1
        total = 1
        failures.append(TestFailure(
            test_id="compilation",
            error_message="Rust compilation failed",
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
