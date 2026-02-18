from __future__ import annotations

import importlib.util
import sys
from pathlib import Path


def _load_module():
    repo_root = Path(__file__).resolve().parents[1]
    module_path = repo_root / "scripts" / "verify_phase4_gate_run_for_sha.py"
    scripts_dir = str((repo_root / "scripts").resolve())
    if scripts_dir not in sys.path:
        sys.path.insert(0, scripts_dir)
    spec = importlib.util.spec_from_file_location("verify_phase4_gate_run_for_sha", module_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_select_latest_successful_run_returns_newest_success():
    module = _load_module()
    payload = {
        "workflow_runs": [
            {"id": 1, "conclusion": "failure", "updated_at": "2026-02-18T01:00:00Z"},
            {"id": 2, "conclusion": "success", "updated_at": "2026-02-18T01:01:00Z"},
            {"id": 3, "conclusion": "success", "updated_at": "2026-02-18T01:03:00Z"},
        ]
    }
    best = module.select_latest_successful_run(payload)
    assert best is not None
    assert best["id"] == 3


def test_select_latest_successful_run_returns_none_when_no_success():
    module = _load_module()
    payload = {
        "workflow_runs": [
            {"id": 1, "conclusion": "failure", "updated_at": "2026-02-18T01:00:00Z"},
            {"id": 2, "conclusion": "cancelled", "updated_at": "2026-02-18T01:01:00Z"},
        ]
    }
    assert module.select_latest_successful_run(payload) is None
