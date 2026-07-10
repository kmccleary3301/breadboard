from __future__ import annotations

from pathlib import Path

from scripts.e4_parity.metadata_non_normative import assert_lane_metadata_non_normative


def test_lane_generators_do_not_read_non_normative_metadata(tmp_path: Path) -> None:
    result = assert_lane_metadata_non_normative(tmp_path)

    assert result == {
        "lane_id": "sentinel_lane",
        "inventory_ct_test_id": "CT-SENTINEL-LANE-C4",
        "semantic_key": "sentinel_capture",
        "normalize_ok": True,
    }
