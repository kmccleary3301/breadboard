from __future__ import annotations

import argparse
import json
from collections import defaultdict
from pathlib import Path
from typing import Any, Callable

try:
    from build_source_index import PLAN_ROOT, _display_path
    from seed_atomic_feature_ledger import DEFAULT_OUT as DEFAULT_LEDGER_OUT
    from seed_atomic_feature_ledger import TARGET_OWNER, write_ledger
except ImportError:  # pragma: no cover - package-style import fallback
    from .build_source_index import PLAN_ROOT, _display_path
    from .seed_atomic_feature_ledger import DEFAULT_OUT as DEFAULT_LEDGER_OUT
    from .seed_atomic_feature_ledger import TARGET_OWNER, write_ledger


SCHEMA_VERSION = "bb.atomic_feature_ledger_report.v1"
REPORT_FILENAME = "BB_E4_ATOMIC_FEATURE_LEDGER_REPORT.json"
DEFAULT_OUT = PLAN_ROOT / REPORT_FILENAME


def _load_ledger(path: Path) -> dict[str, Any]:
    if not path.exists():
        write_ledger(path)
    return json.loads(path.read_text(encoding="utf-8"))


def _primitive(row: dict[str, Any]) -> str:
    mapping = row.get("breadboard_mapping")
    if isinstance(mapping, dict):
        return str(mapping.get("primitive") or "unknown")
    if isinstance(mapping, str):
        return mapping
    return "unknown"

def _mapping_value(row: dict[str, Any], key: str, default: str = "unknown") -> str:
    mapping = row.get("breadboard_mapping")
    if isinstance(mapping, dict):
        return str(mapping.get(key) or default)
    return default


def _fixture_kinds(row: dict[str, Any]) -> list[str]:
    refs = row.get("fixture_refs")
    if not isinstance(refs, list):
        return []
    kinds = {str(ref).split(":", 1)[0] for ref in refs if isinstance(ref, str) and ":" in ref}
    return sorted(kinds)


def _owner(row: dict[str, Any]) -> str:
    value = row.get("owner")
    if isinstance(value, str) and value:
        return value
    return TARGET_OWNER.get(str(row.get("target") or ""), "target.unknown")


def _group(rows: list[dict[str, Any]], name: str, key_fn: Callable[[dict[str, Any]], str]) -> list[dict[str, Any]]:
    buckets: dict[str, list[str]] = defaultdict(list)
    for row in rows:
        buckets[key_fn(row)].append(str(row["feature_id"]))
    return [
        {"key": key, "count": len(feature_ids), "feature_ids": sorted(feature_ids)}
        for key, feature_ids in sorted(buckets.items(), key=lambda item: item[0])
    ]


def build_report(ledger_path: Path = DEFAULT_LEDGER_OUT) -> dict[str, Any]:
    ledger = _load_ledger(ledger_path)
    rows = list(ledger.get("rows") or [])
    targets = sorted({str(row.get("target")) for row in rows})
    primitives = sorted({_primitive(row) for row in rows})
    evidence_tiers = sorted({str(row.get("evidence_tier")) for row in rows})
    gap_kinds = sorted({str(row.get("gap_kind")) for row in rows})
    promotion_states = sorted({str(row.get("promotion_state")) for row in rows})
    owners = sorted({_owner(row) for row in rows})
    claim_types = sorted({str(row.get("claim_type")) for row in rows})
    supports = sorted({_mapping_value(row, "support") for row in rows})
    fixture_kinds = sorted({kind for row in rows for kind in _fixture_kinds(row)})
    return {
        "schema_version": SCHEMA_VERSION,
        "ledger_ref": _display_path(ledger_path),
        "ledger_schema_version": ledger.get("schema_version"),
        "row_count": len(rows),
        "coverage": {
            "targets": targets,
            "primitives": primitives,
            "claim_types": claim_types,
            "evidence_tiers": evidence_tiers,
            "fixture_kinds": fixture_kinds,
            "gap_kinds": gap_kinds,
            "owners": owners,
            "promotion_states": promotion_states,
            "supports": supports,
        },
        "groups": {
            "by_target": _group(rows, "target", lambda row: str(row.get("target") or "unknown")),
            "by_primitive": _group(rows, "primitive", _primitive),
            "by_claim_type": _group(rows, "claim_type", lambda row: str(row.get("claim_type") or "unknown")),
            "by_support": _group(rows, "support", lambda row: _mapping_value(row, "support")),
            "by_e4_row_ref": _group(rows, "e4_row_ref", lambda row: str(row.get("e4_row_ref") or "none")),
            "by_fixture_kind": _group(
                rows,
                "fixture_kind",
                lambda row: ",".join(_fixture_kinds(row)) if _fixture_kinds(row) else "none",
            ),
            "by_target_family_lane": _group(
                rows,
                "target_family_lane",
                lambda row: "/".join(
                    [
                        str(row.get("target") or "unknown"),
                        str(row.get("family") or "unknown"),
                        str(row.get("e4_row_ref") or "none"),
                    ]
                ),
            ),
            "by_evidence_tier": _group(rows, "evidence_tier", lambda row: str(row.get("evidence_tier") or "unknown")),
            "by_gap_kind": _group(rows, "gap_kind", lambda row: str(row.get("gap_kind") or "unknown")),
            "by_owner": _group(rows, "owner", _owner),
            "by_promotion_status": _group(rows, "promotion_state", lambda row: str(row.get("promotion_state") or "unknown")),
        },
        "matrix": [
            {
                "target": str(row.get("target")),
                "family": str(row.get("family")),
                "owner": _owner(row),
                "primitive": _primitive(row),
                "support": _mapping_value(row, "support"),
                "truth_scope": _mapping_value(row, "truth_scope", "not_applicable"),
                "claim_type": str(row.get("claim_type")),
                "evidence_tier": str(row.get("evidence_tier")),
                "gap_kind": str(row.get("gap_kind")),
                "promotion_state": str(row.get("promotion_state")),
                "e4_row_ref": str(row.get("e4_row_ref") or ""),
                "fixture_kinds": _fixture_kinds(row),
                "source_ref_count": len(row.get("source_refs") or []),
                "feature_id": str(row.get("feature_id")),
                "dedupe_key": str(row.get("dedupe_key")),
            }
            for row in sorted(rows, key=lambda item: str(item.get("dedupe_key") or ""))
        ],
    }


def write_report(out_path: Path = DEFAULT_OUT, ledger_path: Path = DEFAULT_LEDGER_OUT) -> dict[str, Any]:
    payload = build_report(ledger_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return {"out_path": _display_path(out_path), "row_count": payload["row_count"]}


def _parse_path(value: str) -> Path:
    path = Path(value).expanduser()
    return path if path.is_absolute() else (Path.cwd() / path)


def main() -> int:
    parser = argparse.ArgumentParser(description="Build grouped P1 atomic feature ledger report.")
    parser.add_argument("--ledger", default=str(DEFAULT_LEDGER_OUT), help="Ledger JSON path; generated if absent.")
    parser.add_argument("--out", default=str(DEFAULT_OUT), help="Output report JSON path.")
    parser.add_argument("--json", action="store_true", help="Print machine-readable summary.")
    args = parser.parse_args()

    summary = write_report(_parse_path(args.out), _parse_path(args.ledger))
    if args.json:
        print(json.dumps(summary, indent=2, sort_keys=True))
    else:
        print(f"atomic_feature_ledger_report={summary['out_path']} rows={summary['row_count']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
