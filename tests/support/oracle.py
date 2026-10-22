"""Semantic provenance oracle for M2-1 facts (CEO clarification, 2026-10-22).

The oracle re-derives each emitted fact from the repository with code paths independent of the extractor:
- Kubernetes YAML is re-read with ruamel's round-trip loader (constructor and `.lc` line info), not the composer and
  `Y` tree the engine uses; relationships (selectors, named ports, backends, scale targets) are recomputed here.
- Dockerfiles are re-read with Docker's own parser (BuildKit, through the `dockerfile` package), not the engine's
  parser; stage, WORKDIR, EXPOSE, ENTRYPOINT/CMD and COPY semantics are recomputed from its output.
- Defaults are checked against the defaults table and the field's absence in the round-trip document.
- Link evidence locations are checked for what they must show (image, build identity, Dockerfile instruction, port).

A fact passes only when the oracle's value and line both agree. Facts whose kind the oracle does not cover, or whose
value depends on build-time variable substitution, are counted as skipped and reported, never passed silently.
"""

from __future__ import annotations

import ast
import json
import posixpath
import re
import shlex
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

try:
    import tomllib
except ModuleNotFoundError:  # Python 3.10
    import tomli as tomllib  # type: ignore[no-redef]

import dockerfile  # dev dependency: Docker's BuildKit parser
from ruamel.yaml import YAML

from fifthnine.resolve.defaults import kubernetes_defaults
from fifthnine.schema.v1 import Fact, FactStatus, Linkage

K8S_KINDS = {"Deployment": "apps/v1", "Service": "v1", "Ingress": "networking.k8s.io/v1", "HorizontalPodAutoscaler": "autoscaling/v2",
             "PodDisruptionBudget": "policy/v1", "ConfigMap": "v1"}


@dataclass
class OracleReport:
    checked: int = 0
    mismatches: list[str] = field(default_factory=list)
    skipped: dict[str, int] = field(default_factory=dict)

    def skip(self, why: str) -> None:
        self.skipped[why] = self.skipped.get(why, 0) + 1

    def check(self, fact: Fact, expected_value: Any, expected_line: int | None, *, path: str | None = None) -> None:
        self.checked += 1
        got_line = fact.source.line_start if fact.source else None
        if fact.value != expected_value:
            self.mismatches.append(f"{fact.kind} {fact.subject.key}: value {fact.value!r}, oracle {expected_value!r}")
        elif expected_line != got_line:
            self.mismatches.append(f"{fact.kind} {fact.subject.key}: line {got_line}, oracle {expected_line}")
        elif path is not None and fact.source is not None and fact.source.path != path:
            self.mismatches.append(f"{fact.kind} {fact.subject.key}: path {fact.source.path}, oracle {path}")


# ---------------------------------------------------------------- Kubernetes (round-trip loader)


@dataclass
class RtObj:
    kind: str
    name: str
    ns: str
    path: str
    doc: Any  # ruamel CommentedMap

    @property
    def subject(self) -> str:
        return f"k8s:{self.kind}/{self.ns}/{self.name}"


def _line(m: Any, key: Any) -> int:
    return int(m.lc.key(key)[0]) + 1 if hasattr(m, "lc") else 0


def _item_line(seq: Any, i: int) -> int:
    return int(seq.lc.item(i)[0]) + 1


def load_rt(root: Path, files: list[str]) -> list[RtObj]:
    yaml = YAML(typ="rt")
    out: list[RtObj] = []
    seen: set[str] = set()
    for rel in sorted(files):
        text = (root / rel).read_text(encoding="utf-8-sig", errors="replace")
        helm = "{{" in text and ("/templates/" in f"/{rel}" or ".Values" in text or "{{-" in text)
        if helm or not re.search(r"^apiVersion\s*:", text, re.M):
            continue
        try:
            docs = list(yaml.load_all(text))
        except Exception:  # noqa: BLE001 - the engine reports parse errors; the oracle only checks what it read
            continue
        for d in docs:
            if not isinstance(d, dict) or K8S_KINDS.get(str(d.get("kind"))) != d.get("apiVersion"):
                continue
            meta = d.get("metadata") or {}
            obj = RtObj(str(d["kind"]), str(meta.get("name")), str(meta.get("namespace") or "_"), rel, d)
            if obj.subject in seen:
                continue
            seen.add(obj.subject)
            out.append(obj)
    return out


