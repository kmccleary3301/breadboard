from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, Iterable, List

from jsonschema import Draft202012Validator, RefResolver

ROOT = Path(__file__).resolve().parents[1]
SCHEMA_DIR = ROOT / "contracts" / "kernel" / "schemas"
FIXTURE_DIR = ROOT / "conformance" / "engine_fixtures"
MANIFEST_PATH = FIXTURE_DIR / "python_reference_manifest_v1.json"

P4_PRIMITIVE_FAMILIES = {
    "effective_config_graph",
    "config_mutation_record",
    "context_resource_pack",
    "capability_registry",
    "effective_tool_surface",
    "extension_hook_execution",
    "resource_ref",
    "resource_access",
    "blob_ref",
    "external_protocol_session",
    "provider_route",
    "provider_exchange",
    "memory_compaction_plan",
    "work_item",
    "permission",
    "effective_operation_policy",
    "side_effect_broker",
    "projection_event",
}


def _load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _schema_store() -> Dict[str, Any]:
    return {path.name: _load_json(path) for path in SCHEMA_DIR.glob("*.json")}


def _fixture_label(path: Path) -> str:
    return str(path.relative_to(FIXTURE_DIR))


def _fixture_contract(fixture: Dict[str, Any], path: Path) -> str:
    contract = fixture.get("contract")
    if not isinstance(contract, str) or not contract:
        raise ValueError(f"{_fixture_label(path)} missing contract")
    return contract


def _fixture_reference_output(fixture: Dict[str, Any], path: Path) -> Dict[str, Any]:
    reference_output = fixture.get("reference_output")
    if isinstance(reference_output, dict):
        return reference_output

    example_ref = fixture.get("example_ref")
    if isinstance(example_ref, str) and example_ref:
        example_path = (path.parent / example_ref).resolve()
        try:
            example_path.relative_to(ROOT)
        except ValueError as exc:
            raise ValueError(f"{_fixture_label(path)} example_ref escapes repo") from exc
        example_output = _load_json(example_path)
        if isinstance(example_output, dict):
            return example_output

    raise ValueError(f"{_fixture_label(path)} missing object reference_output")


def _validator_for(contract: str, schemas: Dict[str, Any]) -> Draft202012Validator:
    schema_name = f"{contract}.schema.json"
    schema = schemas.get(schema_name)
    if schema is None:
        raise ValueError(f"missing schema for {contract}")
    return Draft202012Validator(
        schema,
        resolver=RefResolver.from_schema(schema, store=schemas),
    )


def _manifest_evidence_labels() -> set[str]:
    manifest = _load_json(MANIFEST_PATH)
    labels: set[str] = set()
    for row in manifest.get("rows", []):
        evidence = row.get("evidence", [])
        if isinstance(evidence, list):
            labels.update(label for label in evidence if isinstance(label, str))
    return labels


def _split_families(values: Iterable[str]) -> List[str]:
    families: list[str] = []
    for value in values:
        families.extend(part.strip() for part in value.split(",") if part.strip())
    return families


