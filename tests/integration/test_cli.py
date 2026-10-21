import json
import shutil
from pathlib import Path

import jsonschema
import pytest
from click.testing import CliRunner

from fifthnine import __version__
from fifthnine.cli import cli
from fifthnine.schema.v1 import ScanResult

from ..conftest import ROOT, SAMPLES

SCHEMA = json.loads((ROOT / "schemas/result.v1.json").read_text())


def invoke(*args: str):  # type: ignore[no-untyped-def]
    return CliRunner().invoke(cli, list(args), catch_exceptions=False)


def scan_json(path: Path, *extra: str) -> dict:  # type: ignore[type-arg]
    res = invoke("scan", str(path), "--format", "json", *extra)
    assert res.exit_code == 0, res.output
    data = json.loads(res.stdout)
    jsonschema.validate(data, SCHEMA)
    ScanResult.model_validate(data)
    return data  # type: ignore[no-any-return]


def test_version() -> None:
    res = invoke("--version")
    assert res.exit_code == 0 and __version__ in res.output and "fifthnine.result/v1" in res.output


@pytest.mark.parametrize("sample", ["orders-service-spring", "catalog-api-fastapi"])
def test_samples_scan_incomplete_and_valid(sample: str) -> None:
    data = scan_json(SAMPLES / sample)
    assert data["status"] == "INCOMPLETE"
    assert data["findings"] == [] and data["summary"]["rules_evaluated"] == 0
    assert data["coverage"][0]["code"] == "COV-NO-RULES" and data["coverage"][0]["blocks_assessment"] is True
    assert data["simulated"] is False and data["scan"]["network_used"] is False and data["scan"]["code_executed"] is False
    assert all(not s["path"].startswith("/") for s in data["inventory"]["skipped"])


def test_spring_sample_stack() -> None:
    stacks = {(c["component"], c["version"]): (c["support"], c["version_source"]["path"], c["version_source"]["line_start"])
              for c in scan_json(SAMPLES / "orders-service-spring")["inventory"]["detected_stacks"]}
    assert stacks[("spring-boot", "3.4.13")] == ("untested", "pom.xml", 10)
    assert stacks[("java", "21")] == ("untested", "pom.xml", 20)
    assert stacks[("resilience4j", "2.4.0")][0] == "untested"
    assert ("kubernetes-api", "autoscaling/v2/HorizontalPodAutoscaler") in stacks


def test_fastapi_sample_stack() -> None:
    stacks = {(c["component"], c["version"]) for c in scan_json(SAMPLES / "catalog-api-fastapi")["inventory"]["detected_stacks"]}
    assert {("fastapi", "0.159.1"), ("tenacity", "9.2.1"), ("httpx", "0.28.1"), ("gunicorn", "26.2.0"), ("python", "3.12")} <= stacks


def test_output_is_deterministic_across_runs_and_copies(tmp_path: Path) -> None:
    copy = tmp_path / "copy"
    shutil.copytree(SAMPLES / "orders-service-spring", copy)
    a = invoke("scan", str(SAMPLES / "orders-service-spring"), "--format", "json", "--facts", "all").stdout
    b = invoke("scan", str(SAMPLES / "orders-service-spring"), "--format", "json", "--facts", "all").stdout
    c = invoke("scan", str(copy), "--format", "json", "--facts", "all").stdout
    assert a == b == c  # no timestamps, no absolute paths


def test_facts_option() -> None:
    assert scan_json(SAMPLES / "orders-service-spring")["facts"] == []
    facts = scan_json(SAMPLES / "orders-service-spring", "--facts", "all")["facts"]
    assert any(f["kind"] == "stack.spring-boot.version" and f["value"] == "3.4.13" for f in facts)


def test_output_file_and_console(tmp_path: Path) -> None:
    out = tmp_path / "r.json"
    res = invoke("scan", str(SAMPLES / "catalog-api-fastapi"), "--format", "json", "--output", str(out))
    assert res.exit_code == 0 and json.loads(out.read_text())["status"] == "INCOMPLETE"
    assert "Status: INCOMPLETE" in res.stderr
    console = invoke("scan", str(SAMPLES / "catalog-api-fastapi"))
    assert "Status: INCOMPLETE" in console.stdout and "does not certify production readiness" in console.stdout
    assert "PASS" not in console.stdout


