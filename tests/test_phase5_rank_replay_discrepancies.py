from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

from PIL import Image


def _load_module():
    repo_root = Path(__file__).resolve().parents[1]
    module_path = repo_root / "scripts" / "phase5_rank_replay_discrepancies.py"
    scripts_dir = str((repo_root / "scripts").resolve())
    if scripts_dir not in sys.path:
        sys.path.insert(0, scripts_dir)
    spec = importlib.util.spec_from_file_location("phase5_rank_replay_discrepancies", module_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_classify_advanced_stress_passes():
    module = _load_module()
    payload = {
        "overall_ok": True,
        "scenario_count": 2,
        "validated_count": 2,
        "missing_scenarios": [],
        "records": [
            {
                "scenario": "phase4_replay/a",
                "checks": [{"name": "foo", "pass": True}],
            },
            {
                "scenario": "phase4_replay/b",
                "checks": [{"name": "bar", "pass": True}],
            },
        ],
    }
    items = module._classify_advanced_stress(payload)
    keys = {row.key for row in items}
    assert "advanced:overall" in keys
    assert all(row.risk_score == 0.0 for row in items if row.key == "advanced:overall")


def test_classify_advanced_stress_failing_scenario():
    module = _load_module()
    payload = {
        "overall_ok": False,
        "scenario_count": 2,
        "validated_count": 2,
        "missing_scenarios": [],
        "records": [
            {
                "scenario": "phase4_replay/a",
                "checks": [
                    {"name": "foo", "pass": True},
                    {"name": "max_frame_gap_seconds", "pass": False},
                ],
            },
        ],
    }
    items = module._classify_advanced_stress(payload)
    keys = {row.key for row in items}
    assert "advanced:phase4_replay/a" in keys
    assert "advanced:summary" in keys


def test_classify_reliability_flake_intermittent():
    module = _load_module()
    payload = {
        "scenarios": [
            {"scenario": "phase4_replay/everything_showcase_v1_fullpane_v1", "classification": "intermittent-flake"},
            {"scenario": "phase4_replay/subagents_v1_fullpane_v7", "classification": "stable-pass"},
        ]
    }
    items = module._classify_reliability_flake(payload)
    assert len(items) == 1
    row = items[0]
    assert row.key == "reliability-flake:intermittent"
    assert row.severity == "low"
    assert row.risk_score > 0.0


def test_classify_reliability_flake_transient_single_is_info():
    module = _load_module()
    payload = {
        "scenarios": [
            {
                "scenario": "phase4_replay/everything_showcase_v1_fullpane_v1",
                "classification": "transient-single-recovery",
            },
            {"scenario": "phase4_replay/subagents_v1_fullpane_v7", "classification": "stable-pass"},
        ]
    }
    items = module._classify_reliability_flake(payload)
    assert len(items) == 1
    row = items[0]
    assert row.key == "reliability-flake:transient"
    assert row.severity == "info"
    assert row.risk_score == 0.0


def test_classify_reliability_flake_regression():
    module = _load_module()
    payload = {
        "scenarios": [
            {"scenario": "phase4_replay/everything_showcase_v1_fullpane_v1", "classification": "persistent-fail"},
        ]
    }
    items = module._classify_reliability_flake(payload)
    assert len(items) == 1
    row = items[0]
    assert row.key == "reliability-flake:regression"
    assert row.severity == "high"
    assert row.risk_score >= 0.8


def test_classify_artifact_integrity_passes():
    module = _load_module()
    payload = {
        "overall_ok": True,
        "records": [
            {
                "scenario": "phase4_replay/a",
                "ok": True,
                "metrics": {
                    "missing_artifacts_count": 0,
                    "row_parity_violations": 0,
                    "line_gap_nonzero_count": 0,
                    "isolated_low_edge_rows_total": 0,
                },
            }
        ],
        "missing_scenarios": [],
    }
    items = module._classify_artifact_integrity(payload)
    keys = {row.key for row in items}
    assert "artifact:overall" in keys


def test_classify_latency_trend_near_threshold():
    module = _load_module()
    payload = {
        "overall_ok": True,
        "thresholds": {
            "max_first_frame_seconds": 3.0,
            "max_replay_first_frame_seconds": 2.0,
            "max_duration_seconds": 25.0,
            "max_stall_seconds": 3.0,
            "max_frame_gap_seconds": 3.2,
        },
        "summary": {
            "record_count": 4,
            "ok_count": 4,
            "missing_count": 0,
            "first_frame_seconds": {"max": 2.95},
            "replay_first_frame_seconds": {"max": 1.2},
            "duration_seconds": {"max": 12.0},
            "max_stall_seconds": {"max": 1.5},
            "max_frame_gap_seconds": {"max": 2.9},
        },
    }
    items = module._classify_latency_trend(payload)
    keys = {row.key for row in items}
    # max_frame_gap is near its threshold in this synthetic payload.
    assert "latency:near-threshold" in keys


def test_classify_latency_trend_wait_overlap_downgrade():
    module = _load_module()
    payload = {
        "overall_ok": True,
        "thresholds": {
            "max_first_frame_seconds": 3.0,
            "max_replay_first_frame_seconds": 2.0,
            "max_duration_seconds": 25.0,
            "max_stall_seconds": 3.0,
            "max_frame_gap_seconds": 3.2,
        },
        "summary": {
            "record_count": 2,
            "ok_count": 2,
            "missing_count": 0,
            "first_frame_seconds": {"max": 1.5},
            "replay_first_frame_seconds": {"max": 1.1},
            "duration_seconds": {"max": 10.0},
            "max_stall_seconds": {"max": 1.4},
            "max_frame_gap_seconds": {"max": 2.95},
            "max_non_wait_frame_gap_seconds": {"max": 2.2},
        },
        "near_threshold": {
            "top": [
                {
                    "scenario": "phase4_replay/subagents_concurrency_20_v1",
                    "metric": "max_frame_gap_seconds",
                    "ratio": 0.921,
                    "wait_overlap": True,
                }
            ]
        },
    }
    items = module._classify_latency_trend(payload)
    by_key = {row.key: row for row in items}
    assert "latency:near-threshold" in by_key
    assert "latency:non-wait-frame-gap" in by_key

    row = by_key["latency:near-threshold"]
    assert row.severity == "low"
    assert "wait-overlap dominated" in row.summary

    non_wait_row = by_key["latency:non-wait-frame-gap"]
    assert non_wait_row.severity == "info"
    assert non_wait_row.risk_score == 0.0
    assert "headroom" in non_wait_row.summary


def test_classify_latency_trend_wait_overlap_expected_canary_is_info():
    module = _load_module()
    payload = {
        "overall_ok": True,
        "thresholds": {
            "max_first_frame_seconds": 3.0,
            "max_replay_first_frame_seconds": 2.0,
            "max_duration_seconds": 25.0,
            "max_stall_seconds": 3.0,
            "max_frame_gap_seconds": 3.2,
        },
        "summary": {
            "record_count": 2,
            "ok_count": 2,
            "missing_count": 0,
            "first_frame_seconds": {"max": 1.5},
            "replay_first_frame_seconds": {"max": 1.1},
            "duration_seconds": {"max": 10.0},
            "max_stall_seconds": {"max": 1.4},
            "max_frame_gap_seconds": {"max": 2.95},
            "max_non_wait_frame_gap_seconds": {"max": 2.2},
        },
        "near_threshold": {
            "top": [
                {
                    "scenario": "phase4_replay/subagents_concurrency_20_v1",
                    "metric": "max_frame_gap_seconds",
                    "ratio": 0.921,
                    "wait_overlap": True,
                }
            ]
        },
        "_canary_hotspot": {
            "gap_summary": {
                "top_wait_dominated_count": 3,
                "top_expected_wait_count": 4,
                "top_unexpected_wait_count": 0,
            }
        },
    }
    items = module._classify_latency_trend(payload)
    by_key = {row.key: row for row in items}
    assert by_key["latency:near-threshold"].severity == "info"
    assert by_key["latency:near-threshold"].risk_score == 0.0
    assert "expected harness waits" in by_key["latency:near-threshold"].summary


def test_classify_latency_trend_wait_overlap_with_non_wait_pressure():
    module = _load_module()
    payload = {
        "overall_ok": True,
        "thresholds": {
            "max_first_frame_seconds": 3.0,
            "max_replay_first_frame_seconds": 2.0,
            "max_duration_seconds": 25.0,
            "max_stall_seconds": 3.0,
            "max_frame_gap_seconds": 3.2,
        },
        "summary": {
            "record_count": 2,
            "ok_count": 2,
            "missing_count": 0,
            "first_frame_seconds": {"max": 1.5},
            "replay_first_frame_seconds": {"max": 1.1},
            "duration_seconds": {"max": 10.0},
            "max_stall_seconds": {"max": 1.4},
            "max_frame_gap_seconds": {"max": 2.95},
            "max_non_wait_frame_gap_seconds": {"max": 2.9},
        },
        "near_threshold": {
            "top": [
                {
                    "scenario": "phase4_replay/subagents_concurrency_20_v1",
                    "metric": "max_frame_gap_seconds",
                    "ratio": 0.921,
                    "wait_overlap": True,
                }
            ]
        },
    }
    items = module._classify_latency_trend(payload)
    by_key = {row.key: row for row in items}
    assert "latency:non-wait-frame-gap" in by_key
    row = by_key["latency:non-wait-frame-gap"]
    assert row.severity == "low"
    assert row.risk_score > 0.0
    assert "approaching threshold" in row.summary


def test_classify_latency_drift_gate_pass():
    module = _load_module()
    payload = {
        "overall_ok": True,
        "baseline_present": True,
        "worst_threshold_ratio": 0.91,
        "worst_threshold_ratio_metric": "max_frame_gap_seconds",
    }
    items = module._classify_latency_drift_gate(payload)
    keys = {row.key for row in items}
    assert "latency-drift:pass" in keys


def test_classify_latency_drift_gate_wait_overlap_downgrade():
    module = _load_module()
    payload = {
        "overall_ok": True,
        "baseline_present": True,
        "worst_threshold_ratio": 0.921,
        "worst_threshold_ratio_metric": "max_frame_gap_seconds",
        "worst_metric_context": {
            "scenario": "phase4_replay/subagents_concurrency_20_v1",
            "wait_overlap": True,
            "overlap_actions": ["wait_for_quiet"],
        },
    }
    items = module._classify_latency_drift_gate(payload)
    assert len(items) == 1
    row = items[0]
    assert row.key == "latency-drift:pass"
    assert row.severity == "low"
    assert row.risk_score < 0.7
    assert "wait-overlap-dominated" in row.summary


def test_classify_latency_drift_gate_expected_canary_is_info():
    module = _load_module()
    payload = {
        "overall_ok": True,
        "baseline_present": True,
        "worst_threshold_ratio": 0.921,
        "worst_threshold_ratio_metric": "max_frame_gap_seconds",
        "worst_metric_context": {
            "scenario": "phase4_replay/subagents_concurrency_20_v1",
            "wait_overlap": True,
        },
        "canary_hotspot": {
            "gap_summary": {
                "top_wait_dominated_count": 3,
                "top_expected_wait_count": 4,
                "top_unexpected_wait_count": 0,
            }
        },
    }
    items = module._classify_latency_drift_gate(payload)
    assert len(items) == 1
    row = items[0]
    assert row.key == "latency-drift:pass"
    assert row.severity == "info"
    assert row.risk_score == 0.0
    assert "aligns with expected harness waits" in row.summary


def test_classify_visual_drift_band_aware_summary():
    module = _load_module()
    payload = {
        "rows_sorted": [
            {
                "scenario": "phase4_replay/streaming_v1_fullpane_v8",
                "frame": "frame_final",
                "changed_px_pct": 2.5,
                "mean_abs_diff": 1.2,
                "rms_diff": 3.4,
                "changed_px_pct_header": 0.2,
                "changed_px_pct_transcript": 0.8,
                "changed_px_pct_footer": 4.7,
                "dominant_band": "footer",
                "diff_bbox": {"x": 10, "y": 20, "width": 30, "height": 40, "area_pct": 3.1},
            }
        ]
    }
    items = module._classify_visual_drift(payload)
    assert len(items) == 1
    row = items[0]
    assert row.category == "visual-drift"
    assert "footer/status region" in row.summary
    assert "dominant=footer" in row.evidence


def test_classify_visual_drift_zero_is_info_no_action():
    module = _load_module()
    payload = {
        "rows_sorted": [
            {
                "scenario": "phase4_replay/streaming_v1_fullpane_v8",
                "frame": "frame_final",
                "changed_px_pct": 0.0,
                "mean_abs_diff": 0.0,
                "rms_diff": 0.0,
                "changed_px_pct_header": 0.0,
                "changed_px_pct_transcript": 0.0,
                "changed_px_pct_footer": 0.0,
                "dominant_band": "none",
                "diff_bbox": None,
            }
        ]
    }
    items = module._classify_visual_drift(payload)
    assert len(items) == 1
    row = items[0]
    assert row.severity == "info"
    assert row.risk_score == 0.0
    assert "No residual visual drift" in row.summary


def test_classify_footer_qc_zero_diff_is_info():
    module = _load_module()
    payload = {
        "targets": {
            "todo": {
                "prev_vs_new_final_footer_diff": {
                    "mean_abs_diff_rgb": [0.0, 0.0, 0.0],
                    "rms_diff_rgb": [0.0, 0.0, 0.0],
                }
            }
        }
    }
    items = module._classify_footer_qc(payload)
    assert len(items) == 1
    row = items[0]
    assert row.key == "footer:todo"
    assert row.severity == "info"
    assert row.risk_score == 0.0


def test_classify_footer_qc_zero_diff_but_blank_footer_is_high():
    module = _load_module()
    payload = {
        "targets": {
            "todo": {
                "prev_vs_new_final_footer_diff": {
                    "mean_abs_diff_rgb": [0.0, 0.0, 0.0],
                    "rms_diff_rgb": [0.0, 0.0, 0.0],
                },
                "final_footer_contrast": {
                    "p99_channel_delta": 0.0,
                    "p995_channel_delta": 0.0,
                    "ratio_delta_gt_20": 0.0,
                },
            }
        }
    }
    items = module._classify_footer_qc(payload)
    assert len(items) == 1
    row = items[0]
    assert row.key == "footer:todo"
    assert row.severity == "high"
    assert row.risk_score >= 0.9
    assert "blank or severely under-rendered" in row.summary


def test_classify_footer_qc_missing_diff_bad_contrast_is_high():
    module = _load_module()
    payload = {
        "targets": {
            "thinking": {
                "final_footer_contrast": {
                    "p99_channel_delta": 3.0,
                    "p995_channel_delta": 3.0,
                    "ratio_delta_gt_20": 0.0,
                },
            }
        }
    }
    items = module._classify_footer_qc(payload)
    assert len(items) == 1
    row = items[0]
    assert row.key == "footer:thinking"
    assert row.severity == "high"
    assert row.risk_score >= 0.9
    assert "No previous-final footer reference" in row.summary


def test_classify_footer_qc_backfills_contrast_from_crop(tmp_path: Path):
    module = _load_module()
    crop = tmp_path / "todo_final_boosted.png"
    Image.new("RGB", (64, 24), (31, 36, 48)).save(crop)
    payload = {
        "targets": {
            "todo": {
                "outputs": {"final_crop": crop.name},
                "prev_vs_new_final_footer_diff": {
                    "mean_abs_diff_rgb": [0.0, 0.0, 0.0],
                    "rms_diff_rgb": [0.0, 0.0, 0.0],
                },
            }
        }
    }
    items = module._classify_footer_qc(payload, footer_qc_dir=tmp_path)
    assert len(items) == 1
    row = items[0]
    assert row.key == "footer:todo"
    assert row.severity == "high"
    assert row.risk_score >= 0.9
    assert "final_contrast(" in row.evidence


def test_apply_allowlist_suppresses_known_gap():
    module = _load_module()
    items = [
        module.QueueItem(
            key="ground-truth:screenshot_ground_truth_3",
            category="ground-truth",
            severity="high",
            risk_score=0.94,
            summary="GT headroom",
            evidence="x",
            action="fix",
        )
    ]
    allowlist = {
        "ground-truth:screenshot_ground_truth_3": {
            "max_risk_score": 1.0,
            "note": "known baseline drift",
        }
    }
    out = module._apply_allowlist(items, allowlist)
    assert len(out) == 1
    row = out[0]
    assert row.category.endswith(":known")
    assert row.severity == "info"
    assert row.risk_score == 0.0
    assert "Known/accepted gap" in row.summary


def test_apply_allowlist_budget_exceeded_escalates():
    module = _load_module()
    items = [
        module.QueueItem(
            key="ground-truth:screenshot_ground_truth_1",
            category="ground-truth",
            severity="medium",
            risk_score=0.91,
            summary="GT headroom",
            evidence="x",
            action="fix",
        )
    ]
    allowlist = {
        "ground-truth:screenshot_ground_truth_1": {
            "max_risk_score": 0.5,
            "note": "too strict",
        }
    }
    out = module._apply_allowlist(items, allowlist)
    assert len(out) == 1
    row = out[0]
    assert row.category == "ground-truth"
    assert row.severity == "high"
    assert row.risk_score == 0.91
    assert "budget exceeded" in row.summary


def test_load_allowlist_multiple_forms(tmp_path: Path):
    module = _load_module()
    payload = {
        "items": [{"key": "a", "max_risk_score": 0.8, "note": "n1"}],
        "keys": ["b"],
        "by_key": {"c": {"max_risk_score": 0.4, "note": "n3"}},
    }
    p = tmp_path / "allow.json"
    p.write_text(json.dumps(payload), encoding="utf-8")
    allow = module._load_allowlist(p)
    assert allow["a"]["max_risk_score"] == 0.8
    assert allow["a"]["note"] == "n1"
    assert allow["b"]["max_risk_score"] == 1.0
    assert allow["c"]["max_risk_score"] == 0.4
