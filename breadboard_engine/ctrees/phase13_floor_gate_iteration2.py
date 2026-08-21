from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, Iterable, List


def _load_json(path: str | Path) -> Dict[str, Any]:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _count_comparisons(rows: Iterable[Dict[str, Any]], field: str) -> Dict[str, int]:
    wins = 0
    losses = 0
    ties = 0
    for row in rows:
        comparison = str((row.get(field) or {}).get("comparison") or "")
        if comparison == "win":
            wins += 1
        elif comparison == "loss":
            losses += 1
        else:
            ties += 1
    return {"wins": wins, "losses": losses, "ties": ties}


def _comparison(left: Dict[str, Any], right: Dict[str, Any]) -> Dict[str, str]:
    left_grounded = bool(left.get("grounded_completion"))
    right_grounded = bool(right.get("grounded_completion"))
    if left_grounded and not right_grounded:
        return {"comparison": "win", "basis": "grounded_completion"}
    if right_grounded and not left_grounded:
        return {"comparison": "loss", "basis": "grounded_completion"}
    left_verified = bool(left.get("verified_completion"))
    right_verified = bool(right.get("verified_completion"))
    if left_verified and not right_verified:
        return {"comparison": "win", "basis": "verified_completion"}
    if right_verified and not left_verified:
        return {"comparison": "loss", "basis": "verified_completion"}
    return {"comparison": "tie", "basis": "grounded_parity"}


def build_phase13_floor_gate_iteration2_summary(
    *,
    candidate_a_v1_path: str | Path,
    deterministic_v1_path: str | Path,
    candidate_a_v2_path: str | Path,
    deterministic_v2_path: str | Path,
) -> Dict[str, Any]:
    cand_v1 = _load_json(candidate_a_v1_path)
    det_v1 = _load_json(deterministic_v1_path)
    cand_v2 = _load_json(candidate_a_v2_path)
    det_v2 = _load_json(deterministic_v2_path)

    def rows(payload: Dict[str, Any], key_name: str) -> Dict[str, Dict[str, Any]]:
        out: Dict[str, Dict[str, Any]] = {}
        for row in list(payload.get("rows") or []):
            if key_name in row:
                out[str(row.get("task_id") or "")] = dict(row.get(key_name) or {})
        return out

    cand_v1_rows = rows(cand_v1, "candidate_a_runtime_v1")
    det_v1_rows = rows(det_v1, "deterministic_runtime_v1")
    cand_v2_rows = rows(cand_v2, "candidate_a_runtime_v2")
    det_v2_rows = rows(det_v2, "deterministic_runtime_v2")

    ordered_ids = list(cand_v2_rows.keys())
    out_rows: List[Dict[str, Any]] = []
    for task_id in ordered_ids:
        out_rows.append(
            {
                "task_id": task_id,
                "candidate_a_runtime_v1": cand_v1_rows[task_id],
                "deterministic_runtime_v1": det_v1_rows[task_id],
                "candidate_a_runtime_v2": cand_v2_rows[task_id],
                "deterministic_runtime_v2": det_v2_rows[task_id],
                "candidate_a_runtime_v2_vs_candidate_a_runtime_v1": _comparison(
                    cand_v2_rows[task_id], cand_v1_rows[task_id]
                ),
                "deterministic_runtime_v2_vs_deterministic_runtime_v1": _comparison(
                    det_v2_rows[task_id], det_v1_rows[task_id]
                ),
                "candidate_a_runtime_v2_vs_deterministic_runtime_v2": _comparison(
                    cand_v2_rows[task_id], det_v2_rows[task_id]
                ),
            }
        )

    return {
        "schema_version": "phase13_floor_gate_iteration2_summary_v1",
        "candidate_a_runtime_v1_summary": dict(cand_v1.get("candidate_a_runtime_v1_summary") or {}),
        "deterministic_runtime_v1_summary": dict(det_v1.get("deterministic_runtime_v1_summary") or {}),
        "candidate_a_runtime_v2_summary": dict(cand_v2.get("candidate_a_runtime_v2_summary") or {}),
        "deterministic_runtime_v2_summary": dict(det_v2.get("deterministic_runtime_v2_summary") or {}),
        "counts": {
            "candidate_a_runtime_v2_vs_candidate_a_runtime_v1": _count_comparisons(
                out_rows, "candidate_a_runtime_v2_vs_candidate_a_runtime_v1"
            ),
            "deterministic_runtime_v2_vs_deterministic_runtime_v1": _count_comparisons(
                out_rows, "deterministic_runtime_v2_vs_deterministic_runtime_v1"
            ),
            "candidate_a_runtime_v2_vs_deterministic_runtime_v2": _count_comparisons(
                out_rows, "candidate_a_runtime_v2_vs_deterministic_runtime_v2"
            ),
        },
        "rows": out_rows,
    }
