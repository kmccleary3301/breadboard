from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any, Mapping, Sequence

try:
    from scripts.e4_parity.lane_definitions import DEFAULT_LANE_DEF_DIR, load_lane_defs
    from scripts.e4_parity.lane_inventory_utils import DEFAULT_INVENTORY_PATH, load_inventory
except ModuleNotFoundError:  # pragma: no cover - direct script execution
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    from scripts.e4_parity.lane_definitions import DEFAULT_LANE_DEF_DIR, load_lane_defs
    from scripts.e4_parity.lane_inventory_utils import DEFAULT_INVENTORY_PATH, load_inventory

ROOT = Path(__file__).resolve().parents[2]
WORKSPACE = ROOT.parent
SCORE_SUBLEDGER_PATH = WORKSPACE / "docs_tmp" / "phase_15" / "BB_E4_SCORE_SUBLEDGER.json"
_SCORE_SUBLEDGER_CACHE: dict[str, Any] | None = None
RETIRED_EVIDENCE_PINS_PATH = ROOT / "config" / "e4_retired_evidence_pins.json"
_RETIRED_EVIDENCE_PINS_CACHE: dict[str, Any] | None = None
GENERATED_AT_UTC = "2026-07-07T00:25:00Z"

_MANIFEST_ROLE_TO_INVENTORY_KEY = {
    "capture_ref": "capture",
    "comparator_ref": "comparator",
    "replay_ref": "replay",
    "support_claim_ref": "support_claim",
    "work_item_ref": "work_item",
}


def _flag_value(argv: Sequence[Any], flag: str) -> str | None:
    for index, item in enumerate(argv[:-1]):
        if item == flag and isinstance(argv[index + 1], str):
            return str(argv[index + 1])
    return None


def _artifact_roles(
    lane_id: str,
    reverify: Mapping[str, Any],
    *,
    manifest_path: str | None = None,
) -> dict[str, str]:
    argv = reverify.get("argv")
    if manifest_path is None:
        manifest_path = (
            _flag_value(argv, "--evidence-manifest")
            if isinstance(argv, Sequence)
            else None
        )
    role_keys = {"evidence_manifest", "node_gate"}
    if manifest_path and (ROOT / manifest_path).exists():
        manifest_file = ROOT / manifest_path
        manifest = json.loads(manifest_file.read_text(encoding="utf-8"))
        artifacts = manifest.get("artifacts", []) if isinstance(manifest, Mapping) else []
        if isinstance(artifacts, list):
            for artifact in artifacts:
                role = artifact.get("role") if isinstance(artifact, Mapping) else None
                if isinstance(role, str) and role:
                    role_keys.add(_MANIFEST_ROLE_TO_INVENTORY_KEY.get(role, role))
    return {role_key: f"{lane_id}:{role_key}" for role_key in sorted(role_keys)}

def _load_score_subledger() -> Mapping[str, Any]:
    global _SCORE_SUBLEDGER_CACHE
    if _SCORE_SUBLEDGER_CACHE is None:
        _SCORE_SUBLEDGER_CACHE = json.loads(SCORE_SUBLEDGER_PATH.read_text(encoding="utf-8"))
    return _SCORE_SUBLEDGER_CACHE


def _support_claim_path(reverify: Mapping[str, Any]) -> str | None:
    argv = reverify.get("argv")
    return _flag_value(argv, "--support-claim") if isinstance(argv, Sequence) else None



def _score_row_for_support_claim(support_claim_path: str | None) -> Mapping[str, Any]:
    if support_claim_path is None:
        return {}
    rows = _load_score_subledger().get("score_rows", [])
    if not isinstance(rows, list):
        return {}
    for row in rows:
        if isinstance(row, Mapping) and row.get("support_claim_ref") == support_claim_path:
            return row
    return {}


def _feature_ids_from_refs(refs: Any) -> list[str]:
    if not isinstance(refs, list):
        return []
    feature_ids: list[str] = []
    for ref in refs:
        if not isinstance(ref, str):
            continue
        parts = ref.split("#")
        if len(parts) >= 2 and parts[1]:
            feature_ids.append(parts[1])
    return sorted(dict.fromkeys(feature_ids))



def _ct_test_id(lane_id: str, lane_def: Mapping[str, Any]) -> str:
    ct = lane_def.get("ct")
    if isinstance(ct, Mapping) and isinstance(ct.get("test_id"), str):
        return str(ct["test_id"])
    return f"CT-{lane_id.upper().replace('_', '-')}-C4"

def _phase(lane_id: str, lane_def: Mapping[str, Any]) -> str:
    ct = lane_def.get("ct")
    if isinstance(ct, Mapping) and isinstance(ct.get("test_id"), str):
        match = re.search(r"\bP([0-9]+)\b", str(ct["test_id"]))
        if match:
            return f"P{match.group(1)}"
    match = re.search(r"(?:^|_)p([0-9]+)(?:_|$)", lane_id)
    if match:
        return f"P{match.group(1)}"
    return "P0"



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


def _load_retired_evidence_pins() -> Mapping[str, Any]:
    global _RETIRED_EVIDENCE_PINS_CACHE
    if _RETIRED_EVIDENCE_PINS_CACHE is None:
        payload = json.loads(RETIRED_EVIDENCE_PINS_PATH.read_text(encoding="utf-8"))
        if payload.get("schema_version") != "bb.e4.retired_evidence_pins.v1":
            raise ValueError("retired evidence pins have an unsupported schema version")
        pins = payload.get("lanes")
        if not isinstance(pins, Mapping):
            raise ValueError("retired evidence pins must contain a lanes object")
        _RETIRED_EVIDENCE_PINS_CACHE = dict(pins)
    return _RETIRED_EVIDENCE_PINS_CACHE


