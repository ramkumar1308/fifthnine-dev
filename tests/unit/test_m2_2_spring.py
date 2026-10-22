"""M2-2: Spring effective configuration, profiles, versioned defaults and actuator endpoints.

Fixtures are built in a temporary directory: a Maven module `app`, a Dockerfile that builds it, and a Deployment whose
image links to it (an inferred link, as in M2-1). Cases follow RULES.md F1 to F11 (profiles), X1 to X8 (container
sources), R1 to R14 (actuator paths) and S1 to S15 (version and override matrix).
"""

from __future__ import annotations

import json
import textwrap
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest

from fifthnine.core.engine import ScanOptions, run_scan
from fifthnine.schema.v1 import Fact, FactStatus, OverrideExposure, ScanResult
from tests.support.answer_keys import FactKey, load
from tests.support.oracle import OracleReport
from tests.support.scorer import score
from tests.support.spring_oracle import check_spring

from ..conftest import ROOT

POM = """<?xml version="1.0" encoding="UTF-8"?>
<project xmlns="http://maven.apache.org/POM/4.0.0">
  <modelVersion>4.0.0</modelVersion>
  <parent>
    <groupId>org.springframework.boot</groupId>
    <artifactId>spring-boot-starter-parent</artifactId>
    <version>{boot}</version>
  </parent>
  <artifactId>app</artifactId>
  <version>1.0</version>
{extra}</project>
"""
DOCKERFILE = """FROM maven:3.9-eclipse-temurin-21 AS build
WORKDIR /src
COPY pom.xml .
COPY src ./src
RUN mvn -q -B package

FROM eclipse-temurin:21-jre
WORKDIR /app
COPY --from=build /src/target/app-*.jar /app/app.jar
EXPOSE 8080 8081
{env}{entry}"""
DEPLOYMENT = """apiVersion: apps/v1
kind: Deployment
metadata:
  name: app
  namespace: shop
spec:
  replicas: 2
  selector:
    matchLabels: {{app: app}}
  template:
    metadata:
      labels: {{app: app}}
    spec:
{volumes}      containers:
        - name: app
          image: ghcr.io/acme/app:1.0
{command}          ports:
            - containerPort: 8080
            - containerPort: 8081
{env}{env_from}{mounts}"""
CONFIGMAP = """apiVersion: v1
kind: ConfigMap
metadata:
  name: app-config
  namespace: shop
data:
{data}"""


@dataclass
class Repo:
    boot: str = "3.4.13"
    resources: dict[str, str] = field(default_factory=dict)
    env: dict[str, str | None] = field(default_factory=dict)  # None marks a valueFrom reference
    config_map: dict[str, str] | None = None  # envFrom an in-repository ConfigMap
    secret: bool = False  # envFrom a Secret that is not in the repository
    command: list[str] | None = None
    docker_env: str = ""
    linked: bool = True
    mount: str | None = None  # mountPath of an out-of-repository ConfigMap volume
    pom_extra: str = ""
    entrypoint: str = 'ENTRYPOINT ["java", "-jar", "/app/app.jar"]\n'

    def write(self, tmp: Path) -> Path:
        (tmp / "pom.xml").write_text(POM.format(boot=self.boot, extra=self.pom_extra))
        for rel, text in self.resources.items():
            p = tmp / "src/main/resources" / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(textwrap.dedent(text).lstrip("\n"), encoding="utf-8")
        if self.linked:
            (tmp / "Dockerfile").write_text(DOCKERFILE.format(env=self.docker_env, entry=self.entrypoint))
            env = ""
            if self.env:
                env = "          env:\n"
                for k, v in self.env.items():
                    env += (f"            - name: {k}\n              value: {json.dumps(v)}\n" if v is not None else
                            f"            - name: {k}\n              valueFrom:\n                secretKeyRef: {{name: other, key: k}}\n")
            env_from = ""
            if self.config_map is not None or self.secret:
                env_from = "          envFrom:\n"
                if self.config_map is not None:
                    env_from += "            - configMapRef: {name: app-config}\n"
                if self.secret:
                    env_from += "            - secretRef: {name: app-secrets}\n"
            command = ""
            if self.command is not None:
                command = "          args: " + json.dumps(self.command) + "\n"
            volumes = mounts = ""
            if self.mount:
                volumes = "      volumes:\n        - name: cfg\n          configMap: {name: external}\n"
                mounts = f"          volumeMounts:\n            - {{name: cfg, mountPath: {self.mount}}}\n"
            (tmp / "deploy.yaml").write_text(DEPLOYMENT.format(env=env, env_from=env_from, command=command, volumes=volumes, mounts=mounts))
            if self.config_map is not None:
                data = "".join(f"  {k}: {json.dumps(v)}\n" for k, v in self.config_map.items())
                (tmp / "cm.yaml").write_text(CONFIGMAP.format(data=data))
        return tmp


def _scan(tmp: Path, repo: Repo, profile: str | None = None, *, oracle: bool = True) -> ScanResult:
    r = run_scan(ScanOptions(path=repo.write(tmp), profile=profile, facts="all"))
    if oracle:
        _oracle_clean(tmp, r, profile)
    return r


def _oracle_clean(root: Path, r: ScanResult, profile: str | None = None) -> None:
    rep = OracleReport()
    check_spring(root, r.facts, r.linkages, rep, option=[p.strip() for p in profile.split(",")] if profile else None)
    assert not rep.mismatches, "\n".join(rep.mismatches)


