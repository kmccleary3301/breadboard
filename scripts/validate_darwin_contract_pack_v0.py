from __future__ import annotations

import argparse
import json
from pathlib import Path

from jsonschema import Draft202012Validator


ROOT = Path(__file__).resolve().parents[1]


SCHEMA_CASES: list[tuple[str, list[str]]] = [
    ("docs/contracts/darwin/schemas/campaign_spec_v0.schema.json", ["tests/fixtures/contracts/darwin/sample_campaign_spec_v0.json"]),
    ("docs/contracts/darwin/schemas/policy_bundle_v0.schema.json", ["tests/fixtures/contracts/darwin/sample_policy_bundle_v0.json"]),
    ("docs/contracts/darwin/schemas/candidate_artifact_v0.schema.json", ["tests/fixtures/contracts/darwin/sample_candidate_artifact_v0.json"]),
    ("docs/contracts/darwin/schemas/evaluation_record_v0.schema.json", ["tests/fixtures/contracts/darwin/sample_evaluation_record_v0.json"]),
    ("docs/contracts/darwin/schemas/evidence_bundle_v0.schema.json", ["tests/fixtures/contracts/darwin/sample_evidence_bundle_v0.json"]),
    ("docs/contracts/darwin/schemas/claim_record_v0.schema.json", ["tests/fixtures/contracts/darwin/sample_claim_record_v0.json"]),
    ("docs/contracts/darwin/schemas/lane_registry_v0.schema.json", ["docs/contracts/darwin/registries/lane_registry_v0.json"]),
    ("docs/contracts/darwin/schemas/policy_registry_v0.schema.json", ["docs/contracts/darwin/registries/policy_registry_v0.json"]),
    ("docs/contracts/darwin/schemas/weekly_evidence_packet_v0.schema.json", ["tests/fixtures/contracts/darwin/sample_weekly_evidence_packet_v0.json"]),
]


def _load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def build_report(root: Path = ROOT) -> dict:
    report: dict[str, object] = {"ok": True, "schemas": []}
    schema_rows: list[dict[str, object]] = []
    for schema_rel, fixture_rels in SCHEMA_CASES:
        schema_path = root / schema_rel
        schema = _load_json(schema_path)
        Draft202012Validator.check_schema(schema)
        validator = Draft202012Validator(schema)
        fixture_rows: list[dict[str, object]] = []
        for fixture_rel in fixture_rels:
            fixture_path = root / fixture_rel
            fixture = _load_json(fixture_path)
            errors = [
                {
                    "path": ".".join(str(part) for part in err.absolute_path) if err.absolute_path else "$",
                    "message": err.message,
                }
                for err in validator.iter_errors(fixture)
            ]
            fixture_rows.append({"path": fixture_rel, "ok": not errors, "errors": errors})
            if errors:
                report["ok"] = False
        schema_rows.append({"schema": schema_rel, "fixtures": fixture_rows})
    report["schemas"] = schema_rows
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate DARWIN contract pack v0 schemas and canonical fixtures.")
    parser.add_argument("--json", action="store_true", help="Emit machine-readable JSON report.")
    args = parser.parse_args()

    report = build_report()
    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        for schema_row in report["schemas"]:
            print(schema_row["schema"])
            for fixture_row in schema_row["fixtures"]:
                status = "ok" if fixture_row["ok"] else "error"
                print(f"  - {fixture_row['path']}: {status}")
                for error in fixture_row["errors"]:
                    print(f"      {error['path']}: {error['message']}")
        print(f"overall_ok={report['ok']}")
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
