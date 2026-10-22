"""M2-0: the development answer keys and corpus list are well formed, and every repository location they cite
is correct. These tests check the keys themselves; scoring extractor output against them starts in M2-1."""

from __future__ import annotations

from collections import Counter
from pathlib import Path
from typing import Any

import pytest

from fifthnine.schema.v1 import FactStatus
from tests.support.answer_keys import Corpus, FactKey, InputRole, LabelledFact, Loc, load

ROOT = Path(__file__).resolve().parents[2]
FACTS = ROOT / "benchmarks" / "dev" / "facts"
KEYS = sorted(FACTS.glob("*.facts.yaml"))
CORPUS = ROOT / "benchmarks" / "dev" / "corpus" / "corpus.v1.yaml"


def _locs(key: FactKey) -> list[tuple[str, Loc]]:
    out: list[tuple[str, Loc]] = []
    for link in key.links:
        out += [(link.id, loc) for loc in link.evidence]
    for fact in key.facts:
        if fact.source:
            out.append((fact.id, fact.source))
        out += [(fact.id, i.loc) for i in fact.inputs if i.loc]
    return out


def _text(value: Any) -> list[str] | None:
    """Literal renderings that must appear in the anchor, or None when the value is structured."""
    if isinstance(value, bool):
        return [str(value).lower()]
    if isinstance(value, (int, float, str)):
        return [str(value)]
    if isinstance(value, list) and all(isinstance(v, (int, str)) for v in value):
        return [str(v) for v in value]
    return None


def _is_literal(fact: LabelledFact) -> bool:
    return fact.status is FactStatus.DECLARED and not fact.absent and not any(i.role is InputRole.VALUE_SOURCE for i in fact.inputs)


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
def test_literal_values_appear_on_their_source_line(path: Path) -> None:
    """A declared fact whose value is written on one line must show that value in its anchor."""
    key = load(path, FactKey)
    problems = []
    for fact in key.facts:
        expected = _text(fact.value)
        if fact.source and expected and _is_literal(fact):
            missing = [e for e in expected if e not in fact.source.anchor]
            if missing:
                problems.append(f"{fact.id}: anchor {fact.source.anchor!r} lacks {missing}")
    assert not problems, "\n".join(problems)


@pytest.mark.parametrize("path", KEYS, ids=lambda p: p.name)
def test_env_dependent_facts_name_their_link(path: Path) -> None:
    """CEO requirement M2-R1: a value that reaches the application through a container environment is repository-
    resolved only under the proposed link, so the key must say which link it depends on."""
    key = load(path, FactKey)
    missing = [f.id for f in key.facts if f.kind.startswith(("spring.", "python.settings")) and not f.link_ids]
    assert not missing


@pytest.mark.parametrize("path", KEYS, ids=lambda p: p.name)
def test_defaults_cite_a_version_or_are_verified_across_releases(path: Path) -> None:
    key = load(path, FactKey)
    for fact in key.facts:
        if fact.status is FactStatus.DEFAULT:
            assert fact.default_ref is not None
            if fact.default_ref.applied_version is None:
                assert fact.default_ref.framework == "kubernetes" and fact.default_ref.verified_at, fact.id
            else:
                assert any(i.role is InputRole.VERSION_BASIS for i in fact.inputs), f"{fact.id}: name the version fact used"


def test_corpus_list() -> None:
    corpus = load(CORPUS, Corpus)
    entries = corpus.entries
    assert corpus.status == "proposed"  # not frozen before CEO review (M2-D4)
    assert (ROOT / corpus.selection_doc).is_file()
    assert Counter(e.framework for e in entries) == {"spring-boot": 16, "fastapi": 10}
    # D1: real-world generalization is measured only on the generalization cohort, kept separate from fixtures
    gen = Counter(e.framework for e in entries if e.cohort == "generalization")
    assert gen["spring-boot"] >= 6 and gen["fastapi"] >= 6, gen
    assert all(e.cohort == "educational" for e in entries if e.repository in {"PolarBookshop/catalog-service", "DevOps-Boot/fastapi-k8s"})
    assert all(e.licence.spdx in {"Apache-2.0", "MIT", "MIT-0", "BSD-3-Clause"} for e in entries)
    owners = Counter(e.repository.split("/")[0].lower() for e in entries)
    assert max(owners.values()) <= 2, owners  # C6
    for fw in ("spring-boot", "fastapi"):
        assert sum(e.framework == fw and e.deployment_shape == "helm-only" for e in entries) <= 2  # C3
    assert sum(e.framework == "spring-boot" and e.in_supported_range == "yes" for e in entries) >= 4  # C6
