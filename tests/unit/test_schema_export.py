"""The committed JSON Schemas must match the models (no drift between docs and code)."""

import json

import pytest
from click.testing import CliRunner

from fifthnine.cli import cli

from ..conftest import ROOT


@pytest.mark.parametrize(("which", "file"), [("result", "result.v1.json"), ("config", "config.v1.json"),
                                             ("baseline", "baseline.v1.json"), ("support-matrix", "support-matrix.v1.json")])
def test_committed_schema_matches_model(which: str, file: str) -> None:
    printed = json.loads(CliRunner().invoke(cli, ["schema", which]).output)
    assert printed == json.loads((ROOT / "schemas" / file).read_text())
