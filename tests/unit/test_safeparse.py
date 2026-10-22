import pytest

from fifthnine.core.safeparse import (
    ParseError,
    compose_yaml_documents,
    find_line,
    load_toml,
    load_yaml_documents,
    mapping_scalars,
    parse_xml,
)

BILLION_LAUGHS = """<?xml version="1.0"?>
<!DOCTYPE lolz [<!ENTITY lol "lol"><!ENTITY lol2 "&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;">]>
<project>&lol2;</project>"""

XXE = """<?xml version="1.0"?>
<!DOCTYPE p [<!ENTITY x SYSTEM "file:///etc/passwd">]>
<project>&x;</project>"""


def test_xml_keeps_line_numbers_and_strips_namespaces() -> None:
    root = parse_xml('<project xmlns="http://maven.apache.org/POM/4.0.0">\n  <parent>\n    <version>3.4.13</version>\n  </parent>\n</project>')
    version = root.child("parent").child("version")  # type: ignore[union-attr]
    assert root.tag == "project" and version is not None and version.text == "3.4.13" and version.line == 3


@pytest.mark.parametrize("doc", [BILLION_LAUGHS, XXE])
def test_xml_refuses_doctype_and_entities(doc: str) -> None:
    with pytest.raises(ParseError, match="document type"):
        parse_xml(doc)


def test_xml_malformed_is_parse_error() -> None:
    with pytest.raises(ParseError, match="invalid XML"):
        parse_xml("<project><parent></project>")


def test_yaml_multi_document_with_lines() -> None:
    docs = load_yaml_documents("a: 1\n---\nkind: Deployment\napiVersion: apps/v1\n")
    assert len(docs) == 2
    nodes = compose_yaml_documents("a: 1\n---\nkind: Deployment\napiVersion: apps/v1\n")
    assert mapping_scalars(nodes[1], "kind", "apiVersion") == {"kind": ("Deployment", 3), "apiVersion": ("apps/v1", 4)}


def test_yaml_does_not_construct_python_objects(tmp_path) -> None:  # type: ignore[no-untyped-def]
    marker = tmp_path / "pwned"
    doc = f"!!python/object/apply:os.system ['touch {marker}']\n"
    data = load_yaml_documents(doc)[0].data
    assert not marker.exists()  # nothing executed
    assert list(data) == [f"touch {marker}"]  # the tag is kept as data on a plain sequence


def test_yaml_alias_bomb_does_not_expand() -> None:
    bomb = "a: &a [x, x, x, x, x, x, x, x, x]\n" + "".join(f"{c}: &{c} [*{p}, *{p}, *{p}, *{p}, *{p}, *{p}, *{p}, *{p}, *{p}]\n"
                                                         for p, c in zip("abcdefgh", "bcdefghi", strict=True))
    docs = load_yaml_documents(bomb)
    assert docs[0].data["i"][0] is docs[0].data["h"]  # shared reference, not a copy


def test_yaml_invalid_and_duplicate_keys() -> None:
    with pytest.raises(ParseError):
        load_yaml_documents("a: [1, 2\n")
    with pytest.raises(ParseError):
        load_yaml_documents("a: 1\na: 2\n")


def test_toml_and_find_line() -> None:
    text = '[project]\ndependencies = ["fastapi==0.159.1"]\n'
    assert load_toml(text)["project"]["dependencies"] == ["fastapi==0.159.1"]
    assert find_line(text, "fastapi==0.159.1") == 2
    with pytest.raises(ParseError):
        load_toml("[project\n")
