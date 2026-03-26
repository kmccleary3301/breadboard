from __future__ import annotations

import json
from pathlib import Path

from scripts.build_darwin_stage5_comparative_bundle_v0 import build_stage5_comparative_bundle


def test_build_stage5_comparative_bundle_links_claim_and_registry_surfaces() -> None:
    summary = build_stage5_comparative_bundle()
    payload = json.loads(Path(summary["out_json"]).read_text(encoding="utf-8"))
    assert payload["claim_boundary_ref"] == "docs/darwin_stage5_claim_boundary_2026-03-25.md"
    assert payload["family_registry_ref"] == "artifacts/darwin/stage5/family_registry/family_registry_v0.json"
    assert payload["scaled_scorecard_ref"] == "artifacts/darwin/stage5/scaled_scorecard/scaled_scorecard_v0.json"
    assert payload["comparative_memo_ref"] == "docs/darwin_stage5_comparative_memo_2026-03-25.md"
