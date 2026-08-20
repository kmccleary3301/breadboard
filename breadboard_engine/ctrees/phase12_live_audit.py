from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List

from .live_grounding import summarize_live_run
_CONTROL_ROOT = Path("/shared_folders/querylake_server/ray_testing/ray_SCE")
_PHASE11_MATRIX_PATH = (
    _CONTROL_ROOT
    / "docs_tmp"
    / "c_trees"
    / "phase_11"
    / "artifacts"
    / "candidate_a_live_matrix_v1"
    / "phase11_candidate_a_live_matrix_summary_v1.json"
)

_SYSTEM_KEYS = [
    "practical_flagship",
    "practical_gpt54_mini",
    "candidate_a_flagship",
    "candidate_a_gpt54_mini",
]

def _load_json(path: Path) -> Dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def load_phase11_live_matrix_summary() -> Dict[str, Any]:
    return _load_json(_PHASE11_MATRIX_PATH)

def _audit_system_row(system_key: str, system_row: Dict[str, Any]) -> Dict[str, Any]:
    live_summary = summarize_live_run(
        run_dir=_CONTROL_ROOT / "breadboard_main_verify_20260313" / str(system_row.get("run_dir") or ""),
        completion_summary={
            "completed": bool(system_row.get("completed")),
            "reason": str(system_row.get("reason") or ""),
            "steps": int(system_row.get("steps") or 0),
        },
    )

    return {
        "system_key": system_key,
        "run_dir": str(system_row.get("run_dir") or ""),
        "artifacts": dict(live_summary.get("artifacts") or {}),
        "grounded": dict(live_summary.get("grounded") or {}),
        "tool_counts": dict(live_summary.get("tool_counts") or {}),
        "classification": dict(live_summary.get("classification") or {}),
        "source": dict(system_row),
    }


def build_phase12_grounded_live_audit() -> Dict[str, Any]:
    matrix = load_phase11_live_matrix_summary()
    audited_rows: List[Dict[str, Any]] = []
    system_summaries: Dict[str, Dict[str, Any]] = {
        key: {
            "row_count": 0,
            "raw_completion_count": 0,
            "grounded_completion_count": 0,
            "ungrounded_stop_count": 0,
            "runner_instability_count": 0,
            "failure_family_counts": {},
        }
        for key in _SYSTEM_KEYS
    }

    for row in list(matrix.get("rows") or []):
        audited_row: Dict[str, Any] = {"task_id": str(row.get("task_id") or "")}
        for system_key in _SYSTEM_KEYS:
            audited = _audit_system_row(system_key, dict(row.get(system_key) or {}))
            audited_row[system_key] = audited
            summary = system_summaries[system_key]
            summary["row_count"] += 1
            if bool(audited["source"].get("completed")):
                summary["raw_completion_count"] += 1
            if bool(audited["grounded"].get("grounded_completion")):
                summary["grounded_completion_count"] += 1
            if bool(audited["grounded"].get("ungrounded_stop")):
                summary["ungrounded_stop_count"] += 1
            if str(audited["classification"].get("failure_family") or "") == "runner_harness_instability":
                summary["runner_instability_count"] += 1
            family = str(audited["classification"].get("failure_family") or "unknown")
            family_counts = dict(summary.get("failure_family_counts") or {})
            family_counts[family] = int(family_counts.get(family) or 0) + 1
            summary["failure_family_counts"] = family_counts
        audited_rows.append(audited_row)

    return {
        "schema_version": "phase12_grounded_live_audit_v1",
        "source_matrix_path": str(_PHASE11_MATRIX_PATH),
        "row_count": len(audited_rows),
        "rows": audited_rows,
        "system_summaries": system_summaries,
    }