def _props(r: ScanResult, *, suffix: str | None = None) -> dict[str, Fact]:
    out: dict[str, Fact] = {}
    for f in r.facts:
        if f.kind == "spring.property" and f.status is not FactStatus.OVERRIDDEN:
            out[f.subject.key.split(":", 2)[2]] = f
    return out


def _one(r: ScanResult, kind: str, subject: str) -> Fact:
    hits = [f for f in r.facts if f.kind == kind and f.subject.key == subject and f.status is not FactStatus.OVERRIDDEN]
    assert len(hits) == 1, [f.subject.key for f in r.facts if f.kind == kind]
    return hits[0]


def _profiles(r: ScanResult) -> Fact:
    return _one(r, "spring.active_profiles", "spring:.")


def _endpoint(r: ScanResult, group: str = "readiness") -> Fact:
    return _one(r, "spring.actuator.endpoint", f"spring:.:health/{group}")


def _notices(r: ScanResult) -> str:
    return "\n".join(f"{c.scope}: {c.message}" for c in r.coverage)


# ---------------------------------------------------------------- the sample, scored against its answer key


def test_orders_sample_scores_correct_against_the_key() -> None:
    root = ROOT / "samples" / "orders-service-spring"
    r = run_scan(ScanOptions(path=root, facts="all"))
    key = load(ROOT / "benchmarks" / "dev" / "facts" / "orders-service-spring.facts.yaml", FactKey)
    _oracle_clean(root, r)
    s = score(key, [f for f in r.facts], r.linkages, "M2-2")
    assert s.passed, s.report()
    assert not s.violated
    assert sum(f.milestone in ("M2-1", "M2-2") for f in key.facts) == len(s.correct)


# ---------------------------------------------------------------- profiles (F1 to F11)


def test_f1_f3_prod_file_wins_dev_file_is_not_read(tmp_path: Path) -> None:
    repo = Repo(resources={
        "application.yml": "management:\n  endpoints:\n    web:\n      exposure:\n        include: health\n",
        "application-prod.yml": "management:\n  endpoints:\n    web:\n      exposure:\n        include: '*'\n",
        "application-dev.yml": "server:\n  error:\n    include-stacktrace: always\n",
    }, env={"SPRING_PROFILES_ACTIVE": "prod"})
    r = _scan(tmp_path, repo)
    assert _profiles(r).value == ["prod"]
    include = _props(r)["management.endpoints.web.exposure.include"]
    assert include.value == ["*"] and include.source and include.source.path.endswith("application-prod.yml")
    assert include.scope == {"profile": "prod"} and include.link_ids
    assert "server.error.include-stacktrace" not in {k for k, f in _props(r).items() if f.status is not FactStatus.DEFAULT}
    shadowed = [f for f in r.facts if f.status is FactStatus.OVERRIDDEN]
    assert [f.value for f in shadowed] == [["health"]] and shadowed[0].scope == {"profile": "default"}
    assert shadowed[0].overridden_by == include.fact_id


def test_f4_no_profile_signal_uses_default_and_says_so(tmp_path: Path) -> None:
    repo = Repo(resources={"application.yml": "server:\n  port: 8080\n", "application-prod.yml": "server:\n  port: 9999\n"})
    r = _scan(tmp_path, repo)
    p = _profiles(r)
    assert p.value == [] and p.status is FactStatus.DEFAULT and p.override_exposure is OverrideExposure.NONE
    assert _props(r)["server.port"].value == 8080 and _props(r)["server.port"].scope == {"profile": "default"}
    assert "uses the default profile" in _notices(r) and "application-prod.yml" in _notices(r)


def test_f5_attached_secret_could_set_the_profile(tmp_path: Path) -> None:
    repo = Repo(resources={"application.yml": "server:\n  port: 8080\n"}, secret=True)
    r = _scan(tmp_path, repo)
    p = _profiles(r)
    assert p.override_exposure is OverrideExposure.ENV_POSSIBLE
    assert [s.object for s in p.exposure_sources] == ["k8s:Secret/shop/app-secrets"]
    assert "SPRING_PROFILES_ACTIVE" in p.exposure_sources[0].variables
    assert "could still activate one" in _notices(r)


def test_f6_on_profile_document_is_used_only_when_active(tmp_path: Path) -> None:
    yml = "server:\n  port: 8080\n---\nspring:\n  config:\n    activate:\n      on-profile: prod\nserver:\n  port: 9090\n"
    (tmp_path / "a").mkdir()
    (tmp_path / "b").mkdir()
    on = _scan(tmp_path / "a", Repo(resources={"application.yml": yml}, env={"SPRING_PROFILES_ACTIVE": "prod"}))
    port = _props(on)["server.port"]
    assert port.value == 9090 and port.scope == {"profile": "prod"}
    off = _scan(tmp_path / "b", Repo(resources={"application.yml": yml}, env={"SPRING_PROFILES_ACTIVE": "dev"}))
    assert _props(off)["server.port"].value == 8080


def test_f7_later_profile_wins(tmp_path: Path) -> None:
    repo = Repo(resources={
        "application-prod.yml": "server:\n  port: 1111\n", "application-eu.yml": "server:\n  port: 2222\n",
    }, env={"SPRING_PROFILES_ACTIVE": "prod,eu"})
    r = _scan(tmp_path, repo)
    assert _profiles(r).value == ["prod", "eu"]
    assert _props(r)["server.port"].value == 2222
    assert [f.value for f in r.facts if f.status is FactStatus.OVERRIDDEN and f.subject.key.endswith("server.port")] == [1111]


