"""Kubernetes manifests as located object trees (M2-1).

Reads plain YAML only (Helm templates and Kustomize overlays are not rendered, E3). Uses the hardened composer, so
no object construction, no alias expansion and the M1 document cap apply. Each object keeps its file and the `Y`
tree, so every fact cites the line of the field it came from.
"""

from __future__ import annotations

from dataclasses import dataclass

from fifthnine.core.safeparse import ParseError, compose_yaml_documents
from fifthnine.extract.ynode import Y, to_y

READ_KINDS = frozenset({"Deployment", "Service", "Ingress", "HorizontalPodAutoscaler", "PodDisruptionBudget", "ConfigMap"})
READ_API = {
    "Deployment": {"apps/v1"},
    "Service": {"v1"},
    "Ingress": {"networking.k8s.io/v1"},
    "HorizontalPodAutoscaler": {"autoscaling/v2"},
    "PodDisruptionBudget": {"policy/v1"},
    "ConfigMap": {"v1"},
}


@dataclass(frozen=True)
class K8sDoc:
    api_version: str
    kind: str
    name: str | None
    namespace: str | None  # None when metadata.namespace is not set
    path: str
    root: Y
    name_line: int  # metadata.name line, or the kind line when there is no name

    @property
    def ns(self) -> str:
        return self.namespace if self.namespace else "_"

    @property
    def subject(self) -> str:
        return f"k8s:{self.kind}/{self.ns}/{self.name or '_'}"

    @property
    def read(self) -> bool:
        return self.kind in READ_KINDS and self.api_version in READ_API.get(self.kind, set())


def load_docs(path: str, text: str) -> list[K8sDoc]:
    """All Kubernetes objects in one YAML file. Raises ParseError on malformed YAML, duplicate keys or aliases."""
    out: list[K8sDoc] = []
    for node in compose_yaml_documents(text):
        y = to_y(node)
        if not y.is_map:
            continue
        api, kind = y.scalar("apiVersion"), y.scalar("kind")
        if not isinstance(api, str) or not isinstance(kind, str):
            continue
        meta = y.get("metadata")
        name = meta.scalar("name") if meta is not None else None
        ns = meta.scalar("namespace") if meta is not None else None
        name_node = meta.get("name") if meta is not None else None
        kind_node = y.get("kind")
        line = name_node.key_line if name_node is not None else (kind_node.key_line if kind_node is not None else y.line)
        out.append(K8sDoc(api, kind, str(name) if name is not None else None, str(ns) if ns else None, path, y, line))
    return out


__all__ = ["READ_API", "READ_KINDS", "K8sDoc", "ParseError", "load_docs"]
