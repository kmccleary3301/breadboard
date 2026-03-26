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


DEFAULT_STAGE5_REGISTRY = ROOT / "artifacts" / "darwin" / "stage5" / "family_registry" / "family_registry_v0.json"
DEFAULT_TRANSFER = ROOT / "artifacts" / "darwin" / "stage5" / "bounded_transfer" / "bounded_transfer_outcomes_v0.json"
OUT_DIR = ROOT / "artifacts" / "darwin" / "stage5" / "composition_canary"
OUT_JSON = OUT_DIR / "composition_canary_v0.json"
OUT_MD = OUT_DIR / "composition_canary_v0.md"


def _load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def build_stage5_composition_canary(
    *,
    stage5_registry_path: Path = DEFAULT_STAGE5_REGISTRY,
    transfer_path: Path = DEFAULT_TRANSFER,
    out_dir: Path = OUT_DIR,
) -> dict[str, object]:
    stage5_registry = _load_json(stage5_registry_path)
    transfer = _load_json(transfer_path)
    systems_policy = next(row for row in list(stage5_registry.get("rows") or []) if str(row.get("lane_id")) == "lane.systems" and str(row.get("family_kind")) == "policy")
    retained_transfer = next((row for row in list(transfer.get("rows") or []) if str(row.get("transfer_status")) == "retained"), None)
    if retained_transfer and str(systems_policy.get("stage5_family_state") or "") == "active_proving":
        result = "composition_not_authorized"
        reason = "only_one_local_active_family_exists_on_systems_and_cross_lane_composition_would_confound_transfer"
        candidate_pair = [str(systems_policy.get("family_id") or ""), str(retained_transfer.get("family_id") or "")]
    else:
        result = "composition_not_authorized"
        reason = "no_composition_candidate_pair_meets_current_stage5_policy"
        candidate_pair = []
    payload = {
        "schema": "breadboard.darwin.stage5.composition_canary.v0",
        "source_refs": {
            "stage5_registry_ref": path_ref(stage5_registry_path),
            "bounded_transfer_ref": path_ref(transfer_path),
        },
        "result": result,
        "result_reason": reason,
        "candidate_pair_family_ids": candidate_pair,
    }
    lines = [
        "# Stage-5 Composition Canary",
        "",
        f"- result: `{result}`",
        f"- reason: `{reason}`",
        f"- candidate pair: `{candidate_pair}`",
    ]
    write_json(out_dir / OUT_JSON.name, payload)
    write_text(out_dir / OUT_MD.name, "\n".join(lines) + "\n")
    return {"out_json": str(out_dir / OUT_JSON.name), "result": result}


def main() -> int:
    parser = argparse.ArgumentParser(description="Build the Stage-5 composition canary outcome.")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    summary = build_stage5_composition_canary()
    if args.json:
        print(json.dumps(summary, indent=2, sort_keys=True))
    else:
        print(f"stage5_composition_canary={summary['out_json']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