def test_f8_scan_option_beats_deployment_and_records_the_conflict(tmp_path: Path) -> None:
    repo = Repo(resources={"application-staging.yml": "server:\n  port: 7000\n", "application-prod.yml": "server:\n  port: 9000\n"},
                env={"SPRING_PROFILES_ACTIVE": "prod"})
    r = _scan(tmp_path, repo, profile="staging")
    assert _profiles(r).value == ["staging"] and _profiles(r).precedence == "fifthnine.profile_option"
    assert _props(r)["server.port"].value == 7000
    assert "told to assume profile(s) staging" in _notices(r) and "prod" in _notices(r)


def test_f9_profile_groups_expand_depth_first(tmp_path: Path) -> None:
    repo = Repo(resources={
        "application.yml": "spring:\n  profiles:\n    group:\n      prod: [prod-db, prod-obs]\n      prod-db: [pool]\n",
        "application-prod-db.yml": "server:\n  port: 1\n", "application-prod-obs.yml": "server:\n  port: 2\n",
        "application-pool.yml": "server:\n  port: 3\n",
    }, env={"SPRING_PROFILES_ACTIVE": "prod"})
    r = _scan(tmp_path, repo)
    assert _profiles(r).value == ["prod", "prod-db", "pool", "prod-obs"]
    assert _props(r)["server.port"].value == 2


def test_profile_from_a_base_file_and_include(tmp_path: Path) -> None:
    repo = Repo(resources={
        "application.yml": "spring:\n  profiles:\n    active: eu\n    include: metrics\n",
        "application-eu.yml": "server:\n  port: 1\n", "application-metrics.yml": "server:\n  port: 2\n",
    })
    r = _scan(tmp_path, repo)
    assert _profiles(r).value == ["metrics", "eu"] and _profiles(r).precedence == "spring.packaged_application_file"
    assert _props(r)["server.port"].value == 1


def test_f10_config_import_is_reported_and_marks_file_exposure(tmp_path: Path) -> None:
    repo = Repo(resources={"application.yml": "spring:\n  config:\n    import: optional:configserver:http://cfg:8888\nserver:\n  port: 8080\n"})
    r = _scan(tmp_path, repo)
    port = _props(r)["server.port"]
    assert any(s.via == "volume" and "configserver" in (s.object or "") for s in port.exposure_sources)
    assert "spring.config.import" in _notices(r)


def test_f11_properties_file_beats_yaml_in_the_same_location(tmp_path: Path) -> None:
    repo = Repo(resources={"application.yml": "server:\n  port: 8080\n", "application.properties": "server.port=8181\n"})
    r = _scan(tmp_path, repo)
    port = _props(r)["server.port"]
    assert port.value == 8181 and port.source and port.source.path.endswith("application.properties")
    shadow = next(f for f in r.facts if f.status is FactStatus.OVERRIDDEN)
    assert shadow.value == 8080


def test_config_directory_beats_classpath_root(tmp_path: Path) -> None:
    repo = Repo(resources={"application.yml": "server:\n  port: 1\n", "config/application.yml": "server:\n  port: 2\n"})
    assert _props(_scan(tmp_path, repo))["server.port"].value == 2


def test_profile_specific_file_cannot_select_profiles(tmp_path: Path) -> None:
    repo = Repo(resources={"application-prod.yml": "spring:\n  profiles:\n    include: x\nserver:\n  port: 5\n", "application-x.yml": "server:\n  port: 6\n"},
                env={"SPRING_PROFILES_ACTIVE": "prod"})
    r = _scan(tmp_path, repo)
    assert _profiles(r).value == ["prod"] and "not allowed in a profile-specific file" in _notices(r)


def test_legacy_spring_profiles_selector_is_rejected_like_boot_3(tmp_path: Path) -> None:
    repo = Repo(resources={"application.yml": "server:\n  port: 1\n---\nspring:\n  profiles: prod\nserver:\n  port: 2\n"},
                env={"SPRING_PROFILES_ACTIVE": "prod"})
    r = _scan(tmp_path, repo)
    assert _props(r)["server.port"].value == 1 and "spring.profiles is not accepted" in _notices(r)


def test_unresolvable_active_profile_is_unresolved_not_guessed(tmp_path: Path) -> None:
    repo = Repo(resources={"application.yml": "spring:\n  profiles:\n    active: ${APP_PROFILE}\nserver:\n  port: 8080\n"})
    r = _scan(tmp_path, repo)
    p = _profiles(r)
    assert p.status is FactStatus.UNRESOLVED and p.value is None
    assert _props(r)["server.port"].status is FactStatus.INFERRED


# ---------------------------------------------------------------- container sources (X1 to X8)


def test_x1_literal_env_beats_file_via_relaxed_binding(tmp_path: Path) -> None:
    repo = Repo(resources={"application.yml": "server:\n  port: 8080\n  shutdown: immediate\n"}, env={"SERVER_PORT": "9000"})
    r = _scan(tmp_path, repo)
    port = _props(r)["server.port"]
    assert port.value == 9000 and port.precedence == "spring.os_environment" and port.source and port.source.path == "deploy.yaml"
    assert any(f.status is FactStatus.OVERRIDDEN and f.value == 8080 for f in r.facts)
    assert _props(r)["server.shutdown"].value == "immediate"


