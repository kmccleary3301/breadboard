from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, Iterable, List


_CONTROL_ROOT = Path("/shared_folders/querylake_server/ray_testing/ray_SCE")
_PHASE12_ROOT = _CONTROL_ROOT / "docs_tmp" / "c_trees" / "phase_12"
_ANCHOR_V2_PATH = (
    _PHASE12_ROOT
    / "artifacts"
    / "controller_iteration2_v1"
    / "anchor"
    / "phase12_controller_iteration2_anchor_summary_v1.json"
)
_CALIBRATION_V2_PATH = (
    _PHASE12_ROOT
    / "artifacts"
    / "controller_iteration2_v1"
    / "calibration"
    / "phase12_controller_iteration2_calibration_summary_v1.json"
)

_ANCHOR_SYSTEM_KEYS = [
    "practical_flagship_old_controller",
    "candidate_a_flagship_old_controller",
    "deterministic_control_v1",
    "candidate_a_control_v1",
    "deterministic_control_v2",
    "candidate_a_control_v2",
]

_CALIBRATION_SYSTEM_KEYS = [
    "practical_flagship_old_controller",
    "deterministic_control_v1",
    "candidate_a_control_v1",
    "deterministic_control_v2",
    "candidate_a_control_v2",
]


def _load_json(path: Path) -> Dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def load_phase12_anchor_v2_summary() -> Dict[str, Any]:
    return _load_json(_ANCHOR_V2_PATH)


def load_phase12_calibration_v2_summary() -> Dict[str, Any]:
    return _load_json(_CALIBRATION_V2_PATH)


def classify_phase13_loop_subfamily(system_row: Dict[str, Any]) -> Dict[str, Any]:
    failure_family = str(system_row.get("failure_family") or "")
    completed = bool(system_row.get("completed"))
    grounded_completion = bool(system_row.get("grounded_completion"))
    verified_completion = bool(system_row.get("verified_completion"))
    ungrounded_stop = bool(system_row.get("ungrounded_stop"))
    first_write_step = system_row.get("first_write_step")
    first_verify_step = system_row.get("first_verify_step")
    watchdog_triggered = bool(system_row.get("watchdog_triggered"))
    reason = str(system_row.get("reason") or "")

    subfamily = "other"
    evidence: List[str] = []

    if failure_family == "runner_harness_instability":
        subfamily = "runner_harness_instability"
        evidence.append("phase12_failure_family=runner_harness_instability")
    elif ungrounded_stop or failure_family == "premature_natural_language_stop":
        subfamily = "premature_natural_language_stop"
        evidence.append("ungrounded_stop=true")
    elif grounded_completion and not verified_completion:
        subfamily = "verify_without_close"
        evidence.append("grounded_without_verified=true")
    elif failure_family == "patch_without_verification":
        subfamily = "patch_without_verify"
        evidence.append("phase12_failure_family=patch_without_verification")
    elif first_write_step is None and first_verify_step == 1:
        subfamily = "early_verify_no_edit"
        evidence.append("first_verify_step=1")
        evidence.append("first_write_step=None")
    elif first_write_step is None and first_verify_step is not None:
        subfamily = "inspect_shell_thrash"
        evidence.append(f"first_verify_step={first_verify_step}")
        evidence.append("first_write_step=None")
    elif first_write_step is not None and not verified_completion:
        subfamily = "patch_without_verify"
        evidence.append(f"first_write_step={first_write_step}")
    elif reason == "max_steps_exhausted" and watchdog_triggered:
        subfamily = "budget_floor_mismatch"
        evidence.append("max_steps_exhausted")
        evidence.append("watchdog_triggered=true")
    elif failure_family:
        subfamily = failure_family
        evidence.append(f"phase12_failure_family={failure_family}")
    else:
        evidence.append(f"reason={reason}")

    return {
        "loop_subfamily": subfamily,
        "evidence_note": "; ".join(evidence),
    }


def _count(items: Iterable[str]) -> Dict[str, int]:
    counts: Dict[str, int] = {}
    for item in items:
        counts[item] = int(counts.get(item) or 0) + 1
    return counts


