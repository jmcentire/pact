"""Tests for artifact archival and context loading."""

import pytest
from pathlib import Path

from pact.archive import (
    archive_artifacts,
    extract_slug,
    list_archived_sessions,
    load_archived_artifacts,
    slugify,
    _extract_slug_from_markdown,
    _extract_slug_from_yaml,
    _unique_dir,
)


# ============================================================================
# slugify
# ============================================================================

class TestSlugify:
    def test_basic(self):
        assert slugify("Auth Service") == "auth-service"

    def test_special_chars(self):
        result = slugify("Hello, World! (test)")
        assert result == "hello-world-test"

    def test_max_length(self):
        result = slugify("a very long name that exceeds the limit", max_length=10)
        assert len(result) <= 10

    def test_empty(self):
        assert slugify("") == ""


# ============================================================================
# markdown slug extraction
# ============================================================================

class TestMarkdownSlug:
    def test_heading(self):
        content = "# Implement User Login\n\nContent."
        assert _extract_slug_from_markdown(content) == "implement-user-login"

    def test_task_colon(self):
        content = "# Task: Implement OAuth2\n\nDetails."
        assert _extract_slug_from_markdown(content) == "implement-oauth2"

    def test_generic_task_heading_skipped(self):
        content = "# Task\n\nImplement user login flow."
        slug = _extract_slug_from_markdown(content)
        assert slug == "implement-user-login-flow"

    def test_design_document_heading(self):
        content = "# Design Document\n\n*Auto-maintained by pact.*\n\n## Status: Active\n"
        slug = _extract_slug_from_markdown(content)
        assert slug == "status-active"


# ============================================================================
# YAML slug extraction
# ============================================================================

class TestYamlSlug:
    def test_name_key(self):
        content = "name: user-service\nbudget: 10.0"
        assert _extract_slug_from_yaml(content) == "user-service"

    def test_no_name_keys(self):
        content = "budget: 10.0\nlanguage: python"
        assert _extract_slug_from_yaml(content) == ""


# ============================================================================
# extract_slug (integration)
# ============================================================================

class TestExtractSlug:
    def test_markdown(self, tmp_path):
        p = tmp_path / "task.md"
        p.write_text("# Task: Build Payment API\n\nContent.", encoding="utf-8")
        assert extract_slug(p) == "build-payment-api"

    def test_yaml(self, tmp_path):
        p = tmp_path / "pact.yaml"
        p.write_text("name: auth-service\nbudget: 10.0", encoding="utf-8")
        assert extract_slug(p) == "auth-service"

    def test_empty(self, tmp_path):
        p = tmp_path / "empty.md"
        p.write_text("", encoding="utf-8")
        assert extract_slug(p) == ""

    def test_nonexistent(self, tmp_path):
        assert extract_slug(tmp_path / "nope.md") == ""


# ============================================================================
# _unique_dir
# ============================================================================

class TestUniqueDir:
    def test_no_collision(self, tmp_path):
        assert _unique_dir(tmp_path, "slug") == tmp_path / "slug"

    def test_collision(self, tmp_path):
        (tmp_path / "slug").mkdir()
        assert _unique_dir(tmp_path, "slug") == tmp_path / "slug-2"

    def test_multiple_collisions(self, tmp_path):
        (tmp_path / "s").mkdir()
        (tmp_path / "s-2").mkdir()
        assert _unique_dir(tmp_path, "s") == tmp_path / "s-3"


# ============================================================================
# archive_artifacts
# ============================================================================

class TestArchiveArtifacts:
    def test_no_existing_files(self, tmp_path):
        subdir, archived = archive_artifacts(
            tmp_path, ["task.md", "sops.md"], tmp_path / "archive"
        )
        assert subdir is None
        assert archived == []

    def test_archives_to_slug_dir(self, tmp_path):
        (tmp_path / "task.md").write_text("# Task: Build Login\n", encoding="utf-8")
        (tmp_path / "sops.md").write_text("# Operating Procedures\n\n## Tech Stack\n", encoding="utf-8")
        archive_base = tmp_path / ".pact" / "archive"

        subdir, archived = archive_artifacts(
            tmp_path,
            ["task.md", "sops.md"],
            archive_base,
            slug_source_priority=["task.md"],
        )

        assert subdir.name == "build-login"
        assert len(archived) == 2
        assert not (tmp_path / "task.md").exists()
        assert not (tmp_path / "sops.md").exists()
        assert (subdir / "task.md").exists()
        assert (subdir / "sops.md").exists()

    def test_partial_files(self, tmp_path):
        (tmp_path / "task.md").write_text("# Test\n", encoding="utf-8")
        archive_base = tmp_path / "archive"

        subdir, archived = archive_artifacts(
            tmp_path, ["task.md", "sops.md", "pact.yaml"], archive_base
        )

        assert len(archived) == 1

    def test_collision_increments(self, tmp_path):
        archive_base = tmp_path / "archive"

        (tmp_path / "task.md").write_text("# Feature X\n", encoding="utf-8")
        subdir1, _ = archive_artifacts(tmp_path, ["task.md"], archive_base)
        assert subdir1.name == "feature-x"

        (tmp_path / "task.md").write_text("# Feature X\nRevised.", encoding="utf-8")
        subdir2, _ = archive_artifacts(tmp_path, ["task.md"], archive_base)
        assert subdir2.name == "feature-x-2"


