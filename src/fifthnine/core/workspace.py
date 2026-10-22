"""Repository discovery: deterministic walk with ignores and untrusted-input limits.

Nothing here follows symlinks, opens network connections or executes repository content.
"""

from __future__ import annotations

import os
import stat
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath

import pathspec

from fifthnine.schema.v1 import SkippedFile

DEFAULT_MAX_FILE_SIZE = 1_048_576
DEFAULT_MAX_FILES = 20_000
BINARY_SNIFF_BYTES = 8192

# Directories never descended into. Documented in docs/fifthnine/M1.md with their coverage effect.
BUILTIN_EXCLUDED_DIRS = frozenset({
    ".git", ".hg", ".svn", "node_modules", ".venv", "venv", "__pycache__", ".tox", ".nox",
    ".mypy_cache", ".pytest_cache", ".ruff_cache", ".gradle", ".idea", ".vscode", "target", "build", "dist",
    ".terraform", "vendor", "site-packages", ".eggs",
})

# File kinds FifthNine understands. Order matters only for documentation.
KIND_MAVEN_POM = "maven-pom"
KIND_GRADLE_BUILD = "gradle-build"
KIND_GRADLE_CATALOG = "gradle-version-catalog"
KIND_PYPROJECT = "pyproject"
KIND_REQUIREMENTS = "requirements"
KIND_LOCKFILE = "python-lockfile"
KIND_PYTHON_VERSION = "python-version-file"
KIND_SPRING_CONFIG = "spring-config"
KIND_DOCKERFILE = "dockerfile"
KIND_YAML = "yaml"  # inspected for Kubernetes objects
KIND_HELM_CHART = "helm-chart"
KIND_KUSTOMIZATION = "kustomization"
KIND_JAVA_SOURCE = "java-source"
KIND_KOTLIN_SOURCE = "kotlin-source"
KIND_PYTHON_SOURCE = "python-source"
KIND_FIFTHNINE_CONFIG = "fifthnine-config"

PARSED_KINDS = frozenset({
    KIND_MAVEN_POM, KIND_GRADLE_BUILD, KIND_GRADLE_CATALOG, KIND_PYPROJECT, KIND_REQUIREMENTS, KIND_LOCKFILE,
    KIND_PYTHON_VERSION, KIND_SPRING_CONFIG, KIND_DOCKERFILE, KIND_YAML, KIND_HELM_CHART, KIND_KUSTOMIZATION,
})


def classify(path: PurePosixPath) -> str | None:
    name = path.name
    lower = name.lower()
    if name == ".fifthnine.yml":
        return KIND_FIFTHNINE_CONFIG
    if name == "pom.xml":
        return KIND_MAVEN_POM
    if name in ("build.gradle", "build.gradle.kts", "settings.gradle", "settings.gradle.kts"):
        return KIND_GRADLE_BUILD if name.startswith("build") else None
    if name == "libs.versions.toml":
        return KIND_GRADLE_CATALOG
    if name == "pyproject.toml":
        return KIND_PYPROJECT
    if lower.startswith("requirements") and lower.endswith((".txt", ".in")):
        return KIND_REQUIREMENTS
    if name in ("uv.lock", "poetry.lock"):
        return KIND_LOCKFILE
    if name == ".python-version":
        return KIND_PYTHON_VERSION
    if name == "Chart.yaml":
        return KIND_HELM_CHART
    if name in ("kustomization.yaml", "kustomization.yml", "Kustomization"):
        return KIND_KUSTOMIZATION
    if name == "Dockerfile" or name.startswith("Dockerfile.") or lower.endswith(".dockerfile") or name == "Containerfile":
        return KIND_DOCKERFILE
    stem, _, ext = name.rpartition(".")
    if ext in ("yml", "yaml", "properties") and (stem == "application" or stem.startswith("application-")):
        return KIND_SPRING_CONFIG
    if ext in ("yml", "yaml"):
        return KIND_YAML
    if ext == "java":
        return KIND_JAVA_SOURCE
    if ext in ("kt", "kts"):
        return KIND_KOTLIN_SOURCE
    if ext == "py":
        return KIND_PYTHON_SOURCE
    return None


@dataclass
class FileEntry:
    path: str  # repository-relative POSIX path
    size: int
    kind: str | None


@dataclass
class Workspace:
    root: Path
    files: list[FileEntry] = field(default_factory=list)
    skipped: list[SkippedFile] = field(default_factory=list)
    excluded_dirs: list[str] = field(default_factory=list)
    ignored_files: int = 0  # matched by .gitignore or ignore_paths
    ignored_dirs: int = 0
    invalid_ignore_patterns: list[str] = field(default_factory=list)
    file_limit_reached: bool = False

    def read_text(self, entry: FileEntry) -> str:
        """Read a discovered file (size-capped by discover). A UTF-8 byte order mark is dropped; undecodable bytes
        become U+FFFD. OSError propagates to the caller, which records the file as skipped."""
        data = (self.root / entry.path).read_bytes()
        return data.decode("utf-8-sig", errors="replace")


class IgnorePatternError(ValueError):
    """An ignore_paths entry from .fifthnine.yml is not a valid gitignore pattern."""


