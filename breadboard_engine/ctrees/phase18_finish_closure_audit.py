from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict


def _load_json(path: str | Path) -> Dict[str, Any]:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _classify_row(row: Dict[str, Any]) -> str:
    signals = dict(row.get("receipt_signals") or {})
    outcome = dict(row.get("receipt_outcome") or {})
    completion_summary = dict(row.get("completion_summary") or {})
    reason = str(completion_summary.get("reason") or "")

    if bool(outcome.get("branch_selected")) and bool(signals.get("edit_receipt_observed")) and bool(signals.get("verification_receipt_observed")):
        return "edit_ready_no_close"
    if bool(outcome.get("branch_selected")) and bool(signals.get("explicit_proof_receipt_observed")):
        return "proof_ready_no_close"
    if bool(outcome.get("branch_selected")) and bool(signals.get("verification_receipt_observed")):
        return "verification_reentry_no_close"
    if reason == "max_steps_exhausted":
        return "max_step_exhausted_before_close"
    return "other_closure_gap"


def build_phase18_finish_closure_audit(summary_path: str | Path) -> Dict[str, Any]:
    payload = _load_json(summary_path)
    families = dict(payload.get("families") or {})
    family_summaries: Dict[str, Any] = {}
    all_row_classes: Dict[str, int] = {}

    for family_name, family_payload in families.items():
        rows = list(family_payload.get("rows") or [])
        class_counts: Dict[str, int] = {}
        for row in rows:
            label = _classify_row(dict(row))
            class_counts[label] = class_counts.get(label, 0) + 1
            all_row_classes[label] = all_row_classes.get(label, 0) + 1
        receipt_summary = dict(family_payload.get("receipt_summary") or {})
        family_summaries[family_name] = {
            "row_count": len(rows),
            "class_counts": class_counts,
            "explicit_branch_receipt_rows": sum(
                1 for row in rows if bool(((row.get("receipt_signals") or {}).get("explicit_branch_receipt_observed")))
            ),
            "explicit_proof_receipt_rows": sum(
                1 for row in rows if bool(((row.get("receipt_signals") or {}).get("explicit_proof_receipt_observed")))
            ),
            "verification_receipt_rows": sum(
                1 for row in rows if bool(((row.get("receipt_signals") or {}).get("verification_receipt_observed")))
            ),
            "receipt_valid_completed": int(receipt_summary.get("receipt_valid_completed") or 0),
            "verified_completed_v2": int(receipt_summary.get("verified_completed_v2") or 0),
        }

    return {
        "schema_version": "phase18_finish_closure_audit_v1",
        "source_path": str(summary_path),
        "families": family_summaries,
        "global_class_counts": all_row_classes,
        "tied_at_real_surface": all(
            family.get("receipt_valid_completed") == 0 and family.get("verified_completed_v2") == 0
            for family in family_summaries.values()
        ),
    }
