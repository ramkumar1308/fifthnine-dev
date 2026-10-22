"""M2-1: Docker and Kubernetes facts, workload relationships, container environment, effective command, linkage.

Inputs are the discovered workspace files; outputs are facts (schema revision 3), linkage proposals and coverage
notices. Nothing here evaluates a reliability rule, reads a Secret's contents, contacts a cluster or runs code.

Honesty rules applied throughout (M2_PLAN.md, M2-1_PLAN.md):
- a value that is not in the repository is `unresolved` with a reason, or carried as override exposure; never guessed;
- a field known not to be set is `declared` with `absent=True`;
- Kubernetes defaults come only from data/defaults/kubernetes.v1.yaml (verified across 1.34 to 1.37);
- a fact that depends on the container-to-source link carries `link_ids`;
- Docker's default build target (the final stage) is kept distinct from the build target of the deployed image,
  which is not in the repository.
"""

from __future__ import annotations

import ast
import hashlib
import posixpath
import re
from dataclasses import dataclass, field
from typing import Any

from fifthnine.core.detect import is_helm_template
from fifthnine.core.facts import FactBuilder, loc, ref, via
from fifthnine.core.safeparse import ParseError, find_line, load_toml, parse_xml, section_start
from fifthnine.core.workspace import KIND_DOCKERFILE, KIND_MAVEN_POM, KIND_PYPROJECT, KIND_PYTHON_SOURCE, KIND_YAML, Workspace
from fifthnine.extract.dockerfile import DockerfileError, DockerfileModel, parse_dockerfile
from fifthnine.extract.k8s import K8sDoc, load_docs
from fifthnine.extract.ynode import Y
from fifthnine.resolve.defaults import kubernetes_defaults
from fifthnine.schema.v1 import (
    CoverageNotice,
    ExposureSource,
    FactStatus,
    InputRole,
    Linkage,
    LinkStatus,
    OverrideExposure,
)

EXTRACTOR = "deployment-m2.1"
K8S_HINT = re.compile(r"^apiVersion\s*:", re.MULTILINE)
CONTROLLER_ANNOTATION = re.compile(
    r"^(nginx\.ingress\.kubernetes\.io|traefik\.ingress\.kubernetes\.io|alb\.ingress\.kubernetes\.io|haproxy\.org|"
    r"ingress\.kubernetes\.io|konghq\.com|kubernetes\.io/ingress\.class|cert-manager\.io)"
)
MODULE_REF = re.compile(r"^([A-Za-z_][\w]*(?:\.[A-Za-z_][\w]*)*):([A-Za-z_]\w*)$")
SLEEP = re.compile(r"^\s*sleep\s+(\d+)\s*$")

R = InputRole


@dataclass
class BuildIdentity:
    kind: str  # maven | gradle | pyproject
    name: str
    path: str
    line: int
    root: str  # directory of the build file ("" for the repository root)
    fact_id: str


@dataclass
class DeploymentResult:
    facts: FactBuilder
    linkages: list[Linkage] = field(default_factory=list)
    coverage: list[CoverageNotice] = field(default_factory=list)
    parsed: list[str] = field(default_factory=list)
    dockerfiles: dict[str, DockerfileModel] = field(default_factory=dict)
    env_models: dict[str, EnvModel] = field(default_factory=dict)
    dockerfile_for_link: dict[str, str] = field(default_factory=dict)  # link id -> Dockerfile path
    container_links: dict[str, Linkage] = field(default_factory=dict)  # container subject -> its source-root link (when one was proposed)
    container_args: dict[str, list[tuple[str, str, int]]] = field(default_factory=dict)  # container subject -> (token, path, line) of command + args


# ---------------------------------------------------------------- container environment (RULES.md X1 to X5)


@dataclass
class EnvSource:
    order: int
    kind: str  # ConfigMap | Secret
    name: str
    in_repository: bool
    keys: dict[str, tuple[str, str, int]]  # key -> (value, path, line) for in-repository ConfigMaps
    fact_id: str
    prefix: str = ""


@dataclass
class EnvResolution:
    value: str | None
    status: FactStatus
    source: tuple[str, int] | None
    via_fact: str | None
    exposure: list[ExposureSource]
    reason: str | None = None


@dataclass
class EnvModel:
    """Ordered environment of one container. Kubernetes rule: explicit `env` entries win over `envFrom`; among
    `envFrom` sources, later entries win for duplicate keys."""

    container: str
    explicit: dict[str, EnvResolution] = field(default_factory=dict)
    sources: list[EnvSource] = field(default_factory=list)
    duplicates: list[str] = field(default_factory=list)  # repeated `env` names whose earlier entries are ignored

    def unknown_sources(self) -> list[EnvSource]:
        return [s for s in self.sources if not s.in_repository]

    def resolve(self, var: str) -> EnvResolution | None:
        """How `var` reaches the container, or None when nothing in the repository sets it (exposure still applies:
        see `exposure_for_unset`)."""
        if var in self.explicit:
            return self.explicit[var]
        winner: EnvSource | None = None
        for src in self.sources:
            if src.in_repository and (var[len(src.prefix):] if src.prefix and var.startswith(src.prefix) else var if not src.prefix else None) in src.keys:
                winner = src
        later_unknown = [s for s in self.unknown_sources() if winner is None or s.order > winner.order]
        exposure = [ExposureSource(via="envFrom", object=f"k8s:{s.kind}/{_ns_of(self.container)}/{s.name}", variables=[var]) for s in later_unknown]
        if winner is None:
            return None
        key = var[len(winner.prefix):] if winner.prefix else var
        value, path, line = winner.keys[key]
        return EnvResolution(value, FactStatus.DECLARED, (path, line), winner.fact_id, exposure)

    def exposure_for(self, var: str) -> list[ExposureSource]:
        """Out-of-repository sources that could set `var` (none when an explicit env entry sets it)."""
        if var in self.explicit:
            return []
        res = self.resolve(var)
        if res is not None:
            return res.exposure
        return [ExposureSource(via="envFrom", object=f"k8s:{s.kind}/{_ns_of(self.container)}/{s.name}", variables=[var]) for s in self.unknown_sources()]


def _ns_of(container_subject: str) -> str:
    return container_subject.split("/")[1] if container_subject.count("/") >= 2 else "_"


# ---------------------------------------------------------------- build identities


def _build_identities(ws: Workspace, fb: FactBuilder, out: DeploymentResult) -> list[BuildIdentity]:
    ids: list[BuildIdentity] = []
    for entry in ws.files:
        name = posixpath.basename(entry.path)
        root = posixpath.dirname(entry.path)
        try:
            if entry.kind == KIND_MAVEN_POM:
                pom = parse_xml(ws.read_text(entry))
                art = pom.child("artifactId")
                if art is None or not art.text.strip():
                    continue
                group = pom.child_text("groupId") or (pom.child("parent").child_text("groupId") if pom.child("parent") is not None else None)  # type: ignore[union-attr]
                fid = fb.add("build.identity", f"maven:{entry.path}", {"group": group, "artifact": art.text.strip()}, FactStatus.DECLARED,
                             source=ref(entry.path, art.line))
                ids.append(BuildIdentity("maven", art.text.strip(), entry.path, art.line, root, fid))
            elif name in ("settings.gradle", "settings.gradle.kts"):
                text = ws.read_text(entry)
                for number, text_line in enumerate(text.splitlines(), 1):
                    m = re.match(r"""^\s*rootProject\.name\s*=\s*["']([^"']+)["']""", text_line)
                    if m:
                        fid = fb.add("build.identity", f"gradle:{entry.path}", {"name": m.group(1)}, FactStatus.DECLARED, source=ref(entry.path, number))
                        ids.append(BuildIdentity("gradle", m.group(1), entry.path, number, root, fid))
                        break
            elif entry.kind == KIND_PYPROJECT:
                text = ws.read_text(entry)
                data = load_toml(text)
                for section, table in (("[project]", data.get("project")), ("[tool.poetry]", (data.get("tool") or {}).get("poetry"))):
                    if isinstance(table, dict) and isinstance(table.get("name"), str):
                        start = section_start(text, section) or 1
                        line = find_line(text, "name", start) or start
                        fid = fb.add("build.identity", f"pyproject:{entry.path}", {"name": table["name"]}, FactStatus.DECLARED, source=ref(entry.path, line))
                        ids.append(BuildIdentity("pyproject", table["name"], entry.path, line, root, fid))
                        break
        except (OSError, ParseError):
            continue  # already reported by stack detection
    return ids


