from __future__ import annotations

import importlib.util
import sys
from pathlib import Path


def _load_module():
    repo_root = Path(__file__).resolve().parents[1]
    module_path = repo_root / "scripts" / "audit_longrun_parity_disabled.py"
    scripts_dir = str((repo_root / "scripts").resolve())
    if scripts_dir not in sys.path:
        sys.path.insert(0, scripts_dir)
    spec = importlib.util.spec_from_file_location("audit_longrun_parity_disabled", module_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_audit_configs_detects_enabled_longrun(tmp_path):
    module = _load_module()
    cfg_root = tmp_path / "agent_configs"
    cfg_root.mkdir(parents=True)
    bad = cfg_root / "sample_e4.yaml"
    bad.write_text("version: 1\nlong_running:\n  enabled: true\n", encoding="utf-8")

    payload = module.audit_configs([bad], tmp_path)
    assert payload["ok"] is False
    assert payload["checked"] == 1
    assert payload["failures"][0]["reason"] == "long_running.enabled=true"


def test_repo_parity_audit_passes():
    module = _load_module()
    repo_root = Path(__file__).resolve().parents[1]
    paths = module.discover_parity_configs(repo_root)
    assert paths, "expected at least one parity/E4 config to be discovered"
    payload = module.audit_configs(paths, repo_root)
    assert payload["ok"] is True


def test_audit_separates_archives_and_target_projections_without_hiding_load_errors(tmp_path):
    module = _load_module()
    configs = tmp_path / "agent_configs"
    archive = configs / "deprecated"
    archive.mkdir(parents=True)
    (archive / "old_e4.yaml").write_text("extends: missing.yaml\n", encoding="utf-8")
    (configs / "target_e4.yaml").write_text(
        "schema_version: bb.e4.target_config.v1\ntarget_id: example@1\n",
        encoding="utf-8",
    )
    invalid = configs / "broken_e4.yaml"
    invalid.write_text("extends: missing.yaml\n", encoding="utf-8")

    payload = module.audit_configs(module.discover_parity_configs(tmp_path), tmp_path)

    assert payload["ok"] is False
    assert payload["checked"] == 1
    assert payload["failures"][0]["config"] == "agent_configs/broken_e4.yaml"
    assert payload["failures"][0]["reason"] == "load_error"
    assert payload["skipped"] == [
        {"config": "agent_configs/target_e4.yaml", "reason": "target_config_projection"}
    ]