# ============================================================================
# list + load
# ============================================================================

class TestListAndLoad:
    def test_list_empty(self, tmp_path):
        assert list_archived_sessions(tmp_path / "nope") == []

    def test_list_sessions(self, tmp_path):
        archive = tmp_path / "archive"
        (archive / "login").mkdir(parents=True)
        (archive / "login" / "task.md").write_text("t", encoding="utf-8")
        (archive / "payment").mkdir()
        (archive / "payment" / "task.md").write_text("t", encoding="utf-8")

        result = list_archived_sessions(archive)
        assert len(result) == 2

    def test_load_specific(self, tmp_path):
        archive = tmp_path / "archive"
        (archive / "login").mkdir(parents=True)
        (archive / "login" / "task.md").write_text("# Login Task", encoding="utf-8")

        result = load_archived_artifacts(archive, slug="login")
        assert result["task.md"] == "# Login Task"

    def test_load_latest(self, tmp_path):
        import time
        archive = tmp_path / "archive"
        (archive / "old").mkdir(parents=True)
        (archive / "old" / "task.md").write_text("old", encoding="utf-8")
        time.sleep(0.05)
        (archive / "new").mkdir(parents=True)
        (archive / "new" / "task.md").write_text("new", encoding="utf-8")

        result = load_archived_artifacts(archive)
        assert result["task.md"] == "new"


# ============================================================================
# ProjectManager integration
# ============================================================================

class TestProjectManagerArchive:
    def test_init_archives_existing(self, tmp_path):
        """pact init archives existing artifacts and writes fresh templates."""
        from pact.project import ProjectManager

        # Simulate a previous session's artifacts
        (tmp_path / "task.md").write_text("# Task: Build Login Flow\n\nReal task.", encoding="utf-8")
        (tmp_path / "sops.md").write_text("# Operating Procedures\n\nCustom sops.", encoding="utf-8")
        (tmp_path / "pact.yaml").write_text("budget: 5.0\n", encoding="utf-8")
        (tmp_path / "design.md").write_text("# Design\n\nReal design.", encoding="utf-8")

        pm = ProjectManager(tmp_path)
        pm.init(budget=10.0)

        # Old files are archived
        archive_dir = tmp_path / ".pact" / "archive"
        assert archive_dir.exists()
        sessions = list_archived_sessions(archive_dir)
        assert len(sessions) == 1
        assert sessions[0]["slug"] == "build-login-flow"

        # Archived files contain original content
        archived = load_archived_artifacts(archive_dir, "build-login-flow")
        assert "Real task." in archived["task.md"]
        assert "Custom sops." in archived["sops.md"]

        # Fresh templates are written
        assert "Describe your task here." in (tmp_path / "task.md").read_text()
        assert "Python 3.12+" in (tmp_path / "sops.md").read_text()

    def test_init_no_archive_on_fresh(self, tmp_path):
        """pact init on a fresh directory doesn't create archive."""
        from pact.project import ProjectManager

        pm = ProjectManager(tmp_path)
        pm.init()

        archive_dir = tmp_path / ".pact" / "archive"
        assert not archive_dir.exists()

    def test_init_multiple_archives(self, tmp_path):
        """Multiple pact init cycles create distinct archives."""
        from pact.project import ProjectManager

        pm = ProjectManager(tmp_path)

        # First project
        pm.init()
        (tmp_path / "task.md").write_text("# Task: Feature A\n", encoding="utf-8")

        # Second project (archives Feature A)
        pm.init()
        (tmp_path / "task.md").write_text("# Task: Feature B\n", encoding="utf-8")

        # Third project (archives Feature B)
        pm.init()

        sessions = list_archived_sessions(tmp_path / ".pact" / "archive")
        slugs = {s["slug"] for s in sessions}
        assert "feature-a" in slugs
        assert "feature-b" in slugs

    def test_load_previous_context(self, tmp_path):
        """load_previous_context returns most recent archived artifacts."""
        from pact.project import ProjectManager

        pm = ProjectManager(tmp_path)
        pm.init()
        (tmp_path / "task.md").write_text("# Task: Build Auth\n\nAuth details.", encoding="utf-8")

        pm.init()  # archives "Build Auth"

        context = pm.load_previous_context()
        assert "Auth details." in context.get("task.md", "")
