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


def test_atp_contract_governance_passes_with_acr_and_adr(tmp_path: Path) -> None:
    mod = _load_module("check_atp_contract_governance", "scripts/check_atp_contract_governance.py")
    template = tmp_path / "PULL_REQUEST_TEMPLATE.md"
    template.write_text("ATP_CONTRACT_BREAK_CHECKLIST_V1.md\n", encoding="utf-8")
    report = mod.evaluate(
        changed_files=[
            "docs/contracts/atp/schemas/retrieval_adapter_request_v1.schema.json",
            "docs/contracts/policies/acr/ACR-20260223-atp-contract.md",
            "docs/contracts/policies/adr/ADR-20260223-atp-contract.md",
        ],
        pr_template_path=template,
    )
    assert report["ok"] is True


def test_atp_contract_governance_fails_without_decision_docs(tmp_path: Path) -> None:
    mod = _load_module("check_atp_contract_governance_fail", "scripts/check_atp_contract_governance.py")
    template = tmp_path / "PULL_REQUEST_TEMPLATE.md"
    template.write_text("ATP_CONTRACT_BREAK_CHECKLIST_V1.md\n", encoding="utf-8")
    report = mod.evaluate(
        changed_files=["docs/contracts/atp/schemas/retrieval_adapter_request_v1.schema.json"],
        pr_template_path=template,
    )
    assert report["ok"] is False
    assert "missing_acr_for_atp_contract_change" in report["failures"]
    assert "missing_adr_for_atp_contract_change" in report["failures"]


def test_atp_contract_governance_fails_without_template_binding(tmp_path: Path) -> None:
    mod = _load_module("check_atp_contract_governance_template", "scripts/check_atp_contract_governance.py")
    template = tmp_path / "PULL_REQUEST_TEMPLATE.md"
    template.write_text("no atp checklist binding\n", encoding="utf-8")
    report = mod.evaluate(
        changed_files=[
            "docs/contracts/atp/schemas/retrieval_adapter_request_v1.schema.json",
            "docs/contracts/policies/acr/ACR-20260223-atp-contract.md",
            "docs/contracts/policies/adr/ADR-20260223-atp-contract.md",
        ],
        pr_template_path=template,
    )
    assert report["ok"] is False
    assert "pr_template_missing_atp_contract_break_checklist_binding" in report["failures"]
