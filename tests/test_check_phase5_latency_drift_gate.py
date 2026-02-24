from __future__ import annotations

import importlib.util
import sys
from pathlib import Path


def _load_module():
    repo_root = Path(__file__).resolve().parents[1]
    module_path = repo_root / "scripts" / "check_phase5_latency_drift_gate.py"
    scripts_dir = str((repo_root / "scripts").resolve())
    if scripts_dir not in sys.path:
        sys.path.insert(0, scripts_dir)
    spec = importlib.util.spec_from_file_location("check_phase5_latency_drift_gate", module_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _trend_payload(*, baseline_present: bool = True) -> dict:
    payload = {
        "overall_ok": True,
        "baseline_present": baseline_present,
        "thresholds": {
            "max_first_frame_seconds": 3.0,
            "max_replay_first_frame_seconds": 2.0,
            "max_duration_seconds": 25.0,
            "max_stall_seconds": 3.0,
            "max_frame_gap_seconds": 3.2,
        },
        "summary": {
            "missing_count": 0,
            "first_frame_seconds": {"max": 2.1},
            "replay_first_frame_seconds": {"max": 1.4},
            "duration_seconds": {"max": 18.0},
            "max_stall_seconds": {"max": 2.0},
            "max_frame_gap_seconds": {"max": 2.94},
        },
        "delta_summary": {
            "first_frame_seconds": {"mean_delta": 0.01},
            "replay_first_frame_seconds": {"mean_delta": 0.02},
            "duration_seconds": {"mean_delta": 0.05},
            "max_stall_seconds": {"mean_delta": 0.0},
            "max_frame_gap_seconds": {"mean_delta": 0.01},
        },
        "near_threshold": {
            "top": [
                {
                    "scenario": "phase4_replay/subagents_concurrency_20_v1",
                    "metric": "max_frame_gap_seconds",
                    "ratio": 0.91875,
                    "wait_overlap": True,
                    "overlap_actions": ["wait_for_quiet"],
                }
            ]
        },
    }
    if not baseline_present:
        payload["delta_summary"] = {}
    return payload


def test_latency_drift_gate_pass():
    module = _load_module()
    result = module.check_latency_drift_gate(
        trend_payload=_trend_payload(baseline_present=True),
        max_threshold_ratio=0.95,
        max_mean_delta_first_frame=0.20,
        max_mean_delta_replay_first_frame=0.20,
        max_mean_delta_duration=0.60,
        max_mean_delta_stall=0.20,
        max_mean_delta_max_gap=0.20,
        allow_bootstrap_no_baseline=False,
    )
    assert result.ok is True
    assert result.report["overall_ok"] is True
    assert result.report["config"]["max_threshold_ratio"] == 0.95
    assert result.report["config"]["max_mean_delta_duration"] == 0.60
    assert result.report["worst_metric_context"]["scenario"] == "phase4_replay/subagents_concurrency_20_v1"
    assert result.report["worst_metric_context"]["wait_overlap"] is True


def test_latency_drift_gate_fails_ratio():
    module = _load_module()
    payload = _trend_payload(baseline_present=True)
    payload["summary"]["max_frame_gap_seconds"]["max"] = 3.15
    result = module.check_latency_drift_gate(
        trend_payload=payload,
        max_threshold_ratio=0.95,
        max_mean_delta_first_frame=0.20,
        max_mean_delta_replay_first_frame=0.20,
        max_mean_delta_duration=0.60,
        max_mean_delta_stall=0.20,
        max_mean_delta_max_gap=0.20,
        allow_bootstrap_no_baseline=False,
    )
    assert result.ok is False
    assert result.report["worst_threshold_ratio"] > 0.95


def test_latency_drift_gate_bootstrap_allowed():
    module = _load_module()
    result = module.check_latency_drift_gate(
        trend_payload=_trend_payload(baseline_present=False),
        max_threshold_ratio=0.95,
        max_mean_delta_first_frame=0.20,
        max_mean_delta_replay_first_frame=0.20,
        max_mean_delta_duration=0.60,
        max_mean_delta_stall=0.20,
        max_mean_delta_max_gap=0.20,
        allow_bootstrap_no_baseline=True,
    )
    assert result.ok is True


def test_latency_drift_gate_bootstrap_required_when_no_baseline():
    module = _load_module()
    result = module.check_latency_drift_gate(
        trend_payload=_trend_payload(baseline_present=False),
        max_threshold_ratio=0.95,
        max_mean_delta_first_frame=0.20,
        max_mean_delta_replay_first_frame=0.20,
        max_mean_delta_duration=0.60,
        max_mean_delta_stall=0.20,
        max_mean_delta_max_gap=0.20,
        allow_bootstrap_no_baseline=False,
    )
    assert result.ok is False
    failed = [row for row in result.report["checks"] if row["name"] == "baseline_present_or_bootstrap_allowed"]
    assert len(failed) == 1
    assert failed[0]["pass"] is False
