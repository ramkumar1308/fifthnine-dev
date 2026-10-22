"""Fetch every development-corpus repository at its pinned commit.

Usage: python benchmarks/dev/corpus/fetch.py DEST_DIR

Needs network access to github.com. This is benchmark tooling, never part of the scanner, and is not run by the
offline test suite. Each repository lands in DEST_DIR/<owner>__<repo> with HEAD at the pinned commit; the script
fails if the fetched commit does not match. Blobs larger than 1 MiB are not downloaded, matching the scanner's
default per-file limit.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from fifthnine.core.safeparse import load_yaml_documents, to_plain

CORPUS = Path(__file__).with_name("corpus.v1.yaml")


def _git(cwd: Path, *args: str) -> str:
    # Argument list, no shell; repository and commit come from the reviewed corpus file.
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True).stdout.strip()  # noqa: S603, S607


def main(dest: Path) -> int:
    data = to_plain(load_yaml_documents(CORPUS.read_text(encoding="utf-8"))[0].data)
    dest.mkdir(parents=True, exist_ok=True)
    failures = 0
    for entry in data["entries"]:
        repo, commit = entry["repository"], entry["commit"]
        target = dest / repo.replace("/", "__")
        if not (target / ".git").is_dir():
            target.mkdir(parents=True, exist_ok=True)
            _git(target, "init", "-q")
            _git(target, "remote", "add", "origin", f"https://github.com/{repo}.git")
        try:
            _git(target, "fetch", "-q", "--depth", "1", "--filter=blob:limit=1m", "origin", commit)
            _git(target, "-c", "advice.detachedHead=false", "checkout", "-q", "--force", commit)
        except subprocess.CalledProcessError as exc:
            print(f"{entry['id']} {repo}: fetch failed: {exc.stderr.strip()}", file=sys.stderr)
            failures += 1
            continue
        head = _git(target, "rev-parse", "HEAD")
        status = "ok" if head == commit else f"MISMATCH (got {head})"
        failures += status != "ok"
        print(f"{entry['id']} {repo} {commit[:12]} {status}")
    return 1 if failures else 0


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print(__doc__, file=sys.stderr)
        sys.exit(2)
    sys.exit(main(Path(sys.argv[1])))