# ---------------------------------------------------------------- Dockerfiles


def _docker_facts(path: str, model: DockerfileModel, fb: FactBuilder) -> dict[str, str]:
    """Emit Dockerfile facts. Returns ids of the default-target facts used later."""
    ids: dict[str, str] = {}
    base = f"docker:{path}"
    for st in model.stages:
        s = f"{base}#{st.index}"
        status = FactStatus.DECLARED if st.base.unresolved is None else FactStatus.UNRESOLVED
        fb.add("docker.stage.base_image", s, st.base.value if status is FactStatus.DECLARED else None, status,
               source=ref(path, st.from_line), unresolved_reason=st.base.unresolved, display=f"{path} stage {st.index}")
        if st.workdir is None:
            fb.add("docker.workdir", s, None, FactStatus.UNRESOLVED, inputs=[loc(R.ABSENCE, path, st.from_line)],
                   unresolved_reason="WORKDIR is not set in this stage, so it comes from the base image, which is not in the repository")
        elif st.workdir.unresolved:
            fb.add("docker.workdir", s, None, FactStatus.UNRESOLVED, source=ref(path, st.workdir.line), unresolved_reason=st.workdir.unresolved)
        else:
            fb.add("docker.workdir", s, st.workdir.value, FactStatus.DECLARED, source=ref(path, st.workdir.line))
        if st.expose:
            unresolved = [e for e in st.expose if e.unresolved]
            lines = sorted({e.line for e in st.expose})
            if unresolved:
                fb.add("docker.expose", s, None, FactStatus.UNRESOLVED, source=ref(path, lines[0]), unresolved_reason=unresolved[0].unresolved)
            else:
                ports: list[int | str] = []
                for e in st.expose:
                    pair = e.value
                    assert isinstance(pair, tuple)
                    num, proto = pair
                    ports.append(num if proto == "tcp" else f"{num}/{proto}")
                fb.add("docker.expose", s, ports, FactStatus.DECLARED, source=ref(path, lines[0]),
                       inputs=[loc(R.VALUE_SOURCE, path, n) for n in lines[1:]])
        for label, state, val in (("entrypoint", st.entrypoint_state, st.entrypoint), ("cmd", st.cmd_state, st.cmd)):
            if state == "set" and val is not None:
                fid = fb.add(f"docker.{label}", s, {"form": val[0], "argv": val[1]}, FactStatus.DECLARED, source=ref(path, val[2]))
            elif state == "empty" and st.cmd_cleared_at is not None:
                fid = fb.add("docker.cmd", s, {"form": "cleared", "argv": []}, FactStatus.DECLARED, source=ref(path, st.cmd_cleared_at))
            else:
                fid = fb.add(f"docker.{label}", s, None, FactStatus.UNRESOLVED, inputs=[loc(R.ABSENCE, path, st.from_line)],
                             unresolved_reason=f"{label.upper()} is not set in the Dockerfile chain, so it comes from the base image, "
                                               "which is not in the repository")
            if st is model.default_target:
                ids[label] = fid
        for name, v in sorted(st.env.items()):
            if v.unresolved:
                fb.add("docker.env", f"{s}#{name}", None, FactStatus.UNRESOLVED, source=ref(path, v.line), unresolved_reason=v.unresolved)
            else:
                fb.add("docker.env", f"{s}#{name}", v.value, FactStatus.DECLARED, source=ref(path, v.line))
        dest_count: dict[str, int] = {}
        for frm, sources, dest, line in st.copies:
            if frm is not None:
                # several COPY --from to the same destination stay distinct facts: the n-th gets a "~n" suffix
                dest_count[dest] = dest_count.get(dest, 0) + 1
                suffix = "" if dest_count[dest] == 1 else f"~{dest_count[dest]}"
                fb.add("docker.copy_from_stage", f"{s}>{dest}{suffix}",
                       {"from_stage": frm, "src": sources[0] if len(sources) == 1 else sources, "dest": dest},
                       FactStatus.DECLARED, source=ref(path, line))
        if st.user is not None:
            fb.add("docker.user", s, st.user.value if not st.user.unresolved else None,
                   FactStatus.DECLARED if not st.user.unresolved else FactStatus.UNRESOLVED, source=ref(path, st.user.line),
                   unresolved_reason=st.user.unresolved)
    target = model.default_target
    if target is not None:
        ids["default_target"] = fb.add(
            "docker.default_target", base, {"stage_index": target.index, "stage_name": target.name}, FactStatus.DECLARED,
            source=ref(path, target.from_line),
            display=f"{path} default build target")
        ids["build_target"] = fb.add(
            "docker.build_target", base, None, FactStatus.UNRESOLVED, inputs=[via(R.REFERENCE, ids["default_target"])],
            unresolved_reason=("The build target used to produce the deployed image is not read from the repository (a CI --target "
                               "flag, Compose file, workflow or registry configuration is not interpreted). Docker builds the final "
                               "stage when no --target is given; that is the default target, not a verified production build."))
    return ids


# ---------------------------------------------------------------- Kubernetes helpers


def _num_or_str(v: Any) -> Any:
    return v if isinstance(v, int) and not isinstance(v, bool) else (str(v) if v is not None else None)


def _int_fact(fb: FactBuilder, kind: str, subject: str, node: Y, path: str, unit: str | None = None) -> None:
    """A field that must be an integer: a template or other non-integer text is unresolved, never a declared value."""
    number = _port_number(node.value)
    if number is None:
        fb.add(kind, subject, None, FactStatus.UNRESOLVED, source=ref(path, node.key_line),
               unresolved_reason=f"{node.value!r} is not an integer (a placeholder filled in at deploy time?)")
    else:
        fb.add(kind, subject, number, FactStatus.DECLARED, unit=unit, source=ref(path, node.key_line))


def _labels(y: Y | None) -> dict[str, tuple[str, int]]:
    if y is None or not y.is_map:
        return {}
    return {k: (str(v.value), v.key_line) for k, v in y.value.items() if not v.is_map and not v.is_seq}


def _match(selector: dict[str, tuple[str, int]], labels: dict[str, tuple[str, int]]) -> bool:
    return bool(selector) and all(k in labels and labels[k][0] == v for k, (v, _) in selector.items())


def _seq(y: Y | None) -> list[Y]:
    return y.seq() if y is not None else []


def _argv(y: Y | None) -> list[str] | None:
    if y is None or not y.is_seq:
        return None
    items = [str(i.value) for i in y.seq()]
    return items or None  # Kubernetes treats an empty list as not set


@dataclass
class _Workload:
    doc: K8sDoc
    labels: dict[str, tuple[str, int]]
    containers: list[tuple[str, Y]]  # (container subject, node)
    port_facts: dict[str, dict[str, tuple[int, str]]]  # container subject -> port name -> (number, fact id)


def _k8s_default(fb: FactBuilder, kind: str, subject: str, key: str, absence: tuple[str, int], extra: list[Any] | None = None) -> tuple[str, Any]:
    entry = kubernetes_defaults()[key]
    fid = fb.add(kind, subject, entry.value, FactStatus.DEFAULT, unit=entry.unit, default_ref=entry.ref,
                 inputs=[loc(R.ABSENCE, absence[0], absence[1]), *(extra or [])])
    return fid, entry.value


# ---------------------------------------------------------------- main entry


