from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path

import pytest
from scripts.e4_parity import validate_frozen_e4_evidence as validator


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts/e4_parity/validate_frozen_e4_evidence.py"
FROZEN_REPORTS = [
    ROOT
    / "docs/conformance/e4_target_support/oh_my_pi_p3_7_memory_work_compiler/frozen_c4_validation_report.json",
    ROOT
    / "docs/conformance/e4_target_support/pi_p5_l2_extension_session_residual/frozen_c4_validation_report.json",
]


@pytest.mark.parametrize("validation_report", FROZEN_REPORTS)
def test_frozen_evidence_validator_accepts_hash_bound_retired_chain(
    validation_report: Path,
    tmp_path: Path,
) -> None:
    output = tmp_path / f"{validation_report.parent.name}.json"

    completed = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--validation-report",
            validation_report.relative_to(ROOT).as_posix(),
            "--json-out",
            str(output),
        ],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr or completed.stdout
    report = json.loads(output.read_text(encoding="utf-8"))
    assert report["ok"] is True
    assert report["accepted"] is True
    assert report["errors"] == []
    assert set(report["validated_paths"]) == {"support_claim", "evidence_manifest"}


def test_frozen_evidence_validator_rejects_tampered_manifest_artifact(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(validator, "ROOT", tmp_path)
    monkeypatch.setattr(validator, "WORKSPACE", tmp_path.parent)

    def write_json(relative: str, payload: object) -> Path:
        path = tmp_path / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        return path

    def sha256(path: Path) -> str:
        return f"sha256:{hashlib.sha256(path.read_bytes()).hexdigest()}"

    scope = {"config_id": "retired_v1", "lane_id": "retired", "provider_model": "none"}
    frozen_artifact = write_json("evidence/payload.json", {"result": "accepted"})
    freeze_row = {"config_path": "agent_configs/retired.yaml", "harness": {"family": "fixture"}}
    freeze_manifest = write_json(
        "evidence/freeze.yaml",
        {"schema_version": "fixture", "e4_configs": {"retired_v1": freeze_row}},
    )
    ledger_row = {
        "feature_id": "feat_retired",
        "e4_row_ref": "retired_v1",
        "promotion_state": "ready",
    }
    ledger = write_json("evidence/ledger.json", {"rows": [ledger_row]})
    support_claim = write_json(
        "evidence/support_claim.json",
        {
            "accepted": True,
            "claim_id": "retired_claim",
            "scope": scope,
            "evidence_manifest_ref": "evidence/manifest.json",
            "freeze_ref": (
                "evidence/freeze.yaml#retired_v1#"
                f"{validator._row_hash('retired_v1', freeze_row)}"
            ),
            "ledger_row_refs": [
                "evidence/ledger.json#feat_retired#"
                f"{validator._row_hash('feat_retired', ledger_row)}"
            ],
        },
    )
    evidence_manifest = write_json(
        "evidence/manifest.json",
        {
            "claim_id": "retired_claim",
            "artifacts": [
                {
                    "path": "evidence/freeze.yaml",
                    "role": "freeze_manifest",
                    "sha256": sha256(freeze_manifest),
                },
                {
                    "path": "evidence/payload.json",
                    "role": "capture_ref",
                    "sha256": sha256(frozen_artifact),
                },
                {
                    "path": "evidence/support_claim.json",
                    "role": "support_claim_ref",
                    "sha256": sha256(support_claim),
                },
            ],
        },
    )
    validation_report = write_json(
        "evidence/validation_report.json",
        {
            "accepted": True,
            "ok": True,
            "claimed_scope": scope,
            "support_claim": "evidence/support_claim.json",
            "evidence_manifest": "evidence/manifest.json",
            "refs": {
                "support_claim": "evidence/support_claim.json",
                "evidence_manifest": "evidence/manifest.json",
                "freeze_manifest": "evidence/freeze.yaml",
            },
            "hashes": {
                "support_claim": sha256(support_claim),
                "evidence_manifest": sha256(evidence_manifest),
                "freeze_manifest": sha256(freeze_manifest),
                "freeze_manifest_row": validator._row_hash("retired_v1", freeze_row),
            },
        },
    )

    assert validator.validate(validation_report)["ok"] is True
    write_json("evidence/payload.json", {"result": "tampered"})

    result = validator.validate(validation_report)

    assert result["ok"] is False
    assert result["errors"] == [
        "frozen evidence manifest artifact hash mismatch: evidence/payload.json"
    ]
