from __future__ import annotations

import importlib.util
import sys
from pathlib import Path


def _load_module():
    repo_root = Path(__file__).resolve().parents[1]
    module_path = repo_root / "scripts" / "build_phase5_postcert_monitoring_bundle.py"
    scripts_dir = str((repo_root / "scripts").resolve())
    if scripts_dir not in sys.path:
        sys.path.insert(0, scripts_dir)
    spec = importlib.util.spec_from_file_location("build_phase5_postcert_monitoring_bundle", module_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _base_inputs():
    footer = {"overall_ok": True, "failure_count": 0}
    discrepancy = {"overall_ok": True, "non_info_count": 0, "top_risk_score": 0.0}
    reliability = {
        "overall_ok": True,
        "counts": {
            "stable-pass": 14,
            "transient-single-recovery": 1,
            "intermittent-flake": 0,
            "persistent-fail": 0,
            "active-regression": 0,
        },
    }
    latency = {"overall_ok": True, "worst_threshold_ratio": 0.92, "worst_metric_context": {}}
    wait = {"overall_ok": True}
    ranking = {
        "count": 2,
        "items": [
            {"severity": "info", "risk_score": 0.0},
            {"severity": "info", "risk_score": 0.0},
        ],
    }
    trend = {
        "overall_ok": True,
        "summary": {
            "max_frame_gap_seconds": {"max": 2.94},
            "max_non_wait_frame_gap_seconds": {"max": 2.04},
        },
        "near_threshold": {"top": [{"scenario": "a"}, {"scenario": "b"}, {"scenario": "c"}, {"scenario": "d"}]},
    }
    return footer, discrepancy, reliability, latency, wait, ranking, trend


def test_build_report_passes_all_green():
    module = _load_module()
    footer, discrepancy, reliability, latency, wait, ranking, trend = _base_inputs()
    report = module.build_report(
        stamp="20260224-roundtrip35",
        footer_gate=footer,
        discrepancy_budget_gate=discrepancy,
        reliability_budget_gate=reliability,
        latency_drift_gate=latency,
        wait_alignment_gate=wait,
        discrepancy_ranking=ranking,
        latency_trend=trend,
        sources={"x": "/tmp/x.json"},
    )
    assert report["overall_ok"] is True
    assert report["checks"]["ranking"]["non_info_count"] == 0
    assert report["checks"]["ranking"]["top_risk_score"] == 0.0
    assert len(report["checks"]["latency_trend"]["near_threshold_top"]) == 3


def test_build_report_fails_on_non_info_or_risk():
    module = _load_module()
    footer, discrepancy, reliability, latency, wait, ranking, trend = _base_inputs()
    ranking["items"].append({"severity": "low", "risk_score": 0.3})
    report = module.build_report(
        stamp="20260224-roundtrip35",
        footer_gate=footer,
        discrepancy_budget_gate=discrepancy,
        reliability_budget_gate=reliability,
        latency_drift_gate=latency,
        wait_alignment_gate=wait,
        discrepancy_ranking=ranking,
        latency_trend=trend,
        sources={},
    )
    assert report["overall_ok"] is False
    assert report["checks"]["ranking"]["non_info_count"] == 1
    assert report["checks"]["ranking"]["top_risk_score"] == 0.3


def test_build_report_fails_when_gate_red():
    module = _load_module()
    footer, discrepancy, reliability, latency, wait, ranking, trend = _base_inputs()
    footer["overall_ok"] = False
    report = module.build_report(
        stamp="20260224-roundtrip35",
        footer_gate=footer,
        discrepancy_budget_gate=discrepancy,
        reliability_budget_gate=reliability,
        latency_drift_gate=latency,
        wait_alignment_gate=wait,
        discrepancy_ranking=ranking,
        latency_trend=trend,
        sources={},
    )
    assert report["overall_ok"] is False

