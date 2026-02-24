from __future__ import annotations

import importlib.util
import sys
from pathlib import Path


def _load_module():
    repo_root = Path(__file__).resolve().parents[1]
    module_path = repo_root / "scripts" / "check_phase5_discrepancy_budget_gate.py"
    scripts_dir = str((repo_root / "scripts").resolve())
    if scripts_dir not in sys.path:
        sys.path.insert(0, scripts_dir)
    spec = importlib.util.spec_from_file_location("check_phase5_discrepancy_budget_gate", module_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_budget_gate_passes_all_info():
    module = _load_module()
    ranking = {
        "items": [
            {"key": "a", "category": "x", "severity": "info", "risk_score": 0.0, "summary": "ok"},
            {"key": "b", "category": "x", "severity": "info", "risk_score": 0.0, "summary": "ok"},
        ]
    }
    report = module.build_report(
        ranking=ranking,
        max_high=0,
        max_medium=0,
        max_low=0,
        max_unknown=0,
        max_non_info=0,
        max_top_risk=0.0,
    )
    assert report["overall_ok"] is True
    assert report["non_info_count"] == 0
    assert report["top_risk_score"] == 0.0


def test_budget_gate_fails_low_and_top_risk():
    module = _load_module()
    ranking = {
        "items": [
            {
                "key": "reliability-flake:intermittent",
                "category": "reliability-flake",
                "severity": "low",
                "risk_score": 0.25,
                "summary": "intermittent",
            }
        ]
    }
    report = module.build_report(
        ranking=ranking,
        max_high=0,
        max_medium=0,
        max_low=0,
        max_unknown=0,
        max_non_info=0,
        max_top_risk=0.0,
    )
    assert report["overall_ok"] is False
    checks = {row["check"] for row in report["failures"]}
    assert "low_budget" in checks
    assert "non_info_budget" in checks
    assert "top_risk_budget" in checks
    assert report["top_actionable_items"][0]["key"] == "reliability-flake:intermittent"


def test_budget_gate_unknown_severity_caught():
    module = _load_module()
    ranking = {
        "items": [
            {"key": "mystery", "category": "x", "severity": "weird", "risk_score": 0.0, "summary": "?"}
        ]
    }
    report = module.build_report(
        ranking=ranking,
        max_high=0,
        max_medium=0,
        max_low=0,
        max_unknown=0,
        max_non_info=0,
        max_top_risk=0.0,
    )
    assert report["overall_ok"] is False
    checks = {row["check"] for row in report["failures"]}
    assert "unknown_budget" in checks