def resolve_deployment(ws: Workspace) -> DeploymentResult:
    fb = FactBuilder(EXTRACTOR)
    out = DeploymentResult(facts=fb)
    identities = _build_identities(ws, fb, out)

    # Dockerfiles
    docker_ids: dict[str, dict[str, str]] = {}
    for entry in ws.files:
        if entry.kind != KIND_DOCKERFILE:
            continue
        try:
            model = parse_dockerfile(entry.path, ws.read_text(entry))
        except (OSError, DockerfileError) as exc:
            out.coverage.append(CoverageNotice(code="FN-OPS-002", scope=entry.path, message=f"Dockerfile not read: {exc}"))
            continue
        out.dockerfiles[entry.path] = model
        out.parsed.append(entry.path)
        docker_ids[entry.path] = _docker_facts(entry.path, model, fb)
        notes = model.unsupported + [u for st in model.stages for u in st.unsupported]
        for line, why in sorted(notes):
            out.coverage.append(CoverageNotice(code="COV-UNSUPPORTED", scope=f"{entry.path}:{line}", message=f"{why}; values it would set are not known."))

    # Kubernetes documents
    docs: list[K8sDoc] = []
    for entry in ws.files:
        if entry.kind != KIND_YAML:
            continue
        try:
            text = ws.read_text(entry)
        except OSError:
            continue
        if is_helm_template(entry.path, text) or not K8S_HINT.search(text):  # same test as stack detection
            continue
        try:
            docs.extend(load_docs(entry.path, text))
        except ParseError:
            continue  # stack detection already records the file as a parse error
    docs.sort(key=lambda d: (d.path, d.name_line))
    seen_subjects: dict[str, str] = {}
    read_docs: list[K8sDoc] = []
    for d in docs:
        if not d.read:
            continue
        if d.subject in seen_subjects:
            out.coverage.append(CoverageNotice(code="COV-DUPLICATE-OBJECT", scope=f"{d.path}:{d.name_line}",
                                               message=f"{d.subject} is also defined in {seen_subjects[d.subject]}; only the first definition "
                                                       "(by path) is read. Overlays or environment copies are not merged (E3)."))
            continue
        seen_subjects[d.subject] = d.path
        read_docs.append(d)

    for d in read_docs:
        meta = d.root.get("metadata")
        ns_node = meta.get("namespace") if meta is not None else None
        if d.namespace:
            fb.add("k8s.object.namespace", d.subject, d.namespace, FactStatus.DECLARED, source=ref(d.path, ns_node.key_line if ns_node else d.name_line))
        else:
            fb.add("k8s.object.namespace", d.subject, None, FactStatus.UNRESOLVED, inputs=[loc(R.ABSENCE, d.path, d.name_line)],
                   unresolved_reason="metadata.namespace is not set; the namespace is chosen when the manifest is applied.")

    configmaps: dict[tuple[str, str], tuple[K8sDoc, dict[str, tuple[str, str, int]]]] = {}
    for d in read_docs:
        if d.kind != "ConfigMap" or d.name is None:
            continue
        keys: dict[str, tuple[str, str, int]] = {}
        data = d.root.get("data")
        if data is not None and data.is_map:
            for k, v in data.value.items():
                keys[k] = (str(v.value), d.path, v.key_line)
                fb.add("k8s.configmap.entry", f"{d.subject}#{k}", str(v.value), FactStatus.DECLARED, source=ref(d.path, v.key_line))
        configmaps[(d.ns, d.name)] = (d, keys)

    workloads = [_deployment(d, fb, configmaps, out) for d in read_docs if d.kind == "Deployment"]
    services: dict[tuple[str, str | None], _ServiceInfo] = {(d.ns, d.name): _service(d, fb, workloads, out) for d in read_docs
                                                             if d.kind == "Service" and d.name}
    for d in read_docs:
        if d.kind == "Ingress":
            _ingress(d, fb, services, out)
        elif d.kind == "HorizontalPodAutoscaler":
            _hpa(d, fb, workloads, out)
        elif d.kind == "PodDisruptionBudget":
            _pdb(d, fb, workloads, out)

    # linkage, then effective command and config mounts that depend on it
    proposed: list[tuple[_Workload, str, Y, Linkage | None]] = []
    for w in workloads:
        for csub, c in w.containers:
            proposed.append((w, csub, c, _linkage(ws, w, csub, c, identities, out, fb)))
    proposed = _single_image_per_module(proposed, out)
    for w, csub, c, link in proposed:
        if link is not None and link.status is not LinkStatus.UNRESOLVED:
            out.container_links[csub] = link
        tokens: list[tuple[str, str, int]] = []
        for field_name in ("command", "args"):
            node = c.get(field_name)
            if node is not None and node.is_seq:
                tokens += [(str(i.value), w.doc.path, i.line) for i in node.seq()]
        out.container_args[csub] = tokens
        _effective_command(w, csub, c, link, out, docker_ids, fb)
        _mounts(w, csub, c, link, out, docker_ids, fb)
    return out


# ---------------------------------------------------------------- Deployment


def _deployment(d: K8sDoc, fb: FactBuilder, configmaps: dict[tuple[str, str], tuple[K8sDoc, dict[str, tuple[str, str, int]]]],
                out: DeploymentResult) -> _Workload:
    spec = d.root.get("spec")
    p = d.path
    absence = (p, d.name_line)
    replicas = spec.get("replicas") if spec else None
    if replicas is not None:
        _int_fact(fb, "k8s.deployment.replicas", d.subject, replicas, p)
    else:
        fb.add("k8s.deployment.replicas", d.subject, None, FactStatus.DECLARED, absent=True, source=ref(p, d.name_line))
    strategy = spec.get("strategy") if spec else None
    stype = strategy.get("type") if strategy else None
    if stype is not None:
        st_id = fb.add("k8s.deployment.strategy_type", d.subject, str(stype.value), FactStatus.DECLARED, source=ref(p, stype.key_line))
        st_val = str(stype.value)
    else:
        st_id, st_val = _k8s_default(fb, "k8s.deployment.strategy_type", d.subject, "deployment.strategy_type", absence)
    if st_val == "RollingUpdate":
        ru = strategy.get("rollingUpdate") if strategy else None
        for fld, kind, key in (("maxUnavailable", "k8s.deployment.max_unavailable", "deployment.max_unavailable"),
                               ("maxSurge", "k8s.deployment.max_surge", "deployment.max_surge")):
            node = ru.get(fld) if ru else None
            if node is not None:
                fb.add(kind, d.subject, _num_or_str(node.value), FactStatus.DECLARED, source=ref(p, node.key_line))
            else:
                _k8s_default(fb, kind, d.subject, key, absence, [via(R.REFERENCE, st_id)])
    template = spec.get("template") if spec else None
    pod = template.get("spec") if template else None
    labels = _labels(template.path("metadata", "labels") if template else None)
    grace = pod.get("terminationGracePeriodSeconds") if pod else None
    if grace is not None:
        _int_fact(fb, "k8s.pod.termination_grace_period", d.subject, grace, p, unit="s")
    else:
        _k8s_default(fb, "k8s.pod.termination_grace_period", d.subject, "pod.termination_grace_period", absence)
    w = _Workload(d, labels, [], {})
    containers = pod.get("containers") if pod else None
    for c in _seq(containers):
        cname = c.scalar("name")
        if cname is None:
            continue
        csub = f"{d.subject}/{cname}"
        name_line = c.get("name").key_line  # type: ignore[union-attr]
        w.containers.append((csub, c))
        image = c.get("image")
        if image is not None:
            fb.add("k8s.container.image", csub, str(image.value), FactStatus.DECLARED, source=ref(p, image.key_line))
        ports: dict[str, tuple[int, str]] = {}
        for port in _seq(c.get("ports")):
            cp = port.get("containerPort")
            if cp is None:
                continue
            pname = port.scalar("name")
            key = str(pname) if pname else str(cp.value)
            number = _port_number(cp.value)
            if number is None:  # a template or other non-numeric value; the port is not known
                fb.add("k8s.container.port", f"{csub}#{key}", None, FactStatus.UNRESOLVED, source=ref(p, cp.key_line),
                       unresolved_reason=f"containerPort {cp.value!r} is not a port number (a placeholder filled in at deploy time?)")
                continue
            fid = fb.add("k8s.container.port", f"{csub}#{key}", number, FactStatus.DECLARED, source=ref(p, cp.key_line))
            if pname:
                ports[str(pname)] = (number, fid)
            ports[str(number)] = (number, fid)
        w.port_facts[csub] = ports
        env = _container_env(d, csub, c, fb, configmaps)
        for dup in env.duplicates:
            out.coverage.append(CoverageNotice(code="FN-OPS-002", scope=csub, message=f"env variable {dup} is repeated; the last entry applies "
                                                                                     "and the earlier one is ignored."))
        out.env_models[csub] = env
        _resources(csub, c, p, name_line, fb)
        for probe in ("readiness", "liveness", "startup"):
            _probe(probe, csub, c, p, name_line, ports, fb)
        _prestop(csub, c, p, name_line, fb)
    return w


