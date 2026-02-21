from __future__ import annotations

import importlib.util
import sys
from pathlib import Path


def _load_module():
    repo_root = Path(__file__).resolve().parents[1]
    module_path = repo_root / "scripts" / "render_longrun_phase2_trend_summary.py"
    scripts_dir = str((repo_root / "scripts").resolve())
    if scripts_dir not in sys.path:
        sys.path.insert(0, scripts_dir)
    spec = importlib.util.spec_from_file_location("render_longrun_phase2_trend_summary", module_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_render_markdown_contains_summary_and_table() -> None:
    module = _load_module()
    history = [
        {
            "ts_unix": 1700000000,
            "report_ok": True,
            "parity_audit_ok": True,
            "boundedness_required_ok": True,
            "stable_success_path_ok": True,
            "no_success_regression_ok": True,
            "github_ref": "main",
            "github_sha": "abcdef1234567890",
        }
    ]
    md = module.render_markdown(history)
    assert "LongRun Phase2 Strict Gate Trend" in md
    assert "Pass rate" in md
    assert "| Time (UTC) | Report OK |" in md
    assert "main" in md
