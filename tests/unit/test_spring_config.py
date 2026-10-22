"""Spring configuration readers (M2-2): .properties, YAML 1.1 scalars, flattening, activation, profile expressions."""

from __future__ import annotations

import pytest

from fifthnine.core.safeparse import ParseError
from fifthnine.extract.properties import parse_properties
from fifthnine.extract.spring_config import ProfileExpressionError, evaluate_profile_expression, parse_config_file


def test_properties_follow_java_rules() -> None:
    text = "# c\n! c\na.b = 1\nc:2\nd  3\ne=multi\\\n   line\nf=\\u0041x\\ty\ng=ends\\\\\nh=\\\n"
    (doc,) = parse_properties(text)
    got = {e.key: (e.value, e.line) for e in doc.entries}
    assert got["a.b"] == ("1", 3) and got["c"] == ("2", 4) and got["d"] == ("3", 5)
    assert got["e"] == ("multiline", 6)
    assert got["f"] == ("Ax\ty", 8)
    assert got["g"] == ("ends\\", 9)  # an even number of backslashes does not continue the line
    assert got["h"] == ("", 10)


def test_properties_documents_and_last_duplicate_wins() -> None:
    docs = parse_properties("a=1\na=2\n#---\nb=3\n")
    assert [[(e.key, e.value) for e in d.entries] for d in docs] == [[("a", "2")], [("b", "3")]]
    assert docs[1].start_line == 4


def test_properties_hostile_input_is_linear() -> None:
    assert len(parse_properties("a=\\\n" * 200_000 + "b=1\n")[0].entries) == 1
    assert len(parse_properties("".join(f"k{i}=v\n" for i in range(100_000)))[0].entries) == 100_000


YML = """\
spring:
  lifecycle:
    timeout-per-shutdown-phase: 45s
  main:
    banner-mode: off
    flag: "off"
server:
  port: 8080
  octal: 0755
  nothing:
list:
  - a
  - b: 1
management:
  endpoints.web.exposure.include: health,info
"""


def test_yaml_is_flattened_with_yaml_11_scalars_and_key_lines() -> None:
    (doc,) = parse_config_file("application.yml", YML)
    got = {e.key: (e.value, e.line) for e in doc.entries}
    assert got["spring.lifecycle.timeout-per-shutdown-phase"] == ("45s", 3)
    assert got["spring.main.banner-mode"] == (False, 5)  # SnakeYAML reads an unquoted off as a boolean
    assert got["spring.main.flag"] == ("off", 6)
    assert got["server.port"] == (8080, 8)
    assert got["server.octal"] == (0o755, 9)
    assert got["server.nothing"] == ("", 10)  # a YAML null is the empty string in Spring
    assert got["list[0]"] == ("a", 12) and got["list[1].b"] == (1, 13)
    assert got["management.endpoints.web.exposure.include"] == ("health,info", 15)


def test_activation_and_import_are_conditions_not_properties() -> None:
    text = ("a: 1\n---\nspring:\n  config:\n    activate:\n      on-profile: prod | eu\n      on-cloud-platform: kubernetes\n"
            "    import: optional:file:/x/\nb: 2\n")
    base, prod = parse_config_file("application.yml", text)
    assert not base.conditional and [(e.key, e.value) for e in base.entries] == [("a", 1)]
    assert prod.on_profile == "prod | eu" and prod.on_profile_line == 6
    assert prod.other_conditions == [("spring.config.activate.on-cloud-platform", "kubernetes", 7)]
    assert prod.imports == [("optional:file:/x/", 8)]
    assert [e.key for e in prod.entries] == ["b"]


def test_properties_activation_document() -> None:
    base, other = parse_config_file("application.properties", "a=1\n#---\nspring.config.activate.on-profile=dev\nb=2\n")
    assert base.on_profile is None and other.on_profile == "dev" and other.index == 1


def test_aliases_and_malformed_yaml_are_refused() -> None:
    with pytest.raises(ParseError):
        parse_config_file("application.yml", "a: &x\n  b: 1\nc: *x\n")
    with pytest.raises(ParseError):
        parse_config_file("application.yml", "a: [unclosed\n")


@pytest.mark.parametrize(("expr", "active", "expected"), [
    ("prod", {"prod"}, True), ("prod", {"dev"}, False), ("!dev", {"prod"}, True), ("!dev", {"dev"}, False),
    ("prod,eu", {"eu"}, True), ("prod & eu", {"prod"}, False), ("prod & eu", {"prod", "eu"}, True),
    ("prod | eu", {"eu"}, True), ("(prod | eu) & !dev", {"eu"}, True), ("(prod | eu) & !dev", {"eu", "dev"}, False),
])
def test_profile_expressions(expr: str, active: set[str], expected: bool) -> None:
    assert evaluate_profile_expression(expr, active) is expected


@pytest.mark.parametrize("expr", ["a & b | c", "(a", "a &", "", "(" * 5000])
def test_invalid_profile_expressions_raise(expr: str) -> None:
    with pytest.raises(ProfileExpressionError):
        evaluate_profile_expression(expr, {"a"})
