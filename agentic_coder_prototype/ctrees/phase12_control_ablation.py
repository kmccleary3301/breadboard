from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, Iterable, List


def _load_json(path: str | Path) -> Dict[str, Any]:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _safe_float(value: Any) -> float | None:
    if isinstance(value, (int, float)):
        return float(value)
    return None


def _extract_control_row(row: Dict[str, Any]) -> Dict[str, Any]:
    grounded_summary = dict(row.get("grounded_summary") or {})
    grounded = dict(grounded_summary.get("grounded") or {})
    controller_metrics = dict(grounded_summary.get("controller_metrics") or {})
    classification = dict(grounded_summary.get("classification") or {})
    completion_summary = dict(row.get("completion_summary") or {})
    completion_gate = dict(row.get("completion_gate") or {})
    progress_watchdog = dict(row.get("progress_watchdog") or {})
    reward_v1 = row.get("reward_v1")
    episode_return = None
    if isinstance(reward_v1, dict):
        episode_return = _safe_float(reward_v1.get("episode_return"))

    return {
        "task_id": str(row.get("id") or ""),
        "completed": bool(completion_summary.get("completed")),
        "reason": str(completion_summary.get("reason") or ""),
        "steps": int(completion_summary.get("steps_taken") or 0),
        "run_dir": str(row.get("run_dir") or ""),
        "episode_return": episode_return,
        "grounded_completion": bool(grounded.get("grounded_completion")),
        "verified_completion": bool(controller_metrics.get("verified_completion")),
        "ungrounded_stop": bool(grounded.get("ungrounded_stop")),
        "failure_family": str(classification.get("failure_family") or ""),
        "completion_gate_outcome": str(completion_gate.get("outcome") or ""),
        "completion_gate_satisfied": bool(completion_gate.get("satisfied")),
        "watchdog_triggered": bool(progress_watchdog.get("triggered")),
        "first_write_step": controller_metrics.get("first_write_step"),
        "first_verify_step": controller_metrics.get("first_verify_step"),
        "no_progress_streak_max": int(controller_metrics.get("no_progress_streak_max") or 0),
        "protocol_strategy": str((row.get("phase11_live_protocol") or {}).get("strategy") or ""),
        "base_scenario_id": str((row.get("phase11_live_protocol") or {}).get("base_scenario_id") or ""),
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


def _control_summary(label: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    rows = [_extract_control_row(item) for item in list(payload.get("results") or [])]
    return {
        "system_key": label,
        "summary": dict(payload.get("summary") or {}),
        "rows": rows,
    }


def build_phase12_calibration_control_ablation(
    *,
    practical_old_controller_path: str | Path,
    deterministic_control_path: str | Path,
    candidate_a_control_path: str | Path,
) -> Dict[str, Any]:
    practical_payload = _load_json(practical_old_controller_path)
    deterministic_payload = _load_json(deterministic_control_path)
    candidate_payload = _load_json(candidate_a_control_path)

    practical_rows = {
        row["task_id"]: row
        for row in [_extract_control_row(item) for item in list(practical_payload.get("results") or [])]
    }
    deterministic_rows = {
        row["task_id"]: row
        for row in _control_summary("deterministic_control", deterministic_payload)["rows"]
    }
    candidate_rows = {
        row["task_id"]: row
        for row in _control_summary("candidate_a_control", candidate_payload)["rows"]
    }

    ordered_ids = list(practical_rows.keys())
    rows: List[Dict[str, Any]] = []
    for task_id in ordered_ids:
        practical_row = practical_rows[task_id]
        deterministic_row = deterministic_rows[task_id]
        candidate_row = candidate_rows[task_id]
        rows.append(
            {
                "task_id": task_id,
                "practical_flagship_old_controller": practical_row,
                "deterministic_control": deterministic_row,
                "candidate_a_control": candidate_row,
                "candidate_a_control_vs_deterministic_control": _comparison(candidate_row, deterministic_row),
                "candidate_a_control_vs_practical_old_controller": _comparison(candidate_row, practical_row),
                "deterministic_control_vs_practical_old_controller": _comparison(deterministic_row, practical_row),
            }
        )

    return {
        "schema_version": "phase12_calibration_control_ablation_v1",
        "practical_flagship_old_controller_summary": dict(practical_payload.get("summary") or {}),
        "deterministic_control_summary": dict(deterministic_payload.get("summary") or {}),
        "candidate_a_control_summary": dict(candidate_payload.get("summary") or {}),
        "counts": {
            "candidate_a_control_vs_deterministic_control": _count_comparisons(
                rows, "candidate_a_control_vs_deterministic_control"
            ),
            "candidate_a_control_vs_practical_old_controller": _count_comparisons(
                rows, "candidate_a_control_vs_practical_old_controller"
            ),
            "deterministic_control_vs_practical_old_controller": _count_comparisons(
                rows, "deterministic_control_vs_practical_old_controller"
            ),
        },
        "rows": rows,
    }


def build_phase12_anchor_control_ablation(
    *,
    grounded_old_controller_matrix_path: str | Path,
    deterministic_control_path: str | Path,
    candidate_a_control_path: str | Path,
) -> Dict[str, Any]:
    old_matrix = _load_json(grounded_old_controller_matrix_path)
    deterministic_payload = _load_json(deterministic_control_path)
    candidate_payload = _load_json(candidate_a_control_path)

    deterministic_rows = {
        row["task_id"]: row
        for row in _control_summary("deterministic_control", deterministic_payload)["rows"]
    }
    candidate_rows = {
        row["task_id"]: row
        for row in _control_summary("candidate_a_control", candidate_payload)["rows"]
    }

    rows: List[Dict[str, Any]] = []
    for old_row in list(old_matrix.get("rows") or []):
        task_id = str(old_row.get("task_id") or "")
        practical_old = dict(old_row.get("practical_flagship") or {})
        candidate_old = dict(old_row.get("candidate_a_flagship") or {})
        practical_old_flat = {
            "task_id": task_id,
            "completed": bool((practical_old.get("grounded") or {}).get("completed")),
            "reason": str((practical_old.get("source") or {}).get("reason") or ""),
            "steps": int((practical_old.get("source") or {}).get("steps") or 0),
            "run_dir": str(practical_old.get("run_dir") or ""),
            "episode_return": _safe_float((practical_old.get("source") or {}).get("episode_return")),
            "grounded_completion": bool((practical_old.get("grounded") or {}).get("grounded_completion")),
            "verified_completion": False,
            "ungrounded_stop": bool((practical_old.get("grounded") or {}).get("ungrounded_stop")),
            "failure_family": str((practical_old.get("classification") or {}).get("failure_family") or ""),
            "completion_gate_outcome": "legacy_old_controller",
            "completion_gate_satisfied": False,
            "watchdog_triggered": False,
            "first_write_step": None,
            "first_verify_step": None,
            "no_progress_streak_max": 0,
            "protocol_strategy": "practical_old_controller",
            "base_scenario_id": "",
        }
        candidate_old_flat = {
            "task_id": task_id,
            "completed": bool((candidate_old.get("grounded") or {}).get("completed")),
            "reason": str((candidate_old.get("source") or {}).get("reason") or ""),
            "steps": int((candidate_old.get("source") or {}).get("steps") or 0),
            "run_dir": str(candidate_old.get("run_dir") or ""),
            "episode_return": _safe_float((candidate_old.get("source") or {}).get("episode_return")),
            "grounded_completion": bool((candidate_old.get("grounded") or {}).get("grounded_completion")),
            "verified_completion": False,
            "ungrounded_stop": bool((candidate_old.get("grounded") or {}).get("ungrounded_stop")),
            "failure_family": str((candidate_old.get("classification") or {}).get("failure_family") or ""),
            "completion_gate_outcome": "legacy_old_controller",
            "completion_gate_satisfied": False,
            "watchdog_triggered": False,
            "first_write_step": None,
            "first_verify_step": None,
            "no_progress_streak_max": 0,
            "protocol_strategy": "candidate_a_old_controller",
            "base_scenario_id": "",
        }
        deterministic_row = deterministic_rows[task_id]
        candidate_row = candidate_rows[task_id]
        rows.append(
            {
                "task_id": task_id,
                "practical_flagship_old_controller": practical_old_flat,
                "candidate_a_flagship_old_controller": candidate_old_flat,
                "deterministic_control": deterministic_row,
                "candidate_a_control": candidate_row,
                "candidate_a_control_vs_deterministic_control": _comparison(candidate_row, deterministic_row),
                "candidate_a_control_vs_candidate_a_old_controller": _comparison(candidate_row, candidate_old_flat),
                "deterministic_control_vs_practical_old_controller": _comparison(deterministic_row, practical_old_flat),
            }
        )

    return {
        "schema_version": "phase12_anchor_control_ablation_v1",
        "old_controller_system_summaries": dict(old_matrix.get("system_summaries") or {}),
        "deterministic_control_summary": dict(deterministic_payload.get("summary") or {}),
        "candidate_a_control_summary": dict(candidate_payload.get("summary") or {}),
        "counts": {
            "candidate_a_control_vs_deterministic_control": _count_comparisons(
                rows, "candidate_a_control_vs_deterministic_control"
            ),
            "candidate_a_control_vs_candidate_a_old_controller": _count_comparisons(
                rows, "candidate_a_control_vs_candidate_a_old_controller"
            ),
            "deterministic_control_vs_practical_old_controller": _count_comparisons(
                rows, "deterministic_control_vs_practical_old_controller"
            ),
        },
        "rows": rows,
    }


def build_phase12_iteration2_calibration_ablation(
    *,
    practical_old_controller_path: str | Path,
    deterministic_control_v1_path: str | Path,
    candidate_a_control_v1_path: str | Path,
    deterministic_control_v2_path: str | Path,
    candidate_a_control_v2_path: str | Path,
) -> Dict[str, Any]:
    practical_payload = _load_json(practical_old_controller_path)
    deterministic_v1_payload = _load_json(deterministic_control_v1_path)
    candidate_v1_payload = _load_json(candidate_a_control_v1_path)
    deterministic_v2_payload = _load_json(deterministic_control_v2_path)
    candidate_v2_payload = _load_json(candidate_a_control_v2_path)

    practical_rows = {
        row["task_id"]: row
        for row in [_extract_control_row(item) for item in list(practical_payload.get("results") or [])]
    }
    deterministic_v1_rows = {
        row["task_id"]: row
        for row in _control_summary("deterministic_control_v1", deterministic_v1_payload)["rows"]
    }
    candidate_v1_rows = {
        row["task_id"]: row
        for row in _control_summary("candidate_a_control_v1", candidate_v1_payload)["rows"]
    }
    deterministic_v2_rows = {
        row["task_id"]: row
        for row in _control_summary("deterministic_control_v2", deterministic_v2_payload)["rows"]
    }
    candidate_v2_rows = {
        row["task_id"]: row
        for row in _control_summary("candidate_a_control_v2", candidate_v2_payload)["rows"]
    }

    rows: List[Dict[str, Any]] = []
    for task_id in practical_rows:
        practical_row = practical_rows[task_id]
        deterministic_v1 = deterministic_v1_rows[task_id]
        candidate_v1 = candidate_v1_rows[task_id]
        deterministic_v2 = deterministic_v2_rows[task_id]
        candidate_v2 = candidate_v2_rows[task_id]
        rows.append(
            {
                "task_id": task_id,
                "practical_flagship_old_controller": practical_row,
                "deterministic_control_v1": deterministic_v1,
                "candidate_a_control_v1": candidate_v1,
                "deterministic_control_v2": deterministic_v2,
                "candidate_a_control_v2": candidate_v2,
                "candidate_a_control_v2_vs_deterministic_control_v2": _comparison(candidate_v2, deterministic_v2),
                "candidate_a_control_v2_vs_candidate_a_control_v1": _comparison(candidate_v2, candidate_v1),
                "deterministic_control_v2_vs_deterministic_control_v1": _comparison(deterministic_v2, deterministic_v1),
                "candidate_a_control_v2_vs_practical_old_controller": _comparison(candidate_v2, practical_row),
            }
        )

    return {
        "schema_version": "phase12_iteration2_calibration_ablation_v1",
        "practical_flagship_old_controller_summary": dict(practical_payload.get("summary") or {}),
        "deterministic_control_v1_summary": dict(deterministic_v1_payload.get("summary") or {}),
        "candidate_a_control_v1_summary": dict(candidate_v1_payload.get("summary") or {}),
        "deterministic_control_v2_summary": dict(deterministic_v2_payload.get("summary") or {}),
        "candidate_a_control_v2_summary": dict(candidate_v2_payload.get("summary") or {}),
        "counts": {
            "candidate_a_control_v2_vs_deterministic_control_v2": _count_comparisons(
                rows, "candidate_a_control_v2_vs_deterministic_control_v2"
            ),
            "candidate_a_control_v2_vs_candidate_a_control_v1": _count_comparisons(
                rows, "candidate_a_control_v2_vs_candidate_a_control_v1"
            ),
            "deterministic_control_v2_vs_deterministic_control_v1": _count_comparisons(
                rows, "deterministic_control_v2_vs_deterministic_control_v1"
            ),
            "candidate_a_control_v2_vs_practical_old_controller": _count_comparisons(
                rows, "candidate_a_control_v2_vs_practical_old_controller"
            ),
        },
        "rows": rows,
    }


def build_phase12_iteration2_anchor_ablation(
    *,
    grounded_old_controller_matrix_path: str | Path,
    deterministic_control_v1_path: str | Path,
    candidate_a_control_v1_path: str | Path,
    deterministic_control_v2_path: str | Path,
    candidate_a_control_v2_path: str | Path,
) -> Dict[str, Any]:
    old_matrix = _load_json(grounded_old_controller_matrix_path)
    deterministic_v1_payload = _load_json(deterministic_control_v1_path)
    candidate_v1_payload = _load_json(candidate_a_control_v1_path)
    deterministic_v2_payload = _load_json(deterministic_control_v2_path)
    candidate_v2_payload = _load_json(candidate_a_control_v2_path)

    deterministic_v1_rows = {
        row["task_id"]: row
        for row in _control_summary("deterministic_control_v1", deterministic_v1_payload)["rows"]
    }
    candidate_v1_rows = {
        row["task_id"]: row
        for row in _control_summary("candidate_a_control_v1", candidate_v1_payload)["rows"]
    }
    deterministic_v2_rows = {
        row["task_id"]: row
        for row in _control_summary("deterministic_control_v2", deterministic_v2_payload)["rows"]
    }
    candidate_v2_rows = {
        row["task_id"]: row
        for row in _control_summary("candidate_a_control_v2", candidate_v2_payload)["rows"]
    }

    rows: List[Dict[str, Any]] = []
    for old_row in list(old_matrix.get("rows") or []):
        task_id = str(old_row.get("task_id") or "")
        practical_old = dict(old_row.get("practical_flagship") or {})
        candidate_old = dict(old_row.get("candidate_a_flagship") or {})
        practical_old_flat = {
            "task_id": task_id,
            "completed": bool((practical_old.get("grounded") or {}).get("completed")),
            "reason": str((practical_old.get("source") or {}).get("reason") or ""),
            "steps": int((practical_old.get("source") or {}).get("steps") or 0),
            "run_dir": str(practical_old.get("run_dir") or ""),
            "episode_return": _safe_float((practical_old.get("source") or {}).get("episode_return")),
            "grounded_completion": bool((practical_old.get("grounded") or {}).get("grounded_completion")),
            "verified_completion": False,
            "ungrounded_stop": bool((practical_old.get("grounded") or {}).get("ungrounded_stop")),
            "failure_family": str((practical_old.get("classification") or {}).get("failure_family") or ""),
            "completion_gate_outcome": "legacy_old_controller",
            "completion_gate_satisfied": False,
            "watchdog_triggered": False,
            "first_write_step": None,
            "first_verify_step": None,
            "no_progress_streak_max": 0,
            "protocol_strategy": "practical_old_controller",
            "base_scenario_id": "",
        }
        candidate_old_flat = {
            "task_id": task_id,
            "completed": bool((candidate_old.get("grounded") or {}).get("completed")),
            "reason": str((candidate_old.get("source") or {}).get("reason") or ""),
            "steps": int((candidate_old.get("source") or {}).get("steps") or 0),
            "run_dir": str(candidate_old.get("run_dir") or ""),
            "episode_return": _safe_float((candidate_old.get("source") or {}).get("episode_return")),
            "grounded_completion": bool((candidate_old.get("grounded") or {}).get("grounded_completion")),
            "verified_completion": False,
            "ungrounded_stop": bool((candidate_old.get("grounded") or {}).get("ungrounded_stop")),
            "failure_family": str((candidate_old.get("classification") or {}).get("failure_family") or ""),
            "completion_gate_outcome": "legacy_old_controller",
            "completion_gate_satisfied": False,
            "watchdog_triggered": False,
            "first_write_step": None,
            "first_verify_step": None,
            "no_progress_streak_max": 0,
            "protocol_strategy": "candidate_a_old_controller",
            "base_scenario_id": "",
        }
        deterministic_v1 = deterministic_v1_rows[task_id]
        candidate_v1 = candidate_v1_rows[task_id]
        deterministic_v2 = deterministic_v2_rows[task_id]
        candidate_v2 = candidate_v2_rows[task_id]
        rows.append(
            {
                "task_id": task_id,
                "practical_flagship_old_controller": practical_old_flat,
                "candidate_a_flagship_old_controller": candidate_old_flat,
                "deterministic_control_v1": deterministic_v1,
                "candidate_a_control_v1": candidate_v1,
                "deterministic_control_v2": deterministic_v2,
                "candidate_a_control_v2": candidate_v2,
                "candidate_a_control_v2_vs_deterministic_control_v2": _comparison(candidate_v2, deterministic_v2),
                "candidate_a_control_v2_vs_candidate_a_control_v1": _comparison(candidate_v2, candidate_v1),
                "deterministic_control_v2_vs_deterministic_control_v1": _comparison(deterministic_v2, deterministic_v1),
                "candidate_a_control_v2_vs_candidate_a_flagship_old_controller": _comparison(candidate_v2, candidate_old_flat),
            }
        )

    return {
        "schema_version": "phase12_iteration2_anchor_ablation_v1",
        "old_controller_system_summaries": dict(old_matrix.get("system_summaries") or {}),
        "deterministic_control_v1_summary": dict(deterministic_v1_payload.get("summary") or {}),
        "candidate_a_control_v1_summary": dict(candidate_v1_payload.get("summary") or {}),
        "deterministic_control_v2_summary": dict(deterministic_v2_payload.get("summary") or {}),
        "candidate_a_control_v2_summary": dict(candidate_v2_payload.get("summary") or {}),
        "counts": {
            "candidate_a_control_v2_vs_deterministic_control_v2": _count_comparisons(
                rows, "candidate_a_control_v2_vs_deterministic_control_v2"
            ),
            "candidate_a_control_v2_vs_candidate_a_control_v1": _count_comparisons(
                rows, "candidate_a_control_v2_vs_candidate_a_control_v1"
            ),
            "deterministic_control_v2_vs_deterministic_control_v1": _count_comparisons(
                rows, "deterministic_control_v2_vs_deterministic_control_v1"
            ),
            "candidate_a_control_v2_vs_candidate_a_flagship_old_controller": _count_comparisons(
                rows, "candidate_a_control_v2_vs_candidate_a_flagship_old_controller"
            ),
        },
        "rows": rows,
    }
