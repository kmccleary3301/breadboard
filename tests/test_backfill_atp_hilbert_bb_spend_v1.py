from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

from atp_hilbert_fixture_utils import write_json

MODULE_PATH = Path(__file__).resolve().parents[1] / "scripts" / "backfill_atp_hilbert_bb_spend_v1.py"
sys.path.insert(0, str(MODULE_PATH.parent))
spec = importlib.util.spec_from_file_location("backfill_atp_hilbert_bb_spend_v1", MODULE_PATH)
assert spec and spec.loader
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def test_extract_status_pack_ids_finds_pack_references() -> None:
    text = "Parent tranche pack_d_mixed_induction_numbertheory_minif2f_v1 and child pack_d_induction_core_minif2f_v1"
    assert module._extract_status_pack_ids(text) == [
        "pack_d_induction_core_minif2f_v1",
        "pack_d_mixed_induction_numbertheory_minif2f_v1",
    ]


def test_bb_spend_backfill_payload_contains_entries(tmp_path: Path) -> None:
    module.REPO_ROOT = tmp_path
    index_path = tmp_path / "canonical_baseline_index_v1.json"
    run_dir = tmp_path / "logging" / "run_a"
    write_json(
        run_dir / "meta" / "run_summary.json",
        {
            "turn_diagnostics": [
                {
                    "route_id": "openai/gpt-5.4",
                    "provider_model": "openai/gpt-5.4",
                    "usage": {"prompt_tokens": 100, "completion_tokens": 50},
                }
            ]
        },
    )
    raw_dir = tmp_path / "artifacts" / "benchmarks" / "hilbert_comparison_packs_v2" / "pack_b_core_noimo_minif2f_v1" / "bb_hilbert_like_raw_v1"
    write_json(raw_dir / "task_a.json", {"task_id": "task_a", "run_dir": "logging/run_a"})
    status_doc = tmp_path / "docs" / "pack_b_status.md"
    status_doc.parent.mkdir(parents=True, exist_ok=True)
    status_doc.write_text("# status\n", encoding="utf-8")
    write_json(
        index_path,
        {
            "entries": [
                {
                    "pack_id": "pack_b_core_noimo_minif2f_v1",
                    "task_count": 1,
                    "status_doc": "docs/pack_b_status.md",
                    "report": "artifacts/benchmarks/hilbert_comparison_packs_v2/pack_b_core_noimo_minif2f_v1/cross_system_pilot_report_v1.json",
                }
            ]
        },
    )
    payload = module.build_payload(index_path)
    assert payload["schema"] == "breadboard.atp_hilbert_bb_spend_backfill.v1"
    assert payload["entry_count"] == 1
    pack_ids = {entry["pack_id"] for entry in payload["entries"]}
    assert "pack_b_core_noimo_minif2f_v1" in pack_ids
    assert any(float(entry["estimated_total_cost_usd"]) > 0 for entry in payload["entries"])
