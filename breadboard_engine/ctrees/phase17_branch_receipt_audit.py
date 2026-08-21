from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List


def _load_json(path: str | Path) -> Dict[str, Any]:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _classify_row(row: Dict[str, Any]) -> Dict[str, Any]:
    closure_rule = dict(row.get("closure_rule") or {})
    receipt_signals = dict(row.get("receipt_signals") or {})
    receipt_outcome = dict(row.get("receipt_outcome") or {})

    closure_mode = str(closure_rule.get("closure_mode") or "unknown")
    edit_receipt = bool(receipt_signals.get("edit_receipt_observed"))
    verification_receipt = bool(receipt_signals.get("verification_receipt_observed"))
    proof_like = bool(receipt_signals.get("proof_like_evidence_observed"))
    explicit_proof = bool(receipt_signals.get("explicit_proof_receipt_observed"))
    first_write_step = receipt_signals.get("first_write_step")
    first_verify_step = receipt_signals.get("first_verify_step")
    branch_selected = bool(receipt_outcome.get("branch_selected"))
    receipt_valid = bool(receipt_outcome.get("receipt_valid_completion"))
    verified_v2 = bool(receipt_outcome.get("verified_completion_v2"))

    edit_required_shell_collapse = bool(
        closure_mode == "edit_required"
        and proof_like
        and not edit_receipt
        and first_write_step is None
    )
    proof_required_without_explicit_receipt = bool(
        closure_mode == "proof_required"
        and proof_like
        and not explicit_proof
    )
    verification_reentry_without_verification_receipt = bool(
        str(row.get("task_id") or "").endswith("verify_reentry_v1")
        and first_verify_step is not None
        and not verification_receipt
    )
    shell_selected_as_branch_proxy = bool(
        branch_selected
        and proof_like
        and not explicit_proof
        and not edit_receipt
    )

    return {
        "task_id": str(row.get("task_id") or ""),
        "closure_mode": closure_mode,
        "edit_required_shell_collapse": edit_required_shell_collapse,
        "proof_required_without_explicit_receipt": proof_required_without_explicit_receipt,
        "verification_reentry_without_verification_receipt": verification_reentry_without_verification_receipt,
        "shell_selected_as_branch_proxy": shell_selected_as_branch_proxy,
        "receipt_valid_completion": receipt_valid,
        "verified_completion_v2": verified_v2,
        "receipt_signals": {
            "edit_receipt_observed": edit_receipt,
            "verification_receipt_observed": verification_receipt,
            "proof_like_evidence_observed": proof_like,
            "explicit_proof_receipt_observed": explicit_proof,
            "first_write_step": first_write_step,
            "first_verify_step": first_verify_step,
        },
        "receipt_outcome": {
            "branch_selected": branch_selected,
            "receipt_valid_completion": receipt_valid,
            "verified_completion_v2": verified_v2,
            "accepted_invalid_finish": bool(receipt_outcome.get("accepted_invalid_finish")),
        },
    }


def summarize_phase17_branch_receipt_audit(
    *,
    rescore_payload_path: str | Path,
) -> Dict[str, Any]:
    payload = _load_json(rescore_payload_path)
    families = dict(payload.get("families") or {})

    family_summaries: Dict[str, Any] = {}
    for family, family_payload in families.items():
        rows = [_classify_row(dict(row)) for row in list(family_payload.get("rows") or [])]
        family_summaries[family] = {
            "family": family,
            "source_path": str(family_payload.get("source_path") or ""),
            "receipt_summary": dict(family_payload.get("receipt_summary") or {}),
            "rows": rows,
            "diagnosis": {
                "edit_required_shell_collapse_rows": sum(1 for row in rows if row["edit_required_shell_collapse"]),
                "proof_required_without_explicit_receipt_rows": sum(
                    1 for row in rows if row["proof_required_without_explicit_receipt"]
                ),
                "verification_reentry_without_verification_receipt_rows": sum(
                    1 for row in rows if row["verification_reentry_without_verification_receipt"]
                ),
                "shell_selected_as_branch_proxy_rows": sum(
                    1 for row in rows if row["shell_selected_as_branch_proxy"]
                ),
            },
        }

    return {
        "schema_version": "phase17_branch_receipt_audit_v1",
        "source_path": str(rescore_payload_path),
        "families": family_summaries,
    }
