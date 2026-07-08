from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

import pytest

from agentic_coder_prototype.conformance import c4_chain
from agentic_coder_prototype.conformance.c4_chain import validate_c4_chain
from agentic_coder_prototype.conformance.catalog_binding import CATALOG_PATH, stable_entries_hash
from scripts.e4_parity.validators import registries


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _write_registry(repo_root: Path) -> None:
    _write_json(
        repo_root / "contracts/kernel/registries/target_families.v1.json",
        {
            "schema_version": "bb.registry.v1",
            "registry_id": "target_families",
            "revision": 1,
            "generated_at_utc": "2026-07-06T00:00:00Z",
            "entries": [
                {"id": "oh_my_pi", "status": "active", "description": "Oh My Pi"},
                {"id": "codex", "status": "active", "description": "Codex"},
            ],
        },
    )


def _catalog() -> dict[str, Any]:
    entries = [
        {
            "role_id": "lane_alpha:capture",
            "path": "docs/conformance/e4_target_support/lane_alpha/raw_capture_manifest.json",
            "sha256": "sha256:" + "5" * 64,
            "bytes": 2,
            "exists": True,
            "artifact_kind": "capture",
            "lane_id": "lane_alpha",
            "media_type": "application/json",
            "derived_from": [],
            "generated_by": "fixture",
        },
        {
            "role_id": "e4_static:config/e4_lane_inventory",
            "path": "docs/conformance/e4_lane_inventory.json",
            "sha256": "sha256:" + "a" * 64,
            "bytes": 2,
            "exists": True,
            "artifact_kind": "lane_inventory",
            "media_type": "application/json",
            "derived_from": [],
            "generated_by": "fixture",
        },
    ]
    return {
        "schema_version": "bb.e4.artifact_catalog.v1",
        "catalog_id": "e4_artifact_catalog_v1",
        "generated_at_utc": "2026-07-03T00:00:00Z",
        "revision": 1,
        "entries": entries,
        "integrity": {
            "entry_count": len(entries),
            "entries_hash": "sha256:" + "3" * 64,
            "stable_entries_hash": stable_entries_hash(entries),
        },
    }


def _base_claim(catalog: dict[str, Any], *, schema_version: str = "bb.e4.support_claim.v3") -> dict[str, Any]:
    scope = {
        "lane_id": "lane_alpha",
        "config_id": "config_alpha",
        "run_id": "run_alpha",
        "target_version": "target-alpha",
        "provider_model": "provider-alpha",
        "sandbox_mode": "sandbox-alpha",
    }
    if schema_version == "bb.e4.support_claim.v3":
        catalog_binding = {
            "catalog_path": CATALOG_PATH,
            "segment_id": "lane_alpha",
            "segment_hash": stable_entries_hash([entry for entry in catalog["entries"] if entry.get("lane_id") == "lane_alpha"]),
            "shared_segment_hash": stable_entries_hash([entry for entry in catalog["entries"] if not entry.get("lane_id")]),
        }
    else:
        catalog_binding = {
            "catalog_path": CATALOG_PATH,
            "catalog_revision": catalog["revision"],
            "catalog_hash": catalog["integrity"]["stable_entries_hash"],
        }
    return {
        "schema_version": schema_version,
        "claim_id": "claim_alpha",
        "config_id": "config_alpha",
        "lane_id": "lane_alpha",
        "kind": "target_support",
        "accepted": True,
        "summary": "Fixture support claim.",
        "target_family": "oh_my_pi",
        "target_version": "target-alpha",
        "run_id": "run_alpha",
        "provider_model": "provider-alpha",
        "sandbox_mode": "sandbox-alpha",
        "scope": scope,
        "exclusions": ["No broad target parity claim is made."],
        "exclusion_facets": {
            "excluded_families": ["all_other_families"],
            "excluded_lanes": [],
            "excluded_sandbox_modes": [],
            "excluded_provider_modes": [],
            "excluded_behavior_classes": ["broad_target_parity"],
        },
        "claim_semantics": {
            "asserted_behaviors": [
                {
                    "behavior_id": "behavior_alpha",
                    "description": "scope is bound",
                    "comparator_assertion_ids": ["scope_match"],
                }
            ]
        },
        "freeze_ref": "config/e4_target_freeze_manifest.yaml#config_alpha#sha256:" + "4" * 64,
        "capture_ref": "docs/conformance/e4_target_support/lane_alpha/raw_capture_manifest.json#sha256:" + "5" * 64,
        "replay_ref": "docs/conformance/e4_target_support/lane_alpha/bb_replay_result.json#sha256:" + "6" * 64,
        "comparator_ref": "docs/conformance/e4_target_support/lane_alpha/comparator_report.json#sha256:" + "7" * 64,
        "evidence_manifest_ref": "docs/conformance/support_claims/lane_alpha_v1_c4_evidence_manifest.json",
        "validation_refs": ["artifacts/conformance/node_gate/ct_lane_alpha_c4_chain.json#sha256:" + "8" * 64],
        "ledger_row_refs": ["docs_tmp/phase_15/BB_E4_ATOMIC_FEATURE_LEDGER_SEED.json#feat_alpha#sha256:" + "9" * 64],
        "catalog_binding": catalog_binding,
        "reverify_command": {"argv": ["python", "scripts/validate_e4_c4_chain.py", "--check-only", "claim_alpha"], "cwd": "."},
        "generated_at_utc": "2026-07-03T00:00:00Z",
    }


