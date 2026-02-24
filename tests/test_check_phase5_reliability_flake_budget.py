from __future__ import annotations

import importlib.util
import sys
from pathlib import Path


def _load_module():
    repo_root = Path(__file__).resolve().parents[1]
    module_path = repo_root / "scripts" / "check_phase5_reliability_flake_budget.py"
    scripts_dir = str((repo_root / "scripts").resolve())
    if scripts_dir not in sys.path:
        sys.path.insert(0, scripts_dir)
    spec = importlib.util.spec_from_file_location("check_phase5_reliability_flake_budget", module_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_flake_budget_passes_transient_only():
    module = _load_module()
    report = {
        "scenarios": [
            {"scenario": "s1", "classification": "stable-pass"},
            {"scenario": "s2", "classification": "transient-single-recovery"},
        ]
    }
    out = module.build_report(
        flake_report=report,
        max_persistent=0,
        max_active=0,
        max_intermittent=0,
        max_transient=1,
    )
    assert out["overall_ok"] is True
    assert out["counts"]["transient-single-recovery"] == 1


def test_flake_budget_fails_intermittent():
    module = _load_module()
    report = {
        "scenarios": [
            {"scenario": "s1", "classification": "intermittent-flake", "fail_count": 1, "sample_count": 12},
        ]
    }
    out = module.build_report(
        flake_report=report,
        max_persistent=0,
        max_active=0,
        max_intermittent=0,
        max_transient=1,
    )
    assert out["overall_ok"] is False
    checks = {row["check"] for row in out["failures"]}
    assert "intermittent_budget" in checks
    assert out["flagged"][0]["classification"] == "intermittent-flake"


def test_flake_budget_fails_unknown_classification():
    module = _load_module()
    report = {"scenarios": [{"scenario": "s1", "classification": "mystery"}]}
    out = module.build_report(
        flake_report=report,
        max_persistent=0,
        max_active=0,
        max_intermittent=0,
        max_transient=1,
    )
    assert out["overall_ok"] is False
    checks = {row["check"] for row in out["failures"]}
    assert "unknown_classification" in checks

