from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Mapping, Sequence

try:
    from scripts.e4_parity.lane_definitions import DEFAULT_LANE_DEF_DIR, load_lane_defs
    from scripts.e4_parity.lane_inventory_utils import DEFAULT_INVENTORY_PATH, load_inventory
except ModuleNotFoundError:  # pragma: no cover - direct script execution
    from lane_definitions import DEFAULT_LANE_DEF_DIR, load_lane_defs
    from lane_inventory_utils import DEFAULT_INVENTORY_PATH, load_inventory

ROOT = Path(__file__).resolve().parents[2]
GENERATED_AT_UTC = "2026-07-07T00:25:00Z"


def _ct_test_id(lane_id: str, lane_def: Mapping[str, Any]) -> str:
    ct = lane_def.get("ct")
    if isinstance(ct, Mapping) and isinstance(ct.get("test_id"), str):
        return str(ct["test_id"])
    return f"CT-{lane_id.upper().replace('_', '-')}-C4"


def _run_field(lane_def: Mapping[str, Any], field: str, default: str) -> str:
    run = lane_def.get("run")
    if isinstance(run, Mapping) and isinstance(run.get(field), str):
        return str(run[field])
    return default

def _claim_id(lane_def: Mapping[str, Any]) -> str:
    return f"{lane_def['config_id']}_c4_support_claim"


def _builder(lane_def: Mapping[str, Any]) -> dict[str, Any] | None:
    capture = lane_def.get("capture")
    strategy = capture.get("strategy") if isinstance(capture, Mapping) else None
    argv = capture.get("argv") if isinstance(capture, Mapping) else None
    if argv is None or strategy == "probe_argv":
        return None
    if not isinstance(argv, list) or not all(isinstance(item, str) for item in argv):
        raise ValueError(f"lane_def {lane_def.get('lane_id')!r} has invalid capture.argv")
    return {"argv": list(argv), "cwd": "."}


def _reverify_command(lane_def: Mapping[str, Any]) -> dict[str, Any]:
    command = lane_def.get("reverify_command")
    if isinstance(command, Mapping) and isinstance(command.get("argv"), list):
        return {"argv": list(command["argv"]), "cwd": str(command.get("cwd", "."))}
    lane_id = str(lane_def["lane_id"])
    config_id = str(lane_def["config_id"])
    ct_id = _ct_test_id(lane_id, lane_def).lower().replace("-", "_")
    return {
        "argv": [
            ".venv/bin/python",
            "scripts/validate_e4_c4_chain.py",
            "--config-id",
            config_id,
            "--support-claim",
            f"docs/conformance/support_claims/{config_id}_c4_support_claim.json",
            "--evidence-manifest",
            f"docs/conformance/support_claims/{config_id}_c4_evidence_manifest.json",
            "--json-out",
            f"artifacts/conformance/node_gate/{ct_id}.json",
            "--check-only",
        ],
        "cwd": ".",
    }


def lane_inventory_row(lane_def: Mapping[str, Any]) -> dict[str, Any]:
    lane_id = str(lane_def["lane_id"])
    config_id = str(lane_def["config_id"])
    reverify = _reverify_command(lane_def)
    ct_argv = [item for item in reverify["argv"] if item != "--check-only"]
    return {
        "lane_id": lane_id,
        "config_id": config_id,
        "claim_id": _claim_id(lane_def),
        "phase": lane_id.split("_p", 1)[1].split("_", 1)[0].upper() if "_p" in lane_id else "P0",
        "kind": lane_def["kind"],
        "status": lane_def["status"],
        "points": lane_def["points"],
        "target_family": lane_def["target_family"],
        "target_version": lane_def["target_version"],
        "run_id": _run_field(lane_def, "run_id", "derived-from-lane-def"),
        "provider_model": _run_field(lane_def, "provider_model", "no-provider"),
        "sandbox_mode": _run_field(lane_def, "sandbox_mode", "read-only"),
        "primitives": list(lane_def.get("claim", {}).get("scope", {}).get("behaviors", [])),
        "builder": _builder(lane_def),
        "comparator_id": lane_def.get("compare", {}).get("comparator"),
        "ct": {
            "test_id": _ct_test_id(lane_id, lane_def),
            "gate_level": "C4",
            "command": {"argv": ct_argv, "cwd": reverify["cwd"]},
        },
        "reverify_command": reverify,
        "artifacts_root": lane_def.get("artifacts_root"),
    }


def build_inventory(lane_defs: Mapping[str, Mapping[str, Any]]) -> dict[str, Any]:
    rows = [lane_inventory_row(lane_defs[lane_id]) for lane_id in sorted(lane_defs)]
    return {
        "schema_version": "bb.e4.lane_inventory.v1",
        "inventory_id": "e4_lane_inventory_from_lane_defs_v1",
        "generated_at_utc": GENERATED_AT_UTC,
        "revision": 1,
        "lanes": rows,
    }


def compare_with_canonical(generated: Mapping[str, Any], canonical: Mapping[str, Any]) -> dict[str, Any]:
    generated_rows = {row["lane_id"]: row for row in generated.get("lanes", []) if isinstance(row, Mapping)}
    canonical_rows = {row["lane_id"]: row for row in canonical.get("lanes", []) if isinstance(row, Mapping)}
    required_fields = ("config_id", "claim_id", "kind", "status", "points", "target_family", "target_version", "builder", "ct", "reverify_command")
    errors: list[str] = []
    for lane_id, generated_row in sorted(generated_rows.items()):
        canonical_row = canonical_rows.get(lane_id)
        if canonical_row is None:
            errors.append(f"canonical inventory missing lane {lane_id}")
            continue
        for field in required_fields:
            if generated_row.get(field) != canonical_row.get(field):
                errors.append(f"{lane_id}.{field} mismatch")
    for lane_id in sorted(set(canonical_rows) - set(generated_rows)):
        errors.append(f"lane_def missing canonical lane {lane_id}")
    return {
        "schema_version": "bb.e4.lane_inventory_consistency_report.v1",
        "generated_at_utc": GENERATED_AT_UTC,
        "generated_lane_count": len(generated_rows),
        "canonical_lane_count": len(canonical_rows),
        "errors": errors,
        "ok": not errors,
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Generate and validate E4 lane inventory rows from lane_def YAML.")
    parser.add_argument("--lane-def-dir", default=str(DEFAULT_LANE_DEF_DIR))
    parser.add_argument("--canonical", default=str(DEFAULT_INVENTORY_PATH))
    parser.add_argument("--out", default="")
    parser.add_argument("--report", default="")
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args(argv)

    generated = build_inventory(load_lane_defs(Path(args.lane_def_dir)))
    report = compare_with_canonical(generated, load_inventory(Path(args.canonical)))
    if args.out:
        Path(args.out).write_text(json.dumps(generated, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    if args.report:
        Path(args.report).write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    if not args.out and not args.report:
        print(json.dumps({"inventory": generated, "report": report}, indent=2, sort_keys=True))
    return 3 if args.check and not report["ok"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
