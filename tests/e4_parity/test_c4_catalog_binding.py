from __future__ import annotations

import json
from pathlib import Path
import pytest
from typing import Any

from agentic_coder_prototype.conformance import c4_chain
from agentic_coder_prototype.conformance.c4_chain import _validate_catalog_binding, validate_c4_chain
from agentic_coder_prototype.conformance.catalog_binding import CATALOG_PATH, catalog_segments, stable_entries_hash


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _catalog(stable_sha: str = "sha256:" + "1" * 64) -> dict[str, Any]:
    entries = [
        {
            "role_id": "lane_alpha:capture",
            "path": "docs/conformance/e4_target_support/lane_alpha/raw_capture_manifest.json",
            "sha256": stable_sha,
            "bytes": 2,
            "exists": True,
            "artifact_kind": "capture",
            "lane_id": "lane_alpha",
            "media_type": "application/json",
            "derived_from": [],
            "generated_by": "scripts/e4_parity/adapters/oh_my_pi_compiler_capture.py",
        },
        {
            "role_id": "lane_alpha:support_claim",
            "path": "docs/conformance/support_claims/lane_alpha_v1_c4_support_claim.json",
            "sha256": "sha256:" + "2" * 64,
            "bytes": 3,
            "exists": True,
            "artifact_kind": "support_claim",
            "lane_id": "lane_alpha",
            "media_type": "application/json",
            "derived_from": [],
            "generated_by": "scripts/e4_parity/adapters/oh_my_pi_compiler_capture.py",
        },
    ]
    return {
        "schema_version": "bb.e4.artifact_catalog.v1",
        "catalog_id": "e4_artifact_catalog_v1",
        "generated_at_utc": "2026-07-03T00:00:00Z",
        "revision": 2,
        "entries": entries,
        "integrity": {
            "entry_count": len(entries),
            "entries_hash": "sha256:" + "3" * 64,
            "stable_entries_hash": stable_entries_hash(entries),
        },
    }


def _claim(
    catalog: dict[str, Any],
    *,
    digest: str | None = None,
    revision: int | bool | None = None,
    catalog_path: str = CATALOG_PATH,
) -> dict[str, Any]:
    return {
        "catalog_binding": {
            "catalog_path": catalog_path,
            "catalog_revision": revision if revision is not None else catalog["revision"],
            "catalog_hash": digest if digest is not None else catalog["integrity"]["stable_entries_hash"],
        }
    }


def test_c4_catalog_binding_accepts_stable_digest(tmp_path: Path) -> None:
    repo_root = tmp_path / "repo"
    catalog = _catalog()
    _write_json(repo_root / CATALOG_PATH, catalog)
    errors: list[str] = []

    _validate_catalog_binding(repo_root, _claim(catalog), errors)

    assert errors == []


@pytest.mark.parametrize(
    ("claim_kwargs", "expected_error"),
    [
        (
            {"digest": "sha256:" + "0" * 64},
            "support_claim.catalog_binding.catalog_hash mismatch",
        ),
        (
            {"catalog_path": "docs/conformance/not_the_catalog.json"},
            f"support_claim.catalog_binding.catalog_path must be {CATALOG_PATH}",
        ),
        (
            {"revision": 0},
            "support_claim.catalog_binding.catalog_revision must be an integer >= 1",
        ),
        (
            {"revision": True},
            "support_claim.catalog_binding.catalog_revision must be an integer >= 1",
        ),
    ],
)
def test_c4_catalog_binding_rejects_invalid_claim_binding_values(
    tmp_path: Path,
    claim_kwargs: dict[str, Any],
    expected_error: str,
) -> None:
    repo_root = tmp_path / "repo"
    catalog = _catalog()
    _write_json(repo_root / CATALOG_PATH, catalog)
    errors: list[str] = []

    _validate_catalog_binding(repo_root, _claim(catalog, **claim_kwargs), errors)

    assert any(expected_error in error for error in errors)

def test_c4_catalog_binding_rejects_future_revision(tmp_path: Path) -> None:
    repo_root = tmp_path / "repo"
    catalog = _catalog()
    _write_json(repo_root / CATALOG_PATH, catalog)
    errors: list[str] = []

    _validate_catalog_binding(repo_root, _claim(catalog, revision=catalog["revision"] + 1), errors)

    assert "support_claim.catalog_binding.catalog_revision must be <= live catalog revision" in errors