def test_x2_x3_in_repo_config_map_and_later_secret(tmp_path: Path) -> None:
    repo = Repo(resources={"application.yml": "server:\n  port: 8080\n"}, config_map={"SERVER_PORT": "9100"}, secret=True)
    port = _props(_scan(tmp_path, repo))["server.port"]
    assert port.value == 9100 and port.source and port.source.path == "cm.yaml"
    assert port.override_exposure is OverrideExposure.ENV_POSSIBLE
    assert [s.object for s in port.exposure_sources] == ["k8s:Secret/shop/app-secrets"]
    assert port.exposure_sources[0].variables[0] == "SERVER_PORT"


def test_x4_value_from_unknown_source_makes_the_key_unresolved(tmp_path: Path) -> None:
    repo = Repo(resources={"application.yml": "clients:\n  pay:\n    base-url: ${PAY_URL:http://localhost}\n"}, env={"PAY_URL": None})
    f = _props(_scan(tmp_path, repo))["clients.pay.base-url"]
    assert f.status is FactStatus.UNRESOLVED and f.value is None and f.unresolved_reason


def test_x5_mounted_config_directory_makes_every_key_file_possible(tmp_path: Path) -> None:
    repo = Repo(resources={"application.yml": "server:\n  port: 8080\n"}, mount="/app/config")
    port = _props(_scan(tmp_path, repo))["server.port"]
    assert port.override_exposure is OverrideExposure.FILE_POSSIBLE
    assert [(s.via, s.object) for s in port.exposure_sources] == [("volume", "k8s:ConfigMap/shop/external")]


def test_x6_config_location_variable_is_file_exposure(tmp_path: Path) -> None:
    repo = Repo(resources={"application.yml": "server:\n  port: 8080\n"}, env={"SPRING_CONFIG_LOCATION": "file:/etc/app/"})
    r = _scan(tmp_path, repo)
    port = _props(r)["server.port"]
    assert port.override_exposure is OverrideExposure.FILE_POSSIBLE and "SPRING_CONFIG_LOCATION" in port.exposure_sources[0].object  # type: ignore[operator]
    assert "changes where Spring Boot reads configuration" in _notices(r)


def test_x7_application_json_beats_os_environment(tmp_path: Path) -> None:
    repo = Repo(resources={"application.yml": "server:\n  port: 8080\n"},
                env={"SERVER_PORT": "9000", "SPRING_APPLICATION_JSON": '{"server":{"port":9500},"management":{"server":{"port":9501}}}'})
    props = _props(_scan(tmp_path, repo))
    assert props["server.port"].value == 9500 and props["server.port"].precedence == "spring.application_json"
    assert props["management.server.port"].value == 9501


def test_source_order_command_line_json_system_property_environment_file(tmp_path: Path) -> None:
    yml = "server:\n  port: 1\n"
    env = {"SERVER_PORT": "2", "JAVA_TOOL_OPTIONS": "-Dserver.port=3", "SPRING_APPLICATION_JSON": '{"server":{"port":4}}'}
    expected = [(["--server.port=5"], 5, "spring.command_line"), (None, 4, "spring.application_json")]
    for n, (args, value, rule) in enumerate(expected):
        (tmp_path / str(n)).mkdir()
        f = _props(_scan(tmp_path / str(n), Repo(resources={"application.yml": yml}, env=env, command=args)))["server.port"]
        assert (f.value, f.precedence) == (value, rule)
    (tmp_path / "s").mkdir()
    no_json = {k: v for k, v in env.items() if k != "SPRING_APPLICATION_JSON"}
    f = _props(_scan(tmp_path / "s", Repo(resources={"application.yml": yml}, env=no_json)))["server.port"]
    assert (f.value, f.precedence) == (3, "spring.system_property")
    (tmp_path / "e").mkdir()
    f = _props(_scan(tmp_path / "e", Repo(resources={"application.yml": yml}, env={"SERVER_PORT": "2"})))["server.port"]
    assert (f.value, f.precedence) == (2, "spring.os_environment")
    shadows = sorted(g.value for g in _scan(tmp_path / "e", Repo(resources={"application.yml": yml}, env={"SERVER_PORT": "2"})).facts
                     if g.status is FactStatus.OVERRIDDEN)
    assert shadows == [1]


def test_default_profile_file_is_read_when_no_profile_is_active(tmp_path: Path) -> None:
    res = {"application.yml": "server:\n  port: 8080\n", "application-default.yml": "server:\n  port: 7000\n"}
    f = _props(_scan(tmp_path, Repo(resources=res)))["server.port"]
    assert f.value == 7000
    (tmp_path / "p").mkdir()
    f = _props(_scan(tmp_path / "p", Repo(resources=res), profile="prod"))["server.port"]
    assert f.value == 8080  # an active profile replaces "default"


def test_spring_profiles_default_replaces_the_default_profile(tmp_path: Path) -> None:
    res = {"application.yml": "spring:\n  profiles:\n    default: dev\nserver:\n  port: 8080\n",
           "application-dev.yml": "server:\n  port: 7001\n", "application-default.yml": "server:\n  port: 7000\n"}
    r = _scan(tmp_path, Repo(resources=res))
    assert _props(r)["server.port"].value == 7001
    active = _one(r, "spring.active_profiles", "spring:.")
    assert active.value == ["dev"]
    (tmp_path / "a").mkdir()
    f = _props(_scan(tmp_path / "a", Repo(resources=res), profile="prod"))["server.port"]
    assert f.value == 8080  # an active profile ends the default profile
    (tmp_path / "e").mkdir()
    f = _props(_scan(tmp_path / "e", Repo(resources={**res, "application.yml": "server:\n  port: 8080\n"},
                                          env={"SPRING_PROFILES_DEFAULT": "dev"})))["server.port"]
    assert f.value == 7001
    (tmp_path / "g").mkdir()
    grp = {**res, "application.yml": "spring:\n  profiles:\n    default: dev\n    group:\n      dev: extra\n",
           "application-extra.yml": "server:\n  port: 7002\n"}
    assert _props(_scan(tmp_path / "g", Repo(resources=grp)))["server.port"].value == 7002


