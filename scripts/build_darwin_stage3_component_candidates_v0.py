from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from breadboard_ext.darwin.stage3_component_transfer import (
    BOUNDED_DIR,
    OPERATOR_COMPONENT_MAP,
    OUT_DIR,
    ROOT,
    candidate_rank,
    classify_component_candidate,
    load_json,
    write_json,
    write_text,
)


OPERATOR_EV = BOUNDED_DIR / "operator_ev_report_v0.json"
TOPOLOGY_EV = BOUNDED_DIR / "topology_ev_report_v0.json"
OUT_JSON = OUT_DIR / "component_candidates_v0.json"
OUT_MD = OUT_DIR / "component_candidates_v0.md"


def write_component_candidates() -> dict[str, str | int]:
    operator_payload = load_json(OPERATOR_EV)
    topology_payload = load_json(TOPOLOGY_EV)
    topology_lookup = {(row["lane_id"], row["topology_id"]): row for row in topology_payload.get("rows") or []}

    rows: list[dict] = []
    lines = ["# Stage-3 Component Candidates", ""]
    for row in operator_payload.get("rows") or []:
        mapping = OPERATOR_COMPONENT_MAP.get(row["operator_id"])
        if not mapping:
            continue
        topology_row = topology_lookup.get((row["lane_id"], "policy.topology.pev_v0"))
        promotion_class = classify_component_candidate(
            valid_count=int(row["valid_comparison_count"]),
            invalid_count=int(row["invalid_comparison_count"]),
            improvement_count=int(row["improvement_count"]),
            retention_rate=float(row["matched_budget_retention_rate"]),
            average_runtime_delta_ms=float(row["average_runtime_delta_ms"]),
        )
        candidate = {
            "component_family_id": f"{mapping['component_family']}.{row['lane_id']}.v0",
            "lane_id": row["lane_id"],
            "component_kind": mapping["component_kind"],
            "component_key": mapping["component_key"],
            "source_operator_id": row["operator_id"],
            "topology_id": "policy.topology.pev_v0" if mapping["component_kind"] == "topology" else (topology_row["topology_id"] if topology_row else None),
            "valid_comparison_count": row["valid_comparison_count"],
            "invalid_comparison_count": row["invalid_comparison_count"],
            "improvement_count": row["improvement_count"],
            "degradation_count": row["degradation_count"],
            "noop_count": row["noop_count"],
            "average_delta": row["average_delta"],
            "average_runtime_delta_ms": row["average_runtime_delta_ms"],
            "matched_budget_retention_rate": row["matched_budget_retention_rate"],
            "promotion_class": promotion_class,
            "priority": mapping["priority"],
            "evidence_refs": [
                str(OPERATOR_EV.relative_to(ROOT)),
                str(TOPOLOGY_EV.relative_to(ROOT)),
            ],
        }
        rows.append(candidate)

    rows = sorted(rows, key=candidate_rank)
    for row in rows:
        lines.append(
            f"- `{row['lane_id']}` / `{row['component_family_id']}`: class=`{row['promotion_class']}`, "
            f"valid=`{row['valid_comparison_count']}`, improvements=`{row['improvement_count']}`, avg_runtime_delta_ms=`{row['average_runtime_delta_ms']}`"
        )

    payload = {
        "schema": "breadboard.darwin.stage3.component_candidates.v0",
        "row_count": len(rows),
        "rows": rows,
    }
    write_json(OUT_JSON, payload)
    write_text(OUT_MD, "\n".join(lines) + "\n")
    return {"out_json": str(OUT_JSON), "out_md": str(OUT_MD), "row_count": len(rows)}


def main() -> int:
    parser = argparse.ArgumentParser(description="Build Stage-3 component-family candidates from bounded inference outputs.")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    summary = write_component_candidates()
    if args.json:
        print(json.dumps(summary, indent=2, sort_keys=True))
    else:
        print(f"component_candidates={summary['out_json']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