def test_c4_catalog_binding_rejects_missing_catalog_file(tmp_path: Path) -> None:
    errors: list[str] = []

    _validate_catalog_binding(tmp_path / "repo", _claim(_catalog()), errors)

    assert any("support_claim.catalog_binding.catalog_path: missing path" in error for error in errors)


def test_c4_catalog_binding_rejects_malformed_catalog_file(tmp_path: Path) -> None:
    repo_root = tmp_path / "repo"
    catalog_path = repo_root / CATALOG_PATH
    catalog_path.parent.mkdir(parents=True, exist_ok=True)
    catalog_path.write_text("{not json", encoding="utf-8")
    errors: list[str] = []

    _validate_catalog_binding(repo_root, _claim(_catalog()), errors)

    assert any("support_claim.catalog_binding.catalog_path: unable to load JSON" in error for error in errors)




def test_c4_catalog_binding_accepts_emitted_v2_segments(tmp_path: Path) -> None:
    repo_root = tmp_path / "repo"
    catalog = _catalog()
    catalog["schema_version"] = "bb.e4.artifact_catalog.v2"
    catalog["catalog_id"] = "e4_artifact_catalog_v2"
    catalog["segments"] = catalog_segments(catalog["entries"])
    catalog["integrity"]["segments_hash"] = "sha256:" + "4" * 64
    _write_json(repo_root / CATALOG_PATH, catalog)
    by_segment = {segment["segment_id"]: segment for segment in catalog["segments"]}
    errors: list[str] = []

    c4_chain._validate_segment_catalog_binding(
        repo_root,
        {
            "lane_id": "lane_alpha",
            "catalog_binding": {
                "catalog_path": CATALOG_PATH,
                "segment_id": "lane_alpha",
                "segment_hash": by_segment["lane_alpha"]["stable_entries_hash"],
                "shared_segment_hash": by_segment["shared"]["stable_entries_hash"],
            },
        },
        errors,
    )

    assert errors == []


def test_c4_catalog_binding_rejects_emitted_v2_segment_hash_mismatch(tmp_path: Path) -> None:
    repo_root = tmp_path / "repo"
    catalog = _catalog()
    catalog["schema_version"] = "bb.e4.artifact_catalog.v2"
    catalog["catalog_id"] = "e4_artifact_catalog_v2"
    catalog["segments"] = catalog_segments(catalog["entries"])
    catalog["integrity"]["segments_hash"] = "sha256:" + "4" * 64
    _write_json(repo_root / CATALOG_PATH, catalog)
    by_segment = {segment["segment_id"]: segment for segment in catalog["segments"]}
    errors: list[str] = []

    c4_chain._validate_segment_catalog_binding(
        repo_root,
        {
            "lane_id": "lane_alpha",
            "catalog_binding": {
                "catalog_path": CATALOG_PATH,
                "segment_id": "lane_alpha",
                "segment_hash": "sha256:" + "0" * 64,
                "shared_segment_hash": by_segment["shared"]["stable_entries_hash"],
            },
        },
        errors,
    )

    assert any("support_claim.catalog_binding.segment_hash mismatch" in error for error in errors)
