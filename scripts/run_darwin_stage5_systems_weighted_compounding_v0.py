from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import shutil
import signal
import sys
from contextlib import contextmanager

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from breadboard_ext.darwin.stage4_family_program import path_ref  # noqa: E402
from scripts.run_darwin_stage4_live_economics_pilot_v0 import _write_json  # noqa: E402
from scripts.run_darwin_stage5_compounding_pilot_v0 import run_stage5_compounding_pilot  # noqa: E402


OUT_DIR = ROOT / "artifacts" / "darwin" / "stage5" / "systems_weighted"
LANE_ORDER = ("lane.systems", "lane.repo_swe")
DEFAULT_STAGE5_LANE_TIMEOUT_S = 120


class _Stage5LaneTimeoutError(TimeoutError):
    pass


@contextmanager
def _lane_timeout(seconds: int):
    def _handler(signum, frame):
        raise _Stage5LaneTimeoutError(f"systems_weighted_lane_timeout_after_{seconds}s")

    previous = signal.signal(signal.SIGALRM, _handler)
    signal.alarm(int(seconds))
    try:
        yield
    finally:
        signal.alarm(0)
        signal.signal(signal.SIGALRM, previous)


def _stage_lane_dir(round_dir: Path, lane_id: str) -> Path:
    return round_dir / lane_id.replace(".", "_")


def _stage_status_from_summary(payload: dict[str, object]) -> str:
    if str(payload.get("run_completion_status") or "") != "complete":
        return "partial_or_stale"
    if str(payload.get("live_claim_surface_status") or "") == "claim_eligible_live":
        return "completed_live"
    return "completed_scaffold"


def _failure_status(exc: Exception) -> str:
    if isinstance(exc, _Stage5LaneTimeoutError):
        return "timed_out"
    return "failed"


def _atomic_promote_directory(staging_dir: Path, final_dir: Path) -> None:
    final_dir.parent.mkdir(parents=True, exist_ok=True)
    if final_dir.exists():
        shutil.rmtree(final_dir)
    staging_dir.replace(final_dir)


def _load_policy_payload(payload: dict[str, object], *, summary_path: Path) -> dict[str, object]:
    policy_ref_raw = str(payload.get("policy_ref") or "")
    if not policy_ref_raw:
        return {}
    policy_ref = Path(policy_ref_raw)
    candidates = []
    if policy_ref.is_absolute():
        candidates.append(policy_ref)
    else:
        candidates.append(ROOT / policy_ref)
        candidates.append(summary_path.parent / policy_ref.name)
    for candidate in candidates:
        if candidate.exists():
            return json.loads(candidate.read_text(encoding="utf-8"))
    return {}


