from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from breadboard_ext.darwin.stage4_family_program import (  # noqa: E402
    DEEP_DIR,
    FAMILY_OPERATOR_MAP,
    OUT_DIR,
    build_stage4_family_id,
    candidate_rank,
    classify_stage4_family_candidate,
    load_json,
    path_ref,
    write_json,
    write_text,
)


COMPARISONS = DEEP_DIR / "matched_budget_comparisons_v0.json"
PRIOR_STABILITY = DEEP_DIR / "family_prior_stability_v0.json"
REPLAY = DEEP_DIR / "replay_checks_v0.json"
RUNS = DEEP_DIR / "campaign_runs_v0.json"
OUT_JSON = OUT_DIR / "family_candidates_v0.json"
OUT_MD = OUT_DIR / "family_candidates_v0.md"


def build_stage4_family_candidates() -> dict[str, str | int]:
    comparisons = load_json(COMPARISONS)
    prior_stability = load_json(PRIOR_STABILITY)
    replay = load_json(REPLAY)
    runs = load_json(RUNS)

    prior_lookup = {(row["lane_id"], row["operator_id"]): row for row in prior_stability.get("rows") or []}
    replay_lookup = {(row["lane_id"], row["operator_id"]): row for row in replay.get("rows") or []}
    candidate_ids_by_key: dict[tuple[str, str], list[str]] = {}
    for row in runs.get("runs") or []:
        key = (str(row["lane_id"]), str(row["operator_id"]))
        candidate_ids_by_key.setdefault(key, []).append(str(row["candidate_id"]))

    grouped: dict[tuple[str, str], list[dict]] = {}
    for row in comparisons.get("rows") or []:
        key = (str(row["lane_id"]), str(row["operator_id"]))
        grouped.setdefault(key, []).append(row)

    rows: list[dict] = []
    for (lane_id, operator_id), entries in grouped.items():
        mapping = FAMILY_OPERATOR_MAP.get(operator_id)
        if not mapping:
            continue
        valid = [row for row in entries if bool(row["comparison_valid"])]
        positive = [row for row in valid if bool(row["positive_power_signal"])]
        invalid = [row for row in entries if not bool(row["comparison_valid"])]
        replay_row = replay_lookup.get((lane_id, operator_id), {})
        prior_row = prior_lookup.get((lane_id, operator_id), {})
        signal_rate = (len(positive) / len(valid)) if valid else 0.0
        invalidity_rate = (len(invalid) / len(entries)) if entries else 0.0
        candidate = {
            "family_id": build_stage4_family_id(lane_id=lane_id, operator_id=operator_id),
            "lane_id": lane_id,
            "family_kind": mapping["family_kind"],
            "family_key": mapping["family_key"],
            "source_operator_id": operator_id,
            "candidate_ids": sorted(candidate_ids_by_key.get((lane_id, operator_id), [])),
            "valid_comparison_count": len(valid),
            "positive_power_signal_count": len(positive),
            "invalid_comparison_count": len(invalid),
            "signal_rate": round(signal_rate, 6),
            "invalidity_rate": round(invalidity_rate, 6),
            "latest_priority": float(prior_row.get("latest_priority", 0.0)),
            "priority_span": float(prior_row.get("priority_span", 0.0)),
            "replay_status": "supported" if replay_row.get("replay_supported") else "missing",
            "promotion_class": classify_stage4_family_candidate(
                valid_count=len(valid),
                positive_count=len(positive),
                invalidity_rate=invalidity_rate,
                replay_supported=bool(replay_row.get("replay_supported")),
            ),
            "evidence_refs": [
                path_ref(COMPARISONS),
                path_ref(PRIOR_STABILITY),
                path_ref(REPLAY),
                path_ref(RUNS),
            ],
        }
        rows.append(candidate)

    rows = sorted(rows, key=candidate_rank)
    lines = ["# Stage-4 Family Candidates", ""]
    for row in rows:
        lines.append(
            f"- `{row['lane_id']}` / `{row['family_id']}`: class=`{row['promotion_class']}`, "
            f"valid=`{row['valid_comparison_count']}`, positive=`{row['positive_power_signal_count']}`, "
            f"signal_rate=`{row['signal_rate']}`"
        )

    payload = {
        "schema": "breadboard.darwin.stage4.family_candidates.v0",
        "row_count": len(rows),
        "rows": rows,
    }
    write_json(OUT_JSON, payload)
    write_text(OUT_MD, "\n".join(lines) + "\n")
    return {"out_json": str(OUT_JSON), "out_md": str(OUT_MD), "row_count": len(rows)}


def main() -> int:
    parser = argparse.ArgumentParser(description="Build the Stage-4 family candidates from deep live search outputs.")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    summary = build_stage4_family_candidates()
    if args.json:
        print(json.dumps(summary, indent=2, sort_keys=True))
    else:
        print(f"stage4_family_candidates={summary['out_json']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
