from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, Iterable, List

from .closure_rule_registry import build_phase15_task_closure_rule_map
from .live_grounding import summarize_live_run


def _load_json(path: str | Path) -> Dict[str, Any]:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _grounded_summary_for_row(row: Dict[str, Any]) -> Dict[str, Any]:
    grounded_summary = dict(row.get("grounded_summary") or {})
    if grounded_summary:
        return grounded_summary
    run_dir = row.get("run_dir")
    completion_summary = dict(row.get("completion_summary") or {})
    return summarize_live_run(run_dir=run_dir, completion_summary=completion_summary)


def score_row_with_receipts(
    row: Dict[str, Any],
    *,
    closure_rule_map: Dict[str, Dict[str, Any]] | None = None,
) -> Dict[str, Any]:
    rules = closure_rule_map or build_phase15_task_closure_rule_map()
    task_id = str(row.get("id") or row.get("task_id") or "")
    grounded_summary = _grounded_summary_for_row(row)
    tool_counts = dict(grounded_summary.get("tool_counts") or {})
    controller_metrics = dict(grounded_summary.get("controller_metrics") or {})
    grounded = dict(grounded_summary.get("grounded") or {})
    classification = dict(grounded_summary.get("classification") or {})
    completion_summary = dict(row.get("completion_summary") or {})

    closure_rule = dict(rules.get(task_id) or {})
    closure_mode = str(closure_rule.get("closure_mode") or "unknown")
    invalid_finish_trap = bool(closure_rule.get("invalid_finish_trap"))

    successful_writes = int(tool_counts.get("successful_writes") or 0)
    successful_tests = int(tool_counts.get("successful_tests") or 0)
    test_commands = int(tool_counts.get("test_commands") or 0)
    run_shell_calls = int(tool_counts.get("run_shell_calls") or 0)
    proof_receipt_calls = int(tool_counts.get("proof_receipt_calls") or 0)
    verification_receipt_calls = int(tool_counts.get("verification_receipt_calls") or 0)
    branch_receipt_calls = int(tool_counts.get("branch_receipt_calls") or 0)
    finish_receipt_calls = int(tool_counts.get("finish_receipt_calls") or 0)

    edit_receipt_observed = successful_writes > 0
    verification_receipt_observed = successful_tests > 0 or verification_receipt_calls > 0
    shell_grounding_observed = run_shell_calls > 0 or successful_tests > 0 or test_commands > 0

    # Legacy runs do not emit explicit proof receipts. Newer branch/receipt runs can.
    proof_like_evidence_observed = shell_grounding_observed and not edit_receipt_observed
    explicit_proof_receipt_observed = proof_receipt_calls > 0

    completed = bool(completion_summary.get("completed"))
    accepted_invalid_finish = bool(
        invalid_finish_trap
        and completed
        and not edit_receipt_observed
        and not explicit_proof_receipt_observed
    )

    if closure_mode == "edit_required":
        receipt_valid_completion = completed and edit_receipt_observed
        verified_completion_v2 = receipt_valid_completion and verification_receipt_observed
        branch_selected = edit_receipt_observed
    elif closure_mode == "proof_required":
        receipt_valid_completion = completed and explicit_proof_receipt_observed
        verified_completion_v2 = receipt_valid_completion
        branch_selected = proof_like_evidence_observed or explicit_proof_receipt_observed
    elif closure_mode == "edit_or_proof":
        receipt_valid_completion = completed and (edit_receipt_observed or explicit_proof_receipt_observed)
        verified_completion_v2 = (
            (edit_receipt_observed and verification_receipt_observed)
            or explicit_proof_receipt_observed
        ) and completed
        branch_selected = edit_receipt_observed or proof_like_evidence_observed or explicit_proof_receipt_observed
    else:
        receipt_valid_completion = False
        verified_completion_v2 = False
        branch_selected = False

    return {
        "task_id": task_id,
        "closure_rule": closure_rule,
        "legacy_grounded_completion": bool(grounded.get("grounded_completion")),
        "legacy_verified_completion": bool(controller_metrics.get("verified_completion")),
        "receipt_signals": {
            "edit_receipt_observed": edit_receipt_observed,
            "verification_receipt_observed": verification_receipt_observed,
            "proof_like_evidence_observed": proof_like_evidence_observed,
            "explicit_proof_receipt_observed": explicit_proof_receipt_observed,
            "shell_grounding_observed": shell_grounding_observed,
            "explicit_branch_receipt_observed": branch_receipt_calls > 0,
            "explicit_finish_request_observed": finish_receipt_calls > 0,
            "first_write_step": controller_metrics.get("first_write_step"),
            "first_verify_step": controller_metrics.get("first_verify_step"),
        },
        "receipt_outcome": {
            "branch_selected": branch_selected,
            "receipt_valid_completion": receipt_valid_completion,
            "verified_completion_v2": verified_completion_v2,
            "accepted_invalid_finish": accepted_invalid_finish,
        },
        "classification": classification,
        "completion_summary": completion_summary,
    }


def summarize_receipt_rescore(
    *,
    result_payload_path: str | Path,
    family_label: str,
    closure_rule_map: Dict[str, Dict[str, Any]] | None = None,
) -> Dict[str, Any]:
    payload = _load_json(result_payload_path)
    rows = [
        score_row_with_receipts(row, closure_rule_map=closure_rule_map)
        for row in list(payload.get("results") or [])
    ]
    total = len(rows)
    return {
        "family": family_label,
        "source_path": str(result_payload_path),
        "legacy_summary": dict(payload.get("summary") or {}),
        "receipt_summary": {
            "total": total,
            "legacy_grounded_completed": sum(1 for row in rows if row["legacy_grounded_completion"]),
            "legacy_verified_completed": sum(1 for row in rows if row["legacy_verified_completion"]),
            "receipt_valid_completed": sum(1 for row in rows if row["receipt_outcome"]["receipt_valid_completion"]),
            "verified_completed_v2": sum(1 for row in rows if row["receipt_outcome"]["verified_completion_v2"]),
            "accepted_invalid_finishes": sum(1 for row in rows if row["receipt_outcome"]["accepted_invalid_finish"]),
            "edit_receipt_rows": sum(1 for row in rows if row["receipt_signals"]["edit_receipt_observed"]),
            "proof_like_rows": sum(1 for row in rows if row["receipt_signals"]["proof_like_evidence_observed"]),
            "explicit_proof_receipt_rows": sum(
                1 for row in rows if row["receipt_signals"]["explicit_proof_receipt_observed"]
            ),
        },
        "rows": rows,
    }


def build_phase15_phase14_receipt_rescore_summary(
    *,
    probe_paths: Dict[str, str | Path],
    floor_paths: Dict[str, str | Path],
    calibration_paths: Dict[str, str | Path],
    closure_rule_map: Dict[str, Dict[str, Any]] | None = None,
) -> Dict[str, Any]:
    rules = closure_rule_map or build_phase15_task_closure_rule_map()

    def _surface(surface_name: str, paths: Dict[str, str | Path]) -> Dict[str, Any]:
        families = {
            family: summarize_receipt_rescore(
                result_payload_path=path,
                family_label=family,
                closure_rule_map=rules,
            )
            for family, path in paths.items()
        }
        return {
            "surface": surface_name,
            "families": families,
        }

    return {
        "schema_version": "phase15_phase14_receipt_rescore_v1",
        "surfaces": [
            _surface("probe", probe_paths),
            _surface("floor", floor_paths),
            _surface("calibration", calibration_paths),
        ],
    }