def run_stage5_systems_weighted_compounding(*, rounds: int = 2, out_dir: Path = OUT_DIR) -> dict[str, object]:
    lane_runs: list[dict[str, object]] = []
    lane_timeout_s = int(os.environ.get("DARWIN_STAGE5_SYSTEMS_WEIGHTED_LANE_TIMEOUT_S") or DEFAULT_STAGE5_LANE_TIMEOUT_S)
    for round_index in range(1, int(rounds) + 1):
        round_dir = out_dir / f"round_r{round_index}"
        for lane_id in LANE_ORDER:
            final_lane_dir = _stage_lane_dir(round_dir, lane_id)
            staging_lane_dir = round_dir / ".staging" / lane_id.replace(".", "_")
            if staging_lane_dir.exists():
                shutil.rmtree(staging_lane_dir)
            staging_lane_dir.parent.mkdir(parents=True, exist_ok=True)
            try:
                with _lane_timeout(lane_timeout_s):
                    lane_summary = run_stage5_compounding_pilot(
                        lane_id=lane_id,
                        out_dir=staging_lane_dir,
                        round_index=round_index,
                    )
                _atomic_promote_directory(staging_lane_dir, final_lane_dir)
                summary_path = final_lane_dir / "compounding_pilot_v0.json"
                payload = json.loads(summary_path.read_text(encoding="utf-8"))
                lane_runs.append(
                    {
                        "round_index": round_index,
                        "lane_id": lane_id,
                        "summary_path": str(summary_path),
                        "lane_execution_status": _stage_status_from_summary(payload),
                        "error_class": None,
                        "error_message": None,
                    }
                )
            except Exception as exc:
                if staging_lane_dir.exists():
                    shutil.rmtree(staging_lane_dir)
                lane_runs.append(
                    {
                        "round_index": round_index,
                        "lane_id": lane_id,
                        "summary_path": None,
                        "lane_execution_status": _failure_status(exc),
                        "error_class": exc.__class__.__name__,
                        "error_message": str(exc),
                    }
                )

    bundle_rows = []
    lane_totals: dict[str, dict[str, int]] = {}
    completed_row_count = 0
    round_rows: list[dict[str, object]] = []
    lane_runs_by_round: dict[int, list[dict[str, object]]] = {}
    for row in lane_runs:
        lane_runs_by_round.setdefault(int(row["round_index"]), []).append(row)
        payload = {}
        policy_payload = {}
        if row.get("summary_path"):
            summary_path = Path(str(row["summary_path"]))
            payload = json.loads(summary_path.read_text(encoding="utf-8"))
            policy_payload = _load_policy_payload(payload, summary_path=summary_path)
        round_complete = str(row.get("lane_execution_status") or "") == "completed_live"
        bundle_row = {
            "round_index": int(row["round_index"]),
            "lane_id": str(row["lane_id"]),
            "lane_execution_status": str(row.get("lane_execution_status") or "unknown"),
            "error_class": row.get("error_class"),
            "error_message": row.get("error_message"),
            "round_complete": round_complete,
            "live_claim_surface_status": str(payload.get("live_claim_surface_status") or "unknown"),
            "claim_eligible_comparison_count": int(payload.get("claim_eligible_comparison_count") or 0),
            "comparison_valid_count": int(payload.get("comparison_valid_count") or 0),
            "reuse_lift_count": int(payload.get("reuse_lift_count") or 0),
            "no_lift_count": int(payload.get("no_lift_count") or 0),
            "flat_count": int(payload.get("flat_count") or 0),
            "arm_status_counts": dict(payload.get("arm_status_counts") or {}),
            "lane_weight": str(dict(policy_payload.get("cross_lane_weighting") or {}).get("lane_weight") or "unset"),
            "summary_ref": path_ref(Path(str(row["summary_path"]))) if row.get("summary_path") else None,
        }
        bundle_rows.append(bundle_row)
        if round_complete:
            completed_row_count += 1
        lane_totals.setdefault(
            bundle_row["lane_id"],
            {
                "claim_eligible_comparison_count": 0,
                "comparison_valid_count": 0,
                "reuse_lift_count": 0,
                "no_lift_count": 0,
                "flat_count": 0,
            },
        )
        lane_totals[bundle_row["lane_id"]]["claim_eligible_comparison_count"] += bundle_row["claim_eligible_comparison_count"]
        lane_totals[bundle_row["lane_id"]]["comparison_valid_count"] += bundle_row["comparison_valid_count"]
        lane_totals[bundle_row["lane_id"]]["reuse_lift_count"] += bundle_row["reuse_lift_count"]
        lane_totals[bundle_row["lane_id"]]["no_lift_count"] += bundle_row["no_lift_count"]
        lane_totals[bundle_row["lane_id"]]["flat_count"] += bundle_row["flat_count"]
    for round_index in range(1, int(rounds) + 1):
        round_lane_rows = lane_runs_by_round.get(round_index, [])
        round_complete = (
            len(round_lane_rows) == len(LANE_ORDER)
            and all(str(row.get("lane_execution_status") or "") == "completed_live" for row in round_lane_rows)
        )
        round_rows.append(
            {
                "round_index": round_index,
                "round_status": "complete" if round_complete else "partial_or_stale",
                "expected_lane_count": len(LANE_ORDER),
                "completed_lane_count": sum(1 for row in round_lane_rows if str(row.get("lane_execution_status") or "") == "completed_live"),
                "row_count": len(round_lane_rows),
            }
        )

    bundle = {
        "schema": "breadboard.darwin.stage5.systems_weighted_compounding.v0",
        "round_count": int(rounds),
        "lane_count": len(LANE_ORDER),
        "lane_order": list(LANE_ORDER),
        "row_count": len(bundle_rows),
        "completed_row_count": completed_row_count,
        "bundle_complete": len(round_rows) == int(rounds) and all(str(row["round_status"]) == "complete" for row in round_rows),
        "round_rows": round_rows,
        "rows": bundle_rows,
        "lane_totals": lane_totals,
    }
    bundle_path = out_dir / "systems_weighted_compounding_v0.json"
    _write_json(bundle_path, bundle)
    return {"summary_path": str(bundle_path), "row_count": len(bundle_rows), "round_count": int(rounds)}


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the Stage-5 systems-weighted compounding slice.")
    parser.add_argument("--rounds", type=int, default=2)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    summary = run_stage5_systems_weighted_compounding(rounds=args.rounds)
    if args.json:
        print(json.dumps(summary, indent=2, sort_keys=True))
    else:
        print(f"stage5_systems_weighted_compounding={summary['summary_path']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
