from __future__ import annotations

import importlib.util
import sys
from pathlib import Path


def _load_module():
    repo_root = Path(__file__).resolve().parents[1]
    module_path = repo_root / "scripts" / "check_phase5_canary_wait_alignment.py"
    scripts_dir = str((repo_root / "scripts").resolve())
    if scripts_dir not in sys.path:
        sys.path.insert(0, scripts_dir)
    spec = importlib.util.spec_from_file_location("check_phase5_canary_wait_alignment", module_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_build_report_pass():
    module = _load_module()
    report = module.build_report(
        hotspot={
            "scenario": "phase4_replay/subagents_concurrency_20_v1",
            "gap_summary": {
                "top_wait_dominated_count": 3,
                "top_expected_wait_count": 4,
                "top_unexpected_wait_count": 0,
            },
        },
        max_top_unexpected_waits=0,
    )
    assert report["overall_ok"] is True
    assert report["metrics"]["top_unexpected_wait_count"] == 0


def test_build_report_fail():
    module = _load_module()
    report = module.build_report(
        hotspot={
            "scenario": "phase4_replay/subagents_concurrency_20_v1",
            "gap_summary": {
                "top_wait_dominated_count": 3,
                "top_expected_wait_count": 2,
                "top_unexpected_wait_count": 2,
            },
        },
        max_top_unexpected_waits=0,
    )
    assert report["overall_ok"] is False
    assert report["metrics"]["top_unexpected_wait_count"] == 2
