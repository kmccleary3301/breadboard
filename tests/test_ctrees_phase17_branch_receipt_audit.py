from __future__ import annotations

import json

from agentic_coder_prototype.ctrees.phase17_branch_receipt_audit import summarize_phase17_branch_receipt_audit


def test_phase17_branch_receipt_audit_flags_shell_branch_proxy_and_missing_receipts(tmp_path) -> None:
    payload = {
        "families": {
            "execution_first": {
                "source_path": "dummy.json",
                "receipt_summary": {"total": 2},
                "rows": [
                    {
                        "task_id": "exec__phase15_probe_edit_receipt_required_v1",
                        "closure_rule": {"closure_mode": "edit_required"},
                        "receipt_signals": {
                            "edit_receipt_observed": False,
                            "verification_receipt_observed": False,
                            "proof_like_evidence_observed": True,
                            "explicit_proof_receipt_observed": False,
                            "first_write_step": None,
                            "first_verify_step": 1,
                        },
                        "receipt_outcome": {
                            "branch_selected": False,
                            "receipt_valid_completion": False,
                            "verified_completion_v2": False,
                            "accepted_invalid_finish": False,
                        },
                    },
                    {
                        "task_id": "exec__phase15_probe_explicit_proof_receipt_v1",
                        "closure_rule": {"closure_mode": "proof_required"},
                        "receipt_signals": {
                            "edit_receipt_observed": False,
                            "verification_receipt_observed": False,
                            "proof_like_evidence_observed": True,
                            "explicit_proof_receipt_observed": False,
                            "first_write_step": None,
                            "first_verify_step": 1,
                        },
                        "receipt_outcome": {
                            "branch_selected": True,
                            "receipt_valid_completion": False,
                            "verified_completion_v2": False,
                            "accepted_invalid_finish": False,
                        },
                    },
                ],
            }
        }
    }
    path = tmp_path / "rescore.json"
    path.write_text(json.dumps(payload), encoding="utf-8")

    summary = summarize_phase17_branch_receipt_audit(rescore_payload_path=path)
    family = summary["families"]["execution_first"]
    diagnosis = family["diagnosis"]

    assert diagnosis["edit_required_shell_collapse_rows"] == 1
    assert diagnosis["proof_required_without_explicit_receipt_rows"] == 1
    assert diagnosis["shell_selected_as_branch_proxy_rows"] == 1


def test_phase17_branch_receipt_audit_flags_verify_reentry_without_verification_receipt(tmp_path) -> None:
    payload = {
        "families": {
            "deterministic": {
                "source_path": "dummy.json",
                "receipt_summary": {"total": 1},
                "rows": [
                    {
                        "task_id": "deterministic__phase15_probe_verify_reentry_v1",
                        "closure_rule": {"closure_mode": "edit_required"},
                        "receipt_signals": {
                            "edit_receipt_observed": False,
                            "verification_receipt_observed": False,
                            "proof_like_evidence_observed": True,
                            "explicit_proof_receipt_observed": False,
                            "first_write_step": None,
                            "first_verify_step": 1,
                        },
                        "receipt_outcome": {
                            "branch_selected": False,
                            "receipt_valid_completion": False,
                            "verified_completion_v2": False,
                            "accepted_invalid_finish": False,
                        },
                    }
                ],
            }
        }
    }
    path = tmp_path / "rescore.json"
    path.write_text(json.dumps(payload), encoding="utf-8")

    summary = summarize_phase17_branch_receipt_audit(rescore_payload_path=path)
    diagnosis = summary["families"]["deterministic"]["diagnosis"]

    assert diagnosis["verification_reentry_without_verification_receipt_rows"] == 1
