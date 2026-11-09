"""Re-read every upstream default citation in the fact keys from tagged source.

Usage: python benchmarks/dev/facts/verify_upstream.py GIT_ROOT [GIT_ROOT ...]

Each GIT_ROOT is a local clone (shallow and blob-filtered is fine) whose remote is the cited GitHub repository.
For every default_ref, the script checks that the tag resolves to the cited commit and that the anchor text is on
the cited line, at the cited tag and at every tag in verified_at. Needs network if blobs must be fetched lazily.
Benchmark tooling only; the offline test suite never runs it.
"""

from __future__ import annotations

import functools
import re
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


def _check(label: str, d: object, repos: dict[str, Path]) -> int:
    """Check one citation at its cited tag (exact line, commit) and at every tag in verified_at (anchor in the file)."""
    failures = 0
    repo = repos.get(d.repository)  # type: ignore[attr-defined]
    if repo is None:
        print(f"{label}: no local clone of {d.repository}")  # type: ignore[attr-defined]
        return 1
    for tag in [d.tag, *[t for t in d.verified_at if t != d.tag]]:  # type: ignore[attr-defined]
        _fetch(repo, tag)
        try:
            lines = _git(repo, "show", f"tags/{tag}:{d.path}").splitlines()  # type: ignore[attr-defined]
        except subprocess.CalledProcessError:
            print(f"{label}: {d.path} missing at {tag}")  # type: ignore[attr-defined]
            failures += 1
            continue
        if tag == d.tag:  # type: ignore[attr-defined]
            commit = _git(repo, "rev-parse", f"tags/{tag}^{{commit}}").strip()
            ok = commit == d.commit and len(lines) >= d.line and d.anchor in lines[d.line - 1]  # type: ignore[attr-defined]
            where = f"{d.path}:{d.line}"  # type: ignore[attr-defined]
        else:
            ok = any(d.anchor in line for line in lines)  # type: ignore[attr-defined]
            where = f"{d.path} (anchor present)"  # type: ignore[attr-defined]
        print(f"{label} {d.repository}@{tag} {where}: {'ok' if ok else 'FAIL'}")  # type: ignore[attr-defined]
        failures += not ok
    return failures


@functools.lru_cache(maxsize=None)
def _tags(repo: Path, minor: str) -> list[str]:
    """Release tags `vMINOR.N` of the minor, from the remote, oldest first (no release candidates or milestones)."""
    out = _git(repo, "ls-remote", "--tags", "origin", f"refs/tags/v{minor}.*")
    found = {line.split("refs/tags/")[1].removesuffix("^{}") for line in out.splitlines() if "refs/tags/" in line}
    return sorted((t for t in found if re.fullmatch(rf"v{re.escape(minor)}\.\d+", t)), key=lambda t: int(t.rsplit(".", 1)[1]))


def check_spring_table(repos: dict[str, Path]) -> int:
    """Every entry of data/defaults/spring-boot.v1.yaml: tag to commit, anchor on the cited line at the cited release, and the
    anchor text present in the same file at the first release of the minor, a middle one and the latest one."""
    import yaml

    table = yaml.safe_load((ROOT / "src/fifthnine/data/defaults/spring-boot.v1.yaml").read_text())
    failures = 0
    for key, entry in sorted(table["entries"].items()):
        for minor, v in sorted(entry["versions"].items()):
            rel = table["releases"][minor]
            repo = repos.get(table["repository"])
            if repo is None:
                print(f"table:spring:{key}: no local clone of {table['repository']}")
                return 1
            cited = _verify_cited(repo, key, minor, rel, v)
            failures += cited
            if entry.get("exact_patch_only"):
                continue  # a component version is exact to the cited patch by definition
            listed = [t for t in rel.get("verified_at", []) if t != rel["tag"]]
            if not listed or rel["tag"] not in rel.get("verified_at", []) or any(t not in _tags(repo, minor) for t in listed):
                print(f"table:spring:{key} {minor}: verified_at is missing, lacks the cited tag, or names a tag that does not exist")
                failures += 1
            for tag in listed:
                _fetch(repo, tag)
                try:
                    lines = _git(repo, "show", f"tags/{tag}:{v['path']}").splitlines()
                    ok = any(v["anchor"] in line for line in lines)
                except subprocess.CalledProcessError:
                    ok = False
                print(f"table:spring:{key} {tag} {v['path']} (anchor present): {'ok' if ok else 'FAIL'}")
                failures += not ok
    return failures


def _verify_cited(repo: Path, key: str, minor: str, rel: dict[str, str], v: dict[str, object]) -> int:
    tag = rel["tag"]
    _fetch(repo, tag)
    try:
        commit = _git(repo, "rev-parse", f"tags/{tag}^{{commit}}").strip()
        lines = _git(repo, "show", f"tags/{tag}:{v['path']}").splitlines()
    except subprocess.CalledProcessError:
        print(f"table:spring:{key} {tag}: {v['path']} missing")
        return 1
    line = int(v["line"])  # type: ignore[call-overload]
    ok = commit == rel["commit"] and len(lines) >= line and str(v["anchor"]) in lines[line - 1]
    print(f"table:spring:{key} {tag}@{commit[:7]} {v['path']}:{line}: {'ok' if ok else 'FAIL'}")
    return int(not ok)


def main(roots: list[Path]) -> int:
    from fifthnine.resolve.defaults import kubernetes_defaults

    repos = {_remote(r): r for r in roots}
    failures = 0
    for key_path in sorted((ROOT / "benchmarks/dev/facts").glob("*.facts.yaml")):
        for fact in load(key_path, FactKey).facts:
            if fact.default_ref is not None:
                failures += _check(fact.id, fact.default_ref, repos)
    # the packaged table the engine actually uses: every entry, at every verified release
    for key, entry in sorted(kubernetes_defaults().items()):
        if entry.ref.upstream is not None:
            failures += _check(f"table:{key}", entry.ref.upstream, repos)
    failures += check_spring_table(repos)
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main([Path(a) for a in sys.argv[1:]]) if len(sys.argv) > 1 else 2)
