from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from scripts.e4_parity import generate_e4_coverage as generator
from scripts.e4_parity import validate_e4_coverage as validator


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _accepted_claim(claim_id: str, family: str, behavior_id: str = "config_graph_primary") -> dict[str, Any]:
    return {
        "schema_version": "bb.e4.support_claim.v2",
        "claim_id": claim_id,
        "accepted": True,
        "target_family": family,
        "target_version": "target@example",
        "claim_semantics": {
            "asserted_behaviors": [
                {
                    "behavior_id": behavior_id,
                    "description": "Registered target behavior.",
                }
            ]
        },
    }


def _registry() -> dict[str, Any]:
    return {
        "schema_version": "bb.registry.v1",
        "registry_id": "target_families",
        "entries": [
            {
                "value": "pi",
                "metadata": {
                    "coverage_unclaimed_status": "out_of_scope",
                },
            },
            {
                "value": "claude_code",
                "metadata": {
                    "coverage_unclaimed_status": "not_started",
                },
            },
        ],
    }


def _coverage_rows(status: str = "not_started") -> list[dict[str, Any]]:
    return [
        {
            "behavior_family": family,
            "behavior_id": f"pi_{family}_unclaimed",
            "description": f"No accepted v2 C4 claim currently covers {family} for pi.",
            "status": status,
            "claim_refs": [],
        }
        for family in validator.load_json(validator.SCHEMA_PATH)["properties"]["rows"]["items"]["properties"]["behavior_family"]["enum"]
    ]


def test_generate_coverage_uses_target_family_registry_and_metadata(monkeypatch, tmp_path: Path) -> None:
    support_claims = tmp_path / "support_claims"
    coverage_dir = tmp_path / "coverage"
    _write_json(support_claims / "pi_support_claim.json", _accepted_claim("pi_claim", "pi"))

    monkeypatch.setattr(generator, "SUPPORT_CLAIMS_DIR", support_claims)
    monkeypatch.setattr(generator, "COVERAGE_DIR", coverage_dir)
    monkeypatch.setattr(generator, "_load_target_families_registry", _registry)

    report = generator.generate()

    assert report["ok"] is True
    assert sorted(path.name for path in coverage_dir.glob("*_target_coverage.json")) == [
        "claude_code_target_coverage.json",
        "pi_target_coverage.json",
    ]
    pi_payload = json.loads((coverage_dir / "pi_target_coverage.json").read_text(encoding="utf-8"))
    claude_payload = json.loads((coverage_dir / "claude_code_target_coverage.json").read_text(encoding="utf-8"))
    assert pi_payload["schema_version"] == "bb.e4.target_coverage.v2"
    assert pi_payload["target_family"] == "pi"
    assert any(row["status"] == "c4_accepted" and row["behavior_id"] == "config_graph_primary" for row in pi_payload["rows"])
    assert {row["status"] for row in claude_payload["rows"]} == {"not_started"}


def test_generate_coverage_rejects_accepted_claim_for_unregistered_family(monkeypatch, tmp_path: Path) -> None:
    support_claims = tmp_path / "support_claims"
    coverage_dir = tmp_path / "coverage"
    _write_json(support_claims / "rogue_support_claim.json", _accepted_claim("rogue_claim", "rogue_family"))

    monkeypatch.setattr(generator, "SUPPORT_CLAIMS_DIR", support_claims)
    monkeypatch.setattr(generator, "COVERAGE_DIR", coverage_dir)
    monkeypatch.setattr(generator, "_load_target_families_registry", _registry)

    report = generator.generate()

    assert report["ok"] is False
    assert report["errors"] == [f"{support_claims / 'rogue_support_claim.json'}: unregistered target_family 'rogue_family'"]


def test_validate_coverage_rejects_unregistered_target_family(monkeypatch, tmp_path: Path) -> None:
    coverage_path = tmp_path / "rogue_target_coverage.json"
    _write_json(
        coverage_path,
        {
            "schema_version": "bb.e4.target_coverage.v2",
            "target_family": "rogue_family",
            "frozen_target_versions": [],
            "generated_at_utc": "2026-07-03T00:00:00Z",
            "rows": _coverage_rows(),
        },
    )

    monkeypatch.setattr(validator, "SUPPORT_CLAIMS_DIR", tmp_path / "support_claims")
    monkeypatch.setattr(validator, "_load_target_families_registry", _registry)

    report = validator.validate([coverage_path])

    assert report["ok"] is False
    assert f"{coverage_path}: unregistered target_family 'rogue_family'" in report["errors"]


def test_validate_coverage_accepts_registered_family(monkeypatch, tmp_path: Path) -> None:
    coverage_path = tmp_path / "pi_target_coverage.json"
    _write_json(
        coverage_path,
        {
            "schema_version": "bb.e4.target_coverage.v2",
            "target_family": "pi",
            "frozen_target_versions": [],
            "generated_at_utc": "2026-07-03T00:00:00Z",
            "rows": _coverage_rows("out_of_scope"),
        },
    )

    monkeypatch.setattr(validator, "SUPPORT_CLAIMS_DIR", tmp_path / "support_claims")
    monkeypatch.setattr(validator, "_load_target_families_registry", lambda: {"entries": [{"value": "pi"}]})

    report = validator.validate([coverage_path])

    assert report["schema_version"] == "bb.e4.coverage_validation_report.v1"
    assert report["file_count"] == 1
    assert report["ok"] is True
    assert report["errors"] == []
