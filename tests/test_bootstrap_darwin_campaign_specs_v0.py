from __future__ import annotations

import json
from pathlib import Path

from breadboard_ext.darwin.contracts import validate_campaign_spec
from scripts.bootstrap_darwin_campaign_specs_v0 import build_bootstrap_specs, write_bootstrap_specs


def test_build_bootstrap_specs_are_contract_valid() -> None:
    specs = build_bootstrap_specs()
    assert len(specs) == 3
    for spec in specs:
        assert not validate_campaign_spec(spec)


def test_write_bootstrap_specs_emits_manifest_and_specs(tmp_path: Path) -> None:
    summary = write_bootstrap_specs(tmp_path)
    assert summary["spec_count"] == 3

    manifest = json.loads((tmp_path / "bootstrap_manifest_v0.json").read_text(encoding="utf-8"))
    assert manifest["schema"] == "breadboard.darwin.bootstrap_manifest.v0"
    assert len(manifest["specs"]) == 3

    for row in manifest["specs"]:
        spec_path = Path(row["path"])
        spec = json.loads((tmp_path / spec_path.name).read_text(encoding="utf-8"))
        assert spec["lane_id"] in {"lane.atp", "lane.harness", "lane.systems"}
