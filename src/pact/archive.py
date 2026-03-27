"""Archive existing artifacts before writing new ones.

When starting a new project (``pact init``), any artifacts from a previous
session are moved into a slug-named subdirectory under ``.pact/archive/``
to keep the working directory clean.

    .pact/archive/user-login-flow/
        task.md
        sops.md
        pact.yaml
        design.md
        tasks.json
        ...

The slug is derived from file content (e.g., the task title in task.md).
All files from the same session share one slug for coherence. Numeric
suffixes handle collisions.
"""

from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path


def slugify(text: str, max_length: int = 40) -> str:
    """Convert text to a filesystem-friendly slug."""
    text = text.lower().strip()
    text = re.sub(r"[^a-z0-9\s-]", "", text)
    text = re.sub(r"[\s_]+", "-", text)
    text = re.sub(r"-+", "-", text)
    text = text.strip("-")
    if len(text) > max_length:
        text = text[:max_length].rsplit("-", 1)[0]
    return text


def _extract_slug_from_markdown(content: str) -> str:
    """Extract a slug from the first meaningful heading or content line."""
    _SKIP = {"task", "design document", "operating procedures"}

    found_heading = False
    for line in content.splitlines():
        stripped = line.strip()
        if not stripped:
            continue

        if stripped.startswith("#"):
            found_heading = True
            heading = re.sub(r"^#+\s*", "", stripped).strip()
            if ":" in heading:
                before, _, after = heading.partition(":")
                if before.strip().lower() in _SKIP | {"system", "system briefing"}:
                    if after.strip():
                        return slugify(after.strip())
                return slugify(heading)
            if heading.lower() in _SKIP:
                continue
            return slugify(heading)

        elif found_heading:
            if stripped.startswith(("*", "---", "```", "| ")):
                continue
            slug = slugify(stripped[:60])
            if slug and len(slug) >= 3:
                return slug

    return ""


def _extract_slug_from_yaml(content: str) -> str:
    """Extract a slug from YAML by looking for name-like keys."""
    try:
        import yaml

        data = yaml.safe_load(content)
    except Exception:
        return ""
    if not isinstance(data, dict):
        return ""
    for key in ("system", "name", "project", "description"):
        val = data.get(key)
        if isinstance(val, str) and val.strip():
            return slugify(val)
    return ""


def extract_slug(path: Path) -> str:
    """Extract a meaningful slug from a file's content."""
    try:
        content = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return ""
    if not content.strip():
        return ""

    if path.suffix == ".md":
        return _extract_slug_from_markdown(content)
    elif path.suffix in (".yaml", ".yml"):
        return _extract_slug_from_yaml(content)
    return ""


def _unique_dir(parent: Path, slug: str) -> Path:
    """Find a unique subdirectory: parent/slug, parent/slug-2, ..."""
    candidate = parent / slug
    if not candidate.exists():
        return candidate
    for i in range(2, 10000):
        candidate = parent / f"{slug}-{i}"
        if not candidate.exists():
            return candidate
    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    return parent / f"{slug}-{ts}"


def archive_artifacts(
    directory: Path,
    artifact_names: list[str],
    archive_base: Path,
    slug_source_priority: list[str] | None = None,
) -> tuple[Path | None, list[tuple[Path, Path]]]:
    """Archive existing artifacts into a slug-named subdirectory.

    Moves each existing artifact from *directory* into a new subdirectory
    under *archive_base*, named after the content-derived slug.

    Args:
        directory: Directory containing current artifacts.
        artifact_names: Filenames to check and archive.
        archive_base: Parent for archive subdirectories
            (e.g., ``.pact/archive``).
        slug_source_priority: Ordered filenames to try for slug extraction.
            The first file that yields a non-empty slug wins.
            Defaults to *artifact_names*.

    Returns:
        ``(archive_subdir, [(original, archived), ...])`` for files that
        were moved, or ``(None, [])`` if nothing to archive.
    """
    existing = [directory / name for name in artifact_names if (directory / name).exists()]
    if not existing:
        return None, []

    # Extract slug from the most descriptive file
    slug = ""
    for name in slug_source_priority or artifact_names:
        path = directory / name
        if path.exists():
            slug = extract_slug(path)
            if slug:
                break

    # Fallback: modification timestamp of the newest artifact
    if not slug:
        mtime = max(p.stat().st_mtime for p in existing)
        slug = datetime.fromtimestamp(mtime).strftime("%Y%m%d-%H%M%S")

    archive_base.mkdir(parents=True, exist_ok=True)
    subdir = _unique_dir(archive_base, slug)
    subdir.mkdir(parents=True, exist_ok=True)

    archived: list[tuple[Path, Path]] = []
    for path in existing:
        dest = subdir / path.name
        path.rename(dest)
        archived.append((path, dest))

    return subdir, archived


def list_archived_sessions(archive_base: Path) -> list[dict[str, str | Path]]:
    """List all archived sessions with their slug and path.

    Returns a list of dicts with keys ``slug``, ``path``, and ``files``.
    Sorted by directory modification time (newest first).
    """
    if not archive_base.exists():
        return []
    dirs = [d for d in archive_base.iterdir() if d.is_dir()]
    dirs.sort(key=lambda d: d.stat().st_mtime, reverse=True)
    result = []
    for d in dirs:
        files = [f.name for f in d.iterdir() if f.is_file()]
        result.append({"slug": d.name, "path": d, "files": files})
    return result


def load_archived_artifacts(archive_base: Path, slug: str | None = None) -> dict[str, str]:
    """Load artifact contents from an archived session.

    Args:
        archive_base: Parent archive directory.
        slug: Specific session slug to load. If None, loads the most
            recent archived session.

    Returns:
        Dict mapping filename to file content.
    """
    if not archive_base.exists():
        return {}

    if slug:
        subdir = archive_base / slug
        if not subdir.is_dir():
            return {}
    else:
        sessions = list_archived_sessions(archive_base)
        if not sessions:
            return {}
        subdir = sessions[0]["path"]

    artifacts = {}
    for f in subdir.iterdir():
        if f.is_file():
            try:
                artifacts[f.name] = f.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                continue
    return artifacts
