from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path


def _load_module():
    repo_root = Path(__file__).resolve().parents[1]
    module_path = repo_root / "scripts" / "analyze_phase5_frame_gap_hotspots.py"
    scripts_dir = str((repo_root / "scripts").resolve())
    if scripts_dir not in sys.path:
        sys.path.insert(0, scripts_dir)
    spec = importlib.util.spec_from_file_location("analyze_phase5_frame_gap_hotspots", module_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_analyze_hotspots(tmp_path: Path):
    module = _load_module()
    run_dir = tmp_path / "run"
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "scenario_manifest.json").write_text(
        json.dumps(
            {
                "scenario": "phase4_replay/subagents_concurrency_20_v1",
                "run_id": "r1",
                "executed_actions": [
                    {"action": {"type": "send"}, "started_at": 9.0, "ended_at": 10.1},
                    {"action": {"type": "wait_for_quiet"}, "started_at": 10.1, "ended_at": 11.6},
                ],
            }
        )
        + "\n",
        encoding="utf-8",
    )
    rows = [
        {"frame": 1, "timestamp": 10.0},
        {"frame": 2, "timestamp": 10.2},
        {"frame": 3, "timestamp": 11.4},
        {"frame": 4, "timestamp": 11.7},
    ]
    (run_dir / "index.jsonl").write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")

    payload = module.analyze_hotspots(
        run_dir=run_dir,
        top_k=2,
        expected_waits_by_scenario={
            "phase4_replay/subagents_concurrency_20_v1": {
                "expected_wait_actions": ["wait_for_quiet", "wait_until", "sleep"],
                "note": "Replay harness intentionally waits for quiet.",
            }
        },
    )
    assert payload["schema_version"] == "phase5_frame_gap_hotspots_v1"
    assert payload["scenario"] == "phase4_replay/subagents_concurrency_20_v1"
    assert payload["gap_summary"]["count"] == 3
    assert abs(payload["gap_summary"]["max"] - 1.2) < 1e-9
    assert len(payload["top_gaps"]) == 2
    assert payload["top_gaps"][0]["from_frame"] == 2
    assert payload["top_gaps"][0]["to_frame"] == 3
    assert abs(payload["top_gaps"][0]["gap_seconds"] - 1.2) < 1e-9
    assert "wait_for_quiet" in payload["top_gaps"][0]["overlap_actions"]
    assert payload["top_gaps"][0]["wait_overlap"] is True
    assert payload["top_gaps"][0]["wait_dominated"] is True
    assert payload["top_gaps"][0]["non_wait_actions"] == []
    assert payload["top_gaps"][0]["unexpected_wait_actions"] == []
    assert payload["top_gaps"][0]["wait_alignment"] == "expected"
    assert payload["expected_wait_actions"] == ["sleep", "wait_for_quiet", "wait_until"]
    assert payload["expected_wait_note"] == "Replay harness intentionally waits for quiet."
    assert payload["gap_summary"]["top_wait_dominated_count"] >= 1
    assert payload["gap_summary"]["top_expected_wait_count"] >= 1
