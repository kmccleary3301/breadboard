from __future__ import annotations

import argparse
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
LANE_REGISTRY = ROOT / "docs" / "contracts" / "darwin" / "registries" / "lane_registry_v0.json"
OUT_PATH = ROOT / "artifacts" / "darwin" / "bootstrap" / "future_lane_placeholders_v0.json"


def _load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def build_future_lane_placeholders() -> dict:
    registry = _load_json(LANE_REGISTRY)
    rows = []
    for lane in registry.get("lanes") or []:
        if lane["lane_id"] not in {"lane.scheduling", "lane.research"}:
            continue
        rows.append(
            {
                "lane_id": lane["lane_id"],
                "launch_phase": lane["launch_phase"],
                "objective_class": lane["objective_class"],
                "readiness_prerequisites": lane["readiness_prerequisites"],
                "placeholder_reason": "Prepared for the next DARWIN tranche but intentionally not activated in the >60% execution slice.",
            }
        )
    return {
        "schema": "breadboard.darwin.future_lane_placeholders.v0",
        "lane_count": len(rows),
        "lanes": rows,
    }


def write_future_lane_placeholders() -> dict:
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    payload = build_future_lane_placeholders()
    OUT_PATH.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return {"out_path": str(OUT_PATH), "lane_count": payload["lane_count"]}


def main() -> int:
    parser = argparse.ArgumentParser(description="Emit placeholder records for deferred DARWIN lanes.")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    summary = write_future_lane_placeholders()
    if args.json:
        print(json.dumps(summary, indent=2, sort_keys=True))
    else:
        print(f"future_lane_placeholders={summary['out_path']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