def _containers(o: RtObj) -> dict[str, Any]:
    cs = (((o.doc.get("spec") or {}).get("template") or {}).get("spec") or {}).get("containers") or []
    return {str(c.get("name")): c for c in cs}


def _template_labels(o: RtObj) -> dict[str, str]:
    meta = ((o.doc.get("spec") or {}).get("template") or {}).get("metadata") or {}
    return {str(k): str(v) for k, v in (meta.get("labels") or {}).items()}


def _selects(selector: dict[str, Any], o: RtObj, ns: str) -> bool:
    labels = _template_labels(o)
    return bool(selector) and o.ns == ns and all(labels.get(str(k)) == str(v) for k, v in selector.items())


def _port_by_name(c: Any, name: Any) -> int | None:
    for p in c.get("ports") or []:
        if str(p.get("name")) == str(name) or str(p.get("containerPort")) == str(name):
            return int(p["containerPort"])
    return None


def check_k8s(root: Path, files: list[str], facts: list[Fact], rep: OracleReport) -> None:
    objs = load_rt(root, files)
    by = {o.subject: o for o in objs}
    deploys = [o for o in objs if o.kind == "Deployment"]
    defaults = kubernetes_defaults()
    for f in facts:
        if not f.kind.startswith("k8s."):
            continue
        subj = f.subject.key.split("#", 1)[0]
        parts = subj.split("/")
        obj = by.get("/".join(parts[:3]))
        if obj is None:
            rep.skip(f"{f.kind}: object not re-read")
            continue
        d, spec = obj.doc, obj.doc.get("spec") or {}
        frag = f.subject.key.split("#", 1)[1] if "#" in f.subject.key else None
        k = f.kind
        if k == "k8s.object.namespace":
            meta = d.get("metadata") or {}
            if meta.get("namespace"):  # null or empty means not set, as the API server reads it
                rep.check(f, str(meta["namespace"]), _line(meta, "namespace"))
            else:
                rep.check(f, None, None)
        elif k == "k8s.configmap.entry":
            data = d.get("data") or {}
            rep.check(f, str(data.get(frag)), _line(data, frag))
        elif k == "k8s.deployment.replicas":
            rep.check(f, spec.get("replicas"), _line(spec, "replicas") if "replicas" in spec else _line(d["metadata"], "name"))
        elif k in ("k8s.deployment.strategy_type", "k8s.deployment.max_unavailable", "k8s.deployment.max_surge"):
            st = spec.get("strategy") or {}
            fld = {"k8s.deployment.strategy_type": ("type", "deployment.strategy_type"),
                   "k8s.deployment.max_unavailable": ("maxUnavailable", "deployment.max_unavailable"),
                   "k8s.deployment.max_surge": ("maxSurge", "deployment.max_surge")}[k]
            holder = st if fld[0] == "type" else (st.get("rollingUpdate") or {})
            if fld[0] in holder:
                rep.check(f, holder[fld[0]], _line(holder, fld[0]))
            else:
                _check_default(f, defaults[fld[1]].value, rep)
        elif k == "k8s.pod.termination_grace_period":
            pod = (spec.get("template") or {}).get("spec") or {}
            if "terminationGracePeriodSeconds" in pod:
                rep.check(f, pod["terminationGracePeriodSeconds"], _line(pod, "terminationGracePeriodSeconds"))
            else:
                _check_default(f, defaults["pod.termination_grace_period"].value, rep)
        elif k.startswith("k8s.container."):
            _check_container(f, obj, parts[3] if len(parts) > 3 else "", frag, by, rep)
        elif k == "k8s.service.selects":
            sel = spec.get("selector") or {}
            matched = sorted(o.subject for o in deploys if _selects(sel, o, obj.ns))
            # one side sets a namespace and the other does not: the relationship is decided at apply time
            split = [o for o in deploys if _selects(sel, o, o.ns) and o.ns != obj.ns and "_" in (o.ns, obj.ns)]
            stype = spec.get("type") or defaults["service.type"].value
            first = min((_line(sel, x) for x in sel), default=_line(d, "spec"))
            rep.check(f, None if split and not matched else {"type": stype, "workloads": matched}, first)
        elif k == "k8s.service.target_port":
            rep.skip("k8s.service.target_port: default checked through k8s.service.port")
        elif k == "k8s.service.type":
            _check_default(f, defaults["service.type"].value, rep)
        elif k == "k8s.service.port":
            sel = spec.get("selector") or {}
            matched = [o for o in deploys if _selects(sel, o, obj.ns)]
            for p in spec.get("ports") or []:
                key = str(p.get("name") or p.get("port"))
                if key != frag:
                    continue
                target = p.get("targetPort", p.get("port"))
                if isinstance(target, int):
                    cport: Any = target
                else:
                    nums = {_port_by_name(c, target) for o in matched for c in _containers(o).values()} - {None}
                    cport = nums.pop() if len(nums) == 1 else None
                # a named targetPort that resolves to no single container port leaves the fact unresolved
                expected = None if cport is None else {"port": p.get("port"), "target_port": target, "container_port": cport}
                rep.check(f, expected, _line(p, "port"))
        elif k == "k8s.ingress.route":
            _check_ingress(f, obj, by, deploys, rep)
        elif k == "k8s.ingress.controller_annotations":
            rep.skip(k)
        elif k == "k8s.hpa.target":
            ref_ = spec.get("scaleTargetRef") or {}
            tgt = by.get(f"k8s:{ref_.get('kind')}/{obj.ns}/{ref_.get('name')}")
            min_ = spec.get("minReplicas", defaults["hpa.min_replicas"].value)
            expected = {"workload": tgt.subject, "min": min_, "max": spec.get("maxReplicas")} if tgt else None
            rep.check(f, expected, _line(ref_, "name"))
        elif k == "k8s.hpa.min_replicas":
            _check_default(f, defaults["hpa.min_replicas"].value, rep)
        elif k == "k8s.hpa.metric":
            for m in spec.get("metrics") or []:
                body = m.get("resource") or m.get("containerResource")
                if body is None:
                    continue
                key = f"{body.get('container')}/{body.get('name')}" if m.get("type") == "ContainerResource" else str(body.get("name"))
                if key != frag:
                    continue
                tgt = body.get("target") or {}
                val: dict[str, Any] = {"type": m.get("type"), "resource": body.get("name"), "target_type": tgt.get("type")}
                if m.get("type") == "ContainerResource":
                    val["container"] = body.get("container")
                line = None
                for fld, out_name in (("averageUtilization", "average_utilization"), ("averageValue", "average_value"), ("value", "value")):
                    if fld in tgt:
                        val[out_name] = tgt[fld]
                        line = _line(tgt, fld)
                rep.check(f, val, line)
        elif k == "k8s.pdb":
            sel = ((spec.get("selector") or {}).get("matchLabels")) or {}
            matched = sorted(o.subject for o in deploys if _selects(sel, o, obj.ns))
            val = {"workloads": matched}
            line = _line(d["metadata"], "name")
            for fld, out_name in (("minAvailable", "min_available"), ("maxUnavailable", "max_unavailable")):
                if fld in spec:
                    val[out_name] = spec[fld]
                    line = _line(spec, fld)
            rep.check(f, val, line)
        else:
            rep.skip(f"{k}: not covered")