def test_spring_profiles_default_that_cannot_be_read_makes_profile_facts_unresolved(tmp_path: Path) -> None:
    res = {"application.yml": "server:\n  port: 8080\n", "application-dev.yml": "server:\n  port: 7001\n"}
    r = _scan(tmp_path, Repo(resources=res, env={"SPRING_PROFILES_DEFAULT": None}), oracle=False)
    active = _one(r, "spring.active_profiles", "spring:.")
    assert active.status is FactStatus.UNRESOLVED
    assert _props(r)["server.port"].status is not FactStatus.DECLARED  # never a silently derived value
    (tmp_path / "f").mkdir()
    picked = {"application.yml": "spring:\n  profiles:\n    default: ${PICK}\n", "application-dev.yml": res["application-dev.yml"]}
    r2 = _scan(tmp_path / "f", Repo(resources=picked), oracle=False)
    assert _one(r2, "spring.active_profiles", "spring:.").status is FactStatus.UNRESOLVED
    (tmp_path / "p").mkdir()
    inside = {"application.yml": "server:\n  port: 8080\n", "application-dev.yml": "spring:\n  profiles:\n    default: dev\n"}
    r3 = _scan(tmp_path / "p", Repo(resources=inside))
    assert _one(r3, "spring.active_profiles", "spring:.").value == []  # a profile-specific file cannot select the default profile


def test_dash_d_after_dash_jar_is_a_program_argument(tmp_path: Path) -> None:
    res = {"application.yml": "server:\n  port: 8080\n"}
    f = _props(_scan(tmp_path, Repo(resources=res, command=["-Dserver.port=9999"])))["server.port"]
    assert f.value == 8080  # entrypoint is "java -jar app.jar": -D is not a JVM option
    (tmp_path / "a").mkdir()
    r = Repo(resources=res, entrypoint='ENTRYPOINT ["java"]\nCMD ["-Dserver.port=9999", "-jar", "/app/app.jar"]\n')
    f = _props(_scan(tmp_path / "a", r))["server.port"]
    assert (f.value, f.precedence) == (9999, "spring.system_property")


def test_dockerfile_entrypoint_options_are_read_and_inferred(tmp_path: Path) -> None:
    ep = 'ENTRYPOINT ["java", "-Dserver.port=9999", "-jar", "/app/app.jar", "--management.server.port=9100"]\n'
    r = _scan(tmp_path, Repo(resources={"application.yml": "server:\n  port: 8080\n"}, entrypoint=ep))
    props = _props(r)
    assert (props["server.port"].value, props["server.port"].status) == (9999, FactStatus.INFERRED)
    assert props["management.server.port"].value == 9100
    (tmp_path / "n").mkdir()
    r2 = _scan(tmp_path / "n", Repo(resources={"application.yml": "server:\n  port: 8080\n"}, entrypoint="CMD [\"java\", \"-jar\", \"/app/app.jar\"]\n"))
    assert any("ENTRYPOINT" in n.message for n in r2.coverage)


def test_legacy_underscore_environment_names(tmp_path: Path) -> None:
    res = {"application.yml": "server:\n  servlet:\n    context-path: /a\n"}
    env = {"SERVER_SERVLET_CONTEXT_PATH": "/b"}
    f = _props(_scan(tmp_path, Repo(resources=res, env=env)))["server.servlet.context-path"]
    assert (f.value, f.precedence) == ("/b", "spring.os_environment")
    res2 = {"application.yml": "app:\n  timeout: ${x-y:1}\n"}
    (tmp_path / "p").mkdir()
    g = _props(_scan(tmp_path / "p", Repo(resources=res2, env={"X_Y": "2"})))["app.timeout"]
    assert g.value in (2, "2")


def test_boolean_words_and_empty_values_follow_spring_conversion(tmp_path: Path) -> None:
    res = {"application.properties": "management.endpoint.health.probes.enabled=yes\nmanagement.endpoints.web.exposure.include=\nserver.port=\n"
                                     "management.server.port=8080\n"}
    r = _scan(tmp_path, Repo(resources=res))
    props = _props(r)
    assert props["management.endpoint.health.probes.enabled"].value is True
    assert "server.port" not in props or props["server.port"].status is FactStatus.DEFAULT
    ep = [f for f in r.facts if f.kind == "spring.actuator.endpoint" and f.status is not FactStatus.OVERRIDDEN]
    assert ep and all(f.value.get("port_type") == "SAME" for f in ep if isinstance(f.value, dict))


def test_x8_kubernetes_config_dependency_marks_file_exposure(tmp_path: Path) -> None:
    repo = Repo(resources={"application.yml": "server:\n  port: 8080\n"},
                pom_extra="  <dependencies><dependency><artifactId>spring-cloud-starter-kubernetes-client-config</artifactId></dependency></dependencies>\n")
    port = _props(_scan(tmp_path, repo))["server.port"]
    assert port.override_exposure is OverrideExposure.FILE_POSSIBLE
    assert "spring-cloud-kubernetes-config" in (port.exposure_sources[0].object or "")


