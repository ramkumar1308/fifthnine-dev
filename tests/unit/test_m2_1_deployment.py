"""M2-1 deployment facts: answer-key scoring on both samples, semantic oracle, RULES.md fixture cases (X1 to X5, F13,
F14, D5, H1/H3, N6), relationship edge cases, linkage evidence rule, and hostile manifests."""

from __future__ import annotations

import json
import textwrap
from pathlib import Path
from typing import Any

import jsonschema
import pytest

from fifthnine.core.engine import ScanOptions, run_scan
from fifthnine.core.workspace import discover
from fifthnine.output.json_report import render_json
from fifthnine.resolve.deployment import DeploymentResult, resolve_deployment
from fifthnine.schema.v1 import Fact, FactInput, FactStatus, InputRole, LinkStatus, OverrideExposure, SourceRef
from tests.support.answer_keys import FactKey, load
from tests.support.oracle import run_oracle
from tests.support.scorer import score

from ..conftest import ROOT

SAMPLES = ["orders-service-spring", "catalog-api-fastapi"]


def _repo(tmp: Path, files: dict[str, str]) -> Path:
    for rel, text in files.items():
        p = tmp / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(textwrap.dedent(text).lstrip("\n"), encoding="utf-8")
    return tmp


def _resolve(root: Path) -> DeploymentResult:
    return resolve_deployment(discover(root))


def _facts(r: DeploymentResult, kind: str) -> dict[str, Fact]:
    return {f.subject.key: f for f in r.facts.facts() if f.kind == kind}


def _oracle_clean(root: Path, r: DeploymentResult) -> None:
    rep = run_oracle(root, r.facts.facts(), r.linkages)
    assert not rep.mismatches, "\n".join(rep.mismatches)


# ---------------------------------------------------------------- answer keys and oracle on the samples


@pytest.mark.parametrize("sample", SAMPLES)
def test_every_m2_1_key_fact_scores_correct(sample: str) -> None:
    r = _resolve(ROOT / "samples" / sample)
    key = load(ROOT / "benchmarks" / "dev" / "facts" / f"{sample}.facts.yaml", FactKey)
    s = score(key, r.facts.facts(), r.linkages, "M2-1")
    assert s.passed, s.report()
    assert len(s.correct) == sum(f.milestone == "M2-1" for f in key.facts)


@pytest.mark.parametrize("sample", SAMPLES)
def test_semantic_oracle_agrees_on_every_fact(sample: str) -> None:
    root = ROOT / "samples" / sample
    r = _resolve(root)
    rep = run_oracle(root, r.facts.facts(), r.linkages)
    assert not rep.mismatches, "\n".join(rep.mismatches)
    assert set(rep.skipped) <= {"k8s.container.effective_command: checked by check_effective_command"}, rep.skipped
    assert rep.checked >= len(r.facts.facts())


def test_scorer_detects_every_kind_of_error() -> None:
    root = ROOT / "samples" / "orders-service-spring"
    r = _resolve(root)
    key = load(ROOT / "benchmarks" / "dev" / "facts" / "orders-service-spring.facts.yaml", FactKey)
    base = r.facts.facts()
    dep, c = "k8s:Deployment/commerce/orders-service", "k8s:Deployment/commerce/orders-service/orders-service"

    def at(kind: str, subject: str) -> int:
        return next(i for i, f in enumerate(base) if f.kind == kind and f.subject.key == subject)

    grace = base[at("k8s.pod.termination_grace_period", dep)]
    assert grace.default_ref is not None and grace.default_ref.upstream is not None
    cases: list[tuple[int, dict[str, Any], str]] = [
        (at("k8s.deployment.replicas", dep), {"value": 5}, "value"),
        (at("k8s.container.image", c), {"source": SourceRef(path="k8s/deployment.yaml", line_start=26, line_end=26)}, "source"),
        (at("k8s.container.effective_command", c), {"inputs": base[at("k8s.container.effective_command", c)].inputs[:1]}, "inputs"),
        (at("k8s.container.effective_command", c), {"link_ids": []}, "link_ids"),
        (at("k8s.container.resources.requests.cpu", c), {"absent": False}, "absent"),
        (at("docker.build_target", "docker:Dockerfile"), {"status": FactStatus.DECLARED, "unresolved_reason": None}, "status"),
        (at("k8s.pod.termination_grace_period", dep),
         {"default_ref": grace.default_ref.model_copy(update={"upstream": grace.default_ref.upstream.model_copy(update={"line": 1})})}, "default_ref"),
        (at("k8s.container.effective_command", c), {"precedence": "docker.cmd"}, "precedence"),
    ]
    for i, update, field_name in cases:
        facts = list(base)
        facts[i] = facts[i].model_copy(update=update)
        s = score(key, facts, r.linkages, "M2-1")
        assert not s.passed and next(iter(s.wrong.values()))[0] == field_name, (field_name, s.report())
    assert score(key, [f for f in base if f.kind != "k8s.pdb"], r.linkages, "M2-1").missing
    secret = base[at("k8s.container.env_from", c + "#secret:orders-service-secrets")].fact_id
    i = at("k8s.container.env", c + "#SPRING_PROFILES_ACTIVE")
    facts = list(base)
    facts[i] = facts[i].model_copy(update={"inputs": [*facts[i].inputs, FactInput(role=InputRole.VALUE_SOURCE, fact_id=secret)]})
    assert "SP-N02" in score(key, facts, r.linkages, "M2-1").violated
    links = [lk.model_copy(update={"evidence": lk.evidence[1:]}) for lk in r.linkages]
    assert score(key, base, links, "M2-1").links_wrong