def _check_default(f: Fact, value: Any, rep: OracleReport) -> None:
    rep.checked += 1
    if f.status is not FactStatus.DEFAULT or f.value != value or f.default_ref is None or f.source is not None:
        rep.mismatches.append(f"{f.kind} {f.subject.key}: expected default {value!r}, got {f.status.value} {f.value!r}")


def _check_container(f: Fact, obj: RtObj, cname: str, frag: str | None, by: dict[str, RtObj], rep: OracleReport) -> None:
    c = _containers(obj).get(cname)
    if c is None:
        rep.skip(f"{f.kind}: container not found")
        return
    k = f.kind
    name_line = None
    for item in (((obj.doc.get("spec") or {}).get("template") or {}).get("spec") or {}).get("containers") or []:
        if str(item.get("name")) == cname:
            name_line = _line(item, "name")
    if k == "k8s.container.image":
        rep.check(f, str(c.get("image")), _line(c, "image"))
    elif k == "k8s.container.port":
        for p in c.get("ports") or []:
            if str(p.get("name") or p.get("containerPort")) == frag:
                rep.check(f, p.get("containerPort"), _line(p, "containerPort"))
    elif k == "k8s.container.env":
        explicit = {str(e.get("name")): e for e in c.get("env") or []}
        if frag in explicit:
            e = explicit[frag]
            if "valueFrom" in e:
                vf = e["valueFrom"]
                cmk = vf.get("configMapKeyRef")
                cm = by.get(f"k8s:ConfigMap/{obj.ns}/{cmk.get('name')}") if cmk else None
                data = (cm.doc.get("data") or {}) if cm else {}
                if cmk and cmk.get("key") in data:
                    rep.check(f, str(data[cmk["key"]]), _line(data, cmk["key"]), path=cm.path if cm else None)
                else:
                    rep.check(f, None, _line(e, "valueFrom"))
            else:
                rep.check(f, str(e.get("value", "")), _line(e, "value") if "value" in e else _line(e, "name"))
            return
        winner = None
        for ef in c.get("envFrom") or []:  # later entries win duplicates
            cmr = ef.get("configMapRef")
            prefix = str(ef.get("prefix") or "")
            if cmr is None:
                continue
            cm = by.get(f"k8s:ConfigMap/{obj.ns}/{cmr.get('name')}")
            data = (cm.doc.get("data") or {}) if cm else {}
            key = frag[len(prefix):] if prefix and frag and frag.startswith(prefix) else frag
            if cm is not None and key in data:
                winner = (str(data[key]), _line(data, key), cm.path)
        if winner:
            rep.check(f, winner[0], winner[1], path=winner[2])
        else:
            rep.mismatches.append(f"{k} {f.subject.key}: oracle finds no source for this variable")
    elif k == "k8s.container.env_from":
        for i, ef in enumerate(c.get("envFrom") or []):
            for rk, kind in (("configMapRef", "ConfigMap"), ("secretRef", "Secret")):
                if rk in ef and f"{kind.lower()}:{ef[rk].get('name')}" == frag:
                    in_repo = kind == "ConfigMap" and f"k8s:ConfigMap/{obj.ns}/{ef[rk].get('name')}" in by
                    val = {"kind": kind, "name": str(ef[rk].get("name")), "in_repository": in_repo, "order": i}
                    if ef.get("prefix"):
                        val["prefix"] = str(ef["prefix"])
                    rep.check(f, val, _line(ef[rk], "name"))
    elif k.startswith("k8s.container.resources."):
        _, _, _, group, res = k.split(".")
        resources = c.get("resources") or {}
        g = resources.get(group) or {}
        if res in g:
            rep.check(f, str(g[res]), _line(g, res))
        elif group == "requests" and res in (resources.get("limits") or {}):
            _check_default(f, str(resources["limits"][res]), rep)
        else:
            line = _line(resources, group) if group in resources else (_line(c, "resources") if "resources" in c else name_line)
            rep.checked += 1
            if not (f.absent and f.value is None and f.source is not None and f.source.line_start == line):
                rep.mismatches.append(f"{k} {f.subject.key}: oracle says absent at line {line}")
    elif k.startswith("k8s.container.probe."):
        probe = c.get(f"{k.rsplit('.', 1)[1]}Probe")
        if probe is None:
            rep.checked += 1
            if not (f.absent and f.source is not None and f.source.line_start == name_line):
                rep.mismatches.append(f"{k} {f.subject.key}: oracle says absent")
            return
        h = probe.get("httpGet")
        if h is not None:
            port = h.get("port")
            num = port if isinstance(port, int) else _port_by_name(c, port)
            rep.check(f, {"type": "httpGet", "path": str(h.get("path", "/")), "port": num} if num is not None else None,
                      _line(h, "path") if "path" in h else _line(h, "port"))
        else:
            rep.skip(f"{k}: non-httpGet probe")
    elif k == "k8s.container.lifecycle.prestop":
        pre = (c.get("lifecycle") or {}).get("preStop")
        if pre is None:
            rep.checked += 1
            if not (f.absent and f.source is not None and f.source.line_start == name_line):
                rep.mismatches.append(f"{k} {f.subject.key}: oracle says absent")
        elif "exec" in pre:
            rep.check(f, {"type": "exec", "command": [str(x) for x in pre["exec"].get("command") or []]}, _line(pre["exec"], "command"))
        elif "sleep" in pre:
            rep.check(f, {"type": "sleep", "seconds": pre["sleep"].get("seconds")}, _line(pre["sleep"], "seconds"))
        else:
            rep.skip(f"{k}: httpGet preStop")
    elif k == "k8s.container.lifecycle.prestop_sleep":
        pre = (c.get("lifecycle") or {}).get("preStop") or {}
        if "sleep" in pre:  # native sleep action
            rep.check(f, pre["sleep"].get("seconds"), _line(pre["sleep"], "seconds"))
            return
        cmd = [str(x) for x in (pre.get("exec") or {}).get("command") or []]
        m = re.fullmatch(r"\s*sleep\s+(\d+)\s*", cmd[2]) if len(cmd) == 3 and cmd[1] == "-c" else None
        rep.check(f, int(m.group(1)) if m else None, _line(pre["exec"], "command"))
    elif k == "k8s.container.effective_command":
        rep.skip("k8s.container.effective_command: checked by check_effective_command")
    elif k == "k8s.container.volume_mount":
        rep.skip(k)
    else:
        rep.skip(f"{k}: not covered")


