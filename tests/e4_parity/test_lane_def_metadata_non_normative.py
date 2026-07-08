from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from scripts.e4_parity import generate_lane_inventory, lane_acceptance_artifacts, run_lane


class MetadataReadError(AssertionError):
    pass


class GuardedLaneDef(dict[str, Any]):
    """Mapping that fails the test if production code reads non-normative metadata."""

    def __getitem__(self, key: str) -> Any:
        if key == "metadata":
            raise MetadataReadError("lane_def metadata is non-normative")
        return super().__getitem__(key)

    def get(self, key: str, default: Any = None) -> Any:
        if key == "metadata":
            raise MetadataReadError("lane_def metadata is non-normative")
        return super().get(key, default)


@pytest.fixture
def sentinel_lane_def() -> GuardedLaneDef:
    lane_id = "sentinel_lane"
    config_id = "sentinel_config_v1"
    return GuardedLaneDef(
        {
            "schema_version": "bb.e4.lane_def.v2",
            "lane_id": lane_id,
            "config_id": config_id,
            "target_family": "sentinel",
            "target_version": "sentinel 1.0",
            "package_ref": "config/e4_targets/sentinel/1.0",
            "kind": "target_support",
            "status": "claimed",
            "points": 1,
            "capture": {
                "strategy": "replay_dump",
                "argv": None,
                "inputs": [
                    "config/e4_targets/sentinel/1.0",
                    "agent_configs/sentinel.yaml",
                ],
                "workspace_template": None,
            },
            "normalize": {"translator": "identity", "config": {}},
            "replay": {"session": None, "comparator_class": "semantic"},
            "compare": {"comparator": "sentinel_comparator", "config": {}},
            "claim": {"scope": {"behaviors": ["bb.sentinel.v1"], "surfaces": ["sentinel"]}, "exclusions": []},
            "artifacts_root": f"docs/conformance/e4_target_support/{lane_id}",
            "reverify_command": {
                "argv": [
                    "python",
                    "scripts/validate_e4_c4_chain.py",
                    "--config-id",
                    config_id,
                    "--support-claim",
                    f"docs/conformance/support_claims/{config_id}_c4_support_claim.json",
                    "--evidence-manifest",
                    f"docs/conformance/support_claims/{config_id}_c4_evidence_manifest.json",
                    "--json-out",
                    "artifacts/conformance/node_gate/ct_sentinel_lane.json",
                    "--check-only",
                ],
                "cwd": ".",
            },
            "ct": {"test_id": "CT-SENTINEL-LANE-C4"},
            "run": {
                "run_id": "sentinel-run",
                "provider_model": "sentinel/model",
                "sandbox_mode": "read-only sentinel",
            },
            "provenance": {
                "upstream_repo": "https://example.invalid/sentinel",
                "upstream_commit": "abcdef0",
                "upstream_commit_date": "2026-07-07T00:00:00Z",
                "upstream_release_label": "sentinel@1.0",
                "source_paths": ["agent_configs/sentinel.yaml"],
            },
            "acceptance": {
                "behavior_family": "replay_capture",
                "semantic_key": "sentinel_capture",
                "target": "sentinel",
                "assertions": [{"id": "sentinel_present", "description": "sentinel data is present"}],
            },
            "metadata": {"legacy_inventory_ct_test_id": "CT-MUST-NOT-BE-READ"},
        }
    )


def test_lane_generators_do_not_read_non_normative_metadata(
    sentinel_lane_def: GuardedLaneDef, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    row = generate_lane_inventory.lane_inventory_row(sentinel_lane_def)
    assert row["ct"]["test_id"] == "CT-SENTINEL-LANE-C4"
    assert row["run_id"] == "sentinel-run"

    spec = lane_acceptance_artifacts.spec_from_lane(sentinel_lane_def, row)
    assert spec["semantic_key"] == "sentinel_capture"
    assert spec["config_path"] == "agent_configs/sentinel.yaml"

    monkeypatch.setattr(run_lane, "load_lane_defs", lambda _lane_def_dir: {sentinel_lane_def["lane_id"]: sentinel_lane_def})
    monkeypatch.setattr(run_lane, "_inventory_lane", lambda _lane_id, _inventory_path: None)
    result = run_lane.run_lane(
        sentinel_lane_def["lane_id"],
        stage="normalize",
        out_dir=tmp_path,
        lane_def_dir=tmp_path / "lane_defs",
        inventory_path=tmp_path / "inventory.json",
    )
    assert result["ok"] is True
    assert result["stages"][0]["translator"] == "identity"
