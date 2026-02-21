from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest


def _load_module():
    repo_root = Path(__file__).resolve().parents[1]
    module_path = repo_root / "scripts" / "validate_longrun_phase2_live_artifacts.py"
    scripts_dir = str((repo_root / "scripts").resolve())
    if scripts_dir not in sys.path:
        sys.path.insert(0, scripts_dir)
    spec = importlib.util.spec_from_file_location("validate_longrun_phase2_live_artifacts", module_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _sample_payload() -> dict:
    return {
        "schema_version": "longrun_phase2_live_pilot_v1",
        "dry_run": False,
        "caps": {
            "max_pairs": 1,
            "max_total_tokens": 10000,
            "max_total_estimated_cost_usd": 1.0,
        },
        "totals": {
            "runs_executed": 2,
            "pair_rows": 1,
            "prompt_tokens": 600,
            "completion_tokens": 400,
            "total_tokens": 1000,
            "total_estimated_cost_usd": 0.1,
            "budget_exceeded": False,
            "stop_reason": None,
        },
        "pairs": [
            {
                "task_id": "hello_py_function",
                "baseline": {
                    "usage": {"prompt_tokens": 300, "completion_tokens": 200, "total_tokens": 500},
                    "estimated_cost_usd": 0.05,
                    "telemetry": {
                        "arm_status": "completed",
                        "failure_class": "none",
                        "guard_trigger_count": 0,
                        "guard_trigger_by_name": {},
                        "timeout_hit": False,
                        "process_error": None,
                    },
                },
                "longrun": {
                    "usage": {"prompt_tokens": 300, "completion_tokens": 200, "total_tokens": 500},
                    "estimated_cost_usd": 0.05,
                    "telemetry": {
                        "arm_status": "completed",
                        "failure_class": "none",
                        "guard_trigger_count": 1,
                        "guard_trigger_by_name": {"zero_tool_watchdog": 1},
                        "timeout_hit": False,
                        "process_error": None,
                    },
                },
            }
        ],
    }


def test_validate_live_payload_accepts_happy_path(tmp_path: Path) -> None:
    module = _load_module()
    target = tmp_path / "live.json"
    target.write_text(json.dumps(_sample_payload()), encoding="utf-8")
    out = module.validate_payload(target)
    assert out["ok"] is True
    assert out["pair_rows"] == 1


def test_validate_live_payload_rejects_budget_exceeded_by_default(tmp_path: Path) -> None:
    module = _load_module()
    payload = _sample_payload()
    payload["totals"]["budget_exceeded"] = True
    target = tmp_path / "live.json"
    target.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ValueError, match="budget_exceeded=true"):
        module.validate_payload(target)


def test_validate_live_payload_rejects_zero_usage(tmp_path: Path) -> None:
    module = _load_module()
    payload = _sample_payload()
    payload["totals"]["total_tokens"] = 0
    payload["totals"]["prompt_tokens"] = 0
    payload["totals"]["completion_tokens"] = 0
    payload["pairs"][0]["baseline"]["usage"]["total_tokens"] = 0
    payload["pairs"][0]["longrun"]["usage"]["total_tokens"] = 0
    target = tmp_path / "live.json"
    target.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ValueError, match="zero token usage"):
        module.validate_payload(target)


def test_validate_live_payload_allows_cap_overshoot_in_explicit_budget_exceeded_mode(tmp_path: Path) -> None:
    module = _load_module()
    payload = _sample_payload()
    payload["totals"]["budget_exceeded"] = True
    payload["totals"]["total_estimated_cost_usd"] = 1.25
    payload["caps"]["max_total_estimated_cost_usd"] = 1.0
    target = tmp_path / "live.json"
    target.write_text(json.dumps(payload), encoding="utf-8")
    out = module.validate_payload(target, allow_budget_exceeded=True)
    assert out["ok"] is True


def test_validate_live_payload_allows_partial_last_pair_in_budget_exceeded_mode(tmp_path: Path) -> None:
    module = _load_module()
    payload = _sample_payload()
    payload["totals"]["budget_exceeded"] = True
    payload["pairs"][0].pop("longrun", None)
    target = tmp_path / "live.json"
    target.write_text(json.dumps(payload), encoding="utf-8")
    out = module.validate_payload(target, allow_budget_exceeded=True)
    assert out["ok"] is True


def test_validate_live_payload_rejects_missing_telemetry(tmp_path: Path) -> None:
    module = _load_module()
    payload = _sample_payload()
    payload["pairs"][0]["baseline"].pop("telemetry", None)
    target = tmp_path / "live.json"
    target.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ValueError, match="baseline.telemetry must be an object"):
        module.validate_payload(target)
