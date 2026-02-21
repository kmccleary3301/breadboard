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


def test_evolake_nightly_reports_failed_preflight_when_assets_missing(tmp_path: Path):
    module = _load_module("evolake_toy_campaign_nightly", "scripts/evolake_toy_campaign_nightly.py")
    report = module.build_report(tmp_path)
    assert report["producer_mode"] == "bootstrap_structural"
    assert report["ok"] is False
    assert report["classification"] == "failed_preflight"
    assert report["runs_failed"] == report["runs_requested"]


def test_producer_scripts_turn_green_when_expected_paths_exist(tmp_path: Path):
    atp_module = _load_module("atp_ops_digest", "scripts/atp_ops_digest.py")
    evo_module = _load_module("evolake_toy_campaign_nightly", "scripts/evolake_toy_campaign_nightly.py")

    (tmp_path / "scripts").mkdir(parents=True, exist_ok=True)
    (tmp_path / "config").mkdir(parents=True, exist_ok=True)
    (tmp_path / ".github" / "workflows").mkdir(parents=True, exist_ok=True)
    (tmp_path / "breadboard_ext" / "evolake").mkdir(parents=True, exist_ok=True)

    for path in [
        tmp_path / "scripts" / "atp_firecracker_ci.sh",
        tmp_path / "scripts" / "atp_firecracker_repl_load_nightly.sh",
        tmp_path / "scripts" / "atp_snapshot_pool_stability.py",
        tmp_path / "scripts" / "atp_state_ref_regression_recovery.py",
        tmp_path / "config" / "atp_threshold_policy.json",
        tmp_path / "breadboard_ext" / "evolake" / "bridge.py",
        tmp_path / "breadboard_ext" / "evolake" / "tools.py",
        tmp_path / ".github" / "workflows" / "evolake_toy_campaign_nightly.yml",
    ]:
        path.write_text("ok", encoding="utf-8")

    digest = atp_module.build_digest(tmp_path)
    report = evo_module.build_report(tmp_path)
    assert digest["producer_mode"] == "bootstrap_structural"
    assert report["producer_mode"] == "bootstrap_structural"
    assert digest["overall_ok"] is True
    assert digest["decision_state"] == "green"
    assert report["ok"] is True
    assert report["classification"] == "stable_pass"
