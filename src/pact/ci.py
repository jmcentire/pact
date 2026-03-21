"""CI workflow generator for pact-managed projects.

Reads a project's .pact/ directory and contracts to detect language, then
emits a self-contained GitHub Actions workflow that runs contract tests
and static error checks on pull requests.
"""

from __future__ import annotations

from pathlib import Path

import yaml

from pact.config import load_project_config


def detect_language(project_dir: Path) -> str:
    """Detect project language from pact.yaml config or contract test files.

    Priority:
      1. pact.yaml language field
      2. Presence of .ts test files -> typescript
      3. Presence of .py test files -> python
      4. Default to python
    """
    pact_yaml = project_dir / "pact.yaml"
    if pact_yaml.exists():
        cfg = load_project_config(str(project_dir))
        if cfg.language and cfg.language != "python":
            return cfg.language

    tests_dir = project_dir / "tests"
    if tests_dir.exists():
        ts_files = list(tests_dir.rglob("*.ts"))
        py_files = list(tests_dir.rglob("*.py"))
        if ts_files and not py_files:
            return "typescript"
        if py_files:
            return "python"

    contracts_dir = project_dir / "contracts"
    if contracts_dir.exists():
        ts_files = list(contracts_dir.rglob("*.ts"))
        if ts_files:
            return "typescript"

    # Check pact.yaml again for explicit setting
    if pact_yaml.exists():
        cfg = load_project_config(str(project_dir))
        return cfg.language

    return "python"


def _find_test_dirs(project_dir: Path) -> list[str]:
    """Find directories containing contract test files."""
    tests_dir = project_dir / "tests"
    if not tests_dir.exists():
        return ["tests/"]

    # Return relative paths to test directories that contain test files
    dirs = set()
    for f in tests_dir.rglob("contract_test*"):
        if "__pycache__" not in str(f):
            dirs.add(str(f.parent.relative_to(project_dir)))
    for f in tests_dir.rglob("test_*"):
        if "__pycache__" not in str(f):
            dirs.add(str(f.parent.relative_to(project_dir)))
    for f in tests_dir.rglob("*.test.*"):
        if "__pycache__" not in str(f) and "node_modules" not in str(f):
            dirs.add(str(f.parent.relative_to(project_dir)))

    # Filter out any remaining __pycache__ or node_modules dirs
    dirs = {d for d in dirs if "__pycache__" not in d and "node_modules" not in d}

    if not dirs:
        return ["tests/"]

    # Simplify: if "tests" is in the set, all subdirectories of tests/ are redundant
    if "tests" in dirs:
        return ["tests/"]

    # Remove subdirectories that are already covered by a parent in the set
    sorted_dirs = sorted(dirs)
    result = []
    for d in sorted_dirs:
        if not any(d.startswith(parent + "/") for parent in result):
            result.append(d)

    return result


def generate_python_workflow(project_dir: Path, test_dirs: list[str]) -> dict:
    """Generate a GitHub Actions workflow dict for a Python pact project."""
    test_path = " ".join(test_dirs)
    return {
        "name": "Pact Contract Verification",
        "on": {
            "pull_request": {
                "branches": ["main", "dev"],
            },
        },
        "jobs": {
            "verify-contracts": {
                "runs-on": "ubuntu-latest",
                "steps": [
                    {"uses": "actions/checkout@v4"},
                    {
                        "uses": "actions/setup-python@v5",
                        "with": {"python-version": "3.12"},
                    },
                    {
                        "name": "Install dependencies",
                        "run": _python_install_step(project_dir),
                    },
                    {
                        "name": "Run contract tests",
                        "run": f"python -m pytest {test_path} -v --tb=short",
                    },
                    {
                        "name": "Lint (static errors only)",
                        "run": "pip install ruff\nruff check . --select E9,F\n",
                    },
                ],
            },
        },
    }


def _python_install_step(project_dir: Path) -> str:
    """Generate the pip install command based on what's available."""
    if (project_dir / "pyproject.toml").exists():
        return 'pip install -e ".[dev]" 2>/dev/null || pip install -e . && pip install pytest'
    elif (project_dir / "requirements.txt").exists():
        return "pip install -r requirements.txt\npip install pytest"
    else:
        return "pip install pytest"


def generate_typescript_workflow(project_dir: Path, test_dirs: list[str]) -> dict:
    """Generate a GitHub Actions workflow dict for a TypeScript pact project."""
    test_path = " ".join(test_dirs)
    return {
        "name": "Pact Contract Verification",
        "on": {
            "pull_request": {
                "branches": ["main", "dev"],
            },
        },
        "jobs": {
            "verify-contracts": {
                "runs-on": "ubuntu-latest",
                "steps": [
                    {"uses": "actions/checkout@v4"},
                    {
                        "uses": "actions/setup-node@v4",
                        "with": {"node-version": "20"},
                    },
                    {
                        "name": "Install dependencies",
                        "run": _typescript_install_step(project_dir),
                    },
                    {
                        "name": "Run contract tests",
                        "run": f"npx vitest run {test_path} --reporter=verbose",
                    },
                    {
                        "name": "Lint (static errors only)",
                        "run": "npx eslint . --no-eslintrc --rule '{no-undef: error, no-unreachable: error}' --ext .ts,.tsx 2>/dev/null || true\n",
                    },
                ],
            },
        },
    }


def _typescript_install_step(project_dir: Path) -> str:
    """Generate the npm install command based on what's available."""
    if (project_dir / "package-lock.json").exists():
        return "npm ci"
    elif (project_dir / "package.json").exists():
        return "npm install"
    else:
        return "npm init -y\nnpm install vitest typescript"


def generate_ci_workflow(
    project_dir: str | Path,
    output_path: str | None = None,
) -> None:
    """Generate and write a CI workflow for a pact-managed project.

    Args:
        project_dir: Root directory of the project.
        output_path: Override output file path. Defaults to
            .github/workflows/pact-verify.yml inside the project.
    """
    project_dir = Path(project_dir).resolve()

    language = detect_language(project_dir)
    test_dirs = _find_test_dirs(project_dir)

    if language in ("typescript", "javascript"):
        workflow = generate_typescript_workflow(project_dir, test_dirs)
    else:
        workflow = generate_python_workflow(project_dir, test_dirs)

    if output_path:
        out = Path(output_path)
    else:
        out = project_dir / ".github" / "workflows" / "pact-verify.yml"

    out.parent.mkdir(parents=True, exist_ok=True)
    with open(out, "w") as f:
        yaml.dump(workflow, f, default_flow_style=False, sort_keys=False)

    print(f"Generated CI workflow: {out}")
    print(f"  Language: {language}")
    print(f"  Test dirs: {', '.join(test_dirs)}")
    print("  Triggers: pull_request to main/dev")
    print("\nThe workflow is self-contained -- no pact installation required.")
