from __future__ import annotations

import json

from scripts.build_darwin_external_safe_invalidity_summary_v0 import write_external_safe_invalidity_summary
from scripts.build_darwin_external_safe_reviewer_summary_v0 import write_external_safe_reviewer_summary


def test_build_external_safe_reviewer_summary_covers_primary_and_audit_lanes() -> None:
    write_external_safe_invalidity_summary()
    summary = write_external_safe_reviewer_summary()
    payload = json.loads(open(summary["json_path"], "r", encoding="utf-8").read())
    rows = {row["lane_id"]: row for row in payload["rows"]}
    assert rows["lane.repo_swe"]["role"] == "primary_proving"
    assert rows["lane.scheduling"]["role"] == "primary_proving"
    assert rows["lane.atp"]["role"] == "audit_only"
