from __future__ import annotations

import json
from pathlib import Path

from scripts import build_darwin_stage4_canonical_artifact_index_v0 as index_mod
from scripts import build_darwin_stage4_comparative_bundle_v0 as bundle_mod


def test_stage4_precloseout_builders_emit(tmp_path, monkeypatch) -> None:
    out_dir = tmp_path / "precloseout"
    monkeypatch.setattr(index_mod, "OUT_DIR", out_dir)
    monkeypatch.setattr(index_mod, "OUT_JSON", out_dir / "canonical_artifact_index_v0.json")
    monkeypatch.setattr(index_mod, "OUT_MD", out_dir / "canonical_artifact_index_v0.md")
    monkeypatch.setattr(bundle_mod, "OUT_DIR", out_dir)
    monkeypatch.setattr(bundle_mod, "OUT_JSON", out_dir / "comparative_bundle_v0.json")

    index_summary = index_mod.build_stage4_canonical_artifact_index()
    bundle_summary = bundle_mod.build_stage4_comparative_bundle()

    index_payload = json.loads(Path(index_summary["out_json"]).read_text())
    bundle_payload = json.loads(Path(bundle_summary["out_json"]).read_text())
    assert index_payload["row_count"] >= 10
    assert bundle_payload["deep_live_verification_ref"].endswith("verification_bundle_v0.json")
    assert bundle_payload["family_verification_ref"].endswith("family_verification_bundle_v0.json")