def _load_ignore_spec(root: Path, extra_patterns: list[str], invalid: list[str]) -> pathspec.PathSpec:
    """Root .gitignore plus config ignore_paths. Invalid .gitignore lines are skipped (git ignores them too) and
    reported; an invalid ignore_paths entry is a configuration error."""
    lines: list[str] = []
    gitignore = root / ".gitignore"
    try:
        if gitignore.is_file() and not gitignore.is_symlink() and gitignore.stat().st_size <= DEFAULT_MAX_FILE_SIZE:
            candidates = gitignore.read_text(encoding="utf-8-sig", errors="replace").splitlines()
        else:
            candidates = []
    except OSError:
        candidates = []
    for line in candidates:
        try:
            pathspec.PathSpec.from_lines("gitwildmatch", [line])
            lines.append(line)
        except Exception:  # noqa: BLE001 - pathspec raises several error types for bad patterns
            invalid.append(line)
    for pattern in extra_patterns:
        try:
            pathspec.PathSpec.from_lines("gitwildmatch", [pattern])
        except Exception as exc:  # noqa: BLE001
            raise IgnorePatternError(f"invalid ignore_paths entry {pattern!r}: {exc}") from None
        lines.append(pattern)
    return pathspec.PathSpec.from_lines("gitwildmatch", lines)


def _safe_rel(rel: str) -> str | None:
    """None when a path is not valid UTF-8 (undecodable bytes surface as surrogates on POSIX)."""
    try:
        rel.encode("utf-8")
        return rel
    except UnicodeEncodeError:
        return None


def _printable(rel: str) -> str:
    return rel.encode("utf-8", errors="surrogateescape").decode("utf-8", errors="backslashreplace")


def _is_binary(path: Path) -> bool:
    with path.open("rb") as handle:
        return b"\x00" in handle.read(BINARY_SNIFF_BYTES)


def discover(
    root: Path,
    ignore_paths: list[str] | None = None,
    max_file_size: int = DEFAULT_MAX_FILE_SIZE,
    max_files: int = DEFAULT_MAX_FILES,
    exclude_files: frozenset[str] = frozenset(),
) -> Workspace:
    """Walk `root` in sorted order. Honors the root .gitignore and config ignore_paths (gitwildmatch).
    Nested .gitignore files are not read in v0.1 (documented limitation). `exclude_files` holds repository-relative
    paths FifthNine itself writes (the report file), so writing a report inside the tree does not change the scan."""
    root = root.resolve()
    ws = Workspace(root=root)
    spec = _load_ignore_spec(root, ignore_paths or [], ws.invalid_ignore_patterns)
    stack: list[Path] = [root]
    while stack:
        current = stack.pop()
        try:
            entries = sorted(os.scandir(current), key=lambda e: e.name)
        except OSError as exc:
            rel = current.relative_to(root).as_posix() or "."
            ws.skipped.append(SkippedFile(path=rel, reason="unsupported", detail=f"directory not readable: {exc.strerror}"))
            continue
        subdirs: list[Path] = []
        for entry in entries:
            path = Path(entry.path)
            raw_rel = path.relative_to(root).as_posix()
            rel = _safe_rel(raw_rel) or ""
            if not rel:
                ws.skipped.append(SkippedFile(path=_printable(raw_rel), reason="unsupported", detail="name is not valid UTF-8; not read"))
                continue
            if entry.is_symlink():
                target = os.path.realpath(path)
                inside = target == str(root) or target.startswith(str(root) + os.sep)
                ws.skipped.append(SkippedFile(
                    path=rel, reason="symlink_outside_root" if not inside else "ignored",
                    detail="symlink not followed" if inside else "symlink target is outside the repository",
                ))
                continue
            if entry.is_dir(follow_symlinks=False):
                if entry.name in BUILTIN_EXCLUDED_DIRS or (path / "pyvenv.cfg").is_file():
                    ws.excluded_dirs.append(rel)  # any virtualenv, whatever its name, is identified by pyvenv.cfg
                    continue
                if spec.match_file(rel + "/"):
                    ws.ignored_dirs += 1
                    continue
                subdirs.append(path)
                continue
            if not entry.is_file(follow_symlinks=False):
                continue
            if rel in exclude_files:
                continue  # FifthNine's own report file; never part of the scanned repository
            if spec.match_file(rel):
                ws.ignored_files += 1
                continue
            if len(ws.files) >= max_files:
                ws.file_limit_reached = True
                return _finish(ws)
            info = entry.stat(follow_symlinks=False)
            if not stat.S_ISREG(info.st_mode):
                continue
            size = info.st_size
            kind = classify(PurePosixPath(rel))
            if kind in PARSED_KINDS or kind == KIND_FIFTHNINE_CONFIG:
                if size > max_file_size:
                    ws.skipped.append(SkippedFile(path=rel, reason="too_large", detail=f"{size} bytes > limit {max_file_size}"))
                    kind = None
                else:
                    try:
                        binary = _is_binary(path)
                    except OSError as exc:
                        ws.skipped.append(SkippedFile(path=rel, reason="unsupported", detail=f"not readable: {exc.strerror}"))
                        kind = None
                        binary = False
                    if binary:
                        ws.skipped.append(SkippedFile(path=rel, reason="binary", detail="NUL byte in first 8 KiB"))
                        kind = None
            ws.files.append(FileEntry(path=rel, size=size, kind=kind))
        stack.extend(reversed(subdirs))
    return _finish(ws)


def _finish(ws: Workspace) -> Workspace:
    ws.files.sort(key=lambda f: f.path)
    ws.skipped.sort(key=lambda s: (s.path, s.reason))
    ws.excluded_dirs.sort()
    return ws
