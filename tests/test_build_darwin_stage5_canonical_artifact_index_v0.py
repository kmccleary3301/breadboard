from __future__ import annotations

import json
from pathlib import Path

from scripts.build_darwin_stage5_canonical_artifact_index_v0 import build_stage5_canonical_artifact_index


def test_build_stage5_canonical_artifact_index_contains_canonical_entrypoints(tmp_path: Path) -> None:
    summary = build_stage5_canonical_artifact_index()
    assert summary["row_count"] >= 10
    payload = json.loads(Path(summary["out_json"]).read_text(encoding="utf-8"))
    rows = payload["rows"]
    assert any(row["path"] == "artifacts/darwin/stage5/scaled_scorecard/scaled_scorecard_v0.json" and row["status"] == "canonical" for row in rows)
    assert any(row["path"] == "docs/darwin_stage5_claim_boundary_2026-03-25.md" and row["status"] == "canonical" for row in rows)