def test_dockerfile_env_is_inferred_and_container_env_overrides_it(tmp_path: Path) -> None:
    repo = Repo(resources={"application.yml": "server:\n  port: 8080\n"}, docker_env="ENV SERVER_PORT=9300\n")
    port = _props(_scan(tmp_path, repo))["server.port"]
    assert port.value == 9300 and port.status is FactStatus.INFERRED
    (tmp_path / "b").mkdir()
    repo2 = Repo(resources={"application.yml": "server:\n  port: 8080\n"}, docker_env="ENV SERVER_PORT=9300\n", env={"SERVER_PORT": "9400"})
    assert _props(_scan(tmp_path / "b", repo2))["server.port"].value == 9400


# ---------------------------------------------------------------- version and override matrix (S1 to S15)


@pytest.mark.parametrize(("boot", "expected"), [("3.2.12", "immediate"), ("3.3.13", "immediate"), ("3.4.13", "graceful"), ("3.5.16", "graceful")])
def test_s1_s2_shutdown_default_follows_the_boot_minor(tmp_path: Path, boot: str, expected: str) -> None:
    f = _props(_scan(tmp_path, Repo(boot=boot, resources={"application.yml": "server:\n  port: 8080\n"})))["server.shutdown"]
    assert f.value == expected and f.status is FactStatus.DEFAULT and f.default_ref is not None
    assert f.default_ref.upstream is not None and f.default_ref.upstream.tag.startswith(f"v{boot.rsplit('.', 1)[0]}")
    assert f.default_ref.applied_version == boot


def test_unsupported_boot_version_gets_no_defaults(tmp_path: Path) -> None:
    r = _scan(tmp_path, Repo(boot="2.7.18", resources={"application.yml": "server:\n  port: 8080\n"}))
    props = _props(r)
    assert props["server.port"].value == 8080 and "server.shutdown" not in props
    assert _endpoint(r).status is FactStatus.UNRESOLVED


def test_s12_env_beats_file_for_shutdown(tmp_path: Path) -> None:
    repo = Repo(resources={"application.yml": "spring:\n  lifecycle:\n    timeout-per-shutdown-phase: 45s\n"}, env={"SERVER_SHUTDOWN": "immediate"})
    props = _props(_scan(tmp_path, repo))
    assert props["server.shutdown"].value == "immediate" and props["server.shutdown"].status is FactStatus.DECLARED
    assert props["spring.lifecycle.timeout-per-shutdown-phase"].value == 45


def test_s13_command_line_beats_everything(tmp_path: Path) -> None:
    repo = Repo(resources={"application.yml": "spring:\n  lifecycle:\n    timeout-per-shutdown-phase: 45s\n"},
                env={"SPRING_LIFECYCLE_TIMEOUTPERSHUTDOWNPHASE": "20s"}, command=["--spring.lifecycle.timeout-per-shutdown-phase=15s"])
    f = _props(_scan(tmp_path, repo))["spring.lifecycle.timeout-per-shutdown-phase"]
    assert (f.value, f.unit) == (15, "s") and f.precedence == "spring.command_line" and f.override_exposure is OverrideExposure.NONE


def test_s14_java_tool_options_beats_config_file(tmp_path: Path) -> None:
    repo = Repo(resources={"application.yml": "spring:\n  lifecycle:\n    timeout-per-shutdown-phase: 45s\n"},
                env={"JAVA_TOOL_OPTIONS": "-Xmx1g -Dspring.lifecycle.timeout-per-shutdown-phase=15s"})
    f = _props(_scan(tmp_path, repo))["spring.lifecycle.timeout-per-shutdown-phase"]
    assert (f.value, f.unit) == (15, "s") and f.precedence == "spring.system_property"


def test_s15_java_opts_is_ignored_with_a_note(tmp_path: Path) -> None:
    repo = Repo(resources={"application.yml": "server:\n  port: 8080\n"}, env={"JAVA_OPTS": "-Dserver.port=9"})
    r = _scan(tmp_path, repo)
    assert _props(r)["server.port"].value == 8080 and "JAVA_OPTS is not read by the JVM" in _notices(r)


def test_placeholder_uses_container_env_then_default(tmp_path: Path) -> None:
    yml = "clients:\n  a:\n    base-url: ${A_URL:http://localhost:1}\n  b:\n    base-url: ${B_URL:http://localhost:2}\n"
    repo = Repo(resources={"application.yml": yml}, env={"A_URL": "http://a"})
    props = _props(_scan(tmp_path, repo))
    a, b = props["clients.a.base-url"], props["clients.b.base-url"]
    assert a.value == "http://a" and a.precedence == "spring.placeholder:os_environment" and a.source and a.source.path == "deploy.yaml"
    assert b.value == "http://localhost:2" and b.precedence == "spring.placeholder:default" and b.status is FactStatus.DECLARED


def test_nested_and_chained_placeholders(tmp_path: Path) -> None:
    yml = "a: ${B}\nb: ${C:${D:fallback}}\nserver:\n  port: ${PORT:${DEFAULT_PORT:8080}}\n"
    f = _props(_scan(tmp_path, Repo(resources={"application.yml": yml}, env={"DEFAULT_PORT": "8181"})))["server.port"]
    assert f.value == 8181


def test_circular_placeholders_are_unresolved_not_a_hang(tmp_path: Path) -> None:
    yml = "server:\n  port: ${x.a}\nx:\n  a: ${x.b}\n  b: ${x.a}\n"
    f = _props(_scan(tmp_path, Repo(resources={"application.yml": yml})))["server.port"]
    assert f.status is FactStatus.UNRESOLVED