def test_oracle_detects_wrong_values_and_lines() -> None:
    root = ROOT / "samples" / "catalog-api-fastapi"
    r = _resolve(root)
    base = r.facts.facts()
    for kind, update in (("k8s.service.port", {"value": {"port": 80, "target_port": "http", "container_port": 8001}}),
                         ("docker.expose", {"source": SourceRef(path="Dockerfile", line_start=8, line_end=8)}),
                         ("k8s.hpa.target", {"value": {"workload": "k8s:Deployment/_/other", "min": 3, "max": 12}}),
                         ("k8s.container.effective_command", {"value": ["gunicorn"]})):
        facts = [f.model_copy(update=update) if f.kind == kind else f for f in base]
        assert run_oracle(root, facts, r.linkages).mismatches, kind


def test_engine_output_names_exposure_sources_and_validates_against_json_schema() -> None:
    for sample in SAMPLES:
        result = run_scan(ScanOptions(path=ROOT / "samples" / sample, facts="all"))
        assert all(f.exposure_sources for f in result.facts if f.override_exposure is not OverrideExposure.NONE)
        doc = json.loads(render_json(result))
        jsonschema.validate(doc, json.loads((ROOT / "schemas" / "result.v1.json").read_text(encoding="utf-8")))
        assert doc["status"] == "INCOMPLETE" and doc["linkages"]


# ---------------------------------------------------------------- RULES.md fixture cases

DEPLOY = """
apiVersion: apps/v1
kind: Deployment
metadata:
  name: web
  namespace: shop
spec:
  replicas: 2
  selector:
    matchLabels: {app: web}
  template:
    metadata:
      labels:
        app: web
    spec:
      containers:
        - name: web
          image: registry.example/web:1.0
"""


def _deploy(container_extra: str = "", spec_extra: str = "", pod_extra: str = "") -> str:
    base = DEPLOY
    if spec_extra:
        base = base.replace("  replicas: 2\n", "  replicas: 2\n" + textwrap.indent(textwrap.dedent(spec_extra).strip("\n"), "  ") + "\n")
    if pod_extra:
        base = base.replace("      containers:\n", textwrap.indent(textwrap.dedent(pod_extra).strip("\n"), "      ") + "\n      containers:\n")
    return base + (textwrap.indent(textwrap.dedent(container_extra).strip("\n"), "          ") + "\n" if container_extra else "")


