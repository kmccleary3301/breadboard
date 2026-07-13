from __future__ import annotations

from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[3]
WORKSPACE_ROOT = REPO_ROOT.parent
DOCS = REPO_ROOT / "docs" / "rl_phase1"
PHASE_DIR = WORKSPACE_ROOT / "docs_tmp" / "ZYPHRA" / "RL_PHASE_1"


def test_m10_operator_docs_exist() -> None:
    expected = [
        "README.md",
        "env_package_ir.md",
        "swe_hardening.md",
        "verl_export_contract.md",
        "runtime_pool_runbook.md",
        "replay_admission.md",
        "support_ladder.md",
        "manual_qc_guide.md",
        "decision_ledger.md",
        "demo_script.md",
        "m12_transfer_pack.md",
    ]

    for name in expected:
        assert (DOCS / name).exists(), name


def test_docs_preserve_claim_boundary() -> None:
    combined = "\n".join(path.read_text(encoding="utf-8") for path in DOCS.glob("*.md"))

    assert "controlled SWE toy" in combined
    assert "not production" in combined.lower() or "no production" in combined.lower()
    assert "not DataProto" in combined
    assert "MI300X" in combined


def test_handoff_names_critical_files_and_next_work() -> None:
    handoff = PHASE_DIR / "BB_ZYPHRA_RL_PHASE_1_HANDOFF.md"
    text = handoff.read_text(encoding="utf-8")

    assert "Current verified score: 1000 / 1000" in text
    assert "breadboard/rl/env_package/" in text
    assert "breadboard/rl/security/" in text
    assert "docs_tmp/ZYPHRA/RL_PHASE_1/runs/" in text
    assert "M12 target validation has passed" in text
