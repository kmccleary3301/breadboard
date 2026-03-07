from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest


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


def test_aristotle_compliance_gate_passes_on_internal_approved_scope(tmp_path: Path) -> None:
    mod = _load_module("aristotle_compliance_gate_ok", "scripts/_aristotle_compliance_gate.py")
    payload = {
        "status": "approved_internal_only",
        "permissions": {
            "benchmark_evaluation_public_datasets": {"approved": True},
            "store_outputs_for_reproducibility": {"approved": True},
            "internal_comparative_reporting": {"approved": True},
            "external_aggregate_reporting": {"approved": False},
        },
        "enforcement": {"fail_closed_until_explicit_approval": True},
    }
    scope_path = tmp_path / "scope.json"
    scope_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    report = mod.assert_aristotle_compliance(scope_path=scope_path)
    assert report["ok"] is True
    assert report["status"] == "approved_internal_only"


def test_aristotle_compliance_gate_passes_on_operator_override_scope(tmp_path: Path) -> None:
    mod = _load_module("aristotle_compliance_gate_override", "scripts/_aristotle_compliance_gate.py")
    payload = {
        "status": "operator_override_internal_only",
        "permissions": {
            "benchmark_evaluation_public_datasets": {"approved": True},
            "store_outputs_for_reproducibility": {"approved": True},
            "internal_comparative_reporting": {"approved": True},
        },
        "enforcement": {"fail_closed_until_explicit_approval": True},
    }
    scope_path = tmp_path / "scope.json"
    scope_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    report = mod.assert_aristotle_compliance(scope_path=scope_path)
    assert report["ok"] is True
    assert report["status"] == "operator_override_internal_only"


def test_aristotle_compliance_gate_fails_when_pending_or_missing_permissions(tmp_path: Path) -> None:
    mod = _load_module("aristotle_compliance_gate_fail", "scripts/_aristotle_compliance_gate.py")
    payload = {
        "status": "pending",
        "permissions": {
            "benchmark_evaluation_public_datasets": {"approved": True},
            "store_outputs_for_reproducibility": {"approved": False},
            "internal_comparative_reporting": {"approved": False},
        },
        "enforcement": {"fail_closed_until_explicit_approval": True},
    }
    scope_path = tmp_path / "scope.json"
    scope_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    with pytest.raises(mod.AristotleComplianceError):
        mod.assert_aristotle_compliance(scope_path=scope_path)
