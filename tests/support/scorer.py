"""Score extractor output against a fact key (docs/fifthnine/M2-0.md, "Scoring, from M2-1 on").

Match: an engine fact matches a key fact on (kind, subject key, scope). A matched key fact is correct only when every
scored field agrees; otherwise it is classified by the first field that differs. Unmatched key facts are missing;
engine facts with no key entry are unlabelled (listed for review, not counted wrong). Negative keys are evaluated on
engine facts. Links match on (kind, from_ref, to_ref) and are compared on status and evidence locations.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Any

from fifthnine.schema.v1 import Fact, Linkage
from tests.support.answer_keys import MILESTONE_ORDER, FactKey, LabelledFact, violates

LOCATION = re.compile(r"^(.+):(\d+)$")
FIELDS = ("value", "unit", "status", "absent", "override_exposure", "exposure_sources", "source", "inputs", "precedence",
          "link_ids", "overridden_by", "default_ref", "unresolved_reason")


@dataclass
class Score:
    correct: list[str] = field(default_factory=list)
    wrong: dict[str, tuple[str, Any, Any]] = field(default_factory=dict)  # key id -> (field, expected, got)
    missing: list[str] = field(default_factory=list)
    unlabelled: list[str] = field(default_factory=list)
    violated: dict[str, list[str]] = field(default_factory=dict)  # negative id -> engine fact ids
    links_correct: list[str] = field(default_factory=list)
    links_wrong: dict[str, tuple[str, Any, Any]] = field(default_factory=dict)
    links_missing: list[str] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return not (self.wrong or self.missing or self.violated or self.links_wrong or self.links_missing)

    def report(self) -> str:
        lines = [f"correct {len(self.correct)}, wrong {len(self.wrong)}, missing {len(self.missing)}, "
                 f"unlabelled {len(self.unlabelled)}, negative violations {len(self.violated)}, "
                 f"links correct {len(self.links_correct)} wrong {len(self.links_wrong)} missing {len(self.links_missing)}"]
        lines += [f"  WRONG {k}: {f}: expected {e!r}, got {g!r}" for k, (f, e, g) in sorted(self.wrong.items())]
        lines += [f"  MISSING {k}" for k in self.missing]
        lines += [f"  VIOLATED {k}: {v}" for k, v in sorted(self.violated.items())]
        lines += [f"  LINK WRONG {k}: {f}: expected {e!r}, got {g!r}" for k, (f, e, g) in sorted(self.links_wrong.items())]
        lines += [f"  LINK MISSING {k}" for k in self.links_missing]
        return "\n".join(lines)


def _key(kind: str, subject: str, scope: dict[str, str]) -> tuple[str, str, frozenset[tuple[str, str]]]:
    return kind, subject, frozenset(scope.items())


def _locs(evidence: list[str]) -> set[tuple[str, int]]:
    out: set[tuple[str, int]] = set()
    for e in evidence:
        m = LOCATION.match(e)
        if m:
            out.add((m.group(1), int(m.group(2))))
    return out


def score(key: FactKey, facts: list[Fact], links: list[Linkage], milestone: str) -> Score:
    upto = MILESTONE_ORDER[milestone]
    result = Score()
    engine = {_key(f.kind, f.subject.key, f.scope): f for f in facts}
    wanted = [k for k in key.facts if MILESTONE_ORDER[k.milestone] <= upto]
    to_engine: dict[str, str] = {}
    for k in key.facts:  # map every key fact that has a counterpart, so references resolve
        e = engine.get(_key(k.kind, k.subject, k.scope))
        if e is not None:
            to_engine[k.id] = e.fact_id
    to_key = {v: k for k, v in to_engine.items()}

    link_map: dict[str, str] = {}
    by_link = {(lk.kind, lk.from_ref, lk.to_ref): lk for lk in links}
    for kl in key.links:
        if MILESTONE_ORDER[kl.milestone] > upto:
            continue
        el = by_link.get((kl.kind, kl.from_ref, kl.to_ref))
        if el is None:
            result.links_missing.append(kl.id)
            continue
        link_map[kl.id] = el.link_id
        expected = {(loc.path, loc.line) for loc in kl.evidence}
        if el.status.value != kl.status:
            result.links_wrong[kl.id] = ("status", kl.status, el.status.value)
        elif _locs(el.evidence) != expected:
            result.links_wrong[kl.id] = ("evidence", sorted(expected), sorted(_locs(el.evidence)))
        else:
            result.links_correct.append(kl.id)

    for k in wanted:
        e = engine.get(_key(k.kind, k.subject, k.scope))
        if e is None:
            result.missing.append(k.id)
            continue
        diff = _compare(k, e, to_engine, link_map)
        if diff is None:
            result.correct.append(k.id)
        else:
            result.wrong[k.id] = diff
    matched = set(to_engine.values())
    result.unlabelled = sorted(f.fact_id for f in facts if f.fact_id not in matched)

    for n in key.negative:
        if MILESTONE_ORDER[n.milestone] > upto:
            continue
        hits = [f.fact_id for f in facts if violates(n.forbid, _adapt(f, to_key))]  # type: ignore[arg-type]
        if hits:
            result.violated[n.id] = hits
    return result


def _adapt(f: Fact, to_key: dict[str, str]) -> SimpleNamespace:
    return SimpleNamespace(
        kind=f.kind, subject=f.subject.key, scope=f.scope, value=f.value, status=f.status,
        source=SimpleNamespace(path=f.source.path) if f.source else None,
        inputs=[SimpleNamespace(role=i.role, fact=to_key.get(i.fact_id or "", i.fact_id)) for i in f.inputs],
    )


def _compare(k: LabelledFact, e: Fact, to_engine: dict[str, str], link_map: dict[str, str]) -> tuple[str, Any, Any] | None:
    def m(fid: str | None) -> str | None:
        return to_engine.get(fid, f"<unmatched {fid}>") if fid else None

    checks: list[tuple[str, Any, Any]] = [
        ("value", k.value, e.value),
        ("unit", k.unit, e.unit),
        ("status", k.status, e.status),
        ("absent", k.absent, e.absent),
        ("override_exposure", k.override_exposure, e.override_exposure),
        ("exposure_sources",
         sorted((x.via, x.object or "", tuple(sorted(m(f) or "" for f in x.from_facts))) for x in k.exposure_sources),
         sorted((x.via, x.object or "", tuple(sorted(x.from_fact_ids))) for x in e.exposure_sources)),
        ("source", (k.source.path, k.source.line) if k.source else None, (e.source.path, e.source.line_start) if e.source else None),
        ("inputs",
         sorted((i.role.value, ("loc", i.loc.path, i.loc.line) if i.loc else ("fact", m(i.fact))) for i in k.inputs),  # type: ignore[arg-type]
         sorted((i.role.value, ("loc", i.source.path, i.source.line_start) if i.source else ("fact", i.fact_id)) for i in e.inputs)),
        ("precedence", k.precedence, e.precedence),
        ("link_ids", sorted(link_map.get(x, f"<unmatched {x}>") for x in k.link_ids), sorted(e.link_ids)),
        ("overridden_by", m(k.overridden_by), e.overridden_by),
    ]
    if k.default_ref is not None:
        d, ed = k.default_ref, e.default_ref
        got = None if ed is None or ed.upstream is None else (ed.framework, ed.applied_version, ed.upstream.tag, ed.upstream.path,
                                                              ed.upstream.line, sorted(ed.upstream.verified_at))
        checks.append(("default_ref", (d.framework, d.applied_version, d.tag, d.path, d.line, sorted(d.verified_at)), got))
    elif e.default_ref is not None:
        checks.append(("default_ref", None, e.default_ref.framework))
    checks.append(("unresolved_reason", bool(k.unresolved_reason), bool(e.unresolved_reason)))
    for name, expected, got in checks:
        if expected != got:
            return name, expected, got
    return None