def _audit_system_rows(rows: List[Dict[str, Any]], system_keys: List[str]) -> Dict[str, Dict[str, Any]]:
    summaries: Dict[str, Dict[str, Any]] = {}
    for system_key in system_keys:
        system_rows = [dict(row.get(system_key) or {}) for row in rows]
        refined = [classify_phase13_loop_subfamily(item) for item in system_rows]
        summaries[system_key] = {
            "row_count": len(system_rows),
            "grounded_completion_count": sum(1 for item in system_rows if bool(item.get("grounded_completion"))),
            "verified_completion_count": sum(1 for item in system_rows if bool(item.get("verified_completion"))),
            "runner_instability_count": sum(
                1 for item in refined if str(item.get("loop_subfamily") or "") == "runner_harness_instability"
            ),
            "first_write_missing_count": sum(1 for item in system_rows if item.get("first_write_step") is None),
            "early_verify_no_edit_count": sum(
                1 for item in refined if str(item.get("loop_subfamily") or "") == "early_verify_no_edit"
            ),
            "inspect_shell_thrash_count": sum(
                1 for item in refined if str(item.get("loop_subfamily") or "") == "inspect_shell_thrash"
            ),
            "loop_subfamily_counts": _count(str(item.get("loop_subfamily") or "other") for item in refined),
        }
    return summaries


def _build_rows(rows: List[Dict[str, Any]], system_keys: List[str]) -> List[Dict[str, Any]]:
    payload_rows: List[Dict[str, Any]] = []
    for row in rows:
        out: Dict[str, Any] = {"task_id": str(row.get("task_id") or "")}
        for system_key in system_keys:
            system_row = dict(row.get(system_key) or {})
            out[system_key] = {
                **system_row,
                "phase13_runtime_audit": classify_phase13_loop_subfamily(system_row),
            }
        payload_rows.append(out)
    return payload_rows


def build_phase13_runtime_surface_audit() -> Dict[str, Any]:
    anchor = load_phase12_anchor_v2_summary()
    calibration = load_phase12_calibration_v2_summary()
    anchor_rows = list(anchor.get("rows") or [])
    calibration_rows = list(calibration.get("rows") or [])

    anchor_payload_rows = _build_rows(anchor_rows, _ANCHOR_SYSTEM_KEYS)
    calibration_payload_rows = _build_rows(calibration_rows, _CALIBRATION_SYSTEM_KEYS)

    practical_calibration = dict((calibration_payload_rows[0] if calibration_payload_rows else {}).get("practical_flagship_old_controller") or {})
    practical_calibration_grounded = sum(
        1
        for row in calibration_payload_rows
        if bool(dict(row.get("practical_flagship_old_controller") or {}).get("grounded_completion"))
    )
    practical_anchor_grounded = sum(
        1
        for row in anchor_payload_rows
        if bool(dict(row.get("practical_flagship_old_controller") or {}).get("grounded_completion"))
    )

    return {
        "schema_version": "phase13_runtime_surface_audit_v1",
        "source_paths": {
            "anchor_v2_summary": str(_ANCHOR_V2_PATH),
            "calibration_v2_summary": str(_CALIBRATION_V2_PATH),
        },
        "runner_adequacy_summary": {
            "practical_flagship_calibration_grounded_count": practical_calibration_grounded,
            "practical_flagship_anchor_grounded_count": practical_anchor_grounded,
            "practical_flagship_calibration_has_runner_instability": (
                practical_calibration.get("phase13_runtime_audit", {}).get("loop_subfamily")
                == "runner_harness_instability"
            ),
            "anchor_all_systems_zero_grounded": all(
                sum(1 for row in anchor_payload_rows if bool(dict(row.get(system_key) or {}).get("grounded_completion"))) == 0
                for system_key in _ANCHOR_SYSTEM_KEYS
            ),
            "calibration_all_runtime_variants_zero_grounded": all(
                sum(1 for row in calibration_payload_rows if bool(dict(row.get(system_key) or {}).get("grounded_completion"))) == 0
                for system_key in [
                    "deterministic_control_v1",
                    "candidate_a_control_v1",
                    "deterministic_control_v2",
                    "candidate_a_control_v2",
                ]
            ),
        },
        "anchor_system_summaries": _audit_system_rows(anchor_rows, _ANCHOR_SYSTEM_KEYS),
        "calibration_system_summaries": _audit_system_rows(calibration_rows, _CALIBRATION_SYSTEM_KEYS),
        "anchor_rows": anchor_payload_rows,
        "calibration_rows": calibration_payload_rows,
    }

