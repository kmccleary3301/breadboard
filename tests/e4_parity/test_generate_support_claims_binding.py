from __future__ import annotations

import importlib
import json
from pathlib import Path
from typing import Any

import pytest

from agentic_coder_prototype.conformance.catalog_binding import CATALOG_PATH, catalog_segment_hash, stable_entries_hash
from scripts.e4_parity.validators.registries import schema_generation_default


generator = importlib.import_module("scripts.e4_parity.generate_support_claims")
ROOT = Path(__file__).resolve().parents[2]
SUPPORT_CLAIM_SCHEMA_VERSION = schema_generation_default("support_claim")


def _lane(lane_id: str = "lane_alpha") -> dict[str, str]:
    return {"lane_id": lane_id, "support_claim_schema_version": SUPPORT_CLAIM_SCHEMA_VERSION}


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _catalog(*, revision: int | bool = 3, include_stable_hash: bool = True) -> dict[str, Any]:
    entries = [
        {
            "artifact_kind": "capture",
            "bytes": 2,
            "derived_from": [],
            "exists": True,
            "generated_by": "scripts/e4_parity/adapters/oh_my_pi_compiler_capture.py",
            "lane_id": "lane_alpha",
            "media_type": "application/json",
            "path": "docs/conformance/e4_target_support/lane_alpha/raw_capture_manifest.json",
            "role_id": "lane_alpha:capture",
            "sha256": "sha256:" + "1" * 64,
        },
        {
            "artifact_kind": "support_claim",
            "bytes": 3,
            "derived_from": [],
            "exists": True,
            "generated_by": "scripts/e4_parity/adapters/oh_my_pi_compiler_capture.py",
            "lane_id": "lane_alpha",
            "media_type": "application/json",
            "path": "docs/conformance/support_claims/lane_alpha_v1_c4_support_claim.json",
            "role_id": "lane_alpha:support_claim",
            "sha256": "sha256:" + "2" * 64,
        },
    ]
    integrity = {"entry_count": len(entries), "entries_hash": "sha256:" + "3" * 64}
    if include_stable_hash:
        integrity["stable_entries_hash"] = stable_entries_hash(entries)
    return {
        "catalog_id": "e4_artifact_catalog_v1",
        "entries": entries,
        "generated_at_utc": "2026-07-03T00:00:00Z",
        "integrity": integrity,
        "revision": revision,
        "schema_version": "bb.e4.artifact_catalog.v1",
    }