def test_empty_directory_is_incomplete(tmp_path: Path) -> None:
    data = scan_json(tmp_path)
    assert data["status"] == "INCOMPLETE" and data["inventory"]["files_considered"] == 0


def test_gate_mode_without_rules_exits_zero() -> None:
    assert invoke("scan", str(SAMPLES / "orders-service-spring"), "--mode", "gate", "--fail-on", "low").exit_code == 0


@pytest.mark.parametrize("args", [
    ["scan", "does/not/exist"],
    ["scan", ".", "--base-ref", "origin/main"],
    ["scan", ".", "--baseline", "b.json"],
    ["scan", ".", "--format", "sarif"],
    ["scan", ".", "--timing"],
    ["scan", ".", "--rule", "FN-RES-001"],
    ["scan", ".", "--format", "xml"],
    ["scan", ".", "--mode", "block"],
    ["rules", "explain", "FN-RES-002"],
    ["links"],
    ["links", "accept"],
    ["baseline", "create"],
])
def test_error_and_not_yet_implemented_exit_2(args: list[str]) -> None:
    assert invoke(*args).exit_code == 2


def test_invalid_config_exits_2(make_repo) -> None:  # type: ignore[no-untyped-def]
    root = make_repo({".fifthnine.yml": "version: 1\npolicy: {mode: block}\n"})
    res = invoke("scan", str(root))
    assert res.exit_code == 2 and "policy.mode" in res.stderr
    bad = make_repo({".fifthnine.yml": "services:\n  - name: x\n    workload: {name: d, container: c}\n    accepted_by: links-accept\n"})
    assert invoke("scan", str(bad)).exit_code == 2  # D3: links-accept entries need digest and date


def test_config_profile_and_policy(make_repo) -> None:  # type: ignore[no-untyped-def]
    root = make_repo({".fifthnine.yml": "version: 1\nprofile: prod\npolicy: {mode: gate, fail_on: medium}\nignore_paths: ['legacy/']\n",
                      "legacy/pom.xml": "<project/>"})
    data = scan_json(root)
    s = data["scan"]
    assert (s["profile"], s["profile_source"], s["policy_mode"], s["fail_on"]) == ("prod", "config", "gate", "medium")
    assert data["scan"]["config_file"] == ".fifthnine.yml"
    assert scan_json(root, "--profile", "staging")["scan"]["profile_source"] == "cli"


def test_unsupported_and_unresolved_are_reported(make_repo) -> None:  # type: ignore[no-untyped-def]
    pom = ("<project><parent><groupId>org.springframework.boot</groupId>"
           "<artifactId>spring-boot-starter-parent</artifactId><version>2.7.18</version></parent></project>")
    root = make_repo({"pom.xml": pom, "k8s/db.yaml": "apiVersion: apps/v1\nkind: StatefulSet\nmetadata: {name: db}\n",
                      "chart/Chart.yaml": "apiVersion: v2\nname: x\n", "broken.yaml": "a: [1\n"})
    data = scan_json(root)
    codes = [c["code"] for c in data["coverage"]]
    assert codes.count("COV-UNSUPPORTED") >= 3  # Boot 2.x, StatefulSet, Helm
    levels = {(c["component"], c["version"]): c["support"] for c in data["inventory"]["detected_stacks"]}
    assert levels[("spring-boot", "2.7.18")] == "unsupported" and levels[("kubernetes-api", "apps/v1/StatefulSet")] == "unsupported"
    assert {(s["path"], s["reason"]) for s in data["inventory"]["skipped"]} == {("broken.yaml", "parse_error")}


def test_rules_list_and_schema_commands() -> None:
    assert "No reliability rules" in invoke("rules", "list").output
    assert json.loads(invoke("rules", "list", "--format", "json").output)["rules"] == []
    assert json.loads(invoke("schema", "result").output)["title"] == "ScanResult"


def test_python_module_entry_point() -> None:
    import subprocess
    import sys

    proc = subprocess.run([sys.executable, "-m", "fifthnine", "--version"], capture_output=True, text=True, check=False)
    assert proc.returncode == 0 and __version__ in proc.stdout