def _container_env(d: K8sDoc, csub: str, c: Y, fb: FactBuilder,
                   configmaps: dict[tuple[str, str], tuple[K8sDoc, dict[str, tuple[str, str, int]]]]) -> EnvModel:
    p = d.path
    model = EnvModel(csub)
    for order, item in enumerate(_seq(c.get("envFrom"))):
        prefix = str(item.scalar("prefix") or "")
        for ref_key, kind in (("configMapRef", "ConfigMap"), ("secretRef", "Secret")):
            node = item.get(ref_key)
            if node is None:
                continue
            name_node = node.get("name")
            name = str(name_node.value) if name_node is not None else "?"
            cm = configmaps.get((d.ns, name)) if kind == "ConfigMap" else None
            in_repo = cm is not None
            value: dict[str, Any] = {"kind": kind, "name": name, "in_repository": in_repo, "order": order}
            if prefix:
                value["prefix"] = prefix
            inputs = [loc(R.REFERENCE, cm[0].path, cm[0].name_line)] if cm else []
            fid = fb.add("k8s.container.env_from", f"{csub}#{kind.lower()}:{name}", value, FactStatus.DECLARED,
                         source=ref(p, name_node.key_line if name_node else node.key_line), inputs=inputs)
            model.sources.append(EnvSource(order, kind, name, in_repo, dict(cm[1]) if cm else {}, fid, prefix))
    env_items = _seq(c.get("env"))
    last_index = {str(i.scalar("name")): n for n, i in enumerate(env_items) if i.get("name") is not None}
    for n, item in enumerate(env_items):
        name_node = item.get("name")
        if name_node is None:
            continue
        var = str(name_node.value)
        if last_index[var] != n:  # Kubernetes applies the last entry for a repeated name
            model.duplicates.append(f"{var} (line {name_node.key_line})")
            continue
        attach = loc(R.ENV_ATTACHMENT, p, name_node.key_line)
        value_node, from_node = item.get("value"), item.get("valueFrom")
        if from_node is not None:
            cmk = from_node.get("configMapKeyRef")
            cm = configmaps.get((d.ns, str(cmk.scalar("name")))) if cmk is not None else None
            key = str(cmk.scalar("key")) if cmk is not None else None
            if cm is not None and key in cm[1]:
                val, cpath, cline = cm[1][key]
                fid = fb.add("k8s.container.env", f"{csub}#{var}", val, FactStatus.DECLARED, source=ref(cpath, cline),
                             inputs=[attach, loc(R.REFERENCE, p, from_node.key_line)], precedence="k8s.env")
                model.explicit[var] = EnvResolution(val, FactStatus.DECLARED, (cpath, cline), fid, [])
            else:
                kind = next(iter(from_node.value)) if from_node.is_map and from_node.value else "valueFrom"
                reason = ("set by the kubelet at runtime" if kind in ("fieldRef", "resourceFieldRef")
                          else f"{kind} refers to an object or key that is not in the repository (RULES.md X4)")
                fid = fb.add("k8s.container.env", f"{csub}#{var}", None, FactStatus.UNRESOLVED, source=ref(p, from_node.key_line),
                             inputs=[attach], unresolved_reason=reason)
                model.explicit[var] = EnvResolution(None, FactStatus.UNRESOLVED, (p, from_node.key_line), fid, [], reason)
        else:
            val = "" if value_node is None else str(value_node.value)
            line = value_node.key_line if value_node is not None else name_node.key_line
            fid = fb.add("k8s.container.env", f"{csub}#{var}", val, FactStatus.DECLARED, source=ref(p, line), inputs=[attach])
            model.explicit[var] = EnvResolution(val, FactStatus.DECLARED, (p, line), fid, [])
    # keys supplied by in-repository ConfigMaps through envFrom (X2), with exposure from later unknown sources
    for src in model.sources:
        if not src.in_repository:
            continue
        for key in src.keys:
            var = f"{src.prefix}{key}"
            if var in model.explicit:
                continue
            res = model.resolve(var)
            if res is None or res.via_fact != src.fact_id or res.source is None:
                continue
            exposure = OverrideExposure.ENV_POSSIBLE if res.exposure else OverrideExposure.NONE
            fb.add("k8s.container.env", f"{csub}#{var}", res.value, FactStatus.DECLARED, source=ref(*res.source),
                   inputs=[via(R.ENV_ATTACHMENT, src.fact_id)], precedence="k8s.env_from", exposure=exposure, exposure_sources=res.exposure)
    return model


def _resources(csub: str, c: Y, p: str, name_line: int, fb: FactBuilder) -> None:
    res = c.get("resources")
    for group in ("requests", "limits"):
        g = res.get(group) if res else None
        for rname in ("cpu", "memory"):
            node = g.get(rname) if g else None
            kind = f"k8s.container.resources.{group}.{rname}"
            if node is not None:
                fb.add(kind, csub, str(node.value), FactStatus.DECLARED, source=ref(p, node.key_line))
                continue
            limit = res.path("limits", rname) if res else None
            if group == "requests" and limit is not None:
                entry = kubernetes_defaults()["container.request_from_limit"]
                fb.add(kind, csub, str(limit.value), FactStatus.DEFAULT, default_ref=entry.ref,
                       inputs=[loc(R.ABSENCE, p, g.key_line if g else (res.key_line if res else name_line)), loc(R.VALUE_SOURCE, p, limit.key_line)])
                continue
            where = g.key_line if g is not None else (res.key_line if res is not None else name_line)
            fb.add(kind, csub, None, FactStatus.DECLARED, absent=True, source=ref(p, where))


def _probe(probe: str, csub: str, c: Y, p: str, name_line: int, ports: dict[str, tuple[int, str]], fb: FactBuilder) -> None:
    node = c.get(f"{probe}Probe")
    kind = f"k8s.container.probe.{probe}"
    if node is None:
        if probe != "startup":
            fb.add(kind, csub, None, FactStatus.DECLARED, absent=True, source=ref(p, name_line))
        return
    for ptype in ("httpGet", "tcpSocket", "grpc", "exec"):
        h = node.get(ptype)
        if h is None:
            continue
        if ptype == "exec":
            cmd = h.get("command")
            fb.add(kind, csub, {"type": "exec", "command": _argv(cmd)}, FactStatus.DECLARED, source=ref(p, (cmd or h).key_line))
            return
        port_node = h.get("port")
        inputs: list[Any] = []
        port_val: Any = None
        status, reason = FactStatus.DECLARED, None
        if port_node is not None:
            raw = port_node.value
            if isinstance(raw, int) and not isinstance(raw, bool):
                port_val = raw
                inputs.append(loc(R.VALUE_SOURCE, p, port_node.key_line))
            elif str(raw) in ports:
                port_val = ports[str(raw)][0]
                inputs += [loc(R.REFERENCE, p, port_node.key_line), via(R.VALUE_SOURCE, ports[str(raw)][1])]
            else:
                status, reason = FactStatus.UNRESOLVED, f"named port {raw!r} is not declared on this container"
        value: dict[str, Any] = {"type": ptype, "port": port_val}
        source_node = port_node
        if ptype == "httpGet":
            path_node = h.get("path")
            if path_node is None:  # the API server defaults an unset path to "/" (cited, verified default)
                did, default_path = _k8s_default(fb, "k8s.container.probe_path", f"{csub}#{probe}", "probe.httpget_path", (p, h.key_line))
                inputs.append(via(R.VALUE_SOURCE, did))
            value = {"type": ptype, "path": str(path_node.value) if path_node else default_path, "port": port_val}
            source_node = path_node or port_node
        line = source_node.key_line if source_node is not None else h.key_line
        if status is FactStatus.UNRESOLVED:
            fb.add(kind, csub, None, status, source=ref(p, line), inputs=inputs, unresolved_reason=reason)
        else:
            fb.add(kind, csub, value, status, source=ref(p, line), inputs=inputs)
        return


