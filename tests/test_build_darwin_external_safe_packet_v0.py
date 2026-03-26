from __future__ import annotations

import json

from scripts.build_darwin_external_safe_claim_subset_v0 import write_external_safe_claim_subset
from scripts.build_darwin_external_safe_invalidity_summary_v0 import write_external_safe_invalidity_summary
from scripts.build_darwin_external_safe_reviewer_summary_v0 import write_external_safe_reviewer_summary
from scripts.build_darwin_external_safe_memo_v0 import write_external_safe_memo
from scripts.build_darwin_external_safe_packet_v0 import write_external_safe_packet
from scripts.emit_darwin_phase1_final_evidence_v1 import emit_final_evidence


def test_build_external_safe_packet_uses_filtered_claims_and_invalidity_summary() -> None:
    emit_final_evidence()
    write_external_safe_claim_subset()
    write_external_safe_invalidity_summary()
    write_external_safe_reviewer_summary()
    write_external_safe_memo()
    summary = write_external_safe_packet()
    payload = json.loads(open(summary["out_path"], "r", encoding="utf-8").read())
    assert "claim.darwin.phase1.final_program_operational.v1" not in payload["included_claim_ids"]
    assert payload["invalidity_summary_ref"] == "artifacts/darwin/reviews/external_safe_invalidity_summary_v0.json"
    assert payload["cost_caution_ref"] == "docs/darwin_phase2_external_safe_cost_caution_2026-03-19.md"
