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
DEFAULT_REPLAY = ROOT / "artifacts" / "darwin" / "stage4" / "deep_live_search" / "replay_checks_v0.json"
DEFAULT_TRANSFER = ROOT / "artifacts" / "darwin" / "stage5" / "bounded_transfer" / "bounded_transfer_outcomes_v0.json"
DEFAULT_COMPOSITION = ROOT / "artifacts" / "darwin" / "stage5" / "composition_canary" / "composition_canary_v0.json"
OUT_DIR = ROOT / "artifacts" / "darwin" / "stage5" / "replay_posture"
OUT_JSON = OUT_DIR / "replay_posture_v0.json"
OUT_MD = OUT_DIR / "replay_posture_v0.md"


def _load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def build_stage5_replay_posture(
    *,
    stage5_registry_path: Path = DEFAULT_STAGE5_REGISTRY,
    replay_path: Path = DEFAULT_REPLAY,
    transfer_path: Path = DEFAULT_TRANSFER,
    composition_path: Path = DEFAULT_COMPOSITION,
    out_dir: Path = OUT_DIR,
) -> dict[str, object]:
    stage5_registry = _load_json(stage5_registry_path)
    replay = _load_json(replay_path)
    transfer = _load_json(transfer_path)
    composition = _load_json(composition_path)
    replay_lookup = {(str(row.get("lane_id") or ""), str(row.get("operator_id") or "")): dict(row) for row in list(replay.get("rows") or [])}

    rows = []
    for row in list(stage5_registry.get("rows") or []):
        lane_id = str(row.get("lane_id") or "")
        family_kind = str(row.get("family_kind") or "")
        operator_id = (
            "mut.topology.single_to_pev_v1" if family_kind == "topology"
            else "mut.policy.shadow_memory_enable_v1" if family_kind == "policy"
            else "mut.tool_scope.add_git_diff_v1"
        )
        replay_row = replay_lookup.get((lane_id, operator_id), {})
        replay_status = (
            "replay_supported"
            if bool(replay_row.get("replay_supported"))
            else "replay_observed_but_weak" if str(row.get("stage5_family_state") or "") != "held_back"
            else "replay_missing"
        )
        rows.append(
            {
                "subject_type": "family",
                "subject_id": str(row.get("family_id") or ""),
                "replay_status": replay_status,
                "reason": "stage4_replay_supported" if replay_status == "replay_supported" else "stage5_confirmation_without_explicit_replay",
            }
        )
    for row in list(transfer.get("rows") or []):
        rows.append(
            {
                "subject_type": "transfer",
                "subject_id": str(row.get("transfer_id") or ""),
                "replay_status": str(row.get("replay_status") or "unknown"),
                "reason": str(row.get("transfer_reason") or ""),
            }
        )
    rows.append(
        {
            "subject_type": "composition",
            "subject_id": "composition.stage5.canary.v0",
            "replay_status": "not_required" if str(composition.get("result") or "") == "composition_not_authorized" else "replay_pending",
            "reason": str(composition.get("result_reason") or ""),
        }
    )
    payload = {
        "schema": "breadboard.darwin.stage5.replay_posture.v0",
        "source_refs": {
            "stage5_registry_ref": path_ref(stage5_registry_path),
            "stage4_replay_ref": path_ref(replay_path),
            "bounded_transfer_ref": path_ref(transfer_path),
            "composition_canary_ref": path_ref(composition_path),
        },
        "row_count": len(rows),
        "rows": rows,
    }
    lines = ["# Stage-5 Replay Posture", ""]
    for row in rows:
        lines.append(f"- `{row['subject_type']}` / `{row['subject_id']}`: `{row['replay_status']}`")
    write_json(out_dir / OUT_JSON.name, payload)
    write_text(out_dir / OUT_MD.name, "\n".join(lines) + "\n")
    return {"out_json": str(out_dir / OUT_JSON.name), "row_count": len(rows)}


def main() -> int:
    parser = argparse.ArgumentParser(description="Build the Stage-5 replay posture surface.")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    summary = build_stage5_replay_posture()
    if args.json:
        print(json.dumps(summary, indent=2, sort_keys=True))
    else:
        print(f"stage5_replay_posture={summary['out_json']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