def _check_ingress(f: Fact, obj: RtObj, by: dict[str, RtObj], deploys: list[RtObj], rep: OracleReport) -> None:
    frag = f.subject.key.split("#", 1)[1]
    for rule in (obj.doc.get("spec") or {}).get("rules") or []:
        host = str(rule.get("host") or "*")
        for p in (rule.get("http") or {}).get("paths") or []:
            if f"{host}{p.get('path', '/')}" != frag:
                continue
            svc_ref = ((p.get("backend") or {}).get("service")) or {}
            sname = svc_ref.get("name")
            pref = (svc_ref.get("port") or {})
            svc = by.get(f"k8s:Service/{obj.ns}/{sname}")
            sport, cport = pref.get("number"), None
            if svc is not None:
                sspec = svc.doc.get("spec") or {}
                for sp in sspec.get("ports") or []:
                    if str(sp.get("name")) == str(pref.get("name")) or sp.get("port") == pref.get("number"):
                        sport = sp.get("port")
                        target = sp.get("targetPort", sp.get("port"))
                        if isinstance(target, int):
                            cport = target
                        else:
                            sel = sspec.get("selector") or {}
                            nums = {_port_by_name(c, target) for o in deploys if _selects(sel, o, obj.ns) for c in _containers(o).values()} - {None}
                            cport = nums.pop() if len(nums) == 1 else None
            rep.check(f, {"host": host, "path": str(p.get("path", "/")), "path_type": p.get("pathType"), "service": sname,
                          "service_port": sport, "container_port": cport}, _line(p, "path"))