def _frozen_evidence_binding(
    lane_id: str,
) -> tuple[dict[str, Any], str, str]:
    pin = _load_retired_evidence_pins().get(lane_id)
    if not isinstance(pin, Mapping):
        raise ValueError(f"retired lane {lane_id!r} is missing a frozen evidence pin")
    validation_report = pin.get("validation_report")
    digest = pin.get("sha256")
    if not isinstance(validation_report, str) or not validation_report:
        raise ValueError(f"retired lane {lane_id!r} has an invalid frozen validation report path")
    if not isinstance(digest, str) or not digest.startswith("sha256:") or len(digest) != 71:
        raise ValueError(f"retired lane {lane_id!r} has an invalid frozen validation report hash")
    report = json.loads((ROOT / validation_report).read_text(encoding="utf-8"))
    support_claim_path = report.get("support_claim")
    evidence_manifest_path = report.get("evidence_manifest")
    if not isinstance(support_claim_path, str) or not support_claim_path:
        raise ValueError(f"retired lane {lane_id!r} frozen report is missing support_claim")
    if not isinstance(evidence_manifest_path, str) or not evidence_manifest_path:
        raise ValueError(f"retired lane {lane_id!r} frozen report is missing evidence_manifest")
    command = {
        "argv": [
            ".venv/bin/python",
            "scripts/e4_parity/validate_frozen_e4_evidence.py",
            "--validation-report",
            validation_report,
            "--retired-evidence-pins",
            "config/e4_retired_evidence_pins.json",
            "--json-out",
            f"artifacts/conformance/node_gate/ct_frozen_{lane_id}.json",
        ],
        "cwd": ".",
    }
    return command, support_claim_path, evidence_manifest_path


def lane_inventory_row(lane_def: Mapping[str, Any]) -> dict[str, Any]:
    lane_id = str(lane_def["lane_id"])
    config_id = str(lane_def["config_id"])
    source_reverify = _reverify_command(lane_def)
    retained_evidence_status = lane_def.get("claim", {}).get("status")
    evidence_manifest_path: str | None = None
    if lane_def["status"] != "accepted" and retained_evidence_status == "accepted":
        reverify, support_claim_path, evidence_manifest_path = (
            _frozen_evidence_binding(lane_id)
        )
    else:
        reverify = source_reverify
        support_claim_path = _support_claim_path(source_reverify)
    ct_argv = [item for item in reverify["argv"] if item != "--check-only"]
    if support_claim_path and (ROOT / support_claim_path).exists():
        support_claim_payload = json.loads(
            (ROOT / support_claim_path).read_text(encoding="utf-8")
        )
        support_claim = (
            support_claim_payload
            if isinstance(support_claim_payload, Mapping)
            else {}
        )
    else:
        support_claim = {}
    score_row = _score_row_for_support_claim(support_claim_path)
    ledger_feature_ids = _feature_ids_from_refs(score_row.get("ledger_row_refs")) or _feature_ids_from_refs(support_claim.get("ledger_row_refs"))
    if not ledger_feature_ids:
        packet_feature_id = (
            lane_def.get("normalize", {})
            .get("config", {})
            .get("packet_constants", {})
            .get("feature_id")
        )
        if isinstance(packet_feature_id, str) and packet_feature_id:
            ledger_feature_ids = [packet_feature_id]
    row = {
        "lane_id": lane_id,
        "config_id": config_id,
        "claim_id": _claim_id(lane_def),
        "phase": _phase(lane_id, lane_def),
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
        "artifact_roles": _artifact_roles(
            lane_id,
            source_reverify,
            manifest_path=evidence_manifest_path,
        ),
        "ledger_feature_ids": ledger_feature_ids,
        "score_row_id": score_row.get("score_row_id", ""),
        "artifacts_root": lane_def.get("artifacts_root"),
    }
    if isinstance(retained_evidence_status, str) and retained_evidence_status != lane_def["status"]:
        row["evidence_status"] = retained_evidence_status
    return row


def build_inventory(lane_defs: Mapping[str, Mapping[str, Any]]) -> dict[str, Any]:
    rows = [lane_inventory_row(lane_defs[lane_id]) for lane_id in sorted(lane_defs)]
    return {
        "schema_version": "bb.e4.lane_inventory.v2",
        "inventory_id": "e4_lane_inventory_from_lane_defs_v2",
        "generated_at_utc": GENERATED_AT_UTC,
        "revision": 1,
        "lanes": rows,
    }


def lane_row_mismatches(
    generated: Mapping[str, Any],
    canonical: Mapping[str, Any],
) -> list[str]:
    missing = object()
    return [
        field
        for field in sorted(set(generated) | set(canonical))
        if generated.get(field, missing) != canonical.get(field, missing)
    ]


def compare_with_canonical(generated: Mapping[str, Any], canonical: Mapping[str, Any]) -> dict[str, Any]:
    generated_rows = {row["lane_id"]: row for row in generated.get("lanes", []) if isinstance(row, Mapping)}
    canonical_rows = {row["lane_id"]: row for row in canonical.get("lanes", []) if isinstance(row, Mapping)}
    errors: list[str] = []
    for lane_id, generated_row in sorted(generated_rows.items()):
        canonical_row = canonical_rows.get(lane_id)
        if canonical_row is None:
            errors.append(f"canonical inventory missing lane {lane_id}")
            continue
        for field in lane_row_mismatches(generated_row, canonical_row):
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
