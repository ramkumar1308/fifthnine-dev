"""JSON renderer. Output is byte-identical for identical inputs (D6): no timestamps, sorted collections,
stable key order from the v1 models, LF line endings."""

from __future__ import annotations

import json

from fifthnine.schema.v1 import ScanResult


def render_json(result: ScanResult) -> str:
    data = json.loads(result.model_dump_json(by_alias=True))
    return json.dumps(data, indent=2, ensure_ascii=False) + "\n"
