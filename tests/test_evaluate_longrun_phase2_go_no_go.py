from __future__ import annotations

import importlib.util
import sys
from pathlib import Path


def _load_module():
    repo_root = Path(__file__).resolve().parents[1]
    module_path = repo_root / "scripts" / "evaluate_longrun_phase2_go_no_go.py"
    scripts_dir = str((repo_root / "scripts").resolve())
    if scripts_dir not in sys.path:
        sys.path.insert(0, scripts_dir)
    spec = importlib.util.spec_from_file_location("evaluate_longrun_phase2_go_no_go", module_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _base_payload():
    scenarios = []
    scenarios.append(
        {
            "scenario_id": "single_success",
            "baseline": {"summary": {"success_rate": 1.0, "median_episodes": 1}},
            "longrun": {"summary": {"success_rate": 1.0, "median_episodes": 1}},
        }
    )
    scenarios.append(
        {
            "scenario_id": "flaky_then_success",
            "baseline": {"summary": {"success_rate": 1.0, "median_episodes": 2}},
            "longrun": {"summary": {"success_rate": 1.0, "median_episodes": 2}},
        }
    )
    scenarios.append(
        {
            "scenario_id": "persistent_no_progress",
            "baseline": {"summary": {"success_rate": 0.0, "median_episodes": 8}},
            "longrun": {"summary": {"success_rate": 0.0, "median_episodes": 2}},
        }
    )
    scenarios.append(
        {
            "scenario_id": "retry_then_rollback",
            "baseline": {"summary": {"success_rate": 0.0, "median_episodes": 5}},
            "longrun": {"summary": {"success_rate": 0.0, "median_episodes": 2}},
        }
    )
    scenarios.append(
        {
            "scenario_id": "signature_repeat_stop",
            "baseline": {"summary": {"success_rate": 0.0, "median_episodes": 6}},
            "longrun": {"summary": {"success_rate": 0.0, "median_episodes": 2}},
        }
    )
    scenarios.append(
        {
            "scenario_id": "alternating_failure_signatures",
            "baseline": {"summary": {"success_rate": 0.0, "median_episodes": 4}},
            "longrun": {"summary": {"success_rate": 0.0, "median_episodes": 4}},
        }
    )
    return {
        "schema_version": "longrun_phase1_pilot_v1",
        "runs_per_scenario": 5,
        "scenarios": scenarios,
    }


def test_evaluate_passes_on_expected_payload() -> None:
    module = _load_module()
    report = module.evaluate(_base_payload(), {"ok": True, "checked": 13, "failures": []})
    assert report["ok"] is True
    assert all(check["passed"] for check in report["checks"])


def test_evaluate_fails_on_success_regression() -> None:
    module = _load_module()
    payload = _base_payload()
    payload["scenarios"][0]["longrun"]["summary"]["success_rate"] = 0.0
    report = module.evaluate(payload, {"ok": True})
    assert report["ok"] is False
    assert any(c["name"] == "no_success_regression" and not c["passed"] for c in report["checks"])