def _prestop(csub: str, c: Y, p: str, name_line: int, fb: FactBuilder) -> None:
    pre = c.path("lifecycle", "preStop")
    kind = "k8s.container.lifecycle.prestop"
    if pre is None:
        fb.add(kind, csub, None, FactStatus.DECLARED, absent=True, source=ref(p, name_line))
        return
    if pre.get("exec") is not None:
        cmd_node = pre.path("exec", "command")
        argv = _argv(cmd_node) or []
        fid = fb.add(kind, csub, {"type": "exec", "command": argv}, FactStatus.DECLARED, source=ref(p, (cmd_node or pre).key_line))
        # Only the exact form `sh -c "sleep N"` is parsed (RULES.md FN-DEP-003 S3); other strings stay unparsed (S8, S9)
        if len(argv) == 3 and argv[0] in ("sh", "/bin/sh", "bash", "/bin/bash") and argv[1] == "-c":
            m = SLEEP.match(argv[2])
            if m:
                fb.add("k8s.container.lifecycle.prestop_sleep", csub, int(m.group(1)), FactStatus.DECLARED, unit="s",
                       source=ref(p, (cmd_node or pre).key_line), inputs=[via(R.VALUE_SOURCE, fid)])
    elif pre.get("sleep") is not None:
        sec = pre.path("sleep", "seconds")
        fid = fb.add(kind, csub, {"type": "sleep", "seconds": _num_or_str(sec.value) if sec else None}, FactStatus.DECLARED,
                     source=ref(p, (sec or pre).key_line))
        if sec is not None and isinstance(sec.value, int):
            fb.add("k8s.container.lifecycle.prestop_sleep", csub, sec.value, FactStatus.DECLARED, unit="s", source=ref(p, sec.key_line),
                   inputs=[via(R.VALUE_SOURCE, fid)])
    elif pre.get("httpGet") is not None:
        h = pre.get("httpGet")
        assert h is not None
        fb.add(kind, csub, {"type": "httpGet", "path": h.scalar("path"), "port": _num_or_str(h.scalar("port"))}, FactStatus.DECLARED,
               source=ref(p, h.key_line))


# ---------------------------------------------------------------- Service, Ingress, HPA, PDB


@dataclass
class _ServiceInfo:
    doc: K8sDoc
    ports: dict[str, tuple[int, str]]  # name or number -> (service port, fact id)


def _same_ns(a: K8sDoc, b: K8sDoc) -> bool | None:
    """True when both are in the same namespace, False when both are set and differ, None when one is unset and
    the other set (the namespace is decided at apply time, so the relationship is not established)."""
    if a.namespace == b.namespace:
        return True
    if a.namespace and b.namespace:
        return False
    return None


def _port_number(v: Any) -> int | None:
    """A port as an integer, or None for a template, a name or anything else that is not a plain number."""
    if isinstance(v, bool):
        return None
    if isinstance(v, int):
        return v
    if isinstance(v, str) and v.strip().isdigit():
        return int(v.strip())
    return None


def _service(d: K8sDoc, fb: FactBuilder, workloads: list[_Workload], out: DeploymentResult) -> _ServiceInfo:
    spec = d.root.get("spec")
    p = d.path
    t = spec.get("type") if spec else None
    inputs: list[Any] = []
    if t is not None:
        stype = str(t.value)
        inputs.append(loc(R.VALUE_SOURCE, p, t.key_line))
    else:
        tid, stype = _k8s_default(fb, "k8s.service.type", d.subject, "service.type", (p, d.name_line))
        inputs.append(via(R.VALUE_SOURCE, tid))
    selector = _labels(spec.get("selector") if spec else None)
    matched: list[_Workload] = []
    ambiguous: list[str] = []
    for w in workloads:
        if not _match(selector, w.labels):
            continue
        same = _same_ns(d, w.doc)
        if same:
            matched.append(w)
        elif same is None:
            ambiguous.append(w.doc.subject)
    for w in matched:
        for k in selector:
            inputs.append(loc(R.REFERENCE, w.doc.path, w.labels[k][1]))
    first = min((line for _, line in selector.values()), default=spec.key_line if spec else d.name_line)
    if ambiguous:
        fb.add("k8s.service.selects", d.subject, None, FactStatus.UNRESOLVED, source=ref(p, first),
               unresolved_reason=f"selector matches {', '.join(ambiguous)}, but one side sets a namespace and the other does not")
    else:
        fb.add("k8s.service.selects", d.subject, {"type": stype, "workloads": sorted(w.doc.subject for w in matched)}, FactStatus.DECLARED,
               source=ref(p, first), inputs=inputs)
    info = _ServiceInfo(d, {})
    for port in _seq(spec.get("ports") if spec else None):
        pnode = port.get("port")
        if pnode is None:
            continue
        pname = port.scalar("name")
        key = str(pname) if pname else str(pnode.value)
        tnode = port.get("targetPort")
        pin: list[Any] = []
        if tnode is not None:
            target: Any = _num_or_str(tnode.value)
            pin.append(loc(R.REFERENCE, p, tnode.key_line))
        else:
            tpid, _ = _k8s_default(fb, "k8s.service.target_port", f"{d.subject}#{key}", "service.target_port", (p, pnode.key_line))
            target = _num_or_str(pnode.value)
            pin.append(via(R.VALUE_SOURCE, tpid))
        container_port: Any = None
        status, reason = FactStatus.DECLARED, None
        if ambiguous:
            status = FactStatus.UNRESOLVED
            reason = f"selector matches {', '.join(ambiguous)}, but one side sets a namespace and the other does not"
        elif isinstance(target, int):
            container_port = target
        else:
            numbers: set[int] = set()
            for w in matched:
                for csub, _ in w.containers:
                    hit = w.port_facts.get(csub, {}).get(str(target))
                    if hit:
                        numbers.add(hit[0])
                        pin.append(via(R.VALUE_SOURCE, hit[1]))
            if len(numbers) == 1:
                container_port = numbers.pop()
            elif not matched:
                status, reason = FactStatus.UNRESOLVED, f"targetPort {target!r} is named, and no selected workload is in the repository"
            else:
                status = FactStatus.UNRESOLVED
                reason = f"targetPort {target!r} resolves to {sorted(numbers) or 'no'} container port(s) on the selected workloads"
        svc_port = _port_number(pnode.value)
        if svc_port is None:
            status, reason = FactStatus.UNRESOLVED, f"port {pnode.value!r} is not a port number (a placeholder filled in at deploy time?)"
        value = {"port": svc_port, "target_port": target, "container_port": container_port}
        if status is FactStatus.UNRESOLVED:
            fid = fb.add("k8s.service.port", f"{d.subject}#{key}", None, status, source=ref(p, pnode.key_line), inputs=pin, unresolved_reason=reason)
        else:
            fid = fb.add("k8s.service.port", f"{d.subject}#{key}", value, status, source=ref(p, pnode.key_line), inputs=pin)
        if svc_port is not None:
            info.ports[key] = (svc_port, fid)
            info.ports[str(svc_port)] = (svc_port, fid)
    return info


def _ingress(d: K8sDoc, fb: FactBuilder, services: dict[tuple[str, str | None], _ServiceInfo], out: DeploymentResult) -> None:
    p = d.path
    ann = d.root.path("metadata", "annotations")
    if ann is not None and ann.is_map:
        specific = sorted(k for k in ann.value if CONTROLLER_ANNOTATION.match(k))
        if specific:
            fb.add("k8s.ingress.controller_annotations", d.subject, specific, FactStatus.DECLARED, source=ref(p, ann.key_line))
    for rule in _seq(d.root.path("spec", "rules")):
        host_node = rule.get("host")
        host = str(host_node.value) if host_node is not None else "*"
        for path_item in _seq(rule.path("http", "paths")):
            path_node = path_item.get("path")
            path = str(path_node.value) if path_node is not None else "/"
            svc = path_item.path("backend", "service")
            name_node = svc.get("name") if svc else None
            port_node = svc.get("port") if svc else None
            inputs: list[Any] = []
            if host_node is not None:
                inputs.append(loc(R.VALUE_SOURCE, p, host_node.key_line))
            pt = path_item.get("pathType")
            if pt is not None:
                inputs.append(loc(R.VALUE_SOURCE, p, pt.key_line))
            if name_node is not None:
                inputs.append(loc(R.REFERENCE, p, name_node.key_line))
            sname = str(name_node.value) if name_node is not None else None
            info = services.get((d.ns, sname))
            service_port: Any = None
            container_port: Any = None
            pref = None
            if port_node is not None:
                pn, pnum = port_node.get("name"), port_node.get("number")
                pref = str(pn.value) if pn is not None else (str(pnum.value) if pnum is not None else None)
                pline = (pn or pnum or port_node).key_line
                inputs.append(loc(R.REFERENCE, p, pline))
                if pnum is not None:
                    service_port = _num_or_str(pnum.value)
            if info is not None and pref is not None and pref in info.ports:
                service_port = info.ports[pref][0]
                port_fact = fb.get(info.ports[pref][1])
                inputs.append(via(R.VALUE_SOURCE, port_fact.fact_id))
                container_port = port_fact.value.get("container_port") if isinstance(port_fact.value, dict) else None
            subject = f"{d.subject}#{host}{path}"
            fb.add("k8s.ingress.route", subject,
                   {"host": host, "path": path, "path_type": str(pt.value) if pt is not None else None, "service": sname,
                    "service_port": service_port, "container_port": container_port},
                   FactStatus.DECLARED, source=ref(p, (path_node or path_item).key_line), inputs=inputs)
            if info is None:
                out.coverage.append(CoverageNotice(code="FN-OPS-002", scope=f"{p}:{(path_node or path_item).key_line}",
                                                   message=f"Ingress backend Service {sname!r} is not in the repository; the route's target is not known."))


