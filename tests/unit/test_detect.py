from fifthnine.core.detect import detect
from fifthnine.core.workspace import discover

POM = """<?xml version="1.0"?>
<project xmlns="http://maven.apache.org/POM/4.0.0">
  <parent>
    <groupId>org.springframework.boot</groupId>
    <artifactId>spring-boot-starter-parent</artifactId>
    <version>{boot}</version>
  </parent>
  <properties>
    <java.version>17</java.version>
    <boot.version>3.3.13</boot.version>
  </properties>
</project>
"""


def run(make_repo, files):  # type: ignore[no-untyped-def]
    return detect(discover(make_repo(files)))


def found(result, component):  # type: ignore[no-untyped-def]
    return [(d.version, d.source.path, d.source.line_start) for d in result.detections if d.component == component]


def test_maven_parent_literal_and_property(make_repo) -> None:  # type: ignore[no-untyped-def]
    r = run(make_repo, {"a/pom.xml": POM.format(boot="3.4.13"), "b/pom.xml": POM.format(boot="${boot.version}")})
    assert found(r, "spring-boot") == [("3.3.13", "b/pom.xml", 10), ("3.4.13", "a/pom.xml", 6)]
    assert found(r, "java") == [("17", "a/pom.xml", 9)]


def test_maven_unresolved_property(make_repo) -> None:  # type: ignore[no-untyped-def]
    r = run(make_repo, {"pom.xml": POM.format(boot="${revision}")})
    [d] = [d for d in r.detections if d.component == "spring-boot"]
    assert d.version is None and "unresolved property ${revision}" in (d.note or "")


def test_maven_bom_import(make_repo) -> None:  # type: ignore[no-untyped-def]
    pom = """<project><dependencyManagement><dependencies><dependency>
<groupId>org.springframework.boot</groupId><artifactId>spring-boot-dependencies</artifactId>
<version>3.5.16</version><type>pom</type><scope>import</scope></dependency></dependencies></dependencyManagement></project>"""
    assert found(run(make_repo, {"pom.xml": pom}), "spring-boot") == [("3.5.16", "pom.xml", 3)]


def test_hostile_pom_is_a_parse_error_not_a_crash(make_repo) -> None:  # type: ignore[no-untyped-def]
    r = run(make_repo, {"pom.xml": '<!DOCTYPE x [<!ENTITY a "aaaa">]><project>&a;</project>'})
    assert [(s.path, s.reason) for s in r.parse_errors] == [("pom.xml", "parse_error")]


def test_gradle_literals_and_unresolved(make_repo) -> None:  # type: ignore[no-untyped-def]
    r = run(make_repo, {
        "a/build.gradle.kts": ('plugins {\n  id("org.springframework.boot") version "3.2.12"\n}\n'
                               "java { toolchain { languageVersion = JavaLanguageVersion.of(21) } }\n"),
        "b/build.gradle": "plugins {\n  alias(libs.plugins.spring.boot)\n  id 'org.springframework.boot' apply false\n}\n",
    })
    assert found(r, "spring-boot") == [("3.2.12", "a/build.gradle.kts", 2)]
    assert found(r, "java") == [("21", "a/build.gradle.kts", 4)]
    assert [d.source.path for d in r.unresolved if d.component == "spring-boot"] == ["b/build.gradle"]


def test_gradle_version_catalog(make_repo) -> None:  # type: ignore[no-untyped-def]
    toml = '[versions]\nboot = "3.4.13"\n\n[plugins]\nspring-boot = { id = "org.springframework.boot", version.ref = "boot" }\n'
    assert found(run(make_repo, {"gradle/libs.versions.toml": toml}), "spring-boot") == [("3.4.13", "gradle/libs.versions.toml", 2)]


def test_python_pins_ranges_and_lockfile_preference(make_repo) -> None:  # type: ignore[no-untyped-def]
    r = run(make_repo, {
        "pyproject.toml": '[project]\nname = "x"\ndependencies = ["fastapi>=0.110", "tenacity==9.2.1", "requests==2.34.2"]\n',
        "uv.lock": 'version = 1\n\n[[package]]\nname = "fastapi"\nversion = "0.159.1"\n',
        "svc/requirements.txt": "httpx==0.28.1  # pinned\n-r base.txt\ngunicorn\n",
    })
    assert found(r, "fastapi") == [("0.159.1", "uv.lock", 4)]  # range-only entry dropped once an exact version exists
    assert found(r, "tenacity") == [("9.2.1", "pyproject.toml", 3)]
    assert found(r, "httpx") == [("0.28.1", "svc/requirements.txt", 1)]
    assert [(d.version, d.note) for d in r.detections if d.component == "gunicorn"] == [(None, "declared as (no version constraint); no exact version")]
    assert not found(r, "requests")  # not a tracked component


def test_dockerfile_runtime_images(make_repo) -> None:  # type: ignore[no-untyped-def]
    r = run(make_repo, {"Dockerfile": "FROM maven:3.9 AS build\nFROM eclipse-temurin:21-jre\n",
                        "py/Dockerfile": "ARG BASE=python:3.12\nFROM ${BASE}\nFROM docker.io/library/python:3.13.1-slim\n"})
    assert found(r, "java") == [("21", "Dockerfile", 2)]
    assert found(r, "python") == [("3.13", "py/Dockerfile", 3)]
    assert [d.component for d in r.unresolved] == ["container-base-image"]


def test_kubernetes_objects_and_helm(make_repo) -> None:  # type: ignore[no-untyped-def]
    r = run(make_repo, {
        "k8s/app.yaml": "apiVersion: apps/v1\nkind: Deployment\nmetadata: {name: api}\n---\napiVersion: apps/v1\nkind: StatefulSet\nmetadata: {name: db}\n",
        "chart/Chart.yaml": "apiVersion: v2\nname: api\n",
        "chart/templates/deploy.yaml": "apiVersion: apps/v1\nkind: Deployment\nmetadata:\n  name: {{ .Values.name }}\n",
        "notk8s.yml": "foo: bar\n",
    })
    assert [(o.kind, o.name, o.source.line_start) for o in r.k8s_objects] == [("Deployment", "api", 2), ("StatefulSet", "db", 6)]
    assert r.helm_charts == ["chart/Chart.yaml"] and r.helm_templates == ["chart/templates/deploy.yaml"]
