from __future__ import annotations

import json

from scripts.emit_darwin_phase1_final_evidence_v1 import emit_final_evidence
from scripts.build_darwin_external_safe_claim_subset_v0 import write_external_safe_claim_subset


def test_build_external_safe_claim_subset_filters_internal_only_claims() -> None:
    emit_final_evidence()
    summary = write_external_safe_claim_subset()
    payload = json.loads(open(summary["out_path"], "r", encoding="utf-8").read())
    included = {row["source_claim_id"] for row in payload["included_claims"]}
    excluded = {row["source_claim_id"]: row["reason"] for row in payload["excluded_claims"]}
    assert "claim.darwin.phase1.transfer_protocol_operational.v1" in included
    assert excluded["claim.darwin.phase1.final_program_operational.v1"] == "aggregate_closure_claim_is_internal_only"