# ---------------------------------------------------------------- Dockerfiles (BuildKit parser)


@dataclass
class BkStage:
    index: int
    name: str | None
    from_line: int
    base: str
    cmds: list[Any]


def _bk_stages(text: str) -> list[BkStage]:
    stages: list[BkStage] = []
    for cmd in dockerfile.parse_string(text):
        if cmd.cmd.upper() == "FROM":
            vals = [v for v in cmd.value]
            name = vals[2] if len(vals) >= 3 and vals[1].upper() == "AS" else None
            stages.append(BkStage(len(stages), name, cmd.start_line, vals[0], []))
        elif stages:
            stages[-1].cmds.append(cmd)
    return stages


def check_docker(root: Path, files: list[str], facts: list[Fact], rep: OracleReport) -> None:
    by_path: dict[str, list[BkStage]] = {}
    for rel in files:
        try:
            by_path[rel] = _bk_stages((root / rel).read_text(encoding="utf-8-sig", errors="replace"))
        except dockerfile.GoParseError:
            continue
    for f in facts:
        if not f.kind.startswith("docker."):
            continue
        key = f.subject.key[len("docker:"):]
        path, _, rest = key.partition("#")
        stages = by_path.get(path)
        if stages is None:
            rep.skip(f"{f.kind}: Dockerfile not re-read")
            continue
        if f.kind in ("docker.default_target", "docker.build_target"):
            last = stages[-1]
            if f.kind == "docker.default_target":
                rep.check(f, {"stage_index": last.index, "stage_name": last.name}, last.from_line)
            else:
                rep.check(f, None, None)
            continue
        idx_s, _, extra = rest.partition("#")
        idx_s, _, dest = idx_s.partition(">")
        st = stages[int(idx_s)]
        names = {s.name.lower(): s.index for s in stages if s.name}
        chain = [st]
        while chain[0].base.lower() in names and names[chain[0].base.lower()] < chain[0].index:
            chain.insert(0, stages[names[chain[0].base.lower()]])
        cmds = [c for s in chain for c in s.cmds]
        raw_has_var = any("$" in str(v) for c in cmds for v in c.value) or "$" in st.base
        k = f.kind
        if k == "docker.stage.base_image":
            if "$" in st.base:
                rep.skip("docker: build-arg substitution")
                continue
            rep.check(f, f"stage:{st.base}" if st.base.lower() in names else st.base, st.from_line)
        elif k == "docker.workdir":
            wd = [c for c in cmds if c.cmd.upper() == "WORKDIR"]
            if not wd:
                rep.check(f, None, None)
            elif any("$" in w.value[0] for w in wd):
                rep.skip("docker: build-arg substitution")
            else:
                cur = ""
                for w in wd:
                    cur = w.value[0] if w.value[0].startswith("/") else (cur.rstrip("/") + "/" + w.value[0] if cur else None)  # type: ignore[assignment]
                    if cur is None:
                        break
                rep.check(f, cur, wd[-1].start_line if cur is not None else None)
        elif k == "docker.expose":
            ex = [c for c in cmds if c.cmd.upper() == "EXPOSE"]
            if any("$" in v for c in ex for v in c.value):
                rep.skip("docker: build-arg substitution")
                continue
            ports = []
            for c in ex:
                for v in c.value:
                    num, _, proto = v.partition("/")
                    ports.append(int(num) if (proto or "tcp").lower() == "tcp" else f"{num}/{proto.lower()}")
            rep.check(f, ports, min(c.start_line for c in ex))
        elif k in ("docker.entrypoint", "docker.cmd"):
            _check_ep_cmd(f, st, chain, rep)
        elif k == "docker.env":
            if raw_has_var:
                rep.skip("docker: build-arg substitution")
                continue
            val, line = None, None
            for c in cmds:
                if c.cmd.upper() == "ENV":
                    vals = list(c.value)
                    for i in range(0, len(vals) - 1, 2):
                        if vals[i] == extra:
                            # BuildKit's AST keeps the quotes; the builder removes them when it processes the word
                            words = shlex.split(vals[i + 1]) if vals[i + 1] else [""]
                            val, line = (words[0] if len(words) == 1 else vals[i + 1]), c.start_line
            rep.check(f, val, line)
        elif k == "docker.copy_from_stage":
            want_dest, _, nth = dest.partition("~")
            seen_dest = 0
            for c in st.cmds:
                if c.cmd.upper() == "COPY" and any(fl.startswith("--from=") for fl in c.flags):
                    srcs, dst = list(c.value[:-1]), c.value[-1]
                    if dst == want_dest:
                        seen_dest += 1
                    if dst == want_dest and seen_dest == int(nth or 1) and any("$" in v for v in c.value):
                        rep.skip("docker: build-arg substitution")
                    elif dst == want_dest and seen_dest == int(nth or 1):
                        frm = next(fl.split("=", 1)[1] for fl in c.flags if fl.startswith("--from="))
                        rep.check(f, {"from_stage": frm, "src": srcs[0] if len(srcs) == 1 else srcs, "dest": dst}, c.start_line)
        elif k == "docker.user":
            us = [c for c in cmds if c.cmd.upper() == "USER"]
            if not us or "$" in us[-1].value[0]:
                rep.skip("docker: user substitution")
            else:
                rep.check(f, us[-1].value[0], us[-1].start_line)
        else:
            rep.skip(f"{k}: not covered")