def _hpa(d: K8sDoc, fb: FactBuilder, workloads: list[_Workload], out: DeploymentResult) -> None:
    p = d.path
    spec = d.root.get("spec")
    tref = spec.get("scaleTargetRef") if spec else None
    tkind, tname = (tref.scalar("kind"), tref.scalar("name")) if tref else (None, None)
    name_node = tref.get("name") if tref else None
    target = next((w for w in workloads if w.doc.kind == tkind and w.doc.name == tname and _same_ns(d, w.doc)), None)
    inputs: list[Any] = []
    mn = spec.get("minReplicas") if spec else None
    mx = spec.get("maxReplicas") if spec else None
    if mn is not None:
        inputs.append(loc(R.VALUE_SOURCE, p, mn.key_line))
        min_val = _num_or_str(mn.value)
    else:
        mid, min_val = _k8s_default(fb, "k8s.hpa.min_replicas", d.subject, "hpa.min_replicas", (p, d.name_line))
        inputs.append(via(R.VALUE_SOURCE, mid))
    if mx is not None:
        inputs.append(loc(R.VALUE_SOURCE, p, mx.key_line))
    line = name_node.key_line if name_node is not None else d.name_line
    if target is None:
        other = next((w for w in workloads if w.doc.kind == tkind and w.doc.name == tname), None)
        why = (f"scaleTargetRef {tkind}/{tname} exists in {other.doc.path}, but one side sets a namespace and the other does not"
               if other is not None and _same_ns(d, other.doc) is None
               else f"scaleTargetRef {tkind}/{tname} is not in the repository in the same namespace (RULES.md H5)")
        fb.add("k8s.hpa.target", d.subject, None, FactStatus.UNRESOLVED, source=ref(p, line), inputs=inputs, unresolved_reason=why)
    else:
        fb.add("k8s.hpa.target", d.subject, {"workload": target.doc.subject, "min": min_val, "max": _num_or_str(mx.value) if mx else None},
               FactStatus.DECLARED, source=ref(p, line), inputs=inputs)
    for m in _seq(spec.get("metrics") if spec else None):
        mtype = m.scalar("type")
        body = m.get("resource") if mtype == "Resource" else m.get("containerResource") if mtype == "ContainerResource" else None
        if body is None:
            fb.add("k8s.hpa.metric", f"{d.subject}#{mtype}", {"type": mtype}, FactStatus.DECLARED, source=ref(p, m.line))
            continue
        tgt = body.get("target")
        ttype = tgt.get("type") if tgt else None
        value: dict[str, Any] = {"type": mtype, "resource": body.scalar("name"), "target_type": str(ttype.value) if ttype else None}
        key = str(body.scalar("name"))
        if mtype == "ContainerResource":
            value["container"] = body.scalar("container")
            key = f"{body.scalar('container')}/{key}"
        vnode = None
        for field_name, out_name in (("averageUtilization", "average_utilization"), ("averageValue", "average_value"), ("value", "value")):
            node = tgt.get(field_name) if tgt else None
            if node is not None:
                value[out_name] = _num_or_str(node.value)
                vnode = node
        fb.add("k8s.hpa.metric", f"{d.subject}#{key}", value, FactStatus.DECLARED, source=ref(p, (vnode or body).key_line),
               inputs=[loc(R.VALUE_SOURCE, p, ttype.key_line)] if ttype is not None else [])


def _pdb(d: K8sDoc, fb: FactBuilder, workloads: list[_Workload], out: DeploymentResult) -> None:
    p = d.path
    spec = d.root.get("spec")
    sel = spec.get("selector") if spec else None
    labels = _labels(sel.get("matchLabels") if sel else None)
    has_expr = sel is not None and sel.get("matchExpressions") is not None
    node, field_name = None, None
    for fld, out_name in (("minAvailable", "min_available"), ("maxUnavailable", "max_unavailable")):
        if spec is not None and spec.get(fld) is not None:
            node, field_name = spec.get(fld), out_name
    line = node.key_line if node is not None else d.name_line
    inputs = [loc(R.REFERENCE, p, ln) for _, ln in sorted(labels.values(), key=lambda x: x[1])]
    if has_expr:
        fb.add("k8s.pdb", d.subject, None, FactStatus.UNRESOLVED, source=ref(p, line), inputs=inputs,
               unresolved_reason="selector uses matchExpressions, which M2-1 does not evaluate")
        return
    matched = sorted(w.doc.subject for w in workloads if _match(labels, w.labels) and _same_ns(d, w.doc))
    ambiguous = sorted(w.doc.subject for w in workloads if _match(labels, w.labels) and _same_ns(d, w.doc) is None)
    if ambiguous:
        fb.add("k8s.pdb", d.subject, None, FactStatus.UNRESOLVED, source=ref(p, line), inputs=inputs,
               unresolved_reason=f"selector matches {', '.join(ambiguous)}, but one side sets a namespace and the other does not")
        return
    value: dict[str, Any] = {"workloads": matched}
    if node is not None and field_name:
        value[field_name] = _num_or_str(node.value)
    fb.add("k8s.pdb", d.subject, value, FactStatus.DECLARED, source=ref(p, line), inputs=inputs)


# ---------------------------------------------------------------- linkage (ARCHITECTURE.md, unchanged evidence rule)


# Docker reference grammar (distribution/reference): [domain/]path[:tag][@digest], path components in lowercase.
_COMPONENT = r"[a-z0-9]+(?:(?:[._]|__|-+)[a-z0-9]+)*"
_DOMAIN = r"(?:[a-zA-Z0-9](?:[a-zA-Z0-9-]*[a-zA-Z0-9])?(?:\.[a-zA-Z0-9](?:[a-zA-Z0-9-]*[a-zA-Z0-9])?)*|\[[0-9a-fA-F:]+\])(?::[0-9]+)?"
IMAGE_REFERENCE = re.compile(rf"^(?:{_DOMAIN}/)?{_COMPONENT}(?:/{_COMPONENT})*(?::[\w][\w.-]{{0,127}})?(?:@[a-z0-9]+(?:[.+_-][a-z0-9]+)*:[0-9a-fA-F]{{32,}})?$")


def _image_basename(image: str) -> str:
    name = image.split("@", 1)[0]
    last = name.rsplit("/", 1)[-1]
    return last.split(":", 1)[0]


def _python_module_target(ws: Workspace, root: str, ref_text: str) -> tuple[str, int] | None:
    """(path, line) of a top-level assignment `attr = ...` in module `pkg.mod` under `root`, read with ast (never
    imported or executed)."""
    m = MODULE_REF.match(ref_text)
    if not m:
        return None
    rel = m.group(1).replace(".", "/")
    paths = {f.path: f for f in ws.files if f.kind == KIND_PYTHON_SOURCE}
    for candidate in (posixpath.join(root, rel + ".py"), posixpath.join(root, rel, "__init__.py")):
        entry = paths.get(candidate.lstrip("/"))
        if entry is None:
            continue
        try:
            tree = ast.parse(ws.read_text(entry))
        except (SyntaxError, ValueError, OSError):
            return None
        for node in tree.body:
            targets = node.targets if isinstance(node, ast.Assign) else [node.target] if isinstance(node, ast.AnnAssign) else []
            if any(isinstance(t, ast.Name) and t.id == m.group(2) for t in targets):
                return entry.path, node.lineno
    return None