def test_x1_x2_x3_x4_environment(tmp_path: Path) -> None:
    root = _repo(tmp_path, {
        "k8s/cm.yaml": "apiVersion: v1\nkind: ConfigMap\nmetadata: {name: web-env, namespace: shop}\ndata:\n  A: from-cm\n  B: cm-b\n",
        "k8s/deploy.yaml": _deploy("""
            env:
              - name: A
                value: literal
              - name: C
                valueFrom:
                  secretKeyRef: {name: s, key: c}
              - name: D
                valueFrom:
                  configMapKeyRef: {name: web-env, key: B}
            envFrom:
              - configMapRef: {name: web-env}
              - secretRef: {name: web-secrets}
            """),
    })
    r = _resolve(root)
    env = _facts(r, "k8s.container.env")
    c = "k8s:Deployment/shop/web/web"
    assert env[f"{c}#A"].value == "literal" and env[f"{c}#A"].override_exposure is OverrideExposure.NONE  # X1: env beats envFrom
    assert env[f"{c}#B"].value == "cm-b" and env[f"{c}#B"].override_exposure is OverrideExposure.ENV_POSSIBLE  # X2, later secret may win
    assert env[f"{c}#B"].exposure_sources[0].object == "k8s:Secret/shop/web-secrets"  # X3 named
    assert env[f"{c}#C"].status is FactStatus.UNRESOLVED  # X4
    assert env[f"{c}#D"].value == "cm-b" and env[f"{c}#D"].source is not None and env[f"{c}#D"].source.path == "k8s/cm.yaml"
    model = r.env_models[c]
    assert [s.object for s in model.exposure_for("UNSET_VAR")] == ["k8s:Secret/shop/web-secrets"]
    assert model.exposure_for("A") == []
    _oracle_clean(root, r)


def test_x5_config_mount_at_workdir(tmp_path: Path) -> None:
    root = _repo(tmp_path, {
        "pom.xml": "<project><groupId>g</groupId><artifactId>web</artifactId></project>\n",
        "Dockerfile": "FROM temurin\nWORKDIR /app\nCOPY target/web.jar /app/app.jar\nENTRYPOINT [\"java\", \"-jar\", \"/app/app.jar\"]\n",
        "k8s/deploy.yaml": _deploy("""
            volumeMounts:
              - name: cfg
                mountPath: /app/config
            """, pod_extra="""
            volumes:
              - name: cfg
                configMap: {name: external-cfg}
            """),
    })
    r = _resolve(root)
    mount = _facts(r, "k8s.container.volume_mount")["k8s:Deployment/shop/web/web#/app/config"]
    assert mount.value["config_location"] is True and mount.link_ids
    _oracle_clean(root, r)


@pytest.mark.parametrize(("command", "expected", "linked"), [
    ('command: ["gunicorn", "app.main:app"]', ["gunicorn", "app.main:app"], False),  # F13: command replaces image CMD
    ("", ["uvicorn", "app.main:app", "--reload"], True),  # F14: image CMD runs, through the link
])
def test_f13_f14_effective_command(tmp_path: Path, command: str, expected: list[str], linked: bool) -> None:
    root = _repo(tmp_path, {
        "pyproject.toml": '[project]\nname = "web"\nversion = "1"\n',
        "app/__init__.py": "", "app/main.py": "from fastapi import FastAPI\napp = FastAPI()\n",
        "Dockerfile": 'FROM python:3.12\nWORKDIR /srv\nCOPY app ./app\nCMD ["uvicorn", "app.main:app", "--reload"]\n',
        "k8s/deploy.yaml": _deploy(command),
    })
    r = _resolve(root)
    eff = _facts(r, "k8s.container.effective_command")["k8s:Deployment/shop/web/web"]
    assert eff.value == expected
    assert bool(eff.link_ids) is linked
    if linked:
        assert eff.status is FactStatus.INFERRED  # base image ENTRYPOINT is assumed absent, which is a heuristic
    _oracle_clean(root, r)


def test_d5_recreate_has_no_rolling_defaults(tmp_path: Path) -> None:
    root = _repo(tmp_path, {"k8s/deploy.yaml": _deploy(spec_extra="strategy:\n  type: Recreate\n")})
    r = _resolve(root)
    assert _facts(r, "k8s.deployment.strategy_type")["k8s:Deployment/shop/web"].value == "Recreate"
    assert not _facts(r, "k8s.deployment.max_unavailable") and not _facts(r, "k8s.deployment.max_surge")
    _oracle_clean(root, r)


