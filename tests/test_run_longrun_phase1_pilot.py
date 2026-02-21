from __future__ import annotations

import importlib.util
import sys
from pathlib import Path


def _load_module():
    repo_root = Path(__file__).resolve().parents[1]
    module_path = repo_root / "scripts" / "run_longrun_phase1_pilot.py"
    scripts_dir = str((repo_root / "scripts").resolve())
    if scripts_dir not in sys.path:
        sys.path.insert(0, scripts_dir)
    spec = importlib.util.spec_from_file_location("run_longrun_phase1_pilot", module_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_build_scenarios_meets_minimum_matrix_size() -> None:
    module = _load_module()
    scenarios = module.build_scenarios()
    assert len(scenarios) >= 6
    assert all(int(s.runs) == 5 for s in scenarios)


def test_run_pilot_emits_baseline_arm_and_scenarios() -> None:
    module = _load_module()
    payload = module.run_pilot()
    assert payload["schema_version"] == "longrun_phase1_pilot_v1"
    assert payload["baseline_arm"] == "more_steps_only"
    assert int(payload["runs_per_scenario"]) == 5
    assert len(payload["scenarios"]) >= 6
