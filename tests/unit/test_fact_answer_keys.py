"""M2-0: the development answer keys and corpus list are well formed, and every repository location they cite
is correct. These tests check the keys themselves; scoring extractor output against them starts in M2-1."""

from __future__ import annotations

from collections import Counter
from pathlib import Path

import pytest

from fifthnine.schema.v1 import FactStatus
from tests.support.answer_keys import Corpus, FactKey, Loc, load

ROOT = Path(__file__).resolve().parents[2]
FACTS = ROOT / "benchmarks" / "dev" / "facts"
KEYS = sorted(FACTS.glob("*.facts.yaml"))


def _locs(key: FactKey) -> list[tuple[str, Loc]]:
    out: list[tuple[str, Loc]] = []
    for link in key.links:
        out += [(link.id, loc) for loc in link.evidence]
    for fact in key.facts:
        if fact.source:
            out.append((fact.id, fact.source))
        out += [(fact.id, i.loc) for i in fact.inputs if i.loc]
    return out


def test_both_samples_have_keys() -> None:
    assert {p.name for p in KEYS} == {"orders-service-spring.facts.yaml", "catalog-api-fastapi.facts.yaml"}


@pytest.mark.parametrize("path", KEYS, ids=lambda p: p.name)
def test_key_validates(path: Path) -> None:
    key = load(path, FactKey)
    assert key.sample == path.name.removesuffix(".facts.yaml")
    assert (ROOT / "samples" / key.sample).is_dir()


@pytest.mark.parametrize("path", KEYS, ids=lambda p: p.name)
def test_every_cited_location_holds_its_anchor(path: Path) -> None:
    key = load(path, FactKey)
    base = ROOT / "samples" / key.sample
    problems = []
    for owner, loc in _locs(key):
        file = base / loc.path
        if not file.is_file():
            problems.append(f"{owner}: {loc.path} does not exist")
            continue
        lines = file.read_text(encoding="utf-8").splitlines()
        if loc.line > len(lines):
            problems.append(f"{owner}: {loc.path} has {len(lines)} lines, cited {loc.line}")
        elif loc.anchor not in lines[loc.line - 1]:
            problems.append(f"{owner}: {loc.path}:{loc.line} is {lines[loc.line - 1].strip()!r}, expected {loc.anchor!r}")
    assert not problems, "\n".join(problems)


@pytest.mark.parametrize("path", KEYS, ids=lambda p: p.name)
def test_env_dependent_facts_name_their_link(path: Path) -> None:
    """CEO requirement 1: a value that reaches the application through a container environment is repository-
    resolved only under the proposed link, so the key must say which link it depends on."""
    key = load(path, FactKey)
    missing = [f.id for f in key.facts if f.kind.startswith(("spring.", "python.settings")) and f.link is None]
    assert not missing


@pytest.mark.parametrize("path", KEYS, ids=lambda p: p.name)
def test_defaults_cite_a_version_or_explain_why_not(path: Path) -> None:
    key = load(path, FactKey)
    for fact in key.facts:
        if fact.status is FactStatus.DEFAULT:
            assert fact.default is not None
            if fact.default.applied_version is None:
                assert fact.default.note, f"{fact.id}: version-independent default needs a note"
            else:
                assert any(i.role.value == "version_basis" for i in fact.inputs), f"{fact.id}: name the version fact used"


def test_corpus_list() -> None:
    corpus = load(ROOT / "benchmarks" / "dev" / "corpus" / "corpus.v1.yaml", Corpus)
    assert corpus.status == "proposed"  # not frozen before CEO review (D4)
    assert (ROOT / corpus.selection_doc).is_file()
    assert len(corpus.entries) == 20
    assert Counter(e.framework for e in corpus.entries) == {"spring-boot": 10, "fastapi": 10}
    assert all(e.licence.spdx in {"Apache-2.0", "MIT", "MIT-0", "BSD-3-Clause"} for e in corpus.entries)
    assert len({e.commit for e in corpus.entries}) == 20
