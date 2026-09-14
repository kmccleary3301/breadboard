from __future__ import annotations

import importlib.util
import sys
from pathlib import Path


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _load_module(module_name: str, rel_path: str):
    module_path = _repo_root() / rel_path
    scripts_dir = str((_repo_root() / "scripts").resolve())
    if scripts_dir not in sys.path:
        sys.path.insert(0, scripts_dir)
    spec = importlib.util.spec_from_file_location(module_name, module_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


def test_atp_ops_digest_reports_incident_when_required_assets_missing(tmp_path: Path):
    module = _load_module("atp_ops_digest", "scripts/atp_ops_digest.py")
    digest = module.build_digest(tmp_path)
    assert digest["producer_mode"] == "bootstrap_structural"
    assert digest["overall_ok"] is False
    assert digest["decision_state"] == "incident"
    assert digest["missing_count"] >= 1


