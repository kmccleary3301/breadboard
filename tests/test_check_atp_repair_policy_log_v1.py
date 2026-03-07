from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _load_module(module_name: str):
    module_path = _repo_root() / "scripts" / "check_atp_repair_policy_log_v1.py"
    scripts_dir = str((_repo_root() / "scripts").resolve())
    if scripts_dir not in sys.path:
        sys.path.insert(0, scripts_dir)
    spec = importlib.util.spec_from_file_location(module_name, module_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


def _write_json(path: Path, payload: object) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path


def test_check_atp_repair_policy_log_v1_passes_valid_log(tmp_path: Path) -> None:
    module = _load_module("check_atp_repair_policy_log_v1_ok")
    schema_path = _repo_root() / "docs" / "contracts" / "atp" / "schemas" / "atp_repair_policy_decision_v1.schema.json"
    log_path = _write_json(
        tmp_path / "repair_policy_log.json",
        [
            {
                "schema_version": "atp_repair_policy_decision_v1",
                "loop_id": "loop",
                "node_id": "n1",
                "decision_index": 1,
                "diagnostic_class": "tactic_failure",
                "selected_operator": "repair_local_rewrite",
                "ablation_repair_off": False,
                "features": {
                    "attempt_index": 1,
                    "budget_remaining_steps": 2,
                    "goals_estimate": 1,
                    "goal_complexity_estimate": 1.0,
                    "prior_failures": 0,
                    "fallback_used_previous": False,
                },
            }
        ],
    )
    report = module.evaluate(log_path=log_path, schema_path=schema_path)
    assert report["ok"] is True
    assert report["error_count"] == 0


def test_check_atp_repair_policy_log_v1_fails_invalid_row(tmp_path: Path) -> None:
    module = _load_module("check_atp_repair_policy_log_v1_fail")
    schema_path = _repo_root() / "docs" / "contracts" / "atp" / "schemas" / "atp_repair_policy_decision_v1.schema.json"
    log_path = _write_json(
        tmp_path / "repair_policy_log.json",
        [
            {
                "schema_version": "atp_repair_policy_decision_v1",
                "loop_id": "loop",
                "node_id": "n1",
                "decision_index": 0,
                "diagnostic_class": "bad",
                "selected_operator": "repair_local_rewrite",
                "ablation_repair_off": False,
                "features": {
                    "attempt_index": 0,
                    "budget_remaining_steps": 2,
                    "goals_estimate": 1,
                    "goal_complexity_estimate": 1.0,
                    "prior_failures": 0,
                    "fallback_used_previous": False,
                },
            }
        ],
    )
    report = module.evaluate(log_path=log_path, schema_path=schema_path)
    assert report["ok"] is False
    assert report["error_count"] >= 1
