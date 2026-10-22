"""Re-read every upstream default citation in the fact keys from tagged source.

Usage: python benchmarks/dev/facts/verify_upstream.py GIT_ROOT [GIT_ROOT ...]

Each GIT_ROOT is a local clone (shallow and blob-filtered is fine) whose remote is the cited GitHub repository.
For every default_ref, the script checks that the tag resolves to the cited commit and that the anchor text is on
the cited line, at the cited tag and at every tag in verified_at. Needs network if blobs must be fetched lazily.
Benchmark tooling only; the offline test suite never runs it.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from tests.support.answer_keys import FactKey, load  # noqa: E402


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True, text=True).stdout  # noqa: S603, S607


def _remote(repo: Path) -> str:
    url = _git(repo, "remote", "get-url", "origin").strip().removesuffix(".git")
    return "/".join(url.split("/")[-2:])


def _fetch(repo: Path, tag: str) -> None:
    subprocess.run(["git", "-C", str(repo), "fetch", "-q", "--depth", "1", "--filter=blob:none", "origin",  # noqa: S603, S607
                    f"refs/tags/{tag}:refs/tags/{tag}"], check=False, capture_output=True)


def main(roots: list[Path]) -> int:
    repos = {_remote(r): r for r in roots}
    failures = 0
    for key_path in sorted((ROOT / "benchmarks/dev/facts").glob("*.facts.yaml")):
        for fact in load(key_path, FactKey).facts:
            d = fact.default_ref
            if d is None:
                continue
            repo = repos.get(d.repository)
            if repo is None:
                print(f"{fact.id}: no local clone of {d.repository}")
                failures += 1
                continue
            for tag in [d.tag, *[t for t in d.verified_at if t != d.tag]]:
                _fetch(repo, tag)
                try:
                    lines = _git(repo, "show", f"tags/{tag}:{d.path}").splitlines()
                except subprocess.CalledProcessError:
                    print(f"{fact.id}: {d.path} missing at {tag}")
                    failures += 1
                    continue
                if tag == d.tag:
                    commit = _git(repo, "rev-parse", f"tags/{tag}^{{commit}}").strip()
                    ok = commit == d.commit and len(lines) >= d.line and d.anchor in lines[d.line - 1]
                    where = f"{d.path}:{d.line}"
                else:
                    ok = any(d.anchor in line for line in lines)
                    where = f"{d.path} (anchor present)"
                print(f"{fact.id} {d.repository}@{tag} {where}: {'ok' if ok else 'FAIL'}")
                failures += not ok
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main([Path(a) for a in sys.argv[1:]]) if len(sys.argv) > 1 else 2)
