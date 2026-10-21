"""Support matrix schema (D9). A version is 'tested' only when listed under tested_versions with evidence."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class Evidence(_Model):
    version: str
    kind: Literal["runtime_fixtures", "source_review", "runtime_spot_check"]
    detail: str
    rules: list[str] = Field(default_factory=list, description="Rules whose semantics this evidence covers")
    recorded: str = Field(description="ISO date")


class ComponentEntry(_Model):
    id: str
    component: str
    approved_range: str = Field(description="CEO-approved range (E5) or the range FifthNine intends to test")
    tested_versions: list[Evidence] = Field(
        default_factory=list, description="Only runtime_fixtures evidence for every dependent rule qualifies a version as tested"
    )
    other_evidence: list[Evidence] = Field(default_factory=list, description="Source review and spot checks; never makes a version tested")
    limits: list[str] = Field(default_factory=list, description="Known applicability limits")


class SupportMatrix(_Model):
    version: Literal[1] = 1
    as_of: str
    tested_definition: str
    components: list[ComponentEntry]

    def tested(self, component: str) -> list[str]:
        return [e.version for c in self.components if c.component == component for e in c.tested_versions]