def test_h1_h3_request_presence_and_limit_default(tmp_path: Path) -> None:
    root = _repo(tmp_path, {"k8s/deploy.yaml": _deploy("resources:\n  limits:\n    cpu: 500m\n  requests:\n    memory: 1Gi\n")})
    r = _resolve(root)
    c = "k8s:Deployment/shop/web/web"
    cpu = _facts(r, "k8s.container.resources.requests.cpu")[c]
    assert cpu.status is FactStatus.DEFAULT and cpu.value == "500m"  # request defaults to limit (verified 1.34 to 1.37)
    mem_limit = _facts(r, "k8s.container.resources.limits.memory")[c]
    assert mem_limit.absent and mem_limit.value is None
    _oracle_clean(root, r)


def test_n6_controller_annotations_and_backend_resolution(tmp_path: Path) -> None:
    root = _repo(tmp_path, {
        "k8s/deploy.yaml": _deploy("ports:\n  - name: http\n    containerPort: 8080\n"),
        "k8s/svc.yaml": "apiVersion: v1\nkind: Service\nmetadata: {name: web, namespace: shop}\nspec:\n  selector: {app: web}\n"
                        "  ports:\n    - name: http\n      port: 80\n      targetPort: http\n",
        "k8s/ing.yaml": "apiVersion: networking.k8s.io/v1\nkind: Ingress\nmetadata:\n  name: web\n  namespace: shop\n  annotations:\n"
                        "    nginx.ingress.kubernetes.io/rewrite-target: /\nspec:\n  rules:\n    - http:\n        paths:\n          - path: /\n"
                        "            pathType: Prefix\n            backend:\n              service:\n                name: web\n"
                        "                port: {name: http}\n",
    })
    r = _resolve(root)
    assert _facts(r, "k8s.ingress.controller_annotations")["k8s:Ingress/shop/web"].value == ["nginx.ingress.kubernetes.io/rewrite-target"]
    route = _facts(r, "k8s.ingress.route")["k8s:Ingress/shop/web#*/"]
    assert route.value["service_port"] == 80 and route.value["container_port"] == 8080
    svc = _facts(r, "k8s.service.selects")["k8s:Service/shop/web"]
    assert svc.value == {"type": "ClusterIP", "workloads": ["k8s:Deployment/shop/web"]}
    assert any(i.fact_id for i in svc.inputs)  # type came from the verified default
    _oracle_clean(root, r)


def test_namespace_set_versus_unset_is_unresolved_not_assumed(tmp_path: Path) -> None:
    root = _repo(tmp_path, {
        "k8s/deploy.yaml": _deploy(),
        "k8s/svc.yaml": "apiVersion: v1\nkind: Service\nmetadata: {name: web}\nspec:\n  selector: {app: web}\n  ports:\n    - port: 80\n",
        "k8s/hpa.yaml": "apiVersion: autoscaling/v2\nkind: HorizontalPodAutoscaler\nmetadata: {name: web}\nspec:\n"
                        "  scaleTargetRef: {apiVersion: apps/v1, kind: Deployment, name: web}\n  maxReplicas: 3\n",
        "k8s/pdb.yaml": "apiVersion: policy/v1\nkind: PodDisruptionBudget\nmetadata: {name: web, namespace: shop}\nspec:\n"
                        "  minAvailable: 1\n  selector:\n    matchExpressions:\n      - {key: app, operator: In, values: [web]}\n",
    })
    r = _resolve(root)
    assert _facts(r, "k8s.service.selects")["k8s:Service/_/web"].status is FactStatus.UNRESOLVED
    assert _facts(r, "k8s.hpa.target")["k8s:HorizontalPodAutoscaler/_/web"].status is FactStatus.UNRESOLVED
    assert _facts(r, "k8s.hpa.min_replicas")["k8s:HorizontalPodAutoscaler/_/web"].status is FactStatus.DEFAULT
    assert _facts(r, "k8s.pdb")["k8s:PodDisruptionBudget/shop/web"].status is FactStatus.UNRESOLVED


def test_command_with_variable_expansion_is_unresolved(tmp_path: Path) -> None:
    root = _repo(tmp_path, {"k8s/deploy.yaml": _deploy('command: ["sh", "-c", "run --port $(PORT)"]')})
    eff = _facts(_resolve(root), "k8s.container.effective_command")["k8s:Deployment/shop/web/web"]
    assert eff.status is FactStatus.UNRESOLVED


# ---------------------------------------------------------------- linkage evidence rule (unchanged, M2-1-D1)


