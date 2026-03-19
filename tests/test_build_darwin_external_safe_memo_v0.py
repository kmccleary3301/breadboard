from __future__ import annotations

import json

from scripts.build_darwin_external_safe_claim_subset_v0 import write_external_safe_claim_subset
from scripts.build_darwin_external_safe_invalidity_summary_v0 import write_external_safe_invalidity_summary
from scripts.build_darwin_external_safe_reviewer_summary_v0 import write_external_safe_reviewer_summary
from scripts.build_darwin_external_safe_memo_v0 import write_external_safe_memo
from scripts.emit_darwin_phase1_final_evidence_v1 import emit_final_evidence


def test_build_external_safe_memo_preserves_non_superiority_guardrail() -> None:
    emit_final_evidence()
    write_external_safe_claim_subset()
    write_external_safe_invalidity_summary()
    write_external_safe_reviewer_summary()
    summary = write_external_safe_memo()
    payload = json.loads(open(summary["json_path"], "r", encoding="utf-8").read())
    assert "no_superiority_claim" in payload["caution_labels"]
    assert any("Do not infer model superiority" in row for row in payload["do_not_infer"])
    assert payload["replay_read"]["scheduling"].startswith("promoted hybrid scheduling candidate replay is stable")