def _exec_or_shell(c: Any, shell: list[str]) -> list[str]:
    return list(c.value) if c.json else [*shell, c.value[0] if len(c.value) == 1 else " ".join(c.value)]


def _check_ep_cmd(f: Fact, st: BkStage, chain: list[BkStage], rep: OracleReport) -> None:
    """BuildKit semantics: ENTRYPOINT clears a CMD inherited from the parent image unless CMD is set in the same stage."""
    ep: tuple[list[str], int] | None = None
    cmd: tuple[list[str], int] | None = None
    cmd_state, cleared_at = "unknown", None
    shell = ["/bin/sh", "-c"]
    for s in chain:
        cmd_here = False
        for c in s.cmds:
            name = c.cmd.upper()
            if name == "SHELL" and c.json:
                shell = list(c.value)
            elif name == "ENTRYPOINT":
                ep = (_exec_or_shell(c, shell), c.start_line)
                if not cmd_here:
                    cmd, cmd_state, cleared_at = None, "empty", c.start_line
            elif name == "CMD":
                cmd, cmd_state, cmd_here, cleared_at = (_exec_or_shell(c, shell), c.start_line), "set", True, None
    if f.kind == "docker.entrypoint":
        if ep is None:
            rep.check(f, None, None)
        else:
            form = "exec" if any(c.cmd.upper() == "ENTRYPOINT" and c.start_line == ep[1] and c.json for s in chain for c in s.cmds) else "shell"
            rep.check(f, {"form": form, "argv": ep[0]}, ep[1])
    else:
        if cmd_state == "set" and cmd is not None:
            form = "exec" if any(c.cmd.upper() == "CMD" and c.start_line == cmd[1] and c.json for s in chain for c in s.cmds) else "shell"
            rep.check(f, {"form": form, "argv": cmd[0]}, cmd[1])
        elif cmd_state == "empty":
            rep.check(f, {"form": "cleared", "argv": []}, cleared_at)
        else:
            rep.check(f, None, None)


