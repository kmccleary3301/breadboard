from __future__ import annotations

import json
from pathlib import Path

from breadboard.rl.export import (
    build_verl_probe_projection_manifest,
    build_verl_probe_rows_from_m6_summary,
    validate_verl_probe_projection_manifest,
    write_verl_probe_projection_manifest,
)


WORKSPACE_ROOT = Path(__file__).resolve().parents[4]
M6_SUMMARY = WORKSPACE_ROOT / "docs_tmp" / "ZYPHRA" / "RL_PHASE_1" / "runs" / "m6_controlled_swe_toy" / "run_summary.json"


def _rows():
    summary = json.loads(M6_SUMMARY.read_text(encoding="utf-8"))
    return build_verl_probe_rows_from_m6_summary(summary)


def test_verl_probe_projection_manifest_records_projection_boundary() -> None:
    rows = _rows()
    manifest = build_verl_probe_projection_manifest(rows, target_formats=["jsonl", "parquet"])

    assert validate_verl_probe_projection_manifest(manifest, rows) == []
    assert manifest["canonical_truth"] == "breadboard_graph_replay_runtime"
    assert manifest["claim_boundary"] == "verl_shaped_probe_not_trainer_ready"
    assert manifest["row_count"] == 10
    assert manifest["trainable_candidate_count"] == 7
    assert "trainer_dataproto_object" in manifest["lost_fields"]
    assert set(manifest["source_projection_manifest_ids"]) == {row.projection_manifest_id for row in rows}


def test_verl_probe_projection_manifest_file_round_trips(tmp_path) -> None:
    rows = _rows()
    output = tmp_path / "projection_manifest.json"
    manifest = write_verl_probe_projection_manifest(rows, output, target_formats=["jsonl", "parquet"])
    loaded = json.loads(output.read_text(encoding="utf-8"))

    assert loaded == manifest
    assert validate_verl_probe_projection_manifest(loaded, rows) == []
