"""Regression tests for the independent review of PR #1 (findings numbered as in the review)."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest
from click.testing import CliRunner

from fifthnine.cli import cli
from fifthnine.core.detect import detect, detection_facts, strip_jvm_comments
from fifthnine.core.engine import FifthNineError, ScanOptions, load_config, run_scan
from fifthnine.core.safeparse import ParseError, load_toml, load_yaml_documents
from fifthnine.core.support import classify
from fifthnine.core.workspace import Workspace, discover
from fifthnine.schema.v1 import SupportLevel

ALIAS_BOMB = "a: &a [x, x, x, x, x, x, x, x, x, x]\n" + "".join(
    f"{c}: &{c} [*{p}, *{p}, *{p}, *{p}, *{p}, *{p}, *{p}, *{p}, *{p}, *{p}]\n" for p, c in zip("abcdefgh", "bcdefghi", strict=True)
)


def invoke(*args: str):  # type: ignore[no-untyped-def]
    return CliRunner().invoke(cli, list(args), catch_exceptions=False)


def stacks(root: Path) -> dict[tuple[str, str | None], tuple[str, str, int]]:
    result = run_scan(ScanOptions(path=root))
    out = {}
    for c in result.inventory.detected_stacks:
        src = c.version_source
        out[(c.component, c.version)] = (c.support.value, src.path if src else "", src.line_start if src else 0)
    return out


def found(root: Path, component: str) -> list[tuple[str | None, str, int]]:
    r = detect(discover(root))
    return [(d.version, d.source.path, d.source.line_start) for d in r.detections + r.unresolved if d.component == component and d.source]


# 1 -------------------------------------------------------------------------------------------------------


def test_01_alias_bomb_in_config_is_refused_quickly(make_repo) -> None:  # type: ignore[no-untyped-def]
    root = make_repo({".fifthnine.yml": ALIAS_BOMB})
    start = time.monotonic()
    with pytest.raises(FifthNineError, match="aliases"):
        load_config(root, None)
    assert time.monotonic() - start < 2
    assert invoke("scan", str(root)).exit_code == 2


# 2 -------------------------------------------------------------------------------------------------------


def test_02_deep_toml_is_a_parse_error_not_a_crash(make_repo) -> None:  # type: ignore[no-untyped-def]
    with pytest.raises(ParseError, match="nesting"):
        load_toml("a = " + "[" * 50_000 + "]" * 50_000)
    root = make_repo({"pyproject.toml": "a = " + "[" * 50_000 + "]" * 50_000, "requirements.txt": "tenacity==9.2.1\n"})
    res = invoke("scan", str(root), "--format", "json")
    data = json.loads(res.stdout)
    assert res.exit_code == 0 and {(s["path"], s["reason"]) for s in data["inventory"]["skipped"]} == {("pyproject.toml", "parse_error")}
    assert any(c["component"] == "tenacity" for c in data["inventory"]["detected_stacks"])


# 3 -------------------------------------------------------------------------------------------------------


def _big_yaml(kubernetes: bool) -> str:
    crd = "apiVersion: apiextensions.k8s.io/v1\nkind: CustomResourceDefinition\nmetadata:\n  name: big\nspec:\n  props:\n"
    head = crd if kubernetes else "openapi: 3.0.0\npaths:\n"
    body = "".join(f"    field{i}:\n      type: string\n      description: \"description for field {i}\"\n" for i in range(12_000))
    return head + body


def test_03_large_yaml_is_fast_and_non_kubernetes_yaml_is_not_parsed(make_repo) -> None:  # type: ignore[no-untyped-def]
    root = make_repo({"crd.yaml": _big_yaml(True), "openapi.yaml": _big_yaml(False)})
    start = time.monotonic()
    data = json.loads(invoke("scan", str(root), "--format", "json").stdout)
    assert time.monotonic() - start < 5
    assert data["inventory"]["files_parsed"] == 1  # the CRD; the OpenAPI file has no apiVersion/kind lines
    assert ("kubernetes-api", "apiextensions.k8s.io/v1/CustomResourceDefinition") not in {
        (c["component"], c["version"]) for c in data["inventory"]["detected_stacks"]}  # inventoried as a fact only


# 4 -------------------------------------------------------------------------------------------------------


@pytest.mark.skipif(sys.platform != "linux", reason="arbitrary bytes in file names are a POSIX/Linux feature")
def test_04_non_utf8_file_names_are_skipped_and_reported(make_repo) -> None:  # type: ignore[no-untyped-def]
    root = make_repo({"ok.yaml": "apiVersion: v1\nkind: Service\nmetadata: {name: s}\n"})
    (root / os.fsdecode(b"caf\xe9.yaml")).write_text("apiVersion: v1\nkind: Service\n")
    (root / os.fsdecode(b"b\xe9.yaml")).write_bytes(b"x\x00y")
    res = invoke("scan", str(root), "--format", "json")
    assert res.exit_code == 0
    skipped = {s["path"]: s["detail"] for s in json.loads(res.stdout)["inventory"]["skipped"]}
    assert skipped == {"caf\\xe9.yaml": "name is not valid UTF-8; not read", "b\\xe9.yaml": "name is not valid UTF-8; not read"}


def test_04_render_failure_exits_2_not_1(make_repo, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    import fifthnine.cli as cli_module

    def boom(*_a: object, **_k: object) -> str:
        raise RuntimeError("render failed")

    monkeypatch.setattr(cli_module, "render_json", boom)
    assert invoke("scan", str(make_repo({"a.py": ""})), "--format", "json").exit_code == 2


# 5 -------------------------------------------------------------------------------------------------------


def test_05_reports_are_utf8_on_a_legacy_code_page(make_repo) -> None:  # type: ignore[no-untyped-def]
    root = make_repo({"服务/k8s.yaml": "apiVersion: v1\nkind: Service\nmetadata: {name: s}\n", "x.yaml": "apiVersion: v1\nkind: Service\nspec: [\n"})
    env = {**os.environ, "PYTHONIOENCODING": "cp1252"}
    for fmt in ("json", "console"):
        proc = subprocess.run([sys.executable, "-m", "fifthnine", "scan", str(root), "--format", fmt], capture_output=True, env=env, check=False)
        assert proc.returncode == 0, proc.stderr.decode("utf-8", "replace")
        assert "服务/k8s.yaml" in proc.stdout.decode("utf-8")


# 6 and 7 -------------------------------------------------------------------------------------------------


def test_06_requirements_with_hashes_continuations_extras_and_markers(make_repo) -> None:  # type: ignore[no-untyped-def]
    root = make_repo({"requirements.txt": (
        "# pinned with pip-compile --generate-hashes\n"
        "# previously: tenacity==8.2.3\n"
        'fastapi[all]==0.111.0 ; python_version >= "3.10" \\\n'
        "    --hash=sha256:aaaa \\\n"
        "    --hash=sha256:bbbb\n"
        "gunicorn==22.0.0 --hash=sha256:cccc\n"
        "tenacity==9.2.1  # inline comment\n"
        "httpx @ https://example.invalid/httpx.whl\n"
    )})
    assert found(root, "fastapi") == [("0.111.0", "requirements.txt", 3)]
    assert found(root, "gunicorn") == [("22.0.0", "requirements.txt", 6)]
    assert found(root, "tenacity") == [("9.2.1", "requirements.txt", 7)]  # not the comment on line 2
    assert found(root, "httpx") == [(None, "requirements.txt", 8)]  # URL requirement: present, no version


def test_07_line_provenance_for_lockfile_poetry_and_catalog(make_repo) -> None:  # type: ignore[no-untyped-def]
    root = make_repo({
        "uv.lock": ('version = 1\n\n[[package]]\nname = "app"\nversion = "1.0"\ndependencies = [\n    { name = "httpx" },\n]\n\n'
                    '[[package]]\nname = "httpx"\nversion = "0.28.1"\n'),
        "p/pyproject.toml": ('[tool.poetry]\nname = "uvicorn-app"\n\n[tool.poetry.dependencies]\npython = "^3.12"\nuvicorn = "0.30.1"\n'),
        "gradle/libs.versions.toml": ('[versions]\njackson = "3.4.1"\nboot = "3.4.1"\n\n[plugins]\n'
                                      'spring-boot = { id = "org.springframework.boot", version.ref = "boot" }\n'),
    })
    assert found(root, "httpx") == [("0.28.1", "uv.lock", 11)]
    assert found(root, "uvicorn") == [("0.30.1", "p/pyproject.toml", 6)]
    assert found(root, "spring-boot") == [("3.4.1", "gradle/libs.versions.toml", 3)]


# 8 -------------------------------------------------------------------------------------------------------


def test_08_gradle_comments_ignored_and_java_1x_normalized(make_repo) -> None:  # type: ignore[no-untyped-def]
    root = make_repo({
        "a/build.gradle": ("// id 'org.springframework.boot' version '2.7.18'\n/* id 'org.springframework.boot' version '2.6.0' */\n"
                           "repositories { maven { url 'https://repo.example//x' } }\nsourceCompatibility = '1.8'\n"),
        "b/build.gradle.kts": 'plugins {\n    id("org.springframework.boot").version("3.2.1")\n}\nkotlin { jvmToolchain(17) }\n',
        "c/build.gradle": "java { sourceCompatibility = JavaVersion.VERSION_1_8 }\n",
    })
    assert found(root, "spring-boot") == [("3.2.1", "b/build.gradle.kts", 2)]
    assert sorted(found(root, "java")) == [("17", "b/build.gradle.kts", 4), ("8", "a/build.gradle", 4)]
    assert "https://repo.example//x" in strip_jvm_comments("url 'https://repo.example//x' // c")


# 9 -------------------------------------------------------------------------------------------------------


@pytest.mark.parametrize(("version", "level"), [
    ("2.3.4.RELEASE", SupportLevel.UNSUPPORTED), ("4.0.0-SNAPSHOT", SupportLevel.UNSUPPORTED),
    ("2.0.0-M1", SupportLevel.UNSUPPORTED), ("3.4.0-M1", SupportLevel.UNTESTED), ("3.3.13", SupportLevel.UNTESTED),
])
def test_09_maven_qualifiers_compare_by_release(version: str, level: SupportLevel) -> None:
    assert classify("spring-boot", version).support is level


# 10 ------------------------------------------------------------------------------------------------------


def test_10_parse_errors_do_not_leak_values(make_repo) -> None:  # type: ignore[no-untyped-def]
    with pytest.raises(ParseError) as exc:
        load_yaml_documents('password: "hunter2"\npassword: "Sup3rS3cret!"\n')
    assert "hunter2" not in str(exc.value) and "Sup3rS3cret" not in str(exc.value)
    root = make_repo({"k8s/secret.yaml": 'apiVersion: v1\nkind: Secret\nstringData:\n  password: "hunter2"\n  token: "Sup3rS3cret!\n'})
    out = invoke("scan", str(root), "--format", "json", "--facts", "all").stdout
    assert "hunter2" not in out and "Sup3rS3cret" not in out and "parse_error" in out


# 11 ------------------------------------------------------------------------------------------------------


def test_11_ignored_files_are_reported(make_repo) -> None:  # type: ignore[no-untyped-def]
    root = make_repo({".gitignore": "*.yaml\nign/\n", "a.yaml": "apiVersion: v1\nkind: Service\n", "ign/b.py": "", "c.py": ""})
    notes = {c["code"]: c["message"] for c in json.loads(invoke("scan", str(root), "--format", "json").stdout)["coverage"]}
    assert notes["COV-IGNORED"].startswith("1 files and 1 directories excluded")


# 12 ------------------------------------------------------------------------------------------------------


def test_12_utf8_bom_does_not_hide_detections(make_repo) -> None:  # type: ignore[no-untyped-def]
    bom = "﻿"
    root = make_repo({"requirements.txt": bom + "fastapi==0.110.0\n", "Dockerfile": bom + "FROM python:3.12\n",
                      "svc/pyproject.toml": bom + '[project]\nname = "x"\ndependencies = ["httpx==0.28.1"]\n'})
    s = stacks(root)
    assert ("fastapi", "0.110.0") in s and ("python", "3.12") in s and ("httpx", "0.28.1") in s


# 13 ------------------------------------------------------------------------------------------------------


def test_13_invalid_gitignore_line_is_skipped_and_reported(make_repo) -> None:  # type: ignore[no-untyped-def]
    root = make_repo({".gitignore": "a\\\n*.log\n", "x.py": "", "y.log": ""})
    data = json.loads(invoke("scan", str(root), "--format", "json").stdout)
    assert data["inventory"]["files_considered"] == 2  # .gitignore and x.py; y.log still ignored
    assert any(c["code"] == "COV-IGNORED-CONFIG" for c in data["coverage"])


def test_13_config_errors_exit_2(make_repo, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    bad_pattern = make_repo({".fifthnine.yml": "version: 1\nignore_paths: ['a\\']\n"})
    res = invoke("scan", str(bad_pattern))
    assert res.exit_code == 2 and "ignore_paths" in res.stderr
    other = tmp_path / "other"
    other.mkdir()
    (other / ".fifthnine.yml").write_bytes(b"version: 1\nprofile: \xff\n")
    res = invoke("scan", str(other))
    assert res.exit_code == 2 and "not valid UTF-8" in res.stderr


def test_13_unreadable_file_is_skipped(make_repo, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    root = make_repo({"pom.xml": "<project/>", "requirements.txt": "tenacity==9.2.1\n"})
    real = Workspace.read_text

    def read(self: Workspace, entry):  # type: ignore[no-untyped-def]
        if entry.path == "pom.xml":
            raise PermissionError(13, "Permission denied")
        return real(self, entry)

    monkeypatch.setattr(Workspace, "read_text", read)
    result = run_scan(ScanOptions(path=root))
    assert [(s.path, s.reason) for s in result.inventory.skipped] == [("pom.xml", "unsupported")]
    assert any(c.component == "tenacity" for c in result.inventory.detected_stacks)


def test_13_unreadable_root_exits_2(make_repo, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setattr(os, "access", lambda *_a, **_k: False)
    assert invoke("scan", str(make_repo({"a.py": ""}))).exit_code == 2


# 14 ------------------------------------------------------------------------------------------------------


def test_14_files_parsed_counts_only_interpreted_files(make_repo, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    root = make_repo({"chart/templates/d.yaml": "apiVersion: apps/v1\nkind: Deployment\nmetadata:\n  name: {{ .Values.n }}\n"})
    external = tmp_path / "ext.yml"
    external.write_text("version: 1\n")
    data = json.loads(invoke("scan", str(root), "--format", "json", "--config", str(external)).stdout)
    assert (data["inventory"]["files_considered"], data["inventory"]["files_parsed"]) == (1, 0)
    assert data["scan"]["config_file"] is None


# 15 and 16 -----------------------------------------------------------------------------------------------


def test_15_fact_ids_are_unique(make_repo) -> None:  # type: ignore[no-untyped-def]
    root = make_repo({"pyproject.toml": '[project]\nname = "x"\ndependencies = ["httpx>=0.27"]\n[tool.poetry.dependencies]\nhttpx = "^0.27"\n'})
    ids = [f.fact_id for f in detection_facts(detect(discover(root)))]
    assert len(ids) == len(set(ids)) == 2


def test_16_range_in_one_service_is_not_hidden_by_a_pin_in_another(make_repo) -> None:  # type: ignore[no-untyped-def]
    root = make_repo({"svc-a/requirements.txt": "fastapi>=0.110\n", "svc-b/requirements.txt": "fastapi==0.115.0\n"})
    assert sorted(found(root, "fastapi"), key=str) == sorted([(None, "svc-a/requirements.txt", 1), ("0.115.0", "svc-b/requirements.txt", 1)], key=str)


# 17 and 18 -----------------------------------------------------------------------------------------------


def test_17_dockerfile_digest_and_major_only_tags(make_repo) -> None:  # type: ignore[no-untyped-def]
    root = make_repo({"Dockerfile": "FROM eclipse-temurin:17@sha256:abc123 AS build\nFROM python:3\n"})
    assert found(root, "java") == [("17", "Dockerfile", 1)]
    assert found(root, "python") == [(None, "Dockerfile", 2)]


def test_18_report_written_inside_the_repo_does_not_change_the_next_scan(make_repo) -> None:  # type: ignore[no-untyped-def]
    root = make_repo({"a.py": "", "requirements.txt": "httpx==0.28.1\n"})
    out = root / "fifthnine.json"
    invoke("scan", str(root), "--format", "json", "--output", str(out))
    first = out.read_text()
    invoke("scan", str(root), "--format", "json", "--output", str(out))
    assert out.read_text() == first


# --quiet -------------------------------------------------------------------------------------------------


def test_quiet_suppresses_console_but_not_json(make_repo) -> None:  # type: ignore[no-untyped-def]
    root = make_repo({"a.py": ""})
    assert invoke("scan", str(root), "--quiet").stdout == ""
    assert json.loads(invoke("scan", str(root), "--quiet", "--format", "json").stdout)["status"] == "INCOMPLETE"
