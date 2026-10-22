"""M2-1 Dockerfile parser: Dockerfile-reference semantics, hostile input, and a differential check against Docker's
own BuildKit parser (the `dockerfile` dev dependency) on instruction lines and values."""

from __future__ import annotations

import dockerfile
import pytest

from fifthnine.extract.dockerfile import DockerfileError, parse_dockerfile


def test_continuations_comments_and_escape_directive() -> None:
    m = parse_dockerfile("Dockerfile", "# escape=`\nFROM alpine\nEXPOSE 80 `\n# a comment inside a continuation\n  443\n")
    assert [e.value for e in m.stages[0].expose] == [(80, "tcp"), (443, "tcp")]
    assert m.stages[0].expose[0].line == 3


def test_global_arg_visible_only_to_from_and_redeclared_arg_inherits_default() -> None:
    m = parse_dockerfile("Dockerfile", "ARG BASE=python:3.12\nARG PORT=8000\nFROM ${BASE}\nEXPOSE ${PORT}\nARG PORT\nEXPOSE ${PORT}\n")
    st = m.stages[0]
    assert st.base.value == "python:3.12"
    assert st.expose[0].unresolved is not None  # global ARG not visible inside the stage until redeclared
    assert st.expose[1].value == (8000, "tcp")


def test_arg_without_default_stays_unresolved() -> None:
    m = parse_dockerfile("Dockerfile", "ARG IMAGE\nFROM ${IMAGE}\n")
    assert m.stages[0].base.value is None and "IMAGE" in (m.stages[0].base.unresolved or "")


@pytest.mark.parametrize(("expr", "env", "expected"), [
    ("${A:-x}", {}, "x"), ("${A:-x}", {"A": "1"}, "1"), ("${A:+y}", {"A": "1"}, "y"), ("${A:+y}", {}, ""), ("\\$A", {}, "$A"),
])
def test_substitution_operators(expr: str, env: dict[str, str], expected: str) -> None:
    head = "".join(f"ENV {k}={v}\n" for k, v in env.items())
    m = parse_dockerfile("Dockerfile", f"FROM alpine\n{head}WORKDIR /{expr}\n")
    assert m.stages[0].workdir is not None and m.stages[0].workdir.value == f"/{expected}"


def test_env_legacy_form_and_relative_workdir() -> None:
    m = parse_dockerfile("Dockerfile", "FROM alpine\nENV GREETING hello world\nWORKDIR /srv\nWORKDIR app\n")
    st = m.stages[0]
    assert st.env["GREETING"].value == "hello world"
    assert st.workdir is not None and st.workdir.value == "/srv/app"


def test_relative_workdir_on_unknown_base_is_unresolved() -> None:
    m = parse_dockerfile("Dockerfile", "FROM alpine\nWORKDIR app\n")
    assert m.stages[0].workdir is not None and m.stages[0].workdir.unresolved


def test_entrypoint_clears_inherited_cmd_but_not_cmd_set_in_same_stage() -> None:
    m = parse_dockerfile("Dockerfile", "FROM alpine AS base\nCMD [\"a\"]\nFROM base\nENTRYPOINT [\"ep\"]\n")
    assert m.stages[1].cmd_state == "empty"  # CMD came from the parent image
    m2 = parse_dockerfile("Dockerfile", "FROM alpine\nCMD [\"a\"]\nENTRYPOINT [\"ep\"]\n")
    assert m2.stages[0].cmd_state == "set" and m2.stages[0].cmd is not None and m2.stages[0].cmd[1] == ["a"]


def test_base_image_entrypoint_and_cmd_stay_unknown() -> None:
    st = parse_dockerfile("Dockerfile", "FROM python:3.12\nRUN true\n").stages[0]
    assert st.entrypoint_state == "unknown" and st.cmd_state == "unknown"


def test_shell_form_uses_shell_instruction() -> None:
    st = parse_dockerfile("Dockerfile", "FROM alpine\nSHELL [\"/bin/bash\", \"-c\"]\nCMD echo hi\n").stages[0]
    assert st.cmd is not None and st.cmd[1] == ["/bin/bash", "-c", "echo hi"]