# A COPY names a module's artifact only when the path ends like a build output. A directory that merely carries the
# module's name (for example /opt/shenyu-admin inside an image) is not evidence that the module was built there.
_ARTIFACT_SUFFIXES = (".jar", ".war", ".whl", ".tar.gz", ".tgz", ".zip")
_SOURCE_SUFFIXES = (".py", ".java", ".kt")  # source files; a build descriptor alone (pom.xml, requirements.txt) is not the module
# Unnamed build-output copies count only for the archive kinds the tool that fills that directory produces.
_OUTPUT_RULES = (("target/", (".jar", ".war")), ("build/libs/", (".jar", ".war")), ("dist/", (".whl", ".tar.gz")))


def _is_module_content(rel: str, files: list[str]) -> bool:
    """True when the path (relative to the repository) is, or contains, application source files: a configuration
    file such as nginx.conf, or a bare pom.xml, in the module directory is not the module."""
    for f in files:
        if (f == rel or rel in ("", ".") or f.startswith(rel + "/")) and f.endswith(_SOURCE_SUFFIXES):
            return True
    return False


def _stage_by_ref(model: DockerfileModel, ref_: str) -> Any:
    for st in model.stages:
        if (st.name is not None and st.name.lower() == ref_.lower()) or str(st.index) == ref_:
            return st
    return None


def _builds(model: DockerfileModel, ident: BuildIdentity, files: list[str]) -> list[int]:
    """Lines of COPY or ADD instructions in the default target's chain that copy this module's artifact or its source.
    A Dockerfile builds the module only when such an instruction exists; sitting next to the module is not enough.
    Counted: a path ending like a build output that names the module, or a build-output path (target/, build/libs/)
    under the module, or application source inside the module, or a build output taken --from a stage that copied
    the module's source."""
    target = model.default_target
    if target is None:
        return []
    ddir = posixpath.dirname(model.path)
    lines: list[int] = []
    artifact = re.compile(rf"(^|/){re.escape(ident.name)}(?=[-.*]|$)")
    root_prefix = "" if ident.root in ("", ".") else ident.root + "/"

    def inside(rel: str) -> bool:
        return rel == ident.root or ident.root in ("", ".") or rel.startswith(ident.root + "/")

    def copies_module_source(stage: Any) -> bool:
        for st in model.chain(stage):
            for frm2, sources2, _d, _l in st.copies:
                for s2 in sources2:
                    rel2 = posixpath.normpath(posixpath.join(ddir, s2.rstrip("/"))) if s2 not in (".", "./") else ddir
                    if frm2 is None and inside(rel2) and rel2 != "." and _is_module_content(rel2, files):
                        return True
        return False

    for st in model.chain(target):
        for frm, sources, _dest, line in st.copies:
            for s in sources:
                is_output = s.lower().endswith(_ARTIFACT_SUFFIXES)
                if is_output and artifact.search(s):
                    lines.append(line)  # the build context is not in the repository, so naming the artifact counts
                    continue
                if frm is not None:
                    src_stage = _stage_by_ref(model, frm)
                    if is_output and src_stage is not None and copies_module_source(src_stage):
                        lines.append(line)
                    continue
                rel = posixpath.normpath(posixpath.join(ddir, s.rstrip("/"))) if s not in (".", "./") else ddir
                if not inside(rel) or rel == ".":
                    continue
                if any(rel.startswith(root_prefix + d) and rel.lower().endswith(sfx) for d, sfx in _OUTPUT_RULES):
                    lines.append(line)
                elif _is_module_content(rel, files):
                    lines.append(line)
    return sorted(set(lines))


def _image_repository(image: str) -> str:
    return image.split("@", 1)[0].rsplit(":", 1)[0] if ":" in image.split("@", 1)[0].rsplit("/", 1)[-1] else image.split("@", 1)[0]


def _single_image_per_module(proposed: list[tuple[_Workload, str, Y, Linkage | None]],
                             out: DeploymentResult) -> list[tuple[_Workload, str, Y, Linkage | None]]:
    """ARCHITECTURE.md E4: exactly one workload image corresponds to a module. Containers that run the same image
    repository (any tag) are one image. When two different image repositories would link to one module, none of those
    links is proposed."""
    by_module: dict[str, set[str]] = {}
    for _w, csub, c, link in proposed:
        if link is not None and link.status is LinkStatus.INFERRED:
            image = c.get("image")
            by_module.setdefault(link.to_ref, set()).add(_image_repository(str(image.value)) if image is not None else csub)
    clashing = {m for m, repos in by_module.items() if len(repos) > 1}
    if not clashing:
        return proposed
    result: list[tuple[_Workload, str, Y, Linkage | None]] = []
    for w, csub, c, link in proposed:
        if link is not None and link.status is LinkStatus.INFERRED and link.to_ref in clashing:
            out.linkages.remove(link)
            out.dockerfile_for_link.pop(link.link_id, None)
            image_node = c.get("image")
            image_line = f"{w.doc.path}:{image_node.key_line}" if image_node is not None else f"{w.doc.path}:{w.doc.name_line}"
            others = sorted(by_module[link.to_ref])
            link = _unresolved_link("workload_container_to_source_root", csub, [image_line], out,
                                    f"ambiguous: several workload images ({', '.join(others)}) would link to {link.to_ref}")
        result.append((w, csub, c, link))
    return result


def _unresolved_link(kind: str, csub: str, evidence: list[str], out: DeploymentResult, reason: str) -> Linkage:
    link = Linkage(link_id=_link_id(kind, csub, "source:?"), kind=kind, status=LinkStatus.UNRESOLVED, from_ref=csub, to_ref="source:?",
                   evidence=[*evidence, f"reason: {reason}"])
    out.linkages.append(link)
    out.coverage.append(CoverageNotice(code="COV-LINKAGE", scope=csub, message=f"No source link proposed for {csub}: {reason}."))
    return link


def _link_id(kind: str, frm: str, to: str) -> str:
    return "l-" + hashlib.sha256(f"{kind}\x1f{frm}\x1f{to}".encode()).hexdigest()[:16]


def _linkage(ws: Workspace, w: _Workload, csub: str, c: Y, identities: list[BuildIdentity], out: DeploymentResult,
             fb: FactBuilder) -> Linkage | None:
    p = w.doc.path
    kind = "workload_container_to_source_root"
    image_node = c.get("image")
    if image_node is None:
        return None
    image = str(image_node.value)
    evidence: list[str] = [f"{p}:{image_node.key_line}"]
    if not IMAGE_REFERENCE.match(image):
        # a placeholder or template (e.g. <registry>/app:<tag>) is filled in outside the repository; which image is
        # deployed is not known, so no name match is attempted
        return _unresolved_link(kind, csub, evidence, out,
                                f"image {image!r} is not a valid image reference (a placeholder or template filled in at deploy time)")
    basename = _image_basename(image)
    by_name = [i for i in identities if i.name == basename]
    by_command: list[tuple[BuildIdentity, tuple[str, int], int]] = []
    argv = (_argv(c.get("command")) or []) + (_argv(c.get("args")) or [])
    arg_lines = {str(i.value): i.line for n in ("command", "args") if c.get(n) for i in c.get(n).seq()}  # type: ignore[union-attr]
    for token in argv:
        for ident in identities:
            hit = _python_module_target(ws, ident.root, token)
            if hit:
                by_command.append((ident, hit, arg_lines.get(token, 0)))
    candidates = {i.path: i for i in by_name} | {i.path: i for i, _, _ in by_command}

    def unresolved(reason: str) -> Linkage:
        return _unresolved_link(kind, csub, evidence, out, reason)

    if not candidates:
        return unresolved(f"no in-repository build identity matches image basename {basename!r}, and the container command names no in-repository module")
    if len(candidates) > 1:
        return unresolved("ambiguous: " + ", ".join(sorted(f"{i.kind} {i.name} ({i.path})" for i in candidates.values())))
    ident = next(iter(candidates.values()))
    file_paths = sorted(f.path for f in ws.files)
    builders: dict[str, list[int]] = {}
    for path, model in out.dockerfiles.items():
        copies = _builds(model, ident, file_paths)
        if copies:
            builders[path] = copies
    if not builders:
        return unresolved(f"no Dockerfile in the repository builds {ident.name}; the image may be built by buildpacks or a build plugin")
    if len(builders) > 1:
        return unresolved(f"several Dockerfiles could build {ident.name}: {', '.join(sorted(builders))}")
    dpath, copy_lines = next(iter(builders.items()))
    model = out.dockerfiles[dpath]
    target = model.default_target
    assert target is not None
    exposed = {e.value[0] for st in model.chain(target) for e in st.expose if e.unresolved is None and isinstance(e.value, tuple) and e.value[1] == "tcp"}
    expose_unresolved = any(e.unresolved is not None for st in model.chain(target) for e in st.expose)
    container_ports = {num: fid for _, (num, fid) in w.port_facts.get(csub, {}).items()}
    if exposed and container_ports and not set(container_ports) <= exposed:
        return unresolved(f"container ports {sorted(set(container_ports) - exposed)} are not exposed by {dpath}")
    evidence.append(f"{ident.path}:{ident.line}")
    for _ident, (mpath, mline), aline in by_command:
        evidence += [f"{p}:{aline}", f"{mpath}:{mline}"] if aline else [f"{mpath}:{mline}"]
    evidence.append(f"{dpath}:{target.from_line}")
    evidence += [f"{dpath}:{n}" for n in copy_lines]
    expose_lines = sorted({e.line for st in model.chain(target) for e in st.expose})
    evidence += [f"{dpath}:{n}" for n in expose_lines] if container_ports else []
    for fid in sorted(set(container_ports.values())):
        src = fb.get(fid).source
        if src is not None:
            evidence.append(f"{src.path}:{src.line_start}")
    evidence = sorted(set(evidence), key=lambda e: (e.rsplit(":", 1)[0], int(e.rsplit(":", 1)[1]) if e.rsplit(":", 1)[1].isdigit() else 0))
    if expose_unresolved:
        evidence.append("port agreement was not fully checked: an EXPOSE instruction uses a variable with no value in the file")
    evidence.append("assumes the deployed image was built from the Dockerfile's default (final) stage; the build target "
                    "used in production is not read from the repository (a CI --target flag, Compose file or workflow is not interpreted)")
    root = ident.root or "."
    link = Linkage(link_id=_link_id(kind, csub, f"source:{root}"), kind=kind, status=LinkStatus.INFERRED, from_ref=csub, to_ref=f"source:{root}",
                   evidence=evidence, accept_command="fifthnine links accept (milestone M2-5)")
    out.linkages.append(link)
    out.dockerfile_for_link[link.link_id] = dpath
    return link


