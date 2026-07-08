from __future__ import annotations

import json
from pathlib import Path

from breadboard.rl.export import (
    build_verl_probe_rows_from_m6_summary,
    smoke_consume_verl_probe_parquet,
    write_verl_probe_parquet,
)


WORKSPACE_ROOT = Path(__file__).resolve().parents[4]
M6_SUMMARY = WORKSPACE_ROOT / "docs_tmp" / "ZYPHRA" / "RL_PHASE_1" / "runs" / "m6_controlled_swe_toy" / "run_summary.json"


def test_verl_probe_parquet_smoke_consumer_passes(tmp_path) -> None:
    summary = json.loads(M6_SUMMARY.read_text(encoding="utf-8"))
    rows = build_verl_probe_rows_from_m6_summary(summary)
    output = tmp_path / "verl_probe.parquet"
    write_verl_probe_parquet(rows, output)

    report = smoke_consume_verl_probe_parquet(output)

    assert output.exists()
    assert output.stat().st_size > 0
    assert report["tensorizable"] is True
    assert report["row_count"] == 10
    assert report["trainable_candidate_count"] == 7
    assert report["errors"] == []
    assert "not DataProto" in report["compatibility_target"]
