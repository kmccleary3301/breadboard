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


DEFAULT_REUSE_TRACKING = ROOT / "artifacts" / "darwin" / "stage5" / "family_reuse_tracking" / "family_reuse_tracking_v0.json"
OUT_DIR = ROOT / "artifacts" / "darwin" / "stage5" / "third_family_decision"
OUT_JSON = OUT_DIR / "third_family_decision_v0.json"
OUT_MD = OUT_DIR / "third_family_decision_v0.md"


def _load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def build_stage5_third_family_decision(
    *,
    reuse_tracking_path: Path = DEFAULT_REUSE_TRACKING,
    out_dir: Path = OUT_DIR,
) -> dict[str, object]:
    reuse_tracking = _load_json(reuse_tracking_path)
    held_back_rows = [row for row in list(reuse_tracking.get("rows") or []) if str(row.get("stage5_family_state") or "") == "held_back"]
    active_rows = [row for row in list(reuse_tracking.get("rows") or []) if str(row.get("stage5_family_state") or "") in {"active_proving", "challenge_only"}]
    if any(str(row.get("activation_readiness") or "") == "current_center" and float(row.get("reuse_lift_rate") or 0.0) >= 0.3 for row in held_back_rows):
        decision = "activate_third_family"
        reason = "held_back_family_has_current_center_readiness_and_positive_reuse_rate"
        selected_family_id = str(held_back_rows[0].get("family_id") or "")
    else:
        decision = "hold_two_family_center"
        reason = "held_back_families_lack_fresh_stage5_evidence_while_current_two_family_center_remains_interpretable"
        selected_family_id = None
    payload = {
        "schema": "breadboard.darwin.stage5.third_family_decision.v0",
        "source_refs": {
            "family_reuse_tracking_ref": path_ref(reuse_tracking_path),
        },
        "active_family_count": len(active_rows),
        "held_back_family_count": len(held_back_rows),
        "decision": decision,
        "decision_reason": reason,
        "selected_family_id": selected_family_id,
    }
    lines = [
        "# Stage-5 Third-Family Decision",
        "",
        f"- decision: `{decision}`",
        f"- reason: `{reason}`",
        f"- active family count: `{len(active_rows)}`",
        f"- held-back family count: `{len(held_back_rows)}`",
    ]
    write_json(out_dir / OUT_JSON.name, payload)
    write_text(out_dir / OUT_MD.name, "\n".join(lines) + "\n")
    return {"out_json": str(out_dir / OUT_JSON.name), "decision": decision}


def main() -> int:
    parser = argparse.ArgumentParser(description="Build the Stage-5 third-family decision.")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    summary = build_stage5_third_family_decision()
    if args.json:
        print(json.dumps(summary, indent=2, sort_keys=True))
    else:
        print(f"stage5_third_family_decision={summary['out_json']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