def test_validate_c4_chain_can_defer_catalog_binding_errors(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo_root = tmp_path / "repo"
    catalog = _catalog()
    _write_json(repo_root / CATALOG_PATH, catalog)

    scope = {
        "lane_id": "lane_alpha",
        "config_id": "config_alpha",
        "run_id": "run_alpha",
        "target_version": "target-alpha",
        "provider_model": "provider-alpha",
        "sandbox_mode": "sandbox-alpha",
    }
    support_claim_path = repo_root / "docs/conformance/support_claims/lane_alpha_v1_c4_support_claim.json"
    evidence_manifest_path = repo_root / "docs/conformance/support_claims/lane_alpha_v1_c4_evidence_manifest.json"
    capture_path = repo_root / "docs/conformance/e4_target_support/lane_alpha/raw_capture_manifest.json"
    replay_path = repo_root / "docs/conformance/e4_target_support/lane_alpha/bb_replay_result.json"
    comparator_path = repo_root / "docs/conformance/e4_target_support/lane_alpha/comparator_report.json"
    validator_path = repo_root / "artifacts/conformance/node_gate/ct_lane_alpha_c4_chain.json"
    parity_path = repo_root / "docs/conformance/e4_target_support/lane_alpha/parity_results.json"
    freeze_manifest_path = repo_root / "config/e4_target_freeze_manifest.yaml"
    agent_config_path = repo_root / "agent_configs/lane_alpha.yaml"

    _write_json(
        support_claim_path,
        {
            "schema_version": "bb.e4.support_claim.v2",
            "claim_id": "claim_alpha",
            "lane_id": "lane_alpha",
            "config_id": "config_alpha",
            "accepted": True,
            "scope": scope,
            "exclusions": ["No broad support claim is made."],
            "freeze_ref": "config/e4_target_freeze_manifest.yaml#config_alpha#sha256:" + "4" * 64,
            "capture_ref": "docs/conformance/e4_target_support/lane_alpha/raw_capture_manifest.json#sha256:" + "5" * 64,
            "replay_ref": "docs/conformance/e4_target_support/lane_alpha/bb_replay_result.json#sha256:" + "6" * 64,
            "comparator_ref": "docs/conformance/e4_target_support/lane_alpha/comparator_report.json#sha256:" + "7" * 64,
            "evidence_manifest_ref": "docs/conformance/support_claims/lane_alpha_v1_c4_evidence_manifest.json",
            "validation_refs": ["artifacts/conformance/node_gate/ct_lane_alpha_c4_chain.json#sha256:" + "8" * 64],
            "ledger_row_refs": ["docs_tmp/phase_15/BB_E4_ATOMIC_FEATURE_LEDGER_SEED.json#feat_alpha#sha256:" + "9" * 64],
            "generated_at_utc": "2026-07-03T00:00:00Z",
            "catalog_binding": {
                "catalog_path": CATALOG_PATH,
                "catalog_revision": catalog["revision"],
                "catalog_hash": "sha256:" + "0" * 64,
            },
            "claim_semantics": {
                "asserted_behaviors": [
                    {
                        "behavior_id": "behavior_alpha",
                        "summary": "scope is bound",
                        "comparator_assertion_ids": ["scope_match"],
                    }
                ]
            },
        },
    )
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
            **scope,
        },
    )
    _write_json(
        replay_path,
        {
            "schema_version": "bb.e4.bb_replay_result.v1",
            "exit_status": "passed",
            "warnings": [],
            "errors": [],
            "lane_id": "lane_alpha",
            "config_id": "config_alpha",
            "input_hashes": {},
        },
    )
    _write_json(
        comparator_path,
        {
            "schema_version": "bb.e4.comparator_report.v1",
            "failed": 0,
            "warned": 0,
            "details": [{"name": "scope_match"}],
            "assertions": [
                {
                    "assertion_id": "scope_match",
                    "name": "scope_match",
                    "status": "passed",
                    "observed": {"ok": True},
                    "expected": {"ok": True},
                }
            ],
            "lane_id": "lane_alpha",
            "config_id": "config_alpha",
            "scope": scope,
            "input_hashes": {},
        },
    )
    _write_json(validator_path, {"ok": True})
    _write_json(parity_path, {"ok": True})
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
    monkeypatch.setattr(c4_chain, "collect_support_claim_schema_errors", lambda support_claim: [])

    strict = validate_c4_chain(
        repo_root=repo_root,
        freeze_manifest_path=freeze_manifest_path,
        config_id="config_alpha",
        support_claim_path=support_claim_path,
        rerun_comparators=False,
    )
    deferred = validate_c4_chain(
        repo_root=repo_root,
        freeze_manifest_path=freeze_manifest_path,
        config_id="config_alpha",
        support_claim_path=support_claim_path,
        rerun_comparators=False,
        enforce_catalog_binding=False,
    )

    assert strict["ok"] is False
    assert any("support_claim.catalog_binding.catalog_hash mismatch" in error for error in strict["errors"])
    assert strict["catalog_binding_pending"] == []
    assert deferred["ok"] is True
    assert deferred["errors"] == []
    assert any("support_claim.catalog_binding.catalog_hash mismatch" in error for error in deferred["catalog_binding_pending"])