# ---------------------------------------------------------------- effective command and link evidence


def check_effective_command(root: Path, facts: list[Fact], links: list[Linkage], rep: OracleReport) -> None:
    """Kubernetes rule: `command` replaces ENTRYPOINT and CMD; `args` alone replaces CMD; neither keeps both."""
    by_link = {lk.link_id: lk for lk in links}
    yaml_files = sorted({f.source.path for f in facts if f.kind.startswith("k8s.") and f.source and f.source.path.endswith((".yaml", ".yml"))})
    objs = {o.subject: o for o in load_rt(root, yaml_files)}
    for f in facts:
        if f.kind != "k8s.container.effective_command":
            continue
        parts = f.subject.key.split("/")
        obj = objs.get("/".join(parts[:3]))
        c = _containers(obj).get(parts[3]) if obj else None
        if c is None:
            rep.skip("effective_command: container not re-read")
            continue
        command, args = c.get("command"), c.get("args")
        if any("$(" in str(x) for x in [*(command or []), *(args or [])]):
            # $(VAR) expansion happens at container start; the fact stays unresolved at the field that uses it
            rep.check(f, None, _line(c, "command") if command is not None else _line(c, "args"))
            continue
        if command is not None:
            rep.check(f, [str(x) for x in command] + [str(x) for x in (args or [])], _line(c, "command"))
            continue
        if not f.link_ids:
            rep.check(f, None, None)
            continue
        link = by_link[f.link_ids[0]]
        dpath = next(e.rsplit(":", 1)[0] for e in link.evidence if re.search(r"Dockerfile[^:]*:\d+$", e))
        stages = _bk_stages((root / dpath).read_text(encoding="utf-8-sig", errors="replace"))
        st = stages[-1]
        ep = next((c2 for c2 in reversed(st.cmds) if c2.cmd.upper() == "ENTRYPOINT"), None)
        cmd = next((c2 for c2 in reversed(st.cmds) if c2.cmd.upper() == "CMD"), None)
        if ep is None:
            rep.skip("effective_command: ENTRYPOINT not set in the final stage")
            continue
        # CMD set in the same stage survives ENTRYPOINT; otherwise ENTRYPOINT cleared the inherited CMD
        tail = [str(x) for x in args] if args is not None else (list(cmd.value) if cmd is not None else [])
        ep_argv = list(ep.value) if ep.json else ["/bin/sh", "-c", " ".join(ep.value)]
        rep.check(f, ep_argv + tail, ep.start_line, path=dpath)


