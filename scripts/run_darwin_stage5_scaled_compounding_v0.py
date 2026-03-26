from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from breadboard_ext.darwin.stage4_family_program import path_ref  # noqa: E402
from scripts.run_darwin_stage4_live_economics_pilot_v0 import _write_json  # noqa: E402
from scripts.run_darwin_stage5_systems_weighted_compounding_v0 import run_stage5_systems_weighted_compounding  # noqa: E402


OUT_DIR = ROOT / "artifacts" / "darwin" / "stage5" / "scaled_compounding"


def run_stage5_scaled_compounding(*, rounds: int = 2, out_dir: Path = OUT_DIR) -> dict[str, object]:
    systems_weighted_summary = run_stage5_systems_weighted_compounding(rounds=rounds, out_dir=out_dir / "round_series")
    systems_weighted_path = Path(str(systems_weighted_summary["summary_path"]))
    systems_weighted_payload = json.loads(systems_weighted_path.read_text(encoding="utf-8"))
    payload = {
        "schema": "breadboard.darwin.stage5.scaled_compounding.v0",
        "round_count": int(systems_weighted_payload.get("round_count") or rounds),
        "row_count": int(systems_weighted_payload.get("row_count") or 0),
        "bundle_complete": bool(systems_weighted_payload.get("bundle_complete")),
        "completed_row_count": int(systems_weighted_payload.get("completed_row_count") or 0),
        "systems_weighted_ref": path_ref(systems_weighted_path),
        "round_rows": list(systems_weighted_payload.get("round_rows") or []),
        "lane_totals": dict(systems_weighted_payload.get("lane_totals") or {}),
    }
    summary_path = out_dir / "scaled_compounding_v0.json"
    _write_json(summary_path, payload)
    return {
        "summary_path": str(summary_path),
        "round_count": payload["round_count"],
        "bundle_complete": payload["bundle_complete"],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the Stage-5 scaled compounding tranche.")
    parser.add_argument("--rounds", type=int, default=2)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    summary = run_stage5_scaled_compounding(rounds=args.rounds)
    if args.json:
        print(json.dumps(summary, indent=2, sort_keys=True))
    else:
        print(f"stage5_scaled_compounding={summary['summary_path']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
