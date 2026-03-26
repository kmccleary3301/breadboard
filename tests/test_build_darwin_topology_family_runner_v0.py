from __future__ import annotations

import json
from pathlib import Path

from scripts.bootstrap_darwin_campaign_specs_v0 import write_bootstrap_specs
from scripts.build_darwin_topology_family_runner_v0 import build_topology_runner_manifest, write_topology_runner_manifest


def test_build_topology_runner_manifest_has_full_matrix() -> None:
    write_bootstrap_specs()
    payload = build_topology_runner_manifest()
    assert payload["family_count"] == 3
    assert payload["campaign_count"] == 6
    assert payload["matrix_count"] == 18
    assert all(row["policy_bundle_present"] for row in payload["matrix"])
    assert any(row["lane_id"] == "lane.repo_swe" and row["supported"] for row in payload["matrix"])
    assert any(row["lane_id"] == "lane.scheduling" and row["supported"] for row in payload["matrix"])
    assert any(row["lane_id"] == "lane.research" and row["topology_id"] == "policy.topology.pev_v0" and row["supported"] for row in payload["matrix"])


def test_write_topology_runner_manifest_emits_json(tmp_path: Path) -> None:
    write_bootstrap_specs()
    summary = write_topology_runner_manifest(tmp_path)
    payload = json.loads((tmp_path / "topology_family_runner_v0.json").read_text(encoding="utf-8"))
    assert summary["matrix_count"] == 18
    assert payload["schema"] == "breadboard.darwin.topology_family_runner.v0"
