"""Schemas for .fifthnine.yml (repository config) and .fifthnine-baseline.json, version 1. Frozen at M0, revision 2."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from fifthnine.schema.v1 import Severity


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class WorkloadRef(_Model):
    kind: Literal["Deployment"] = "Deployment"
    namespace: str | None = None
    name: str
    container: str


class ExternalOverrides(_Model):
    """Lets a team state what out-of-repo config sources may change (D2).

    mode 'unknown' (default): findings that depend on env-bindable values of this service carry an open
    external_override_env assumption and are at most CONDITIONAL. 'none': the team declares out-of-repo
    sources do not override application settings; the assumption becomes declared_by_team and is cited.
    'keys': only the listed keys may be overridden; findings depending on them stay CONDITIONAL.
    """

    mode: Literal["unknown", "none", "keys"] = "unknown"
    keys: list[str] = Field(default_factory=list)


class ServiceDecl(_Model):
    name: str
    workload: WorkloadRef
    dockerfile: str | None = None
    source_root: str = "."
    framework: Literal["spring-boot", "fastapi"] | None = None
    external_overrides: ExternalOverrides = Field(default_factory=ExternalOverrides)
    accepted_by: Literal["user", "links-accept"] = "user"
    accepted_on: str | None = Field(default=None, description="ISO date; required for links-accept")
    evidence_digest: str | None = Field(
        default=None,
        description="sha256 over the normalized evidence the link was accepted on (Dockerfile, workload container "
        "image/command/args/ports, build descriptor identity). A changed digest makes the link stale (D3).",
    )
    evidence: list[str] = Field(default_factory=list, description="Human-readable evidence shown at acceptance")

    @model_validator(mode="after")
    def _accept_provenance(self) -> "ServiceDecl":
        if self.accepted_by == "links-accept" and (not self.evidence_digest or not self.accepted_on):
            raise ValueError("entries written by `fifthnine links accept` need evidence_digest and accepted_on")
        return self


class PlatformDecl(_Model):
    """Cluster-side facts the repository cannot show. 'unknown' keeps the matching assumption open."""

    admission_defaults: Literal["unknown", "none"] = Field(
        default="unknown", description="'none': no LimitRange or webhook injects resource requests for these workloads"
    )


class RuleConfig(_Model):
    enable: list[str] = Field(default_factory=list)
    disable: list[str] = Field(default_factory=list)
    paths: dict[str, list[str]] = Field(default_factory=dict, description="rule id -> path globs it is limited to")


class Policy(_Model):
    mode: Literal["report", "gate"] = "report"
    fail_on: Severity = Severity.HIGH


class SuppressionDecl(_Model):
    rule: str
    fingerprint: str | None = None
    path: str | None = None
    reason: str = Field(min_length=10)
    expires: str = Field(description="ISO date, required. Expired entries are reported and re-activated.")
    owner: str | None = None


class FifthNineConfig(_Model):
    version: Literal[1] = 1
    profile: str | None = None
    ignore_paths: list[str] = Field(default_factory=list)
    services: list[ServiceDecl] = Field(default_factory=list)
    platform: PlatformDecl = Field(default_factory=PlatformDecl)
    rules: RuleConfig = Field(default_factory=RuleConfig)
    policy: Policy = Field(default_factory=Policy)
    suppressions: list[SuppressionDecl] = Field(default_factory=list)
    integrations: dict[str, list[str]] = Field(
        default_factory=dict, description="Reserved for SARIF import (E8 stretch). Ignored in v0.1 with a notice."
    )


class BaselineEntry(_Model):
    fingerprint: str
    rule_id: str
    rule_version: str
    subject_key: str
    verdict: Literal["VIOLATION", "CONDITIONAL", "UNKNOWN"]


class Baseline(_Model):
    version: Literal[1] = 1
    tool_version: str
    profile: str | None = None
    entries: list[BaselineEntry]
