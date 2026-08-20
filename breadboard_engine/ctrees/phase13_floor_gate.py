from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, Iterable, List

from .live_grounding import summarize_live_run


def _load_json(path: str | Path) -> Dict[str, Any]:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _safe_float(value: Any) -> float | None:
    if isinstance(value, (int, float)):
        return float(value)
    return None


def _extract_runtime_row(row: Dict[str, Any]) -> Dict[str, Any]:
    completion_summary = dict(row.get("completion_summary") or {})
    run_dir = str(row.get("run_dir") or "")
    if run_dir and Path(run_dir).exists():
        grounded_summary = summarize_live_run(
            run_dir=run_dir,
            completion_summary=completion_summary if completion_summary else None,
        )
    else:
        grounded_summary = dict(row.get("grounded_summary") or {})
    grounded = dict(grounded_summary.get("grounded") or {})
    controller_metrics = dict(grounded_summary.get("controller_metrics") or {})
    classification = dict(grounded_summary.get("classification") or {})
    completion_gate = dict(row.get("completion_gate") or {})
    protocol = dict(row.get("phase11_live_protocol") or {})
    runtime_contract = dict(protocol.get("runtime_contract") or {})
    reward_v1 = row.get("reward_v1")
    episode_return = None
    if isinstance(reward_v1, dict):
        episode_return = _safe_float(reward_v1.get("episode_return"))

    return {
        "task_id": str(row.get("id") or ""),
        "completed": bool(completion_summary.get("completed")),
        "reason": str(completion_summary.get("reason") or ""),
        "steps": int(completion_summary.get("steps_taken") or 0),
        "run_dir": run_dir,
        "episode_return": episode_return,
        "grounded_completion": bool(grounded.get("grounded_completion")),
        "verified_completion": bool(controller_metrics.get("verified_completion")),
        "ungrounded_stop": bool(grounded.get("ungrounded_stop")),
        "failure_family": str(classification.get("failure_family") or ""),
        "completion_gate_outcome": str(completion_gate.get("outcome") or ""),
        "completion_gate_satisfied": bool(completion_gate.get("satisfied")),
        "watchdog_triggered": bool((row.get("progress_watchdog") or {}).get("triggered")),
        "first_write_step": controller_metrics.get("first_write_step"),
        "first_verify_step": controller_metrics.get("first_verify_step"),
        "no_progress_streak_max": int(controller_metrics.get("no_progress_streak_max") or 0),
        "protocol_family": str(protocol.get("family") or ""),
        "support_strategy": str(protocol.get("support_strategy") or ""),
        "runtime_phase_count": len(list(runtime_contract.get("phase_policies") or [])),
    }


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


def _system_summary(label: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    rows = [_extract_runtime_row(item) for item in list(payload.get("results") or [])]
    return {
        "system_key": label,
        "summary": dict(payload.get("summary") or {}),
        "rows": rows,
    }


def build_phase13_floor_gate_summary(
    *,
    candidate_a_runtime_path: str | Path,
    deterministic_runtime_path: str | Path,
    candidate_label: str = "candidate_a_runtime_v1",
    deterministic_label: str = "deterministic_runtime_v1",
) -> Dict[str, Any]:
    candidate_payload = _load_json(candidate_a_runtime_path)
    deterministic_payload = _load_json(deterministic_runtime_path)

    candidate_rows = {
        row["task_id"]: row for row in _system_summary("candidate_a_runtime_v1", candidate_payload)["rows"]
    }
    deterministic_rows = {
        row["task_id"]: row for row in _system_summary("deterministic_runtime_v1", deterministic_payload)["rows"]
    }

    ordered_ids = list(candidate_rows.keys())
    rows: List[Dict[str, Any]] = []
    for task_id in ordered_ids:
        candidate_row = candidate_rows[task_id]
        deterministic_row = deterministic_rows[task_id]
        rows.append(
            {
                "task_id": task_id,
                candidate_label: candidate_row,
                deterministic_label: deterministic_row,
                f"{candidate_label}_vs_{deterministic_label}": _comparison(
                    candidate_row, deterministic_row
                ),
            }
        )

    return {
        "schema_version": "phase13_floor_gate_summary_v1",
        f"{candidate_label}_summary": dict(candidate_payload.get("summary") or {}),
        f"{deterministic_label}_summary": dict(deterministic_payload.get("summary") or {}),
        "counts": {
            f"{candidate_label}_vs_{deterministic_label}": _count_comparisons(
                rows, f"{candidate_label}_vs_{deterministic_label}"
            ),
        },
        "rows": rows,
    }