def test_claim_derived_validator_ref_hashes_physical_artifact_outside_binding_catalog(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    validator_path = tmp_path / "docs/conformance/lane_alpha/prevalidation_report.json"
    _write_json(validator_path, {"ok": True})
    monkeypatch.setattr(generator, "ROOT", tmp_path)
    monkeypatch.setattr(generator, "WORKSPACE", tmp_path.parent)
    lane = {
        "lane_id": "lane_alpha",
        "artifact_roles": {"validator_output": "lane_alpha:validator_output"},
    }

    resolved = generator._catalog_hash_ref(
        _catalog(),
        lane,
        "validator_output",
        "docs/conformance/lane_alpha/prevalidation_report.json#sha256:" + "0" * 64,
    )

    assert resolved == f"docs/conformance/lane_alpha/prevalidation_report.json#{generator.sha256_path(validator_path)}"


def test_catalog_binding_uses_fixture_revision_and_segment_hashes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    catalog_path = tmp_path / "e4_artifact_catalog.json"
    catalog = _catalog()
    _write_json(catalog_path, catalog)
    monkeypatch.setattr(generator, "CATALOG_PATH", catalog_path)

    binding = generator._catalog_binding(_lane())

    assert binding == {
        "catalog_path": CATALOG_PATH,
        "catalog_revision": catalog["revision"],
        "segment_id": "lane_alpha",
        "segment_hash": stable_entries_hash(catalog["entries"]),
        "shared_segment_hash": stable_entries_hash([]),
    }


def test_catalog_binding_reuses_prior_revision_when_segment_digests_match(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    catalog_path = tmp_path / "e4_artifact_catalog.json"
    catalog = _catalog(revision=9)
    _write_json(catalog_path, catalog)
    monkeypatch.setattr(generator, "CATALOG_PATH", catalog_path)
    prior_binding = {
        "catalog_path": CATALOG_PATH,
        "catalog_revision": 4,
        "segment_id": "lane_alpha",
        "segment_hash": stable_entries_hash(catalog["entries"]),
        "shared_segment_hash": stable_entries_hash([]),
    }

    binding = generator._catalog_binding(_lane(), prior_binding=prior_binding)

    assert binding == prior_binding


def test_catalog_binding_uses_live_revision_when_segment_digest_changes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    catalog_path = tmp_path / "e4_artifact_catalog.json"
    catalog = _catalog(revision=9)
    _write_json(catalog_path, catalog)
    monkeypatch.setattr(generator, "CATALOG_PATH", catalog_path)

    binding = generator._catalog_binding(
        _lane(),
        prior_binding={
            "catalog_path": CATALOG_PATH,
            "catalog_revision": 4,
            "segment_id": "lane_alpha",
            "segment_hash": "sha256:" + "9" * 64,
            "shared_segment_hash": stable_entries_hash([]),
        },
    )

    assert binding["catalog_revision"] == 9
    assert binding["segment_hash"] == stable_entries_hash(catalog["entries"])


@pytest.mark.parametrize("catalog", [{**_catalog(), "entries": None}, _catalog(revision=True), _catalog(revision=0)])
def test_catalog_binding_rejects_missing_digest_or_invalid_revision(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    catalog: dict[str, Any],
) -> None:
    catalog_path = tmp_path / "e4_artifact_catalog.json"
    _write_json(catalog_path, catalog)
    monkeypatch.setattr(generator, "CATALOG_PATH", catalog_path)

    with pytest.raises(ValueError):
        generator._catalog_binding(_lane())


def test_refresh_freeze_ref_binds_current_row_hash(tmp_path: Path) -> None:
    freeze_path = tmp_path / "freeze.yaml"
    _write_json(freeze_path, {"e4_configs": {"config_alpha": {"revision": 2}}})

    refreshed = generator._refresh_freeze_ref(
        f"{freeze_path}#config_alpha#sha256:{'0' * 64}"
    )

    expected_hash = generator.lane_runtime.sha256_text(
        generator.lane_runtime.canonical_json(
            {"row_id": "config_alpha", "row": {"revision": 2}},
            separators_style="compact",
        )
    )
    assert refreshed == f"{freeze_path}#config_alpha#{expected_hash}"


def test_updated_manifest_refreshes_derived_from_hashes(tmp_path: Path) -> None:
    dependency_path = tmp_path / "dependency.json"
    artifact_path = tmp_path / "artifact.json"
    claim_path = tmp_path / "claim.json"
    manifest_path = tmp_path / "manifest.json"
    _write_json(dependency_path, {"revision": 2})
    _write_json(artifact_path, {"result": True})
    _write_json(claim_path, {"claim": True})
    _write_json(
        manifest_path,
        {
            "artifacts": [
                {
                    "derived_from": [f"{dependency_path}#sha256:{'0' * 64}"],
                    "path": str(artifact_path),
                    "role": "parity_results",
                    "sha256": "sha256:" + "0" * 64,
                },
                {
                    "path": str(claim_path),
                    "role": "support_claim_ref",
                    "sha256": "sha256:" + "0" * 64,
                },
            ]
        },
    )

    generator._updated_manifest(manifest_path, claim_path, {"freeze_ref": ""})

    updated = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert updated["artifacts"][0]["derived_from"] == [generator.ref(dependency_path)]
    assert updated["artifacts"][0]["sha256"] == generator.sha256_path(artifact_path)


def test_updated_node_gate_only_syncs_refs_and_hashes(tmp_path: Path) -> None:
    claim_path = tmp_path / "claim.json"
    manifest_path = tmp_path / "manifest.json"
    node_gate_path = tmp_path / "node_gate.json"
    _write_json(claim_path, {"claim": True})
    _write_json(manifest_path, {"manifest": True})
    _write_json(
        node_gate_path,
        {
            "accepted": False,
            "errors": ["support_claim.catalog_binding.catalog_hash mismatch"],
            "hashes": {"support_claim": "old", "evidence_manifest": "old"},
            "ok": False,
            "refs": {"support_claim": "old", "evidence_manifest": "old"},
        },
    )

    generator._updated_node_gate(node_gate_path, claim_path, manifest_path)

    updated = json.loads(node_gate_path.read_text(encoding="utf-8"))
    assert updated["errors"] == ["support_claim.catalog_binding.catalog_hash mismatch"]
    assert updated["ok"] is False
    assert updated["accepted"] is False
    assert updated["hashes"]["support_claim"] == generator.sha256_path(claim_path)
    assert updated["hashes"]["evidence_manifest"] == generator.sha256_path(manifest_path)
    assert updated["refs"]["support_claim"] == generator.display(claim_path)
    assert updated["refs"]["evidence_manifest"] == generator.display(manifest_path)


def _lane_for_outputs(lane_id: str, *, claim: str, manifest: str, node_gate: str) -> dict[str, Any]:
    return {
        "lane_id": lane_id,
        "status": "accepted",
        "ct": {
            "command": {
                "argv": [
                    "python",
                    "validate.py",
                    "--support-claim",
                    claim,
                    "--evidence-manifest",
                    manifest,
                    "--json-out",
                    node_gate,
                ]
            }
        },
    }


def test_claim_generation_key_orders_manifest_before_claim_outputs() -> None:
    lanes = [
        _lane_for_outputs("z", claim="docs/conformance/support_claims/z_claim.json", manifest="docs/conformance/support_claims/z_manifest.json", node_gate="artifacts/conformance/node_gate/z.json"),
        _lane_for_outputs("a", claim="docs/conformance/support_claims/a_claim.json", manifest="docs/conformance/support_claims/a_manifest.json", node_gate="artifacts/conformance/node_gate/a.json"),
        _lane_for_outputs("m", claim="docs/conformance/support_claims/aa_claim.json", manifest="docs/conformance/support_claims/a_manifest.json", node_gate="artifacts/conformance/node_gate/m.json"),
    ]

    ordered = [lane["lane_id"] for lane in sorted(lanes, key=generator._claim_generation_key)]

    assert ordered == ["a", "m", "z"]


def test_checked_in_v4_claims_bind_to_live_catalog_segments() -> None:
    catalog = json.loads((ROOT / CATALOG_PATH).read_text(encoding="utf-8"))
    claims = sorted((ROOT / "docs" / "conformance" / "support_claims").glob("*_c4_support_claim.json"))
    checked = 0

    for claim_path in claims:
        claim = json.loads(claim_path.read_text(encoding="utf-8"))
        if claim.get("schema_version") != SUPPORT_CLAIM_SCHEMA_VERSION:
            continue
        checked += 1
        binding = claim.get("catalog_binding")
        assert isinstance(binding, dict), claim_path.as_posix()
        lane_id = claim["scope"]["lane_id"]
        assert binding["segment_id"] == lane_id, claim_path.as_posix()
        assert binding["segment_hash"] == catalog_segment_hash(catalog, lane_id), claim_path.as_posix()
        assert binding["shared_segment_hash"] == catalog_segment_hash(catalog, "shared"), claim_path.as_posix()
        assert isinstance(binding["catalog_revision"], int) and not isinstance(binding["catalog_revision"], bool), claim_path.as_posix()
        assert 1 <= binding["catalog_revision"] <= catalog["revision"], claim_path.as_posix()

    assert checked > 0
