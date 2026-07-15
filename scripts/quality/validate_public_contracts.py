#!/usr/bin/env python3
"""Validate candidate public contract sources without activating them."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Iterable

from jsonschema import Draft202012Validator

ROOT = Path(__file__).resolve().parents[2]
PUBLIC_DIR = ROOT / "contracts" / "public"
SCHEMA_DIR = PUBLIC_DIR / "schemas"
SURFACES = ("bbh", "openapi", "python_sdk", "typescript_sdk", "tui", "docs")
FROZEN_OPERATION_IDS = (
    "artifact.get", "artifact.list", "artifact.verify",
    "claim.evidence", "claim.get", "claim.list", "claim.reverify",
    "harness.create", "harness.explain", "harness.get", "harness.list",
    "harness.lock", "harness.update", "harness.validate", "harness_lock.get",
    "integration.get", "integration.list", "integration.probe",
    "lane.capture", "lane.claim", "lane.compare", "lane.create", "lane.get",
    "lane.list", "lane.lock", "lane.normalize", "lane.replay", "lane.run",
    "lane.stage_report", "lane.validate", "lane_execution.cancel",
    "lane_execution.get", "lane_lock.get",
    "session.approve", "session.artifacts", "session.cancel", "session.events",
    "session.get", "session.list", "session.resume", "session.send_input",
    "session.start", "system.describe", "system.health", "system.schemas",
)
FROZEN_RECORD_ROLES = frozenset({
    ("harness_definition", "Harness Definition"), ("effective_harness_lock", "Effective Harness Lock"),
    ("validation_report", "validation report"), ("explanation_effective_graph_report", "explanation/effective-graph report"),
    ("session", "Session"), ("kernel_event", "Kernel Event"),
    ("operator_approval_interaction_record", "operator approval/interaction record"),
    ("provider_route_lock_capability_probe", "Provider Route Lock and capability probe"),
    ("provider_exchange", "Provider Exchange"), ("tool_call_execution_outcome", "tool call and Tool Execution Outcome"),
    ("integration_descriptor", "integration descriptor"), ("artifact_blob_ref_manifest", "Artifact/blob ref and artifact manifest"),
    ("lane_manifest", "Lane Manifest"), ("lane_lock", "Lane Lock"),
    ("lane_execution_journey_report", "lane execution/journey report"), ("stage_report", "stage report"),
    ("replay_plan", "replay plan"), ("replay_execution", "replay execution"),
    ("comparator_report", "comparator report"), ("exact_scope_claim", "Exact-Scope Claim"),
    ("claim_reverification_report", "claim reverification report"), ("stable_problem_error", "stable problem/error"),
    ("pagination_list_envelope", "pagination/list envelope"), ("cli_result_envelope", "CLI/result envelope"),
})


class ContractValidationError(ValueError):
    """A candidate public contract violates its frozen contract."""


def load_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ContractValidationError(f"{path}: {exc}") from exc
    if not isinstance(value, dict):
        raise ContractValidationError(f"{path}: root must be an object")
    return value


def canonical_bytes(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode()


def _schema_errors(instance: Any, schema_name: str, schema_dir: Path = SCHEMA_DIR) -> list[str]:
    schema = load_json(schema_dir / schema_name)
    errors = Draft202012Validator(schema).iter_errors(instance)
    return [
        f"{'.'.join(map(str, error.absolute_path)) or '<root>'}: {error.message}"
        for error in sorted(errors, key=lambda item: (tuple(map(str, item.absolute_path)), item.message))
    ]


def _raise_if_errors(errors: Iterable[str]) -> None:
    items = list(errors)
    if items:
        raise ContractValidationError("\n".join(items))


def _reject_inline_models(value: Any, path: tuple[str, ...] = ()) -> None:
    if isinstance(value, dict):
        for key, child in value.items():
            child_path = (*path, key)
            if key in {"inline_model", "inline_schema", "model"} and isinstance(child, dict):
                raise ContractValidationError(
                    f"{'.'.join(child_path)}: inline record models are forbidden; reference a schema ID"
                )
            if key.endswith("_schema") and child is not None and not isinstance(child, str):
                raise ContractValidationError(f"{'.'.join(child_path)}: schema binding must be a schema ID")
            _reject_inline_models(child, child_path)
    elif isinstance(value, list):
        for index, child in enumerate(value):
            _reject_inline_models(child, (*path, str(index)))


def validate_catalog(catalog: dict[str, Any], schema_dir: Path = SCHEMA_DIR) -> None:
    _raise_if_errors(_schema_errors(catalog, "bb.public_operation_catalog.v1.schema.json", schema_dir))
    _reject_inline_models(catalog)
    rows = catalog["operations"]
    operation_ids = [row["operation_id"] for row in rows]
    duplicates = sorted({item for item in operation_ids if operation_ids.count(item) > 1})
    if duplicates:
        raise ContractValidationError(f"duplicate operation IDs: {', '.join(duplicates)}")
    expected, actual = set(FROZEN_OPERATION_IDS), set(operation_ids)
    if expected != actual:
        missing = sorted(expected - actual)
        extra = sorted(actual - expected)
        raise ContractValidationError(f"frozen operation set mismatch; missing={missing}, extra={extra}")
    identity_fields = {
        "bbh": ("command",), "openapi": ("method", "path"),
        "python_sdk": ("method",), "typescript_sdk": ("method",),
        "tui": ("action_id",), "docs": ("slug",),
    }
    seen_bindings: dict[str, dict[tuple[str, ...], str]] = {surface: {} for surface in SURFACES}
    for row in rows:
        if row["status"] != "candidate":
            raise ContractValidationError(f"{row['operation_id']}: status must remain candidate")
        missing_surfaces = sorted(set(SURFACES) - set(row["bindings"]))
        if missing_surfaces:
            raise ContractValidationError(
                f"{row['operation_id']}: missing surface bindings: {', '.join(missing_surfaces)}"
            )
        for surface in SURFACES:
            if row["bindings"][surface]["status"] != "candidate":
                raise ContractValidationError(f"{row['operation_id']}.{surface}: status must remain candidate")
            binding = row["bindings"][surface]
            if surface == "openapi" and binding["operation_id"] != row["operation_id"]:
                raise ContractValidationError(
                    f"{row['operation_id']}.openapi: operation_id must equal the canonical operation ID"
                )
            identity = tuple(binding[field] for field in identity_fields[surface])
            if identity in seen_bindings[surface]:
                raise ContractValidationError(
                    f"duplicate {surface} binding identity for {seen_bindings[surface][identity]} and {row['operation_id']}: {identity}"
                )
            seen_bindings[surface][identity] = row["operation_id"]


def validate_record_surface(record_surface: dict[str, Any], schema_dir: Path = SCHEMA_DIR) -> None:
    _raise_if_errors(_schema_errors(record_surface, "bb.public_record_surface.v1.schema.json", schema_dir))
    _reject_inline_models(record_surface)
    role_ids = [row["role_id"] for row in record_surface["roles"]]
    if len(role_ids) != len(set(role_ids)):
        raise ContractValidationError("duplicate public record role IDs")
    actual_roles = {(row["role_id"], row["label"]) for row in record_surface["roles"]}
    if actual_roles != FROZEN_RECORD_ROLES:
        missing = sorted(FROZEN_RECORD_ROLES - actual_roles)
        extra = sorted(actual_roles - FROZEN_RECORD_ROLES)
        raise ContractValidationError(f"frozen public record roles mismatch; missing={missing}, extra={extra}")
    schema_ids = [schema_id for row in record_surface["roles"] for schema_id in row["schema_ids"]]
    duplicates = sorted({item for item in schema_ids if schema_ids.count(item) > 1})
    if duplicates:
        raise ContractValidationError(f"record schemas assigned to multiple roles: {', '.join(duplicates)}")


def validate_axis_manifest(manifest: dict[str, Any], schema_dir: Path = SCHEMA_DIR) -> None:
    _raise_if_errors(_schema_errors(manifest, "bb.public_axis_smoke_manifest.v1.schema.json", schema_dir))
    expected_axes = (
        ("maintainability", ("candidate_contracts", "public_contract_tests", "surface_inventory_fixed_point")),
        ("atomicity_composability", ("candidate_contracts", "public_contract_tests")),
        ("generalizability", ("candidate_contracts", "surface_inventory_fixed_point")),
        ("development_velocity", ("candidate_contracts", "public_contract_tests")),
        ("versatility", ("behavior_preservation", "candidate_contracts")),
        ("developer_experience", ("candidate_contracts", "public_contract_tests")),
        ("interpretability", ("candidate_contracts", "surface_inventory_fixed_point")),
        ("intuitive_configuration", ("candidate_contracts", "public_contract_tests")),
        ("forward_compatibility", ("candidate_contracts", "surface_inventory_fixed_point")),
        ("blast_radius", ("behavior_preservation", "product_boundary", "public_contract_tests")),
    )
    actual_axes = tuple((row["id"], tuple(row["check_ids"])) for row in manifest["axes"])
    if actual_axes != expected_axes:
        raise ContractValidationError(f"axis manifest differs from immutable bb.north_star.qc.v1 smoke mapping: {actual_axes}")
    expected_checks = {
        "behavior_preservation": ["python", "-m", "pytest", "-q", "tests/test_breadboard_cli_g2.py", "tests/test_breadboard_cli_harness_run.py", "tests/test_breadboard_sdk_client.py", "tests/test_e4_api_surface.py"],
        "candidate_contracts": ["python", "scripts/quality/validate_public_contracts.py"],
        "product_boundary": ["python", "-c", "import breadboard.product; assert breadboard.product.__all__ == []"],
        "public_contract_tests": ["python", "-m", "pytest", "-q", "tests/contracts/public"],
        "surface_inventory_fixed_point": ["python", "scripts/quality/build_surface_inventory.py", "--check"],
    }
    if manifest["checks"] != expected_checks:
        raise ContractValidationError("axis smoke checks differ from the immutable targeted command set")


def validate_public_contracts(public_dir: Path = PUBLIC_DIR) -> None:
    schema_dir = public_dir / "schemas"
    validate_catalog(load_json(public_dir / "operations.v1.json"), schema_dir)
    validate_record_surface(load_json(public_dir / "record_surface.v1.json"), schema_dir)
    validate_axis_manifest(load_json(public_dir / "axis_smoke.v1.json"), schema_dir)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--public-dir", type=Path, default=PUBLIC_DIR)
    args = parser.parse_args(argv)
    try:
        validate_public_contracts(args.public_dir)
    except ContractValidationError as exc:
        print(f"public contract validation failed: {exc}")
        return 1
    print("public candidate contracts valid: 45 operations, 6 bindings each, non-active")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
