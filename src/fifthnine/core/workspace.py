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
SOURCE_KINDS = frozenset({KIND_JAVA_SOURCE, KIND_KOTLIN_SOURCE, KIND_PYTHON_SOURCE})


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
    gitignored: int = 0
    file_limit_reached: bool = False

    def read_text(self, entry: FileEntry) -> str:
        """Read a discovered file. Callers only pass entries that passed the size cap."""
        data = (self.root / entry.path).read_bytes()
        return data.decode("utf-8", errors="replace")

    def of_kind(self, *kinds: str) -> list[FileEntry]:
        return [f for f in self.files if f.kind in kinds]


def _load_ignore_spec(root: Path, extra_patterns: list[str]) -> pathspec.PathSpec:
    lines: list[str] = []
    gitignore = root / ".gitignore"
    if gitignore.is_file() and not gitignore.is_symlink() and gitignore.stat().st_size <= DEFAULT_MAX_FILE_SIZE:
        lines.extend(gitignore.read_text(encoding="utf-8", errors="replace").splitlines())
    lines.extend(extra_patterns)
    return pathspec.PathSpec.from_lines("gitwildmatch", lines)


def _is_binary(path: Path) -> bool:
    with path.open("rb") as handle:
        return b"\x00" in handle.read(BINARY_SNIFF_BYTES)


def discover(
    root: Path,
    ignore_paths: list[str] | None = None,
    max_file_size: int = DEFAULT_MAX_FILE_SIZE,
    max_files: int = DEFAULT_MAX_FILES,
) -> Workspace:
    """Walk `root` in sorted order. Honors the root .gitignore and config ignore_paths (gitwildmatch).
    Nested .gitignore files are not read in v0.1 (documented limitation)."""
    root = root.resolve()
    ws = Workspace(root=root)
    spec = _load_ignore_spec(root, ignore_paths or [])
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
            rel = path.relative_to(root).as_posix()
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
                    ws.gitignored += 1
                    continue
                subdirs.append(path)
                continue
            if not entry.is_file(follow_symlinks=False):
                continue
            if spec.match_file(rel):
                ws.gitignored += 1
                continue
            if len(ws.files) >= max_files:
                ws.file_limit_reached = True
                return _finish(ws)
            size = entry.stat(follow_symlinks=False).st_size
            mode = entry.stat(follow_symlinks=False).st_mode
            if not stat.S_ISREG(mode):
                continue
            kind = classify(PurePosixPath(rel))
            if kind in PARSED_KINDS or kind == KIND_FIFTHNINE_CONFIG:
                if size > max_file_size:
                    ws.skipped.append(SkippedFile(path=rel, reason="too_large", detail=f"{size} bytes > limit {max_file_size}"))
                    kind = None
                elif _is_binary(path):
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