@pytest.mark.parametrize(("files", "reason"), [
    ({"pom.xml": "<project><artifactId>web</artifactId></project>\n"}, "buildpacks"),
    ({"pom.xml": "<project><artifactId>web</artifactId></project>\n", "Dockerfile": "FROM a\nEXPOSE 8080\n",
      "ops/Dockerfile": "FROM b\nCOPY target/web.jar /a.jar\n"}, "several Dockerfiles"),
    ({"pom.xml": "<project><artifactId>other</artifactId></project>\n", "Dockerfile": "FROM a\n"}, "no in-repository build identity"),
    ({"pom.xml": "<project><artifactId>web</artifactId></project>\n", "Dockerfile": "FROM a\nEXPOSE 9000\n"}, "not exposed"),
])
def test_linkage_requires_the_full_evidence_rule(tmp_path: Path, files: dict[str, str], reason: str) -> None:
    root = _repo(tmp_path, {**files, "k8s/deploy.yaml": _deploy("ports:\n  - containerPort: 8080\n")})
    r = _resolve(root)
    (link,) = r.linkages
    assert link.status is LinkStatus.UNRESOLVED and any(reason in e for e in link.evidence), link.evidence
    eff = _facts(r, "k8s.container.effective_command")["k8s:Deployment/shop/web/web"]
    assert eff.status is FactStatus.UNRESOLVED  # no image link, so the image ENTRYPOINT/CMD are not claimed


def test_linkage_evidence_states_the_default_target_assumption(tmp_path: Path) -> None:
    root = _repo(tmp_path, {
        "pom.xml": "<project><artifactId>web</artifactId></project>\n",
        "Dockerfile": "FROM maven AS build\nRUN mvn package\nFROM temurin\nCOPY --from=build /t/web-1.jar /app.jar\nEXPOSE 8080\n"
                      "ENTRYPOINT [\"java\", \"-jar\", \"/app.jar\"]\n",
        "k8s/deploy.yaml": _deploy("ports:\n  - containerPort: 8080\n"),
    })
    r = _resolve(root)
    (link,) = r.linkages
    assert link.status is LinkStatus.INFERRED
    assert "Dockerfile:3" in link.evidence and any("default (final) stage" in e for e in link.evidence)
    bt = _facts(r, "docker.build_target")["docker:Dockerfile"]
    assert bt.status is FactStatus.UNRESOLVED and bt.value is None
    _oracle_clean(root, r)


# ---------------------------------------------------------------- hostile manifests


@pytest.mark.parametrize("text", [
    "apiVersion: v1\nkind: Service\nmetadata: {name: a}\nmetadata: {name: b}\n",  # duplicate key
    "apiVersion: v1\nkind: ConfigMap\nmetadata: &m {name: a}\ndata: *m\n",  # reused anchor
    "apiVersion: v1\nkind: ConfigMap\nmetadata: {name: a}\ndata: " + "[" * 5000 + "]" * 5000 + "\n",  # deep nesting
])
def test_hostile_manifests_are_skipped_not_fatal(tmp_path: Path, text: str) -> None:
    root = _repo(tmp_path, {"k8s/bad.yaml": text, "k8s/deploy.yaml": _deploy()})
    r = _resolve(root)
    assert "k8s:Deployment/shop/web" in _facts(r, "k8s.deployment.replicas")
    assert not [f for f in r.facts.facts() if f.subject.key.startswith(("k8s:Service/_/", "k8s:ConfigMap/_/"))]


# ---------------------------------------------------------------- regressions found by the corpus oracle run


def test_yaml_null_fields_count_as_unset(tmp_path: Path) -> None:
    root = _repo(tmp_path, {
        "k8s/deploy.yaml": _deploy(),
        "k8s/svc.yaml": "apiVersion: v1\nkind: Service\nmetadata: {name: web, namespace: shop}\nspec:\n  type: null\n"
                        "  selector: {app: web}\n  ports:\n    - port: 80\n",
        "k8s/other.yaml": "apiVersion: v1\nkind: ConfigMap\nmetadata:\n  name: other\n  namespace:\ndata: {A: b}\n",
    })
    r = _resolve(root)
    svc = _facts(r, "k8s.service.selects")["k8s:Service/shop/web"]
    assert svc.value["type"] == "ClusterIP"  # explicit null is defaulted like an omitted field
    assert _facts(r, "k8s.service.type")["k8s:Service/shop/web"].status is FactStatus.DEFAULT
    ns = _facts(r, "k8s.object.namespace")["k8s:ConfigMap/_/other"]
    assert ns.status is FactStatus.UNRESOLVED and ns.value is None
    _oracle_clean(root, r)