def check_links(root: Path, links: list[Linkage], facts: list[Fact], rep: OracleReport) -> None:
    images = {f.subject.key: f for f in facts if f.kind == "k8s.container.image"}
    for link in links:
        for e in link.evidence:
            m = re.match(r"^(.+):(\d+)$", e)
            if not m:
                continue
            path, line = m.group(1), int(m.group(2))
            rep.checked += 1
            try:
                text = (root / path).read_text(encoding="utf-8-sig", errors="replace").splitlines()[line - 1]
            except (OSError, IndexError):
                rep.mismatches.append(f"link {link.from_ref}: evidence {e} does not exist")
                continue
            base = posixpath.basename(path)
            ok = True
            if base.startswith("Dockerfile") or base.endswith(".dockerfile"):
                bk = {c.start_line: c.cmd.upper() for c in dockerfile.parse_string((root / path).read_text(encoding="utf-8-sig", errors="replace"))}
                ok = bk.get(line) in ("FROM", "COPY", "ADD", "EXPOSE")
            elif base == "pom.xml":
                ok = "<artifactId>" in text
            elif base in ("settings.gradle", "settings.gradle.kts"):
                ok = bool(re.search(r"rootProject\.name\s*=\s*['\"][^'\"]+['\"]", text))
            elif base == "pyproject.toml":
                ok = "name" in text and tomllib.loads((root / path).read_text(encoding="utf-8"))["project"]["name"] in text
            elif path.endswith(".py"):
                ok = isinstance(ast.parse(text.strip()).body[0], (ast.Assign, ast.AnnAssign))
            elif link.from_ref in images and images[link.from_ref].source and images[link.from_ref].source.line_start == line:  # type: ignore[union-attr]
                value = str(images[link.from_ref].value)
                ok = value in text
                if not ok and re.search(r"image:\s*[>|][-+]?\s*$", text):  # block scalar: the value starts on the next line
                    nxt = (root / path).read_text(encoding="utf-8-sig", errors="replace").splitlines()[line:line + 3]
                    ok = value in " ".join(x.strip() for x in nxt)
            else:
                ok = bool(re.search(r"containerPort:\s*\d+|app\.\w+|\w+:\w+", text))
            if not ok:
                rep.mismatches.append(f"link {link.from_ref}: evidence {e} does not show what it claims: {text.strip()!r}")


def run_oracle(root: Path, facts: list[Fact], links: list[Linkage]) -> OracleReport:
    rep = OracleReport()
    yaml_files = sorted({f.source.path for f in facts if f.kind.startswith("k8s.") and f.source} |
                        {i.source.path for f in facts for i in f.inputs if i.source and f.kind.startswith("k8s.")})
    docker_files = sorted({f.subject.key[len("docker:"):].split("#", 1)[0] for f in facts if f.kind.startswith("docker.")})
    check_k8s(root, [p for p in yaml_files if p.endswith((".yaml", ".yml"))], facts, rep)
    check_docker(root, docker_files, facts, rep)
    check_effective_command(root, facts, links, rep)
    check_links(root, links, facts, rep)
    return rep


__all__ = ["OracleReport", "json", "run_oracle"]
