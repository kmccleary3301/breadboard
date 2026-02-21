from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path


def _load_module():
    repo_root = Path(__file__).resolve().parents[1]
    module_path = repo_root / "scripts" / "validate_longrun_phase1_scenario_contract.py"
    scripts_dir = str((repo_root / "scripts").resolve())
    if scripts_dir not in sys.path:
        sys.path.insert(0, scripts_dir)
    spec = importlib.util.spec_from_file_location("validate_longrun_phase1_scenario_contract", module_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _sample_contract() -> dict:
    return {
        "contract_version": "longrun_phase1_scenario_contract_v1",
        "pilot_schema_version": "longrun_phase1_pilot_v1",
        "allowed_stop_classes": ["episode_completed", "max_episodes_reached"],
        "required_assertions": [
            "scenario_count_matches_contract",
            "required_scenarios_present",
            "summary_fields_present",
            "stop_reasons_classified",
        ],
        "required_scenarios": [
            {
                "id": "single_success",
                "expected_stop_classes": {
                    "baseline": ["episode_completed"],
                    "longrun": ["episode_completed"],
                },
            }
        ],
    }


def _sample_pilot() -> dict:
    return {
        "schema_version": "longrun_phase1_pilot_v1",
        "runs_per_scenario": 5,
        "scenarios": [
            {
                "scenario_id": "single_success",
                "baseline": {
                    "summary": {
                        "run_count": 5,
                        "success_rate": 1.0,
                        "median_episodes": 1,
                        "stop_reasons": {"episode_completed": 5},
                    }
                },
                "longrun": {
                    "summary": {
                        "run_count": 5,
                        "success_rate": 1.0,
                        "median_episodes": 1,
                        "stop_reasons": {"episode_completed": 5},
                    }
                },
            }
        ],
    }


def test_validate_contract_accepts_expected_shape(tmp_path: Path) -> None:
    module = _load_module()
    pilot_path = tmp_path / "pilot.json"
    contract_path = tmp_path / "contract.json"
    pilot_path.write_text(json.dumps(_sample_pilot()), encoding="utf-8")
    contract_path.write_text(json.dumps(_sample_contract()), encoding="utf-8")
    report = module.validate_contract(
        pilot_payload=json.loads(pilot_path.read_text(encoding="utf-8")),
        contract_payload=json.loads(contract_path.read_text(encoding="utf-8")),
    )
    assert report["ok"] is True
    assert report["schema_version"] == "longrun_phase1_scenario_contract_report_v1"


def test_validate_contract_rejects_unknown_stop_reason_class(tmp_path: Path) -> None:
    module = _load_module()
    pilot = _sample_pilot()
    pilot["scenarios"][0]["longrun"]["summary"]["stop_reasons"] = {"no_progress_signature_repeated": 5}
    report = module.validate_contract(pilot_payload=pilot, contract_payload=_sample_contract())
    assert report["ok"] is False
    checks = report.get("checks") or []
    class_check = [row for row in checks if row.get("name") == "stop_reasons_classified"]
    assert class_check and class_check[0]["passed"] is False
