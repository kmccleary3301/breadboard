from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from breadboard_ext.darwin.stage4_family_program import path_ref, write_json, write_text  # noqa: E402

DEFAULT_REGISTRY = ROOT / "artifacts" / "darwin" / "stage6" / "tranche3" / "family_registry" / "family_registry_v0.json"
DEFAULT_COMPOSITION = ROOT / "artifacts" / "darwin" / "stage6" / "tranche4" / "composition_canary" / "composition_canary_v0.json"
OUT_DIR = ROOT / "artifacts" / "darwin" / "stage6" / "tranche4" / "family_registry"
OUT_JSON = OUT_DIR / "family_registry_v0.json"
OUT_MD = OUT_DIR / "family_registry_v0.md"


def _load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def build_stage6_tranche4_family_registry(
    *,
    registry_path: Path = DEFAULT_REGISTRY,
    composition_path: Path = DEFAULT_COMPOSITION,
    out_dir: Path = OUT_DIR,
) -> dict[str, object]:
    registry = _load_json(registry_path)
    composition = _load_json(composition_path)
    composition_result = str(composition.get("result") or "composition_not_authorized")
    rows = []
    for row in list(registry.get("rows") or []):
        family_state = str(row.get("family_state") or "")
        transfer_status = str(row.get("transfer_status") or "")
        updated = dict(row)
        updated["composition_decision"] = composition_result
        updated["challenge_relevance"] = (
            "primary_center"
            if transfer_status == "retained"
            else "challenge_context_only"
            if transfer_status in {"activation_probe", "inconclusive", "descriptive_only", "degraded_but_valid"}
            else "not_in_scope"
        )
        updated["composition_eligibility"] = (
            "not_authorized"
            if composition_result == "composition_not_authorized"
            else "evaluated"
            if composition_result in {"composition_positive", "composition_neutral", "composition_negative"}
            else "invalid"
        )
        updated["family_state"] = (
            "retained_transfer_source"
            if family_state == "retained_transfer_source"
            else family_state
        )
        rows.append(updated)
    payload = {
        "schema": "breadboard.darwin.stage6.tranche4.family_registry.v0",
        "source_refs": {
            "tranche3_family_registry_ref": path_ref(registry_path),
            "composition_canary_ref": path_ref(composition_path),
        },
        "family_center_decision": str(registry.get("family_center_decision") or "hold_single_retained_family_center"),
        "composition_decision": composition_result,
        "row_count": len(rows),
        "rows": rows,
    }
    lines = ["# Stage 6 Tranche 4 Family Registry", ""]
    for row in rows:
        lines.append(
            f"- `{row['family_id']}`: state=`{row['family_state']}`, transfer=`{row['transfer_status']}`, composition=`{row['composition_eligibility']}`"
        )
    write_json(out_dir / OUT_JSON.name, payload)
    write_text(out_dir / OUT_MD.name, "\n".join(lines) + "\n")
    return {"out_json": str(out_dir / OUT_JSON.name), "row_count": len(rows), "composition_decision": composition_result}


def main() -> int:
    parser = argparse.ArgumentParser(description="Build the Stage 6 tranche-4 family registry.")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    summary = build_stage6_tranche4_family_registry()
    if args.json:
        print(json.dumps(summary, indent=2, sort_keys=True))
    else:
        print(f"stage6_tranche4_family_registry={summary['out_json']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