def _base_v4_claim(catalog: dict[str, Any]) -> dict[str, Any]:
    scope = {
        "lane_id": "lane_alpha",
        "config_id": "config_alpha",
        "run_id": "run_alpha",
        "target_family": "oh_my_pi",
        "target_version": "target-alpha",
        "provider_model": "provider-alpha",
        "sandbox_mode": "sandbox-alpha",
    }
    return {
        "schema_version": "bb.e4.support_claim.v4",
        "claim_id": "claim_alpha",
        "kind": "target_support",
        "accepted": True,
        "summary": "Fixture support claim.",
        "scope": scope,
        "exclusions": ["No broad target parity claim is made."],
        "exclusion_facets": {
            "excluded_families": ["all_other_families", "future_agent_family"],
            "excluded_lanes": [],
            "excluded_sandbox_modes": [],
            "excluded_provider_modes": [],
            "excluded_behavior_classes": ["broad_target_parity"],
        },
        "claim_semantics": {
            "asserted_behaviors": [
                {
                    "behavior_id": "behavior_alpha",
                    "description": "scope is bound",
                    "comparator_assertion_ids": ["scope_match"],
                }
            ]
        },
        "freeze_ref": "config/e4_target_freeze_manifest.yaml#config_alpha#sha256:" + "4" * 64,
        "capture_ref": "docs/conformance/e4_target_support/lane_alpha/raw_capture_manifest.json#sha256:" + "5" * 64,
        "replay_ref": "docs/conformance/e4_target_support/lane_alpha/bb_replay_result.json#sha256:" + "6" * 64,
        "comparator_ref": "docs/conformance/e4_target_support/lane_alpha/comparator_report.json#sha256:" + "7" * 64,
        "evidence_manifest_ref": "docs/conformance/support_claims/lane_alpha_v1_c4_evidence_manifest.json",
        "validation_refs": ["artifacts/conformance/node_gate/ct_lane_alpha_c4_chain.json#sha256:" + "8" * 64],
        "ledger_row_refs": ["docs_tmp/phase_15/BB_E4_ATOMIC_FEATURE_LEDGER_SEED.json#feat_alpha#sha256:" + "9" * 64],
        "catalog_binding": {
            "catalog_path": CATALOG_PATH,
            "catalog_revision": catalog["revision"],
            "segment_id": "lane_alpha",
            "segment_hash": stable_entries_hash([entry for entry in catalog["entries"] if entry.get("lane_id") == "lane_alpha"]),
            "shared_segment_hash": stable_entries_hash([entry for entry in catalog["entries"] if not entry.get("lane_id")]),
        },
        "reverify_command": {"argv": ["python", "scripts/validate_e4_c4_chain.py", "--check-only", "claim_alpha"], "cwd": "."},
        "generated_at_utc": "2026-07-03T00:00:00Z",
    }


