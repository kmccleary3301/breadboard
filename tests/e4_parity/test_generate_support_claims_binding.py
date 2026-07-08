from __future__ import annotations

import importlib
import json
from pathlib import Path
from typing import Any

import pytest

from agentic_coder_prototype.conformance.catalog_binding import CATALOG_PATH, stable_entries_hash


generator = importlib.import_module("scripts.e4_parity.generate_support_claims")
ROOT = Path(__file__).resolve().parents[2]


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
            "generated_by": "scripts/e4_parity/build_oh_my_pi_p3_1_effective_config_graph.py",
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
            "generated_by": "scripts/e4_parity/build_oh_my_pi_p3_1_effective_config_graph.py",
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


def test_catalog_binding_uses_fixture_revision_and_stable_entries_hash(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    catalog_path = tmp_path / "e4_artifact_catalog.json"
    catalog = _catalog()
    _write_json(catalog_path, catalog)
    monkeypatch.setattr(generator, "CATALOG_PATH", catalog_path)

    binding = generator._catalog_binding()

    assert binding == {
        "catalog_path": CATALOG_PATH,
        "catalog_revision": catalog["revision"],
        "catalog_hash": catalog["integrity"]["stable_entries_hash"],
    }


@pytest.mark.parametrize("catalog", [_catalog(include_stable_hash=False), _catalog(revision=True), _catalog(revision=0)])
def test_catalog_binding_rejects_missing_digest_or_invalid_revision(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    catalog: dict[str, Any],
) -> None:
    catalog_path = tmp_path / "e4_artifact_catalog.json"
    _write_json(catalog_path, catalog)
    monkeypatch.setattr(generator, "CATALOG_PATH", catalog_path)

    with pytest.raises(ValueError):
        generator._catalog_binding()


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


def test_checked_in_v2_claims_bind_to_live_catalog_revision_and_stable_digest() -> None:
    catalog = json.loads((ROOT / CATALOG_PATH).read_text(encoding="utf-8"))
    expected_hash = stable_entries_hash(catalog["entries"])
    claims = sorted((ROOT / "docs" / "conformance" / "support_claims").glob("*_c4_support_claim.json"))
    checked = 0

    for claim_path in claims:
        claim = json.loads(claim_path.read_text(encoding="utf-8"))
        if claim.get("schema_version") != "bb.e4.support_claim.v2":
            continue
        checked += 1
        binding = claim.get("catalog_binding")
        assert isinstance(binding, dict), claim_path.as_posix()
        assert binding["catalog_revision"] == catalog["revision"], claim_path.as_posix()
        assert binding["catalog_hash"] == expected_hash, claim_path.as_posix()

    assert checked > 0