# ---------------------------------------------------------------- actuator path algorithm (R1 to R14)


def _ep(tmp: Path, yml: str, boot: str = "3.4.13", env: dict[str, str | None] | None = None, group: str = "readiness") -> Fact:
    return _endpoint(_scan(tmp, Repo(boot=boot, resources={"application.yml": yml}, env=env or {})), group)


def test_r1_defaults_serve_the_group_on_the_main_port(tmp_path: Path) -> None:
    f = _ep(tmp_path, "spring:\n  application:\n    name: x\n")
    assert f.value == {"port": 8080, "path": "/actuator/health/readiness", "port_type": "SAME"} and f.status is FactStatus.DECLARED


def test_r2_r3_separate_management_port(tmp_path: Path) -> None:
    f = _ep(tmp_path, "management:\n  server:\n    port: 8081\n", group="liveness")
    assert f.value == {"port": 8081, "path": "/actuator/health/liveness", "port_type": "DIFFERENT"}


def test_r4_management_port_equal_to_the_default_is_same_by_the_source_rule(tmp_path: Path) -> None:
    assert _ep(tmp_path, "management:\n  server:\n    port: 8080\n").value["port_type"] == "SAME"  # type: ignore[index]


def test_r5_context_path_applies_on_the_same_port_only(tmp_path: Path) -> None:
    (tmp_path / "s").mkdir()
    (tmp_path / "d").mkdir()
    same = _ep(tmp_path / "s", "server:\n  servlet:\n    context-path: /api\n")
    assert same.value["path"] == "/api/actuator/health/readiness"  # type: ignore[index]
    diff = _ep(tmp_path / "d", "server:\n  servlet:\n    context-path: /api\nmanagement:\n  server:\n    port: 8081\n    base-path: /m\n")
    assert diff.value == {"port": 8081, "path": "/m/actuator/health/readiness", "port_type": "DIFFERENT"}  # type: ignore[comparison-overlap]


def test_r7_probes_disabled_means_the_group_is_absent(tmp_path: Path) -> None:
    f = _ep(tmp_path, "management:\n  endpoint:\n    health:\n      probes:\n        enabled: false\n")
    assert f.absent and f.value is None and f.status is FactStatus.DECLARED


def test_r8_additional_paths_are_on_the_main_port(tmp_path: Path) -> None:
    f = _ep(tmp_path, "management:\n  server:\n    port: 8081\n  endpoint:\n    health:\n      probes:\n        add-additional-paths: true\n")
    assert f.value["additional_path"] == {"port": 8080, "path": "/readyz"}  # type: ignore[index]


def test_r9_base_path_and_path_mapping(tmp_path: Path) -> None:
    f = _ep(tmp_path, "management:\n  endpoints:\n    web:\n      base-path: /manage\n      path-mapping:\n        health: status\n")
    assert f.value["path"] == "/manage/status/readiness"  # type: ignore[index]


def test_r10_excluded_health_is_absent(tmp_path: Path) -> None:
    f = _ep(tmp_path, "management:\n  endpoints:\n    web:\n      exposure:\n        include: '*'\n        exclude: health\n")
    assert f.absent


def test_r11_r12_access_none_follows_the_boot_version(tmp_path: Path) -> None:
    yml = "management:\n  endpoint:\n    health:\n      access: none\n"
    (tmp_path / "a").mkdir()
    (tmp_path / "b").mkdir()
    assert _ep(tmp_path / "a", yml).absent
    r = _scan(tmp_path / "b", Repo(boot="3.3.13", resources={"application.yml": yml}))
    assert not _endpoint(r).absent and "not known to Spring Boot 3.3.13" in _notices(r)
    assert _one(r, "spring.actuator.endpoint_access", "spring:.:health").value == "enabled"


def test_r13_placeholder_port_resolved_from_the_container(tmp_path: Path) -> None:
    f = _ep(tmp_path, "server:\n  port: ${PORT:8080}\n", env={"PORT": "9000"})
    assert f.value["port"] == 9000  # type: ignore[index]


def test_r14_management_port_from_the_active_profile_file(tmp_path: Path) -> None:
    repo = Repo(resources={"application.yml": "server:\n  port: 8080\n", "application-prod.yml": "management:\n  server:\n    port: 8081\n"},
                env={"SPRING_PROFILES_ACTIVE": "prod"})
    f = _endpoint(_scan(tmp_path, repo))
    assert f.value["port"] == 8081 and f.value["port_type"] == "DIFFERENT"  # type: ignore[index]
    assert any(i.role.value == "value_source" for i in f.inputs)


def test_unlinked_module_does_not_assume_kubernetes_probe_groups(tmp_path: Path) -> None:
    r = _scan(tmp_path, Repo(resources={"application.yml": "server:\n  port: 8080\n"}, linked=False))
    groups = _one(r, "spring.actuator.probe_groups", "spring:.:health")
    assert groups.status is FactStatus.UNRESOLVED and "not linked to a container" in (groups.unresolved_reason or "")
    assert _props(r)["server.port"].override_exposure is OverrideExposure.ENV_POSSIBLE and not _props(r)["server.port"].exposure_sources
    assert not _props(r)["server.port"].link_ids


# ---------------------------------------------------------------- build facts, redaction, robustness


