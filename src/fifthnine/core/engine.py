"""Scan orchestration: discover, detect, classify support, extract deployment facts (M2-1), report coverage honestly.

Until M3 there are no reliability rules. Every scan therefore reports status INCOMPLETE with a blocking coverage
notice saying so, never a pass (CEO condition C2).
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from pydantic import ValidationError

from fifthnine import __version__
from fifthnine.core import support
from fifthnine.core.detect import K8S_WORKLOAD_KINDS, DetectionResult, detect, detection_facts
from fifthnine.core.safeparse import ParseError, load_yaml_documents, to_plain
from fifthnine.core.workspace import (
    BUILTIN_EXCLUDED_DIRS,
    DEFAULT_MAX_FILE_SIZE,
    KIND_JAVA_SOURCE,
    KIND_KOTLIN_SOURCE,
    KIND_PYTHON_SOURCE,
    IgnorePatternError,
    Workspace,
    discover,
)
from fifthnine.resolve.deployment import resolve_deployment
from fifthnine.schema.config_v1 import FifthNineConfig
from fifthnine.schema.v1 import (
    AssessmentLimits,
    ComponentSupport,
    CoverageNotice,
    Domain,
    Fact,
    Finding,
    Inventory,
    LinkStatus,
    ScanInfo,
    ScanResult,
    ScanStatus,
    Severity,
    Summary,
    SupportLevel,
    ToolInfo,
    Verdict,
    derive_status,
)

RULE_PACK = {"fifthnine-core": "0.0.0 (no rules before M3)"}


class FifthNineError(Exception):
    """User-facing error. The CLI prints the message and exits 2."""


@dataclass(frozen=True)
class ScanOptions:
    path: Path
    profile: str | None = None
    config_path: Path | None = None
    policy_mode: Literal["report", "gate"] | None = None
    fail_on: Severity | None = None
    facts: Literal["referenced", "all", "none"] = "referenced"
    max_file_size: int = DEFAULT_MAX_FILE_SIZE
    output_path: Path | None = None  # report file FifthNine will write; excluded from discovery


def load_config(root: Path, explicit: Path | None) -> tuple[FifthNineConfig | None, str | None]:
    path = explicit if explicit is not None else root / ".fifthnine.yml"
    if not path.exists():
        if explicit is not None:
            raise FifthNineError(f"config file not found: {explicit}")
        return None, None
    if path.is_symlink() or not path.is_file():
        raise FifthNineError(f"config path is not a regular file: {path}")
    if path.stat().st_size > DEFAULT_MAX_FILE_SIZE:
        raise FifthNineError(f"config file larger than {DEFAULT_MAX_FILE_SIZE} bytes: {path}")
    try:
        text = path.read_bytes().decode("utf-8-sig")
    except UnicodeDecodeError:
        raise FifthNineError(f"invalid {path.name}: not valid UTF-8") from None
    except OSError as exc:
        raise FifthNineError(f"cannot read {path}: {exc.strerror}") from None
    try:
        docs = load_yaml_documents(text)
        if len(docs) != 1:
            raise FifthNineError(f"invalid {path.name}: expected exactly one YAML document, found {len(docs)}")
        data = to_plain(docs[0].data)
    except ParseError as exc:
        raise FifthNineError(f"invalid {path.name}: {exc}") from None
    try:
        config = FifthNineConfig.model_validate(data)
    except ValidationError as exc:
        first = exc.errors()[0]
        where = ".".join(str(p) for p in first["loc"]) or "(top level)"
        raise FifthNineError(f"invalid {path.name}: {where}: {first['msg']}") from None
    try:
        shown = path.resolve().relative_to(root).as_posix()
    except ValueError:
        shown = None  # config outside the repository: used, but not part of the inventory
    return config, shown


def _stacks(result: DetectionResult) -> list[ComponentSupport]:
    stacks: dict[tuple[str, str | None, str], ComponentSupport] = {}
    for d in result.detections:
        if support.entry_for(d.component) is None:
            continue
        cs = support.classify(d.component, d.version, d.source)
        stacks[(d.component, d.version, d.source.path if d.source else "")] = cs
    seen_kinds: set[str] = set()
    for obj in sorted(result.k8s_objects, key=lambda o: (o.api_version, o.kind, o.source.path, o.source.line_start)):
        label = f"{obj.api_version}/{obj.kind}"
        read = support.in_range("kubernetes-api", label)
        if label in seen_kinds or (not read and obj.kind not in K8S_WORKLOAD_KINDS):
            continue  # kinds v0.1 neither reads nor treats as workloads are listed in facts and coverage only
        seen_kinds.add(label)
        stacks[("kubernetes-api", label, obj.source.path)] = support.classify("kubernetes-api", label, obj.source)
    return sorted(stacks.values(), key=lambda c: (c.component, c.version or "", c.version_source.path if c.version_source else ""))


def _coverage(ws: Workspace, result: DetectionResult, stacks: list[ComponentSupport]) -> list[CoverageNotice]:
    notes: list[CoverageNotice] = [CoverageNotice(
        code="COV-NO-RULES", scope="repository", blocks_assessment=True,
        message="This build contains no reliability rules (Phase 1 milestone M2: fact extraction). Nothing about the repository's "
                "production readiness was assessed. The scan inventories files, detects supported stacks and versions, and extracts "
                "Docker and Kubernetes facts with provenance (--facts all).",
    )]
    if ws.file_limit_reached:
        notes.append(CoverageNotice(code="COV-LIMIT", scope="repository", blocks_assessment=True,
                                    message=f"Stopped after {len(ws.files)} files (file limit). Files beyond the limit were not inventoried."))
    unsupported = [c for c in stacks if c.support is SupportLevel.UNSUPPORTED]
    for c in unsupported:
        where = f"{c.version_source.path}:{c.version_source.line_start}" if c.version_source else "repository"
        notes.append(CoverageNotice(code="COV-UNSUPPORTED", scope=where,
                                    message=f"{c.component} {c.version} is outside the supported range; rules that depend on it will not run."))
    untested = [c for c in stacks if c.support is SupportLevel.UNTESTED]
    if untested:
        names = ", ".join(sorted({f"{c.component} {c.version or '(version not determined)'}" for c in untested}))
        notes.append(CoverageNotice(code="COV-UNTESTED-VERSION", scope="repository",
                                    message=f"Detected but not tested (data/support_matrix.v1.yaml lists no tested versions yet): {names}. "
                                            "Once rules exist, failing checks on untested versions are reported as CONDITIONAL."))
    for d in [d for d in result.detections if d.version is None] + result.unresolved:
        where = f"{d.source.path}:{d.source.line_start}" if d.source else "repository"
        notes.append(CoverageNotice(code="FN-OPS-002", scope=where, message=f"{d.component}: version not determined ({d.note or d.how})."))
    for c in untested:
        if c.version is not None and support.comparable_version(c.version) is None and c.component != "kubernetes-api":
            where = f"{c.version_source.path}:{c.version_source.line_start}" if c.version_source else "repository"
            notes.append(CoverageNotice(code="FN-OPS-002", scope=where,
                                        message=f"{c.component}: version {c.version!r} is not comparable with the supported range."))
    if ws.ignored_files or ws.ignored_dirs:
        notes.append(CoverageNotice(code="COV-IGNORED", scope="repository",
                                    message=f"{ws.ignored_files} files and {ws.ignored_dirs} directories excluded by .gitignore or "
                                            "ignore_paths; their contents were not inventoried."))
    if ws.invalid_ignore_patterns:
        notes.append(CoverageNotice(code="COV-IGNORED-CONFIG", scope=".gitignore",
                                    message=f"{len(ws.invalid_ignore_patterns)} invalid .gitignore line(s) skipped, as git does."))
    if result.helm_charts or result.helm_templates:
        files = sorted(result.helm_charts + result.helm_templates)
        notes.append(CoverageNotice(code="COV-UNSUPPORTED", scope=", ".join(files[:5]) + (" ..." if len(files) > 5 else ""),
                                    message="Helm charts and templates are not rendered in v0.1 (E3). Run `helm template` and scan the output "
                                            "directory to include these manifests."))
    if result.kustomizations:
        notes.append(CoverageNotice(code="COV-UNSUPPORTED", scope=", ".join(result.kustomizations[:5]),
                                    message="Kustomize overlays are not built in v0.1 (E3). Base YAML files are inventoried as written; "
                                            "run `kustomize build` and scan the output to include patched manifests."))
    unread = sorted({f"{o.api_version}/{o.kind}" for o in result.k8s_objects
                     if support.in_range("kubernetes-api", f"{o.api_version}/{o.kind}") is False and o.kind in K8S_WORKLOAD_KINDS})
    if unread:
        notes.append(CoverageNotice(code="COV-UNSUPPORTED", scope="repository",
                                    message=f"Kubernetes workload kinds not analysed in v0.1: {', '.join(unread)}."))
    sources = {KIND_JAVA_SOURCE: "Java", KIND_KOTLIN_SOURCE: "Kotlin", KIND_PYTHON_SOURCE: "Python"}
    counts = [f"{result.source_counts[k]} {label}" for k, label in sources.items() if result.source_counts.get(k)]
    if counts:
        notes.append(CoverageNotice(code="COV-SOURCE-NOT-ANALYZED", scope="repository",
                                    message=f"Source files inventoried but not parsed in M1: {', '.join(counts)}. "
                                            "Source analysis starts in M2; Kotlin is not supported in v0.1."))
    if result.spring_config_files:
        notes.append(CoverageNotice(code="COV-CONFIG-NOT-RESOLVED", scope="repository",
                                    message=f"{len(result.spring_config_files)} Spring configuration file(s) inventoried; profiles and "
                                            "effective values are resolved starting in M2."))
    if ws.excluded_dirs:
        notes.append(CoverageNotice(code="COV-EXCLUDED", scope="repository",
                                    message=f"{len(ws.excluded_dirs)} directories skipped by built-in exclusions "
                                            f"({', '.join(sorted({p.rsplit('/', 1)[-1] for p in ws.excluded_dirs}))})."))
    notes.append(CoverageNotice(code="FN-OPS-001", domain=Domain.DATA_COMPATIBILITY, scope="repository",
                                message="Data and migration compatibility is not assessed in v0.1.", delegated_to=["squawk", "atlas"]))
    return notes


def run_scan(opts: ScanOptions) -> ScanResult:
    root = opts.path
    if not root.exists():
        raise FifthNineError(f"path does not exist: {root}")
    if not root.is_dir():
        raise FifthNineError(f"path is not a directory: {root}")
    root = root.resolve()
    if not os.access(root, os.R_OK | os.X_OK):
        raise FifthNineError(f"path is not readable: {root}")
    config, config_shown = load_config(root, opts.config_path)

    if opts.profile:
        profile, profile_source = opts.profile, "cli"
    elif config is not None and config.profile:
        profile, profile_source = config.profile, "config"
    else:
        profile, profile_source = None, "none"
    policy_mode = opts.policy_mode or (config.policy.mode if config else "report")
    fail_on = opts.fail_on or (config.policy.fail_on if config else Severity.HIGH)

    exclude: frozenset[str] = frozenset()
    if opts.output_path is not None:
        try:
            exclude = frozenset({opts.output_path.resolve().relative_to(root).as_posix()})
        except ValueError:
            exclude = frozenset()
    try:
        ws = discover(root, ignore_paths=config.ignore_paths if config else None, max_file_size=opts.max_file_size,
                      exclude_files=exclude)
    except IgnorePatternError as exc:
        raise FifthNineError(f"invalid .fifthnine.yml: {exc}") from None
    result = detect(ws)
    stacks = _stacks(result)
    coverage = _coverage(ws, result, stacks)
    deployment = resolve_deployment(ws)
    coverage += deployment.coverage
    if config is not None and config.integrations:
        coverage.append(CoverageNotice(code="COV-IGNORED-CONFIG", scope=config_shown or ".fifthnine.yml",
                                       message="integrations is reserved (SARIF import, E8 stretch) and ignored in v0.1."))

    all_facts: list[Fact] = sorted(detection_facts(result) + deployment.facts.facts(),
                                   key=lambda f: (f.kind, f.subject.key, sorted(f.scope.items()), f.fact_id))
    facts = all_facts if opts.facts == "all" else []  # no findings yet, so no facts are referenced
    linkages = sorted(deployment.linkages, key=lambda lk: lk.link_id)

    inventoried = {f.path for f in ws.files}
    parsed = set(result.parsed) | set(deployment.parsed) | ({config_shown} if config_shown in inventoried else set())
    skipped = sorted(ws.skipped + result.parse_errors, key=lambda s: (s.path, s.reason))
    findings: list[Finding] = []
    status = derive_status(findings, coverage, rules_evaluated=0)
    if status is not ScanStatus.INCOMPLETE:  # invariant until M3: no rules, never a pass
        raise RuntimeError(f"scan produced status {status.value}; expected INCOMPLETE")
    return ScanResult(
        tool=ToolInfo(version=__version__, rule_pack_versions=RULE_PACK),
        scan=ScanInfo(mode="full", profile=profile, profile_source=profile_source, config_file=config_shown,
                      policy_mode=policy_mode, fail_on=fail_on),
        status=status,
        status_reason="No reliability rules are implemented in this build (M2). The repository was inventoried and its "
                      "deployment facts extracted; it was not assessed.",
        inventory=Inventory(files_considered=len(ws.files), files_parsed=len(parsed), skipped=skipped,
                            detected_stacks=stacks),
        linkages=linkages,
        findings=findings,
        coverage=coverage,
        facts=facts,
        summary=Summary(by_verdict={v: 0 for v in Verdict}, by_severity={s: 0 for s in Severity}, blocking=0, new=0, suppressed=0,
                        rules_evaluated=0, rules_not_applicable=0,
                        assessment_limits=AssessmentLimits(
                            linkages_declared=0,
                            linkages_inferred=sum(lk.status is LinkStatus.INFERRED for lk in linkages),
                            linkages_unresolved=sum(lk.status is LinkStatus.UNRESOLVED for lk in linkages),
                            results_unknown_unresolved_value=0, rules_not_assessable=0)),
    )


def exit_code(result: ScanResult) -> int:
    """0: nothing policy-blocking (always in report mode). 1: gate mode and a blocking-eligible finding at or above fail_on."""
    if result.scan.policy_mode != "gate":
        return 0
    order = list(Severity)
    threshold = order.index(result.scan.fail_on)
    blocking = [f for f in result.findings if f.blocking_eligible and f.severity is not None and order.index(f.severity) >= threshold]
    return 1 if blocking else 0


__all__ = ["BUILTIN_EXCLUDED_DIRS", "FifthNineError", "ScanOptions", "exit_code", "load_config", "run_scan"]
