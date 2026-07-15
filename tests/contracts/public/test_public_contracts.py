from __future__ import annotations

import copy
import importlib
import shutil

import pytest

from scripts.quality.build_surface_inventory import build_inventory
from scripts.quality.run_axis_smoke import run_axis_smoke
from scripts.quality.validate_public_contracts import (
    ContractValidationError,
    FROZEN_OPERATION_IDS,
    PUBLIC_DIR,
    SURFACES,
    canonical_bytes,
    load_json,
    validate_axis_manifest,
    validate_catalog,
    validate_record_surface,
    validate_public_contracts,
)


def catalog() -> dict:
    return load_json(PUBLIC_DIR / "operations.v1.json")


def test_catalog_is_the_frozen_non_active_six_surface_candidate() -> None:
    value = catalog()
    validate_catalog(value)
    assert tuple(sorted(row["operation_id"] for row in value["operations"])) == FROZEN_OPERATION_IDS
    assert len(value["operations"]) == 45
    assert value["status"] == "candidate"
    for row in value["operations"]:
        assert set(row["bindings"]) == set(SURFACES)
        assert row["status"] == "candidate"
        assert {binding["status"] for binding in row["bindings"].values()} == {"candidate"}


def test_duplicate_operation_id_is_rejected() -> None:
    value = catalog()
    value["operations"][1]["operation_id"] = value["operations"][0]["operation_id"]
    with pytest.raises(ContractValidationError, match="duplicate operation IDs"):
        validate_catalog(value)


def test_missing_surface_binding_is_rejected() -> None:
    value = catalog()
    del value["operations"][0]["bindings"]["tui"]
    with pytest.raises(ContractValidationError, match="tui.*required property|missing surface bindings"):
        validate_catalog(value)


@pytest.mark.parametrize("surface", SURFACES)
def test_duplicate_surface_binding_identity_is_rejected(surface: str) -> None:
    value = catalog()
    duplicate = copy.deepcopy(value["operations"][0]["bindings"][surface])
    if surface == "openapi":
        duplicate["operation_id"] = value["operations"][1]["operation_id"]
    value["operations"][1]["bindings"][surface] = duplicate
    with pytest.raises(ContractValidationError, match="duplicate"):
        validate_catalog(value)


def test_openapi_operation_id_must_match_canonical_operation_id() -> None:
    value = catalog()
    value["operations"][0]["bindings"]["openapi"]["operation_id"] = "system.health"
    with pytest.raises(ContractValidationError, match="canonical operation ID"):
        validate_catalog(value)


def test_active_or_unknown_status_is_rejected() -> None:
    value = catalog()
    value["operations"][0]["status"] = "active"
    with pytest.raises(ContractValidationError, match="candidate"):
        validate_catalog(value)


def test_duplicated_inline_record_models_are_rejected() -> None:
    value = catalog()
    model = {"type": "object", "properties": {"id": {"type": "string"}}}
    value["operations"][0]["input_schema"] = copy.deepcopy(model)
    value["operations"][1]["input_schema"] = copy.deepcopy(model)
    with pytest.raises(ContractValidationError, match="schema ID|inline record models|not of type 'string'"):
        validate_catalog(value)


def test_record_schema_cannot_be_owned_by_two_public_roles() -> None:
    value = load_json(PUBLIC_DIR / "record_surface.v1.json")
    value["roles"][1]["schema_ids"] = value["roles"][0]["schema_ids"]
    with pytest.raises(ContractValidationError, match="multiple roles"):
        validate_record_surface(value)


def test_count_preserving_record_role_substitution_is_rejected() -> None:
    value = load_json(PUBLIC_DIR / "record_surface.v1.json")
    value["roles"][0]["role_id"] = "substituted_role"
    value["roles"][0]["label"] = "substituted role"
    with pytest.raises(ContractValidationError, match="frozen public record roles mismatch"):
        validate_record_surface(value)


def test_staged_contracts_use_their_staged_schemas(tmp_path) -> None:
    staged = tmp_path / "public"
    shutil.copytree(PUBLIC_DIR, staged)
    schema_path = staged / "schemas" / "bb.public_operation_catalog.v1.schema.json"
    schema = load_json(schema_path)
    schema["properties"]["contract_id"]["const"] = "bb.staged_only.invalid"
    schema_path.write_bytes(canonical_bytes(schema))
    with pytest.raises(ContractValidationError):
        validate_public_contracts(staged)


def test_inventory_is_a_checked_in_fixed_point_with_honest_gaps() -> None:
    first = build_inventory()
    second = build_inventory()
    assert canonical_bytes(first) == canonical_bytes(second)
    assert canonical_bytes(first) == (PUBLIC_DIR / "surface_inventory.v1.json").read_bytes()
    assert first["parity_claimed"] is False
    assert first["candidate_status"] == "candidate"
    assert all(summary["gaps"] > 0 for summary in first["summary"].values())
    assert all(summary["detected"] + summary["gaps"] == 45 for summary in first["summary"].values())


def test_axis_manifest_and_runner_emit_only_the_frozen_names_without_scores() -> None:
    manifest_path = PUBLIC_DIR / "axis_smoke.v1.json"
    manifest = load_json(manifest_path)
    validate_axis_manifest(manifest)
    expected = [
        "maintainability", "atomicity_composability", "generalizability",
        "development_velocity", "versatility", "developer_experience",
        "interpretability", "intuitive_configuration", "forward_compatibility",
        "blast_radius",
    ]
    result = run_axis_smoke(manifest_path, execute=False)
    assert [row["axis_id"] for row in result["axis_results"]] == expected
    assert all(row["score"] is None for row in result["axis_results"])
    assert result["score_promoted"] is False
    assert result["passed"] is False


def test_axis_manifest_cannot_embed_a_promoted_score() -> None:
    manifest = load_json(PUBLIC_DIR / "axis_smoke.v1.json")
    manifest["axes"][0]["score"] = 10
    with pytest.raises(ContractValidationError, match="[Aa]dditional properties"):
        validate_axis_manifest(manifest)


def test_product_boundary_is_declarative_and_exports_no_implementation() -> None:
    product = importlib.import_module("breadboard.product")
    assert product.__all__ == []
    public_names = {name for name in vars(product) if not name.startswith("_")}
    assert public_names == set()
