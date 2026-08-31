from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
CATALOG = ROOT / "contracts/public/operations.v2.json"
MATRIX = Path(__file__).parent / "fixtures/i6_operation_matrix.json"


def test_i6_matrix_is_an_independent_exact_catalog_projection() -> None:
    catalog = json.loads(CATALOG.read_text(encoding="utf-8"))
    matrix = json.loads(MATRIX.read_text(encoding="utf-8"))
    assert matrix["schema_version"] == "bb.product_integration.i6_operation_matrix.v1"

    expected = {
        operation["operation_id"]: (
            operation["bindings"]["openapi"]["method"],
            operation["bindings"]["openapi"]["path"],
            operation["bindings"]["python_sdk"]["method"],
            operation["bindings"]["tui"]["action_id"],
        )
        for operation in catalog["operations"]
    }
    observed = {
        operation["operation_id"]: (
            operation["http_method"],
            operation["path"],
            operation["python_method"],
            operation["typescript_action"],
        )
        for operation in matrix["operations"]
    }

    assert len(matrix["operations"]) == len(observed) == 26
    assert observed == expected
    assert all(operation["cli"] for operation in matrix["operations"])
    assert all(
        operation["success_status"] in {200, 202} for operation in matrix["operations"]
    )
