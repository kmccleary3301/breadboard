from __future__ import annotations

import importlib.util
import sys
from pathlib import Path


def _load_module():
    repo_root = Path(__file__).resolve().parents[1]
    module_path = repo_root / "scripts" / "update_longrun_phase2_trend_history.py"
    scripts_dir = str((repo_root / "scripts").resolve())
    if scripts_dir not in sys.path:
        sys.path.insert(0, scripts_dir)
    spec = importlib.util.spec_from_file_location("update_longrun_phase2_trend_history", module_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_update_history_appends_entry_and_caps_length() -> None:
    module = _load_module()
    report = {
        "schema_version": "longrun_phase2_go_no_go_v1",
        "ok": True,
        "checks": [
            {"name": "scenario_count>=6", "passed": True},
            {"name": "runs_per_scenario>=5", "passed": True},
            {"name": "no_success_regression", "passed": True},
            {"name": "boundedness_improvement_required_set", "passed": True},
            {"name": "stable_success_path", "passed": True},
        ],
    }
    parity = {"ok": True, "checked": 13, "failures": []}
    history = [{"ts_unix": 1, "report_ok": True}, {"ts_unix": 2, "report_ok": False}]
    out = module.update_history(report, parity, history, max_entries=2)
    assert len(out) == 2
    assert out[-1]["report_ok"] is True
    assert out[-1]["parity_checked"] == 13