def _validate_family(
    family: str,
    schemas: Dict[str, Any],
    manifest_labels: set[str],
    require_invalid: bool,
) -> Dict[str, Any]:
    family_dir = FIXTURE_DIR / family
    result: Dict[str, Any] = {
        "family": family,
        "ok": True,
        "valid": [],
        "invalid": [],
        "errors": [],
    }
    if not family_dir.is_dir():
        result["ok"] = False
        result["errors"].append(f"missing fixture family directory: {family}")
        return result

    valid_paths = sorted(
        path
        for path in family_dir.glob("*.json")
        if not path.name.startswith("invalid")
    )
    invalid_paths = sorted(family_dir.glob("invalid*.json"))
    if not valid_paths:
        result["ok"] = False
        result["errors"].append(f"{family} has no valid fixtures")
    if require_invalid and not invalid_paths:
        result["ok"] = False
        result["errors"].append(f"{family} has no invalid fixtures")

    expected_contract = f"bb.{family}.v1" if family in P4_PRIMITIVE_FAMILIES else None

    for path in valid_paths:
        label = _fixture_label(path)
        try:
            fixture = _load_json(path)
            contract = _fixture_contract(fixture, path)
            if expected_contract is not None and contract != expected_contract:
                result["ok"] = False
                result["errors"].append(
                    f"valid fixture {label} declares {contract}; expected {expected_contract}"
                )
            reference_output = _fixture_reference_output(fixture, path)
            errors = list(_validator_for(contract, schemas).iter_errors(reference_output))
            if errors:
                result["ok"] = False
                result["errors"].append(
                    f"valid fixture {label} failed {contract}: {errors[0].message}"
                )
            in_manifest = label in manifest_labels
            if not in_manifest:
                result["ok"] = False
                result["errors"].append(f"valid fixture {label} missing from manifest")
            result["valid"].append(
                {
                    "path": label,
                    "contract": contract,
                    "expected_contract": expected_contract,
                    "schema_version": reference_output.get("schema_version"),
                    "manifest_evidence": in_manifest,
                    "validation_errors": len(errors),
                }
            )
        except Exception as exc:  # pragma: no cover - surfaced in JSON report.
            result["ok"] = False
            result["errors"].append(str(exc))

    for path in invalid_paths:
        label = _fixture_label(path)
        try:
            fixture = _load_json(path)
            contract = _fixture_contract(fixture, path)
            if expected_contract is not None and contract != expected_contract:
                result["ok"] = False
                result["errors"].append(
                    f"invalid fixture {label} declares {contract}; expected {expected_contract}"
                )
            reference_output = _fixture_reference_output(fixture, path)
            errors = list(_validator_for(contract, schemas).iter_errors(reference_output))
            if not errors:
                result["ok"] = False
                result["errors"].append(f"invalid fixture {label} unexpectedly passed {contract}")
            result["invalid"].append(
                {
                    "path": label,
                    "contract": contract,
                    "expected_contract": expected_contract,
                    "schema_version": reference_output.get("schema_version"),
                    "validation_errors": len(errors),
                    "first_error": errors[0].message if errors else None,
                }
            )
        except Exception as exc:  # pragma: no cover - surfaced in JSON report.
            result["ok"] = False
            result["errors"].append(str(exc))

    return result


def build_report(
    families: List[str],
    scenario_id: str | None,
    require_invalid: bool,
) -> Dict[str, Any]:
    schemas = _schema_store()
    manifest_labels = _manifest_evidence_labels()
    family_reports = [
        _validate_family(family, schemas, manifest_labels, require_invalid)
        for family in families
    ]
    errors = [
        error
        for family_report in family_reports
        for error in family_report.get("errors", [])
    ]
    contracts = sorted(
        {
            item["contract"]
            for family_report in family_reports
            for group in ("valid", "invalid")
            for item in family_report[group]
        }
    )
    return {
        "schema_version": "bb.kernel_primitive_ct_report.v1",
        "scenario_id": scenario_id,
        "ok": not errors,
        "families": families,
        "summary": {
            "family_count": len(family_reports),
            "valid_fixture_count": sum(len(report["valid"]) for report in family_reports),
            "invalid_fixture_count": sum(len(report["invalid"]) for report in family_reports),
            "contract_count": len(contracts),
            "contracts": contracts,
        },
        "family_reports": family_reports,
        "errors": errors,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Validate kernel primitive CT fixture coverage."
    )
    parser.add_argument(
        "--families",
        action="append",
        default=[],
        help="Fixture family name or comma-separated names. Repeatable.",
    )
    parser.add_argument(
        "--all-p4",
        action="store_true",
        help="Validate every P4 primitive fixture family.",
    )
    parser.add_argument("--scenario-id", default=None)
    parser.add_argument("--json-out", required=True)
    parser.add_argument(
        "--allow-missing-invalid",
        action="store_true",
        help="Do not require an invalid fixture for every selected family.",
    )
    args = parser.parse_args(argv)

    families = sorted(P4_PRIMITIVE_FAMILIES) if args.all_p4 else _split_families(args.families)
    if not families:
        parser.error("provide --families or --all-p4")
    unknown = sorted(set(families) - {path.name for path in FIXTURE_DIR.iterdir() if path.is_dir()})
    if unknown:
        parser.error(f"unknown fixture families: {', '.join(unknown)}")

    report = build_report(
        families=families,
        scenario_id=args.scenario_id,
        require_invalid=not args.allow_missing_invalid,
    )
    output_path = ROOT / args.json_out
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({"ok": report["ok"], "json_out": str(output_path)}, sort_keys=True))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
