from __future__ import annotations

import importlib.util
import sys
from pathlib import Path


def _load_module():
    repo_root = Path(__file__).resolve().parents[1]
    module_path = repo_root / "scripts" / "evaluate_rlm_phase2_p5.py"
    scripts_dir = str((repo_root / "scripts").resolve())
    if scripts_dir not in sys.path:
        sys.path.insert(0, scripts_dir)
    spec = importlib.util.spec_from_file_location("evaluate_rlm_phase2_p5", module_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_evaluate_reports_ok_for_clean_sample() -> None:
    module = _load_module()
    payload = {
        "schema_version": "rlm_phase2_tier0_v1",
        "runs": [
            {"arm": "A1_RLM_SYNC", "task_success": True, "review_pass": True, "artifact_validation_ok": True, "nondeterminism_detected": False, "wallclock_seconds": 0.1, "subcall_count": 2, "blocked_count": 0, "timeout_count": 0},
            {"arm": "A2_RLM_BATCH", "task_success": True, "review_pass": True, "artifact_validation_ok": True, "nondeterminism_detected": False, "wallclock_seconds": 0.2, "subcall_count": 2, "blocked_count": 0, "timeout_count": 0},
            {"arm": "A3_HYBRID_LONGRUN", "task_success": True, "review_pass": True, "artifact_validation_ok": True, "nondeterminism_detected": False, "wallclock_seconds": 0.3, "subcall_count": 2, "blocked_count": 0, "timeout_count": 0},
        ],
    }
    report = module.evaluate(payload)
    assert report["schema_version"] == "rlm_phase2_p5_evaluation_v1"
    assert report["ok"] is True
    assert len(report["arm_reports"]) == 3


def test_evaluate_reports_failure_when_artifact_or_determinism_breaks() -> None:
    module = _load_module()
    payload = {
        "schema_version": "rlm_phase2_tier0_v1",
        "runs": [
            {"arm": "A1_RLM_SYNC", "task_success": True, "review_pass": True, "artifact_validation_ok": False, "nondeterminism_detected": False, "wallclock_seconds": 0.1, "subcall_count": 2, "blocked_count": 0, "timeout_count": 0},
            {"arm": "A2_RLM_BATCH", "task_success": True, "review_pass": True, "artifact_validation_ok": True, "nondeterminism_detected": True, "wallclock_seconds": 0.2, "subcall_count": 2, "blocked_count": 0, "timeout_count": 0},
            {"arm": "A3_HYBRID_LONGRUN", "task_success": True, "review_pass": True, "artifact_validation_ok": True, "nondeterminism_detected": False, "wallclock_seconds": 0.3, "subcall_count": 2, "blocked_count": 0, "timeout_count": 0},
        ],
    }
    report = module.evaluate(payload)
    assert report["ok"] is False
    assert any(not bool(c.get("passed")) for c in report["checks"])


def test_evaluate_allows_partial_arm_reports_when_enabled() -> None:
    module = _load_module()
    payload = {
        "schema_version": "rlm_phase2_tier0_v1",
        "runs": [
            {"arm": "A1_RLM_SYNC", "task_success": True, "review_pass": True, "artifact_validation_ok": True, "nondeterminism_detected": False, "wallclock_seconds": 0.1, "subcall_count": 2, "blocked_count": 0, "timeout_count": 0},
        ],
    }
    strict_report = module.evaluate(payload, allow_partial_arms=False)
    partial_report = module.evaluate(payload, allow_partial_arms=True)
    assert strict_report["ok"] is False
    assert partial_report["ok"] is True
