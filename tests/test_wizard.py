"""Tests for the pact wizard module."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest
import yaml

from pact.schemas import validate_answer
from pact.wizard import (
    WizardConfig,
    answers_to_config,
    build_wizard_questions,
    generate_pact_yaml,
    generate_sops_md,
    generate_task_md,
    load_wizard_config_from_file,
    resolve_test_framework,
    run_wizard_interactive,
)


class TestWizardQuestions:
    def test_questions_are_valid(self):
        """All wizard questions with defaults must pass their own validation."""
        questions = build_wizard_questions()
        for q in questions:
            if q.default:
                error = validate_answer(q, q.default)
                assert error is None, f"Question {q.id} default fails: {error}"

    def test_question_ids_unique(self):
        questions = build_wizard_questions()
        ids = [q.id for q in questions]
        assert len(ids) == len(set(ids))

    def test_conditional_deps_reference_valid_questions(self):
        questions = build_wizard_questions()
        ids = {q.id for q in questions}
        for q in questions:
            if q.depends_on:
                assert q.depends_on in ids, f"{q.id} depends on missing {q.depends_on}"

    def test_returns_nonempty_list(self):
        questions = build_wizard_questions()
        assert len(questions) >= 5


class TestRunWizardInteractive:
    def test_accepts_all_defaults(self):
        """Pressing Enter for every question produces valid config."""
        questions = build_wizard_questions()
        answers = iter(["my-project", "A test project"] + [""] * 20)
        config = run_wizard_interactive(
            questions,
            input_fn=lambda _: next(answers),
            print_fn=lambda *a: None,
        )
        assert isinstance(config, WizardConfig)
        assert config.project_name == "my-project"
        assert config.language == "python"
        assert config.build_mode == "auto"
        assert config.budget == 10.0

    def test_custom_answers(self):
        questions = build_wizard_questions()
        scripted = [
            "cool-app",       # project_name
            "A web app",      # description
            "typescript",     # language
            "vitest",         # test_framework
            "hierarchy",      # build_mode
            "yes",            # shaping
            "25",             # budget
            "yes",            # parallel_components
            "500",            # max_file_lines
            "no",             # prefer_stdlib
            "no",             # run_interview
        ]
        idx = [0]

        def fake_input(_prompt: str) -> str:
            val = scripted[idx[0]]
            idx[0] += 1
            return val

        config = run_wizard_interactive(
            questions, input_fn=fake_input, print_fn=lambda *a: None,
        )
        assert config.language == "typescript"
        assert config.build_mode == "hierarchy"
        assert config.shaping is True
        assert config.budget == 25.0
        assert config.parallel_components is True
        assert config.prefer_stdlib is False

    def test_validation_retry(self):
        """Invalid answers are rejected and re-prompted."""
        questions = build_wizard_questions()
        scripted = [
            "my-project",     # project_name
            "A project",      # description
            "cobol",          # language -- INVALID (not in enum)
            "python",         # language -- valid retry
        ] + [""] * 20
        idx = [0]

        def fake_input(_prompt: str) -> str:
            val = scripted[idx[0]]
            idx[0] += 1
            return val

        messages: list[str] = []
        config = run_wizard_interactive(
            questions,
            input_fn=fake_input,
            print_fn=lambda *a: messages.append(str(a)),
        )
        assert config.language == "python"
        # Should have printed a validation error
        assert any("not in valid options" in m for m in messages)


class TestAnswersToConfig:
    def test_boolean_parsing(self):
        answers = {
            "project_name": "test",
            "description": "test",
            "shaping": "yes",
            "parallel_components": "false",
            "prefer_stdlib": "true",
            "run_interview": "no",
            "budget": "10",
            "max_file_lines": "300",
        }
        config = answers_to_config(answers)
        assert config.shaping is True
        assert config.parallel_components is False
        assert config.prefer_stdlib is True
        assert config.run_interview is False

    def test_auto_test_framework_cleared(self):
        """'auto' for test_framework becomes empty string."""
        answers = {"test_framework": "auto"}
        config = answers_to_config(answers)
        assert config.test_framework == ""

    def test_explicit_test_framework_preserved(self):
        answers = {"test_framework": "unittest"}
        config = answers_to_config(answers)
        assert config.test_framework == "unittest"

    def test_defaults_for_missing_keys(self):
        config = answers_to_config({})
        assert config.language == "python"
        assert config.budget == 10.0
        assert config.build_mode == "auto"


class TestResolveTestFramework:
    def test_python_default(self):
        assert resolve_test_framework(WizardConfig(language="python")) == "pytest"

    def test_typescript_default(self):
        assert resolve_test_framework(WizardConfig(language="typescript")) == "vitest"

    def test_javascript_default(self):
        assert resolve_test_framework(WizardConfig(language="javascript")) == "jest"

    def test_explicit_override(self):
        config = WizardConfig(language="python", test_framework="unittest")
        assert resolve_test_framework(config) == "unittest"


class TestGenerateTaskMd:
    def test_contains_description(self):
        config = WizardConfig(project_name="MyApp", description="Build a REST API")
        md = generate_task_md(config)
        assert "MyApp" in md
        assert "REST API" in md

    def test_has_sections(self):
        config = WizardConfig(project_name="Test", description="Test project")
        md = generate_task_md(config)
        assert "## Context" in md
        assert "## Constraints" in md
        assert "## Requirements" in md

    def test_defaults_for_empty(self):
        md = generate_task_md(WizardConfig())
        assert "Untitled Project" in md


class TestGenerateSopsMd:
    def test_python_sops(self):
        config = WizardConfig(language="python")
        sops = generate_sops_md(config)
        assert "Python" in sops
        assert "pytest" in sops
        assert "ruff" in sops

    def test_typescript_sops(self):
        config = WizardConfig(language="typescript")
        sops = generate_sops_md(config)
        assert "TypeScript" in sops
        assert "vitest" in sops
        assert "eslint" in sops

    def test_javascript_sops(self):
        config = WizardConfig(language="javascript")
        sops = generate_sops_md(config)
        assert "JavaScript" in sops
        assert "jest" in sops

    def test_max_file_lines(self):
        config = WizardConfig(max_file_lines=500)
        sops = generate_sops_md(config)
        assert "500" in sops

    def test_stdlib_preference(self):
        config = WizardConfig(prefer_stdlib=True)
        sops = generate_sops_md(config)
        assert "standard library" in sops.lower()

    def test_third_party_preference(self):
        config = WizardConfig(prefer_stdlib=False)
        sops = generate_sops_md(config)
        assert "third-party" in sops.lower()


class TestGeneratePactYaml:
    def test_always_has_budget(self):
        cfg = generate_pact_yaml(WizardConfig(budget=25.0))
        assert cfg["budget"] == 25.0

    def test_omits_defaults(self):
        """Default values (python, auto, no shaping) should be omitted."""
        cfg = generate_pact_yaml(WizardConfig())
        assert "language" not in cfg
        assert "build_mode" not in cfg
        assert "shaping" not in cfg
        assert "parallel_components" not in cfg

    def test_includes_nondefaults(self):
        config = WizardConfig(
            language="typescript",
            build_mode="hierarchy",
            shaping=True,
            parallel_components=True,
        )
        cfg = generate_pact_yaml(config)
        assert cfg["language"] == "typescript"
        assert cfg["build_mode"] == "hierarchy"
        assert cfg["shaping"] is True
        assert cfg["parallel_components"] is True

    def test_typescript_test_framework(self):
        config = WizardConfig(language="typescript")
        cfg = generate_pact_yaml(config)
        assert cfg["test_framework"] == "vitest"


class TestLoadWizardConfigFromFile:
    def test_load_json(self, tmp_path: Path):
        f = tmp_path / "config.json"
        f.write_text(json.dumps({"project_name": "test", "budget": 20.0}))
        config = load_wizard_config_from_file(f)
        assert config.project_name == "test"
        assert config.budget == 20.0

    def test_load_yaml(self, tmp_path: Path):
        f = tmp_path / "config.yaml"
        f.write_text(yaml.dump({"project_name": "test", "language": "typescript"}))
        config = load_wizard_config_from_file(f)
        assert config.project_name == "test"
        assert config.language == "typescript"

    def test_missing_fields_use_defaults(self, tmp_path: Path):
        f = tmp_path / "config.json"
        f.write_text("{}")
        config = load_wizard_config_from_file(f)
        assert config.budget == 10.0
        assert config.language == "python"


class TestCmdWizard:
    def test_wizard_creates_project(self, tmp_path: Path):
        """Full integration: wizard creates project with expected files."""
        from pact.cli import cmd_wizard
        import argparse

        scripted = [
            "my-proj",        # project_name
            "Build a thing",  # description
        ] + [""] * 20         # accept all defaults
        idx = [0]

        def fake_input(_prompt: str) -> str:
            val = scripted[idx[0]]
            idx[0] += 1
            return val

        project_dir = str(tmp_path / "wiz-proj")
        args = argparse.Namespace(
            project_dir=project_dir,
            config=None,
            budget=None,
            verbose=False,
        )

        with patch("builtins.input", side_effect=fake_input):
            cmd_wizard(args)

        task_md = (tmp_path / "wiz-proj" / "task.md").read_text()
        sops_md = (tmp_path / "wiz-proj" / "sops.md").read_text()
        pact_yaml_path = tmp_path / "wiz-proj" / "pact.yaml"

        assert "Build a thing" in task_md
        assert "my-proj" in task_md
        assert "pytest" in sops_md
        assert pact_yaml_path.exists()

        with open(pact_yaml_path) as f:
            cfg = yaml.safe_load(f)
        assert cfg["budget"] == 10.0

    def test_wizard_with_config_file(self, tmp_path: Path):
        """Non-interactive mode with --config flag."""
        from pact.cli import cmd_wizard
        import argparse

        config_file = tmp_path / "wiz.json"
        config_file.write_text(json.dumps({
            "project_name": "ci-project",
            "description": "Automated setup",
            "language": "typescript",
            "budget": 50.0,
        }))

        project_dir = str(tmp_path / "ci-proj")
        args = argparse.Namespace(
            project_dir=project_dir,
            config=str(config_file),
            budget=None,
            verbose=False,
        )

        cmd_wizard(args)

        sops_md = (tmp_path / "ci-proj" / "sops.md").read_text()
        assert "TypeScript" in sops_md

        with open(tmp_path / "ci-proj" / "pact.yaml") as f:
            cfg = yaml.safe_load(f)
        assert cfg["budget"] == 50.0
        assert cfg["language"] == "typescript"

    def test_wizard_budget_override(self, tmp_path: Path):
        """--budget flag overrides config file budget."""
        from pact.cli import cmd_wizard
        import argparse

        config_file = tmp_path / "wiz.json"
        config_file.write_text(json.dumps({"project_name": "test", "budget": 10.0}))

        project_dir = str(tmp_path / "override-proj")
        args = argparse.Namespace(
            project_dir=project_dir,
            config=str(config_file),
            budget=99.0,
            verbose=False,
        )

        cmd_wizard(args)

        with open(tmp_path / "override-proj" / "pact.yaml") as f:
            cfg = yaml.safe_load(f)
        assert cfg["budget"] == 99.0