def test_default_target_is_last_stage_and_copy_from_is_recorded() -> None:
    m = parse_dockerfile("Dockerfile", "FROM maven AS build\nRUN mvn package\nFROM temurin\nCOPY --from=build /t/app.jar /app.jar\n")
    assert m.default_target is m.stages[1]
    assert m.stages[1].copies == [("build", ["/t/app.jar"], "/app.jar", 4)]


def test_heredoc_and_onbuild_are_reported_unsupported() -> None:
    m = parse_dockerfile("Dockerfile", "FROM alpine\nRUN <<EOF\necho a\nEOF\nONBUILD RUN x\nCMD [\"ok\"]\n")
    assert any("heredoc" in why for _, why in m.unsupported)
    assert any("ONBUILD" in why for _, why in m.stages[0].unsupported)
    assert m.stages[0].cmd is not None and m.stages[0].cmd[2] == 6


def test_hostile_inputs() -> None:
    long_line = "FROM alpine\nENV X=" + "a" * 500_000 + "\n"
    assert parse_dockerfile("Dockerfile", long_line).stages[0].env["X"].value == "a" * 500_000
    deep = "FROM alpine\nEXPOSE 1 \\\n" + "".join(f" {i} \\\n" for i in range(2, 5000)) + " 5000\n"
    assert len(parse_dockerfile("Dockerfile", deep).stages[0].expose) == 5000
    with pytest.raises(DockerfileError):
        parse_dockerfile("Dockerfile", "".join(f"FROM a{i}\n" for i in range(201)))
    parse_dockerfile("Dockerfile", "FROM alpine\x00\nCMD [\"�\"]\n")  # NUL and replacement characters do not crash
    with pytest.raises(DockerfileError):
        parse_dockerfile("Dockerfile", "# escape=x\nFROM alpine\n")


DIFFERENTIAL = [
    "FROM maven:3.9 AS build\nWORKDIR /src\nCOPY pom.xml .\nRUN mvn -q \\\n  package\nFROM eclipse-temurin:21-jre\nWORKDIR /app\n"
    "COPY --from=build /src/target/app.jar /app/app.jar\nEXPOSE 8080 8081/udp\nENTRYPOINT [\"java\", \"-jar\", \"/app/app.jar\"]\n",
    "FROM python:3.12-slim\nENV A=1 B=2\nWORKDIR /srv\nEXPOSE 8000\nCMD [\"uvicorn\", \"app.main:app\"]\n",
    "# syntax=docker/dockerfile:1\nFROM node:20 AS deps\n# comment\nRUN npm ci\nFROM deps\nCMD npm start\n",
]


@pytest.mark.parametrize("text", DIFFERENTIAL)
def test_instruction_lines_and_values_agree_with_buildkit(text: str) -> None:
    ours = parse_dockerfile("Dockerfile", text)
    bk = dockerfile.parse_string(text)
    froms = [c for c in bk if c.cmd.upper() == "FROM"]
    assert [s.from_line for s in ours.stages] == [c.start_line for c in froms]
    assert [str(s.base.value).removeprefix("stage:") for s in ours.stages] == [c.value[0] for c in froms]
    for st, (lo, hi) in zip(ours.stages, [(c.start_line, (froms + [None])[i + 1].start_line if i + 1 < len(froms) else 10**9)  # type: ignore[union-attr]
                                          for i, c in enumerate(froms)], strict=True):
        cmds = [c for c in bk if lo < c.start_line < hi]
        ex = [c for c in cmds if c.cmd.upper() == "EXPOSE"]
        if ex:
            assert [e.line for e in st.expose] == [c.start_line for c in ex for _ in c.value]
        for name, ours_val in (("ENTRYPOINT", st.entrypoint), ("CMD", st.cmd)):
            hits = [c for c in cmds if c.cmd.upper() == name]
            if hits and ours_val is not None:
                assert ours_val[2] == hits[-1].start_line
                if hits[-1].json:
                    assert ours_val[1] == list(hits[-1].value)
