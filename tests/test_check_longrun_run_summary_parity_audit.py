from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest


def _load_module():
    repo_root = Path(__file__).resolve().parents[1]
    module_path = repo_root / "scripts" / "check_longrun_run_summary_parity_audit.py"
    scripts_dir = str((repo_root / "scripts").resolve())
    if scripts_dir not in sys.path:
        sys.path.insert(0, scripts_dir)
    spec = importlib.util.spec_from_file_location("check_longrun_run_summary_parity_audit", module_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_validate_run_summary_accepts_longrun_parity_block(tmp_path: Path) -> None:
    module = _load_module()
    summary = tmp_path / "run_summary.json"
    summary.write_text(
        """
{
  "longrun": {
    "parity_audit": {
      "schema_version": "longrun_parity_audit_v1",
      "enabled": false,
      "effective_config_hash": "abc",
      "episodes_run": 0,
      "artifact_list": []
    }
  }
}
        """.strip(),
        encoding="utf-8",
    )
    out = module.validate_run_summary(summary)
    assert out["ok"] is True


def test_validate_run_summary_rejects_missing_parity_block(tmp_path: Path) -> None:
    module = _load_module()
    summary = tmp_path / "run_summary.json"
    summary.write_text('{"longrun":{}}', encoding="utf-8")
    with pytest.raises(ValueError, match="parity_audit"):
        module.validate_run_summary(summary)
