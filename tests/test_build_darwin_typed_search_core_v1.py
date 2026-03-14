from __future__ import annotations

import json

from scripts.build_darwin_typed_search_core_v1 import write_typed_search_core


def test_write_typed_search_core_emits_registry_and_selection(tmp_path) -> None:
    summary = write_typed_search_core(tmp_path)
    registry = json.loads((tmp_path / "mutation_operator_registry_v1.json").read_text(encoding="utf-8"))
    selection = json.loads((tmp_path / "search_enabled_lane_selection_v1.json").read_text(encoding="utf-8"))
    assert summary["operator_count"] >= 5
    assert registry["schema"] == "breadboard.darwin.mutation_operator_registry.v1"
    assert {row["lane_id"] for row in selection["lanes"]} == {"lane.harness", "lane.repo_swe"}