def test_stack_version_facts_and_framework_default(tmp_path: Path) -> None:
    r = _scan(tmp_path, Repo(resources={"application.yml": "server:\n  port: 8080\n"}))
    v = {f.subject.key: f for f in r.facts if f.kind == "stack.version"}
    assert v["component:spring-boot"].value == "3.4.13" and v["component:spring-framework"].value == "6.2.15"
    assert v["component:spring-framework"].status is FactStatus.DEFAULT


def test_secret_looking_values_are_redacted(tmp_path: Path) -> None:
    yml = ("spring:\n  datasource:\n    url: jdbc:postgresql://admin:hunter2@db:5432/x?password=abc\n    password: hunter2\n"
           "clients:\n  pay:\n    api-key: k-123\n    base-url: https://user:pw@pay.example/v1\n")
    r = _scan(tmp_path, Repo(resources={"application.yml": yml}))
    text = json.dumps([f.model_dump(mode="json") for f in r.facts])
    for secret in ("hunter2", "k-123", "pw@", "password=abc"):
        assert secret not in text
    props = _props(r)
    assert props["spring.datasource.password"].value == "<redacted>" and props["clients.pay.base-url"].value == "https://<redacted>@pay.example/v1"


def test_broken_and_hostile_files_do_not_crash_the_scan(tmp_path: Path) -> None:
    repo = Repo(resources={
        "application.yml": "a: [unclosed\n", "application-x.yml": "a: &x [1]\nb: *x\n",
        "application.properties": "server.port=8080\n" + "k=v\\\n" * 20000,
    })
    r = _scan(tmp_path, repo, oracle=False)
    assert "Spring configuration file not read" in _notices(r)
    assert _props(r)["server.port"].value == 8080


def test_property_fact_cap(tmp_path: Path) -> None:
    body = "".join(f"server.tomcat.custom{i}-timeout={i}\n" for i in range(1700))
    r = _scan(tmp_path, Repo(resources={"application.properties": body}))
    assert sum(f.kind == "spring.property" and f.status is FactStatus.DECLARED for f in r.facts) == 1500
    assert "first 1500" in _notices(r)


def test_scan_is_deterministic(tmp_path: Path) -> None:
    repo = Repo(resources={"application.yml": "server:\n  port: ${P:8080}\n", "application-prod.yml": "server:\n  shutdown: graceful\n"},
                env={"SPRING_PROFILES_ACTIVE": "prod"}, secret=True)
    a = _scan(tmp_path, repo)
    b = run_scan(ScanOptions(path=tmp_path, facts="all"))
    dump: Any = lambda r: json.dumps([f.model_dump(mode="json") for f in r.facts], sort_keys=True)  # noqa: E731
    assert dump(a) == dump(b)


def test_module_without_config_or_container_emits_only_build_facts(tmp_path: Path) -> None:
    r = _scan(tmp_path, Repo(linked=False))
    assert not [f for f in r.facts if f.kind.startswith("spring.")]
    assert any(f.kind == "stack.version" for f in r.facts)


# ---------------------------------------------------------------- the oracle and the scorer notice wrong facts


def test_spring_oracle_detects_every_kind_of_error() -> None:
    root = ROOT / "samples" / "orders-service-spring"
    r = run_scan(ScanOptions(path=root, facts="all"))

    def mismatches(facts: list[Fact]) -> list[str]:
        rep = OracleReport()
        check_spring(root, facts, r.linkages, rep)
        return rep.mismatches

    assert not mismatches(r.facts)

    def swap(match: Any, **changes: Any) -> list[Fact]:
        done = False
        out = []
        for f in r.facts:
            if not done and match(f):
                f = f.model_copy(update=changes(f) if callable(changes) else changes)
                done = True
            out.append(f)
        assert done
        return out

    def named(kind: str, tail: str, status: FactStatus | None = None) -> Any:
        return lambda f: f.kind == kind and f.subject.key.endswith(tail) and (status is None or f.status is status)

    wrong_value = swap(named("spring.property", ":server.port", FactStatus.DECLARED), value=8081)
    assert any("server.port" in m for m in mismatches(wrong_value))
    moved = swap(named("spring.property", ":server.port", FactStatus.DECLARED), source=_move_line(r, ":server.port"))
    assert any("server.port" in m for m in mismatches(moved))
    scope = swap(named("spring.property", ":server.port", FactStatus.DECLARED), scope={"profile": "dev"})
    assert any("scope" in m for m in mismatches(scope))
    profiles = swap(named("spring.active_profiles", "spring:."), value=["dev"])
    assert any("active_profiles" in m for m in mismatches(profiles))
    endpoint = swap(named("spring.actuator.endpoint", "health/readiness"), value={"port": 8080, "path": "/actuator/health/readiness", "port_type": "SAME"})
    assert any("endpoint" in m for m in mismatches(endpoint))
    default = swap(named("spring.property", ":server.shutdown", FactStatus.DEFAULT), value="immediate")
    assert any("server.shutdown" in m for m in mismatches(default))
    shadow = swap(named("spring.property", "exposure.include", FactStatus.OVERRIDDEN), source=_move_line(r, "exposure.include", FactStatus.OVERRIDDEN))
    assert any("overridden" in m for m in mismatches(shadow))


def _move_line(r: ScanResult, tail: str, status: FactStatus = FactStatus.DECLARED) -> Any:
    f = next(f for f in r.facts if f.kind == "spring.property" and f.subject.key.endswith(tail) and f.status is status)
    assert f.source is not None
    return f.source.model_copy(update={"line_start": f.source.line_start + 1, "line_end": f.source.line_end + 1})
