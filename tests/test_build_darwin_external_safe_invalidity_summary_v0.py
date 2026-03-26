from __future__ import annotations

import json

from scripts.build_darwin_external_safe_invalidity_summary_v0 import write_external_safe_invalidity_summary


def test_build_external_safe_invalidity_summary_preserves_repo_swe_invalid_comparison() -> None:
    summary = write_external_safe_invalidity_summary()
    payload = json.loads(open(summary["out_path"], "r", encoding="utf-8").read())
    assert any(row["lane_id"] == "lane.repo_swe" and row["caveat_class"] == "invalid_comparison" for row in payload["caveats"])