def _write_chain(repo_root: Path, claim: dict[str, Any], catalog: dict[str, Any]) -> Path:
    support_claim_path = repo_root / "docs/conformance/support_claims/lane_alpha_v1_c4_support_claim.json"
    evidence_manifest_path = repo_root / "docs/conformance/support_claims/lane_alpha_v1_c4_evidence_manifest.json"
    capture_path = repo_root / "docs/conformance/e4_target_support/lane_alpha/raw_capture_manifest.json"
    replay_path = repo_root / "docs/conformance/e4_target_support/lane_alpha/bb_replay_result.json"
    comparator_path = repo_root / "docs/conformance/e4_target_support/lane_alpha/comparator_report.json"
    validator_path = repo_root / "artifacts/conformance/node_gate/ct_lane_alpha_c4_chain.json"
    parity_path = repo_root / "docs/conformance/e4_target_support/lane_alpha/parity_results.json"
    secret_path = repo_root / "docs/conformance/e4_target_support/lane_alpha/secret_scan_report.json"
    freeze_manifest_path = repo_root / "config/e4_target_freeze_manifest.yaml"
    agent_config_path = repo_root / "agent_configs/lane_alpha.yaml"

    _write_json(repo_root / CATALOG_PATH, catalog)
    _write_json(support_claim_path, claim)
    _write_json(
        evidence_manifest_path,
        {
            "schema_version": "bb.e4.evidence_manifest.v1",
            "claim_id": "claim_alpha",
            "lane_id": "lane_alpha",
            "forbidden_roots_checked": ["scratch", "tmp", "scratch_runs", "/shared_folders"],
            "artifacts": [
                {"role": "freeze_manifest", "path": "config/e4_target_freeze_manifest.yaml", "sha256": "sha256:" + "4" * 64},
                {"role": "capture_ref", "path": "docs/conformance/e4_target_support/lane_alpha/raw_capture_manifest.json", "sha256": "sha256:" + "5" * 64},
                {"role": "replay_ref", "path": "docs/conformance/e4_target_support/lane_alpha/bb_replay_result.json", "sha256": "sha256:" + "6" * 64},
                {"role": "comparator_ref", "path": "docs/conformance/e4_target_support/lane_alpha/comparator_report.json", "sha256": "sha256:" + "7" * 64},
                {"role": "support_claim_ref", "path": "docs/conformance/support_claims/lane_alpha_v1_c4_support_claim.json", "sha256": "sha256:" + "1" * 64},
                {"role": "parity_results", "path": "docs/conformance/e4_target_support/lane_alpha/parity_results.json", "sha256": "sha256:" + "2" * 64},
                {"role": "secret_scan_report", "path": "docs/conformance/e4_target_support/lane_alpha/secret_scan_report.json", "sha256": "sha256:" + "3" * 64},
                {"role": "validator_output", "path": "artifacts/conformance/node_gate/ct_lane_alpha_c4_chain.json", "sha256": "sha256:" + "8" * 64},
            ],
        },
    )
    _write_json(
        capture_path,
        {
            "schema_version": "bb.e4.raw_capture_manifest.v1",
            "capture_class": "raw_target_capture",
            "raw_source_status": "canonical_raw_present",
            "accepted_as_capture_ref": True,
            "lineage_rationale": "fixture",
            "source_artifacts": [],
            "source_hashes": {},
            **claim["scope"],
        },
    )
    _write_json(replay_path, {"schema_version": "bb.e4.bb_replay_result.v1", "exit_status": "passed", "warnings": [], "errors": [], "lane_id": "lane_alpha", "config_id": "config_alpha", "input_hashes": {}})
    _write_json(
        comparator_path,
        {
            "schema_version": "bb.e4.comparator_report.v1",
            "failed": 0,
            "warned": 0,
            "details": [{"name": "scope_match"}],
            "assertions": [{"assertion_id": "scope_match", "name": "scope_match", "status": "passed", "observed": {"ok": True}, "expected": {"ok": True}}],
            "lane_id": "lane_alpha",
            "config_id": "config_alpha",
            "scope": claim["scope"],
            "input_hashes": {},
        },
    )
    _write_json(validator_path, {"ok": True})
    _write_json(parity_path, {"ok": True})
    _write_json(secret_path, {"ok": True})
    freeze_manifest_path.parent.mkdir(parents=True, exist_ok=True)
    freeze_manifest_path.write_text(
        "\n".join(
            [
                "e4_configs:",
                "  config_alpha:",
                "    config_path: agent_configs/lane_alpha.yaml",
                "    harness:",
                "      runtime_surface:",
                "        provider_model: provider-alpha",
                "    calibration_anchor:",
                "      evidence_paths:",
                "        - docs/conformance/e4_target_support/lane_alpha/raw_capture_manifest.json",
                "        - docs/conformance/e4_target_support/lane_alpha/bb_replay_result.json",
                "        - docs/conformance/e4_target_support/lane_alpha/comparator_report.json",
                "        - docs/conformance/support_claims/lane_alpha_v1_c4_support_claim.json",
                "        - docs/conformance/support_claims/lane_alpha_v1_c4_evidence_manifest.json",
                "        - artifacts/conformance/node_gate/ct_lane_alpha_c4_chain.json",
                "        - docs/conformance/e4_target_support/lane_alpha/parity_results.json",
                "",
            ]
        ),
        encoding="utf-8",
    )
    agent_config_path.parent.mkdir(parents=True, exist_ok=True)
    agent_config_path.write_text("model: provider-alpha\n", encoding="utf-8")
    return support_claim_path


