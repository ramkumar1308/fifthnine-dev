"""Export frozen v1 JSON Schemas from the Pydantic models. Run from the repository root:

    PYTHONPATH=src python scripts/export_schemas.py
"""

import json
from pathlib import Path

from fifthnine.schema.config_v1 import Baseline, FifthNineConfig
from fifthnine.schema.support_v1 import SupportMatrix
from fifthnine.schema.v1 import ScanResult

OUT = Path("schemas")
OUT.mkdir(exist_ok=True)
for name, model in {"result.v1.json": ScanResult, "config.v1.json": FifthNineConfig, "baseline.v1.json": Baseline, "support-matrix.v1.json": SupportMatrix}.items():
    schema = model.model_json_schema(by_alias=True)
    schema["$schema"] = "https://json-schema.org/draft/2020-12/schema"
    (OUT / name).write_text(json.dumps(schema, indent=2, sort_keys=True) + "\n")
    print("wrote", OUT / name)