def test_repeated_copy_from_destination_keeps_every_instruction(tmp_path: Path) -> None:
    root = _repo(tmp_path, {
        "Dockerfile": "FROM maven AS a\nRUN x\nFROM maven AS b\nRUN y\nFROM temurin\n"
                      "COPY --from=a /out/one.jar /app\nCOPY --from=b /out/two.jar /app\n",
    })
    r = _resolve(root)
    copies = _facts(r, "docker.copy_from_stage")
    assert copies["docker:Dockerfile#2>/app"].value["from_stage"] == "a"
    assert copies["docker:Dockerfile#2>/app~2"].value["from_stage"] == "b"
    _oracle_clean(root, r)


def test_named_target_port_without_a_matching_container_port_is_unresolved(tmp_path: Path) -> None:
    root = _repo(tmp_path, {
        "k8s/deploy.yaml": _deploy("ports:\n  - name: http\n    containerPort: 8080\n"),
        "k8s/svc.yaml": "apiVersion: v1\nkind: Service\nmetadata: {name: web, namespace: shop}\nspec:\n  selector: {app: web}\n"
                        "  ports:\n    - name: web\n      port: 80\n      targetPort: missing-name\n",
    })
    r = _resolve(root)
    port = _facts(r, "k8s.service.port")["k8s:Service/shop/web#web"]
    assert port.status is FactStatus.UNRESOLVED and port.value is None
    _oracle_clean(root, r)


def test_docker_env_quotes_are_removed_as_the_builder_does(tmp_path: Path) -> None:
    root = _repo(tmp_path, {"Dockerfile": 'FROM alpine\nENV P="/a b:/c" Q=plain\n'})
    r = _resolve(root)
    env = _facts(r, "docker.env")
    assert env["docker:Dockerfile#0#P"].value == "/a b:/c" and env["docker:Dockerfile#0#Q"].value == "plain"
    _oracle_clean(root, r)


def test_placeholder_image_reference_is_not_linked(tmp_path: Path) -> None:
    root = _repo(tmp_path, {
        "settings.gradle": "rootProject.name = 'web'\n",
        "Dockerfile": "FROM temurin\nCOPY build/libs/web-1.jar /app.jar\nENTRYPOINT [\"java\", \"-jar\", \"/app.jar\"]\n",
        "k8s/deploy.yaml": _deploy().replace("image: ", "image: <registry>/web:<tag> # was ", 1),
    })
    r = _resolve(root)
    (link,) = r.linkages
    assert link.status is LinkStatus.UNRESOLVED and any("not a valid image reference" in e for e in link.evidence)


def test_placeholder_token_in_a_plain_manifest_is_read_but_helm_templates_are_not(tmp_path: Path) -> None:
    root = _repo(tmp_path, {
        "k8s/deploy.yaml": _deploy('env:\n  - name: TOKEN\n    value: "{{HF_TOKEN}}"\n'),
        "chart/templates/deploy.yaml": _deploy().replace("name: web", "name: {{ .Values.name }}"),
    })
    r = _resolve(root)
    assert "k8s:Deployment/shop/web" in _facts(r, "k8s.deployment.replicas")
    assert _facts(r, "k8s.container.env")["k8s:Deployment/shop/web/web#TOKEN"].value == "{{HF_TOKEN}}"
    assert "chart/templates/deploy.yaml" not in r.parsed
    _oracle_clean(root, r)


def test_invalid_utf8_dockerfile_and_manifest_do_not_crash(tmp_path: Path) -> None:
    (tmp_path / "Dockerfile").write_bytes(b"FROM alpine\nENV A=\xff\xfe\nCMD [\"\xc3\x28\"]\n")
    (tmp_path / "deploy.yaml").write_bytes(_deploy().encode() + b"          # \xff\n")
    r = _resolve(tmp_path)
    assert "Dockerfile" in r.parsed
