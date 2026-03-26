from __future__ import annotations

import json
from pathlib import Path

from scripts.build_darwin_stage5_composition_canary_v0 import build_stage5_composition_canary


def test_build_stage5_composition_canary_records_no_go(tmp_path: Path) -> None:
    registry_path = tmp_path / "family_registry_v0.json"
    transfer_path = tmp_path / "bounded_transfer_outcomes_v0.json"
    registry_path.write_text(
        json.dumps(
            {
                "rows": [
                    {"family_id": "family.systems.policy", "lane_id": "lane.systems", "family_kind": "policy", "stage5_family_state": "active_proving"},
                ]
            }
        ),
        encoding="utf-8",
    )
    transfer_path.write_text(
        json.dumps(
            {
                "rows": [
                    {"transfer_status": "retained", "family_id": "family.repo.topology"},
                ]
            }
        ),
        encoding="utf-8",
    )
    summary = build_stage5_composition_canary(stage5_registry_path=registry_path, transfer_path=transfer_path, out_dir=tmp_path / "out")
    payload = json.loads(Path(summary["out_json"]).read_text(encoding="utf-8"))
    assert payload["result"] == "composition_not_authorized"
