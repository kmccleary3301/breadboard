from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import sys


MODULE_PATH = Path(__file__).resolve().parents[1] / "scripts" / "convert_hilbert_results_to_cross_system_v2.py"
sys.path.insert(0, str(MODULE_PATH.parent))
spec = importlib.util.spec_from_file_location("convert_hilbert_results_to_cross_system_v2", MODULE_PATH)
assert spec and spec.loader
converter = importlib.util.module_from_spec(spec)
spec.loader.exec_module(converter)


def test_convert_filters_results_to_manifest_task_ids(tmp_path: Path) -> None:
    manifest = {
        "run_id": "r1",
        "budget": {"class": "B"},
        "toolchain": {"lean_version": "4.16.0", "mathlib_commit": "unknown"},
        "benchmark": {"slice": {"task_ids": ["keep_a", "keep_b"]}},
    }
    task_inputs = {"tasks": [{"task_id": "keep_a", "input_hash": "ha"}, {"task_id": "keep_b", "input_hash": "hb"}]}
    hilbert_results = {
        "problem_ids": ["keep_a", "drop_me", "keep_b"],
        "successes": {"keep_a": True, "drop_me": True, "keep_b": False},
        "proofs": {"keep_a": "proof a", "drop_me": "proof drop", "keep_b": ""},
        "formal_statements": {"keep_a": "theorem keep_a : True := by", "drop_me": "theorem drop_me : True := by", "keep_b": "theorem keep_b : True := by"},
    }

    manifest_path = tmp_path / "manifest.json"
    task_inputs_path = tmp_path / "task_inputs.json"
    hilbert_path = tmp_path / "hilbert.json"
    out_path = tmp_path / "out.jsonl"
    summary_out = tmp_path / "summary.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    task_inputs_path.write_text(json.dumps(task_inputs), encoding="utf-8")
    hilbert_path.write_text(json.dumps(hilbert_results), encoding="utf-8")

    summary = converter.convert(manifest_path, hilbert_path, task_inputs_path, out_path, summary_out)

    rows = [json.loads(line) for line in out_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    assert [row["task_id"] for row in rows] == ["keep_a", "keep_b"]
    assert summary["task_count"] == 2
    assert summary["status_counts"] == {"SOLVED": 1, "UNSOLVED": 1}
