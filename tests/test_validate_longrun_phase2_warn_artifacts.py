from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest


def _load_module():
    repo_root = Path(__file__).resolve().parents[1]
    module_path = repo_root / "scripts" / "validate_longrun_phase2_warn_artifacts.py"
    scripts_dir = str((repo_root / "scripts").resolve())
    if scripts_dir not in sys.path:
        sys.path.insert(0, scripts_dir)
    spec = importlib.util.spec_from_file_location("validate_longrun_phase2_warn_artifacts", module_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_validate_payload_accepts_expected_shape(tmp_path: Path) -> None:
    module = _load_module()
    payload = {
        "schema_version": "longrun_phase1_pilot_v1",
        "runs_per_scenario": 5,
        "scenarios": [
            {"scenario_id": f"s{i}", "baseline": {"summary": {}}, "longrun": {"summary": {}}}
            for i in range(6)
        ],
    }
    target = tmp_path / "pilot.json"
    target.write_text(json.dumps(payload), encoding="utf-8")
    out = module.validate_payload(target)
    assert out["ok"] is True
    assert out["scenario_count"] == 6


def test_validate_payload_rejects_underfilled_matrix(tmp_path: Path) -> None:
    module = _load_module()
    payload = {
        "schema_version": "longrun_phase1_pilot_v1",
        "runs_per_scenario": 5,
        "scenarios": [{"scenario_id": "only_one", "baseline": {"summary": {}}, "longrun": {"summary": {}}}],
    }
    target = tmp_path / "pilot.json"
    target.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ValueError, match="at least 6 scenarios"):
        module.validate_payload(target)