# ---------------------------------------------------------------- effective container command (RULES.md F13, F14)


def _effective_command(w: _Workload, csub: str, c: Y, link: Linkage | None, out: DeploymentResult, docker_ids: dict[str, dict[str, str]],
                       fb: FactBuilder) -> None:
    p = w.doc.path
    kind = "k8s.container.effective_command"
    name_node = c.get("name")
    assert name_node is not None
    cmd_node, args_node = c.get("command"), c.get("args")
    command, args = _argv(cmd_node), _argv(args_node)
    if any("$(" in a for a in (command or []) + (args or [])):
        fb.add(kind, csub, None, FactStatus.UNRESOLVED, source=ref(p, (cmd_node or args_node).key_line),  # type: ignore[union-attr]
               unresolved_reason="command or args use $(VAR) expansion, which the kubelet resolves from the container environment")
        return
    if command is not None:
        assert cmd_node is not None
        inputs = [loc(R.VALUE_SOURCE, p, args_node.key_line)] if args_node is not None else []
        fb.add(kind, csub, command + (args or []), FactStatus.DECLARED, source=ref(p, cmd_node.key_line), inputs=inputs,
               precedence="k8s.container.command")
        return
    linked = link is not None and link.status is LinkStatus.INFERRED
    if not linked:
        fb.add(kind, csub, None, FactStatus.UNRESOLVED, inputs=[loc(R.ABSENCE, p, name_node.key_line)],
               unresolved_reason="the container does not set command, and its image is not linked to an in-repository Dockerfile, "
                                 "so the image ENTRYPOINT and CMD are not known")
        return
    assert link is not None
    dpath = out.dockerfile_for_link[link.link_id]
    model = out.dockerfiles[dpath]
    target = model.default_target
    assert target is not None
    ids = docker_ids[dpath]
    # Every value read through the link assumes the image was built from the Dockerfile's default stage, so the fact
    # depends on the unresolved build target and is never declared.
    base_inputs = [loc(R.ABSENCE, p, name_node.key_line), via(R.REFERENCE, ids["default_target"]), via(R.REFERENCE, ids["build_target"])]
    if target.entrypoint_state == "set" and target.entrypoint is not None:
        ep_form, ep_argv, ep_line = target.entrypoint
        tail = args if args is not None else (target.cmd[1] if target.cmd_state == "set" and target.cmd is not None else [])
        inputs = [*base_inputs, via(R.VALUE_SOURCE, ids["entrypoint"])]
        if args is not None:
            inputs.append(loc(R.VALUE_SOURCE, p, args_node.key_line))  # type: ignore[union-attr]
        elif target.cmd_state == "set":
            inputs.append(via(R.VALUE_SOURCE, ids["cmd"]))
        fb.add(kind, csub, ep_argv + tail, FactStatus.INFERRED, source=ref(dpath, ep_line), inputs=inputs,
               precedence="k8s.container.args" if args is not None else "docker.entrypoint", link_ids=[link.link_id])
        return
    cmd_ref = [via(R.REFERENCE, ids["cmd"])] if target.cmd_state == "set" and "cmd" in ids else []
    fb.add(kind, csub, None, FactStatus.UNRESOLVED, inputs=[*base_inputs, via(R.REFERENCE, ids["entrypoint"]), *cmd_ref], link_ids=[link.link_id],
           unresolved_reason="the Dockerfile sets no ENTRYPOINT, so the image ENTRYPOINT comes from the base image, which is not in the repository "
                             "(many base images, such as nginx, set one)")


def _mounts(w: _Workload, csub: str, c: Y, link: Linkage | None, out: DeploymentResult, docker_ids: dict[str, dict[str, str]],
            fb: FactBuilder) -> None:
    """Volume mounts, and whether an out-of-repository config file could sit where the application reads config
    (RULES.md X5: the image WORKDIR, WORKDIR/config or below)."""
    p = w.doc.path
    pod = w.doc.root.path("spec", "template", "spec")
    volumes = {str(v.scalar("name")): v for v in (_seq(pod.get("volumes") if pod else None))}
    workdir: str | None = None
    if link is not None and link.status is LinkStatus.INFERRED:
        model = out.dockerfiles[out.dockerfile_for_link[link.link_id]]
        tgt = model.default_target
        if tgt is not None and tgt.workdir is not None and tgt.workdir.unresolved is None:
            workdir = str(tgt.workdir.value).rstrip("/") or "/"
    for m in _seq(c.get("volumeMounts")):
        mp = m.get("mountPath")
        if mp is None:
            continue
        vol = volumes.get(str(m.scalar("name")))
        src_kind, src_name = None, None
        if vol is not None:
            for k, label in (("configMap", "ConfigMap"), ("secret", "Secret"), ("persistentVolumeClaim", "PersistentVolumeClaim"),
                             ("emptyDir", "emptyDir"), ("projected", "projected")):
                if vol.get(k) is not None:
                    src_kind = label
                    node = vol.get(k)
                    src_name = (node.scalar("name") or node.scalar("secretName") or node.scalar("claimName")) if node is not None else None
        mount_path = str(mp.value).rstrip("/") or "/"
        reads_config: Any = None
        if workdir is not None:
            reads_config = mount_path in (workdir, f"{workdir}/config") or mount_path.startswith(f"{workdir}/config/")
        value = {"mount_path": mount_path, "volume": m.scalar("name"), "source_kind": src_kind, "source_name": src_name,
                 "config_location": reads_config}
        if reads_config is not None and link is not None:
            # config_location rests on the image WORKDIR of the default stage, which may not be the deployed image
            ids = docker_ids[out.dockerfile_for_link[link.link_id]]
            fb.add("k8s.container.volume_mount", f"{csub}#{mount_path}", value, FactStatus.INFERRED, source=ref(p, mp.key_line),
                   inputs=[via(R.REFERENCE, ids["default_target"]), via(R.REFERENCE, ids["build_target"])], link_ids=[link.link_id])
        else:
            fb.add("k8s.container.volume_mount", f"{csub}#{mount_path}", value, FactStatus.DECLARED, source=ref(p, mp.key_line))