@pytest.fixture()
def c4_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, dict[str, Any]]:
    repo_root = tmp_path / "repo"
    catalog = _catalog()
    _write_registry(repo_root)
    monkeypatch.setattr(registries, "REGISTRY_DIR", repo_root / "contracts/kernel/registries")
    registries.load_registry.cache_clear()

    def accepted_artifact_hash(
        repo_root: Path,
        label: str,
        ref_or_path: str,
        expected_hash: str | None,
        errors: list[str],
        **_: Any,
    ) -> tuple[Path, str]:
        return c4_chain._resolve_path(repo_root, ref_or_path), expected_hash or "sha256:" + "a" * 64

    monkeypatch.setattr(c4_chain, "_validate_artifact_hash", accepted_artifact_hash)
    monkeypatch.setattr(c4_chain, "_validate_freeze_manifest_artifact", accepted_artifact_hash)
    monkeypatch.setattr(c4_chain, "_validate_freeze_row_ref", lambda *args, **kwargs: (None, "sha256:" + "4" * 64))
    monkeypatch.setattr(c4_chain, "_validate_ledger_row_ref", lambda *args, **kwargs: None)
    return repo_root, catalog


def _validate(repo_root: Path, support_claim_path: Path) -> dict[str, Any]:
    return validate_c4_chain(
        repo_root=repo_root,
        freeze_manifest_path=repo_root / "config/e4_target_freeze_manifest.yaml",
        config_id="config_alpha",
        support_claim_path=support_claim_path,
        rerun_comparators=False,
    )


def test_c4_chain_still_accepts_v2_support_claim(c4_env: tuple[Path, dict[str, Any]]) -> None:
    repo_root, catalog = c4_env
    claim = _base_claim(catalog, schema_version="bb.e4.support_claim.v2")
    support_claim_path = _write_chain(repo_root, claim, catalog)

    report = _validate(repo_root, support_claim_path)

    assert report["ok"] is True
    assert report["errors"] == []


def test_c4_chain_accepts_v3_registered_family_and_segment_binding(c4_env: tuple[Path, dict[str, Any]]) -> None:
    repo_root, catalog = c4_env
    claim = _base_claim(catalog)
    support_claim_path = _write_chain(repo_root, claim, catalog)

    report = _validate(repo_root, support_claim_path)

    assert report["ok"] is True
    assert report["errors"] == []


def test_c4_chain_rejects_v3_unregistered_target_family_as_semantic_gate_error(c4_env: tuple[Path, dict[str, Any]]) -> None:
    repo_root, catalog = c4_env
    claim = _base_claim(catalog)
    claim["target_family"] = "new_harness"
    support_claim_path = _write_chain(repo_root, claim, catalog)

    report = _validate(repo_root, support_claim_path)

    assert report["ok"] is False
    assert any("unregistered identifier 'new_harness'" in error for error in report["errors"])
    assert any(
        error["code"] == "unregistered_identifier" and error["klass"] == "semantic"
        for error in report["gate_errors"]
    )


def test_c4_chain_rejects_v3_bad_segment_hash_as_pin_stale(c4_env: tuple[Path, dict[str, Any]]) -> None:
    repo_root, catalog = c4_env
    claim = _base_claim(catalog)
    claim["catalog_binding"] = copy.deepcopy(claim["catalog_binding"])
    claim["catalog_binding"]["segment_hash"] = "sha256:" + "0" * 64
    support_claim_path = _write_chain(repo_root, claim, catalog)

    report = _validate(repo_root, support_claim_path)

    assert report["ok"] is False
    assert any("support_claim.catalog_binding.segment_hash mismatch" in error for error in report["errors"])
    assert any(
        error["klass"] == "pin_stale" and "segment_hash mismatch" in error["message"]
        for error in report["gate_errors"]
    )


def test_c4_chain_accepts_v4_claim_with_scope_sourced_target_fields(c4_env: tuple[Path, dict[str, Any]]) -> None:
    repo_root, catalog = c4_env
    claim = _base_v4_claim(catalog)
    support_claim_path = _write_chain(repo_root, claim, catalog)

    report = _validate(repo_root, support_claim_path)

    assert report["ok"] is True, report["errors"]
    assert report["errors"] == []


def test_c4_chain_rejects_v4_claim_with_root_level_lane_id_duplicate(
    c4_env: tuple[Path, dict[str, Any]],
) -> None:
    repo_root, catalog = c4_env
    claim = _base_v4_claim(catalog)
    claim["lane_id"] = claim["scope"]["lane_id"]
    support_claim_path = _write_chain(repo_root, claim, catalog)

    report = _validate(repo_root, support_claim_path)

    assert report["ok"] is False
    assert any("lane_id" in error and "Additional properties" in error for error in report["errors"]), report["errors"]
