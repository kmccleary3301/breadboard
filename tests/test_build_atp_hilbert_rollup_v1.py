from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

MODULE_PATH = Path(__file__).resolve().parents[1] / "scripts" / "build_atp_hilbert_rollup_v1.py"
sys.path.insert(0, str(MODULE_PATH.parent))
spec = importlib.util.spec_from_file_location("build_atp_hilbert_rollup_v1", MODULE_PATH)
assert spec and spec.loader
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def test_rollup_builds_all_expected_files(tmp_path: Path) -> None:
    payload = module.build_rollup(tmp_path)
    assert payload["schema"] == "breadboard.atp_hilbert_rollup_manifest.v1"
    baseline_json = tmp_path / "canonical_baseline_index_v1.json"
    arm_audit_json = tmp_path / "arm_audit_v1.json"
    bb_spend_json = tmp_path / "bb_spend_backfill_v1.json"
    scoreboard_json = tmp_path / "scoreboard_v1.json"
    rollup_json = tmp_path / "rollup_manifest_v1.json"
    assert baseline_json.exists()
    assert arm_audit_json.exists()
    assert bb_spend_json.exists()
    assert scoreboard_json.exists()
    assert rollup_json.exists()
    scoreboard = json.loads(scoreboard_json.read_text())
    assert scoreboard["headline_totals"]["pack_count"] >= 8
