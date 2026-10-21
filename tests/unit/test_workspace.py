import os
from pathlib import Path, PurePosixPath

import pytest

from fifthnine.core.workspace import classify, discover


@pytest.mark.parametrize(("path", "kind"), [
    ("pom.xml", "maven-pom"), ("svc/build.gradle.kts", "gradle-build"), ("gradle/libs.versions.toml", "gradle-version-catalog"),
    ("pyproject.toml", "pyproject"), ("requirements-prod.txt", "requirements"), ("uv.lock", "python-lockfile"),
    ("src/main/resources/application-prod.yml", "spring-config"), ("config/application.properties", "spring-config"),
    ("Dockerfile", "dockerfile"), ("docker/api.Dockerfile", "dockerfile"), ("Dockerfile.prod", "dockerfile"),
    ("k8s/deploy.yaml", "yaml"), ("chart/Chart.yaml", "helm-chart"), ("overlays/prod/kustomization.yaml", "kustomization"),
    ("A.java", "java-source"), ("b.kt", "kotlin-source"), ("c.py", "python-source"), (".fifthnine.yml", "fifthnine-config"),
    ("README.md", None), ("settings.gradle", None),
])
def test_classify(path: str, kind: str | None) -> None:
    assert classify(PurePosixPath(path)) == kind


def test_walk_is_sorted_and_honors_exclusions(make_repo) -> None:  # type: ignore[no-untyped-def]
    root = make_repo({
        "b.py": "", "a/z.py": "", "a/b.py": "", "node_modules/x/pom.xml": "<project/>", "target/app.jar": "x",
        ".gitignore": "generated/\n*.log\n", "generated/pom.xml": "<project/>", "debug.log": "x",
        "py39/pyvenv.cfg": "home = /usr", "py39/lib/x.py": "", "env/prod/deploy.yaml": "kind: Deployment",
    })
    ws = discover(root)
    assert [f.path for f in ws.files] == [".gitignore", "a/b.py", "a/z.py", "b.py", "env/prod/deploy.yaml"]
    assert ws.excluded_dirs == ["node_modules", "py39", "target"] and ws.gitignored == 2


def test_config_ignore_paths(make_repo) -> None:  # type: ignore[no-untyped-def]
    root = make_repo({"keep.py": "", "fixtures/pom.xml": "<project/>"})
    assert [f.path for f in discover(root, ignore_paths=["fixtures/"]).files] == ["keep.py"]


def test_size_cap_and_binary(make_repo) -> None:  # type: ignore[no-untyped-def]
    root = make_repo({"big/pom.xml": "<project>" + "x" * 2000 + "</project>", "bin/Dockerfile": b"FROM x\x00\x01", "ok.py": "x" * 5000})
    ws = discover(root, max_file_size=1000)
    reasons = {s.path: s.reason for s in ws.skipped}
    assert reasons == {"big/pom.xml": "too_large", "bin/Dockerfile": "binary"}
    assert {f.path: f.kind for f in ws.files}["big/pom.xml"] is None  # inventoried, never parsed
    assert "ok.py" not in reasons  # size cap applies only to files FifthNine parses


@pytest.mark.skipif(os.name == "nt", reason="symlinks need privileges on Windows")
def test_symlinks_are_never_followed(make_repo, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "pom.xml").write_text("<project/>")
    root = make_repo({"real.py": ""})
    (root / "escape").symlink_to(outside, target_is_directory=True)
    (root / "inner.py").symlink_to(root / "real.py")
    ws = discover(root)
    assert [f.path for f in ws.files] == ["real.py"]
    assert {s.path: s.reason for s in ws.skipped} == {"escape": "symlink_outside_root", "inner.py": "ignored"}


def test_file_limit(make_repo) -> None:  # type: ignore[no-untyped-def]
    root = make_repo({f"f{i:02}.py": "" for i in range(10)})
    ws = discover(root, max_files=4)
    assert ws.file_limit_reached and len(ws.files) == 4
