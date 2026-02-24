from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path


def _load_module():
    repo_root = Path(__file__).resolve().parents[1]
    module_path = repo_root / "scripts" / "build_phase5_latency_trend_report.py"
    scripts_dir = str((repo_root / "scripts").resolve())
    if scripts_dir not in sys.path:
        sys.path.insert(0, scripts_dir)
    spec = importlib.util.spec_from_file_location("build_phase5_latency_trend_report", module_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _write_contract(path: Path) -> None:
    payload = {
        "scenario_sets": {
            "hard_gate": ["phase4_replay/foo_v1"],
            "nightly": ["phase4_replay/bar_v1"],
        }
    }
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def _write_run(
    run_root: Path,
    *,
    scenario: str,
    run_id: str,
    first_frame: float,
    replay_first: float,
    duration: float,
    max_gap: float,
    stall: float,
) -> None:
    slug = scenario.replace("phase4_replay/", "", 1)
    run_dir = run_root / slug / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    started_at = 1000.0
    replay_send_end = 1001.0
    rows = [
        {"frame": 1, "timestamp": started_at + first_frame, "text": "frames/frame_0001.txt", "ansi": "frames/frame_0001.ansi"},
        {"frame": 2, "timestamp": replay_send_end + replay_first, "text": "frames/frame_0002.txt", "ansi": "frames/frame_0002.ansi"},
        {"frame": 3, "timestamp": replay_send_end + replay_first + max_gap, "text": "frames/frame_0003.txt", "ansi": "frames/frame_0003.ansi"},
    ]
    (run_dir / "index.jsonl").write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")
    manifest = {
        "scenario": scenario,
        "run_id": run_id,
        "started_at": started_at,
        "duration_seconds": duration,
        "executed_actions": [{"action": {"type": "send", "text": "replay:fixture.jsonl"}, "ended_at": replay_send_end}],
        "frame_validation": {"max_unchanged_streak_seconds_estimate": stall},
    }
    (run_dir / "scenario_manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")


def test_build_phase5_latency_trend_report_pass(tmp_path: Path):
    module = _load_module()
    run_root = tmp_path / "runs"
    contract = tmp_path / "contract.json"
    _write_contract(contract)
    _write_run(
        run_root,
        scenario="phase4_replay/foo_v1",
        run_id="20260224-000001",
        first_frame=1.2,
        replay_first=0.4,
        duration=9.0,
        max_gap=1.1,
        stall=1.0,
    )
    result = module.build_latency_trend(
        run_root=run_root,
        scenario_set="hard_gate",
        contract_file=contract,
        max_first_frame_seconds=3.0,
        max_replay_first_frame_seconds=2.0,
        max_duration_seconds=25.0,
        max_stall_seconds=3.0,
        max_frame_gap_seconds=3.2,
        near_threshold_ratio=0.85,
        baseline_payload=None,
    )
    assert result.payload["overall_ok"] is True
    assert result.payload["summary"]["record_count"] == 1
    assert result.payload["summary"]["ok_count"] == 1
    assert result.payload["delta_summary"] == {}
    assert "max_non_wait_frame_gap_seconds" in result.payload["summary"]
    assert result.payload["near_threshold"]["count"] == 0


def test_build_phase5_latency_trend_report_detects_missing(tmp_path: Path):
    module = _load_module()
    run_root = tmp_path / "runs"
    contract = tmp_path / "contract.json"
    _write_contract(contract)
    # Only nightly run exists while checking hard_gate.
    _write_run(
        run_root,
        scenario="phase4_replay/bar_v1",
        run_id="20260224-000002",
        first_frame=1.1,
        replay_first=0.5,
        duration=9.0,
        max_gap=1.2,
        stall=1.0,
    )
    result = module.build_latency_trend(
        run_root=run_root,
        scenario_set="hard_gate",
        contract_file=contract,
        max_first_frame_seconds=3.0,
        max_replay_first_frame_seconds=2.0,
        max_duration_seconds=25.0,
        max_stall_seconds=3.0,
        max_frame_gap_seconds=3.2,
        near_threshold_ratio=0.85,
        baseline_payload=None,
    )
    assert result.payload["overall_ok"] is False
    assert result.payload["missing_scenarios"] == ["phase4_replay/foo_v1"]


def test_build_phase5_latency_trend_report_baseline_deltas(tmp_path: Path):
    module = _load_module()
    run_root = tmp_path / "runs"
    contract = tmp_path / "contract.json"
    _write_contract(contract)
    _write_run(
        run_root,
        scenario="phase4_replay/foo_v1",
        run_id="20260224-000003",
        first_frame=1.5,
        replay_first=0.8,
        duration=12.0,
        max_gap=1.6,
        stall=1.2,
    )
    baseline_payload = {
        "summary": {
            "first_frame_seconds": {"mean": 1.0, "p95": 1.0, "max": 1.0},
            "replay_first_frame_seconds": {"mean": 0.5, "p95": 0.5, "max": 0.5},
            "duration_seconds": {"mean": 10.0, "p95": 10.0, "max": 10.0},
            "max_stall_seconds": {"mean": 1.0, "p95": 1.0, "max": 1.0},
            "max_frame_gap_seconds": {"mean": 1.0, "p95": 1.0, "max": 1.0},
        }
    }
    result = module.build_latency_trend(
        run_root=run_root,
        scenario_set="hard_gate",
        contract_file=contract,
        max_first_frame_seconds=3.0,
        max_replay_first_frame_seconds=2.0,
        max_duration_seconds=25.0,
        max_stall_seconds=3.0,
        max_frame_gap_seconds=3.2,
        near_threshold_ratio=0.85,
        baseline_payload=baseline_payload,
    )
    delta = result.payload["delta_summary"]
    assert delta["first_frame_seconds"]["mean_delta"] > 0.0
    assert delta["duration_seconds"]["mean_delta"] > 0.0


def test_build_phase5_latency_trend_report_near_threshold_contributors(tmp_path: Path):
    module = _load_module()
    run_root = tmp_path / "runs"
    contract = tmp_path / "contract.json"
    _write_contract(contract)
    _write_run(
        run_root,
        scenario="phase4_replay/foo_v1",
        run_id="20260224-000004",
        first_frame=2.95,  # near 3.0 threshold
        replay_first=1.9,  # keep first-frame max near threshold while still valid
        duration=12.0,
        max_gap=3.0,  # near 3.2 threshold
        stall=1.0,
    )
    result = module.build_latency_trend(
        run_root=run_root,
        scenario_set="hard_gate",
        contract_file=contract,
        max_first_frame_seconds=3.0,
        max_replay_first_frame_seconds=2.0,
        max_duration_seconds=25.0,
        max_stall_seconds=3.0,
        max_frame_gap_seconds=3.2,
        near_threshold_ratio=0.85,
        baseline_payload=None,
    )
    near = result.payload["near_threshold"]
    assert near["count"] >= 2
    top = near["top"]
    assert any(row["metric"] == "first_frame_seconds" for row in top)
    assert any(row["metric"] == "max_frame_gap_seconds" for row in top)


def test_build_phase5_latency_trend_report_wait_overlap_annotation(tmp_path: Path):
    module = _load_module()
    run_root = tmp_path / "runs"
    contract = tmp_path / "contract.json"
    _write_contract(contract)

    scenario = "phase4_replay/foo_v1"
    run_dir = run_root / "foo_v1" / "20260224-000005"
    run_dir.mkdir(parents=True, exist_ok=True)
    started_at = 1000.0
    rows = [
        {"frame": 1, "timestamp": 1001.0},
        {"frame": 2, "timestamp": 1001.5},
        {"frame": 3, "timestamp": 1004.3},
    ]
    (run_dir / "index.jsonl").write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")
    manifest = {
        "scenario": scenario,
        "run_id": "20260224-000005",
        "started_at": started_at,
        "duration_seconds": 8.0,
        "executed_actions": [
            {"action": {"type": "send", "text": "replay:fixture.jsonl"}, "ended_at": 1000.8, "started_at": 1000.7},
            {"action": {"type": "wait_for_quiet"}, "started_at": 1001.4, "ended_at": 1004.3},
        ],
        "frame_validation": {"max_unchanged_streak_seconds_estimate": 1.0},
    }
    (run_dir / "scenario_manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")

    result = module.build_latency_trend(
        run_root=run_root,
        scenario_set="hard_gate",
        contract_file=contract,
        max_first_frame_seconds=3.0,
        max_replay_first_frame_seconds=2.0,
        max_duration_seconds=25.0,
        max_stall_seconds=3.0,
        max_frame_gap_seconds=3.2,
        near_threshold_ratio=0.85,
        baseline_payload=None,
    )
    rec = result.payload["records"][0]
    metrics = rec["metrics"]
    assert metrics["max_frame_gap_wait_overlap"] is True
    assert "wait_for_quiet" in metrics["max_frame_gap_overlap_actions"]
    top = result.payload["near_threshold"]["top"][0]
    assert top["metric"] == "max_frame_gap_seconds"
    assert top["wait_overlap"] is True
