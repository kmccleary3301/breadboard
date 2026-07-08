from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any, Mapping

import pytest

from agentic_coder_prototype.compilation.primitive_records import canonical_record_bytes, sha256_ref
from agentic_coder_prototype.conformance.catalog_binding import catalog_segments, classify_entry, stable_entries_hash
from scripts.e4_parity import build_artifact_catalog as builder
from tests.e4_parity.test_build_artifact_catalog import (
    GENERATED_AT,
    _catalog_entry,
    _sha256_file,
    _write_catalog_fixture,
    _write_json,
)


CLAIM_LAYER_ROLE_IDS = (
    "lane_alpha:support_claim",
    "lane_alpha:support_claim_ref",
    "lane_alpha:node_gate",
    "lane_alpha:validator_output",
    "e4_static:report/bb_e4_atomic_feature_ledger_report_json",
)

STABLE_STATIC_REPORT_ROLE_IDS = (
    "e4_static:report/bb_e4_evidence_governance_json",
)

DERIVED_MANIFEST_ROLE_IDS = (
    "lane_alpha:evidence_manifest",
    "lane_alpha:evidence_manifest_ref",
)


def _entry(
    role_id: str,
    digest_digit: str,
    *,
    lane_id: str | None = "lane_alpha",
    artifact_kind: str = "other",
    derived_from: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "role_id": role_id,
        "path": f"docs/conformance/{role_id.replace(':', '_')}.json",
        "sha256": "sha256:" + digest_digit * 64,
        "bytes": int(digest_digit, 16) + 1,
        "exists": True,
        "artifact_kind": artifact_kind,
        "lane_id": lane_id,
        "media_type": "application/json",
        "derived_from": derived_from or [],
        "generated_by": "manual",
    }


def _catalog_entries() -> list[dict[str, Any]]:
    return [
        _entry("e4_static:config/e4_lane_inventory", "1", lane_id=None, artifact_kind="config"),
        _entry(
            "e4_static:report/bb_e4_evidence_governance_json",
            "2",
            lane_id=None,
            artifact_kind="report",
        ),
        _entry(
            "e4_static:report/bb_e4_atomic_feature_ledger_report_json",
            "3",
            lane_id=None,
            artifact_kind="report",
        ),
        _entry("lane_alpha:capture", "8", artifact_kind="capture"),
        _entry(
            "lane_alpha:evidence_manifest",
            "4",
            artifact_kind="evidence_manifest",
            derived_from=["lane_alpha:support_claim"],
        ),
        _entry("lane_alpha:node_gate", "5", artifact_kind="validator_output"),
        _entry("lane_alpha:support_claim", "6", artifact_kind="support_claim"),
        _entry("lane_alpha:validator_output", "7", artifact_kind="validator_output"),
    ]


def _segment_by_id(entries: list[Mapping[str, Any]], segment_id: str) -> dict[str, Any]:
    by_segment = {segment["segment_id"]: segment for segment in catalog_segments(entries)}
    return by_segment[segment_id]


def _stable_subset(entries: list[Mapping[str, Any]]) -> list[Mapping[str, Any]]:
    return [entry for entry in entries if classify_entry(entry) == "stable"]


def _run_catalog_v2(paths: dict[str, Path], *, generated_at_utc: str = GENERATED_AT) -> None:
    assert builder.main(
        [
            "--inventory",
            str(paths["inventory"]),
            "--report-roles",
            str(paths["report_roles"]),
            "--output",
            str(paths["output"]),
            "--generated-at-utc",
            generated_at_utc,
            "--schema-version",
            "v2",
        ]
    ) == 0


@pytest.mark.parametrize("role_id", CLAIM_LAYER_ROLE_IDS)
def test_claim_artifact_roles_are_classified_as_claim_layer(role_id: str) -> None:
    """Claim artifacts are neither stable anchors nor merely derived manifests; they are the claim layer."""

    lane_id = None if role_id.startswith("e4_static:") else "lane_alpha"

    assert classify_entry(_entry(role_id, "8", lane_id=lane_id, artifact_kind="report")) == "claim_layer"



@pytest.mark.xfail(reason="BR-1: existing accepted v3 catalog bindings include these static reports in the stable hash")
@pytest.mark.parametrize("role_id", STABLE_STATIC_REPORT_ROLE_IDS)
def test_static_report_claim_layer_migration_is_blocked_by_br1(role_id: str) -> None:
    assert classify_entry(_entry(role_id, "8", lane_id=None, artifact_kind="report")) == "claim_layer"

@pytest.mark.parametrize("role_id", DERIVED_MANIFEST_ROLE_IDS)
def test_manifest_roles_stay_derived_not_claim_layer(role_id: str) -> None:
    """Evidence manifests remain derived so their self-referential hash churn stays out of stable binding."""

    assert classify_entry(_entry(role_id, "9", artifact_kind="evidence_manifest")) == "derived"


def test_stable_and_segment_hashes_exclude_derived_and_claim_layer_roles() -> None:
    """Only stable anchors contribute to stable_entries_hash and per-segment stable hashes."""

    entries = _catalog_entries()
    stable_subset = _stable_subset(entries)

    assert [entry["role_id"] for entry in stable_subset] == [
        "e4_static:config/e4_lane_inventory",
        "e4_static:report/bb_e4_evidence_governance_json",
        "lane_alpha:capture",
    ]
    assert stable_entries_hash(entries) == stable_entries_hash(stable_subset)
    assert sha256_ref(canonical_record_bytes(entries)) != sha256_ref(canonical_record_bytes(stable_subset))

    shared_segment = _segment_by_id(entries, "shared")
    lane_segment = _segment_by_id(entries, "lane_alpha")

    assert shared_segment["stable_entries_hash"] == stable_entries_hash(stable_subset[:2])
    assert lane_segment["stable_entries_hash"] == stable_entries_hash([stable_subset[2]])


def test_claim_layer_churn_does_not_change_stable_or_segment_stable_hashes() -> None:
    """Layer-2-through-layer-4 claim churn changes full entry hashes but not layer-1 stable binding hashes."""

    entries = _catalog_entries()
    churned = copy.deepcopy(entries)
    for entry in churned:
        if classify_entry(entry) == "claim_layer":
            entry["sha256"] = "sha256:" + "a" * 64
            entry["bytes"] += 1000
            entry["path"] = entry["path"].replace(".json", ".churned.json")

    assert sha256_ref(canonical_record_bytes(churned)) != sha256_ref(canonical_record_bytes(entries))
    assert stable_entries_hash(churned) == stable_entries_hash(entries)
    assert _segment_by_id(churned, "shared")["entries_hash"] != _segment_by_id(entries, "shared")["entries_hash"]
    assert _segment_by_id(churned, "shared")["stable_entries_hash"] == _segment_by_id(entries, "shared")[
        "stable_entries_hash"
    ]
    assert _segment_by_id(churned, "lane_alpha")["entries_hash"] != _segment_by_id(entries, "lane_alpha")[
        "entries_hash"
    ]
    assert _segment_by_id(churned, "lane_alpha")["stable_entries_hash"] == _segment_by_id(entries, "lane_alpha")[
        "stable_entries_hash"
    ]


def test_catalog_revision_is_keyed_to_stable_entries_hash_not_claim_layer_churn(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Catalog revision advances for stable anchor changes, not for claim-layer byte churn."""

    paths = _write_catalog_fixture(tmp_path, monkeypatch)
    _run_catalog_v2(paths)
    first = json.loads(paths["output"].read_text(encoding="utf-8"))
    first_claim_sha = _catalog_entry(first, "lane_alpha:support_claim")["sha256"]

    _write_json(paths["support_claim"], {"accepted": True, "claim_id": "lane_alpha_v1_c4_support_claim", "revision": 2})
    _run_catalog_v2(paths, generated_at_utc="2026-07-04T00:00:00Z")
    after_claim_layer_churn = json.loads(paths["output"].read_text(encoding="utf-8"))

    assert _catalog_entry(after_claim_layer_churn, "lane_alpha:support_claim")["sha256"] != first_claim_sha
    assert after_claim_layer_churn["integrity"]["entries_hash"] != first["integrity"]["entries_hash"]
    assert after_claim_layer_churn["integrity"]["stable_entries_hash"] == first["integrity"]["stable_entries_hash"]
    assert _segment_by_id(after_claim_layer_churn["entries"], "shared")["stable_entries_hash"] == _segment_by_id(
        first["entries"], "shared"
    )["stable_entries_hash"]
    assert after_claim_layer_churn["revision"] == first["revision"]

    _write_json(paths["capture"], {"kind": "capture", "ordinal": 2})
    _run_catalog_v2(paths, generated_at_utc="2026-07-05T00:00:00Z")
    after_stable_churn = json.loads(paths["output"].read_text(encoding="utf-8"))

    assert _catalog_entry(after_stable_churn, "lane_alpha:capture")["sha256"] == _sha256_file(paths["capture"])
    assert after_stable_churn["integrity"]["stable_entries_hash"] != first["integrity"]["stable_entries_hash"]
    assert after_stable_churn["revision"] == first["revision"] + 1


def test_all_static_report_reclassification_would_violate_br1_hash_neutrality() -> None:
    """BR-1 guard: current accepted v3 bindings still include selected static reports in stable hashes."""

    entries = [
        _entry("e4_static:config/e4_lane_inventory", "1", lane_id=None, artifact_kind="config"),
        _entry(
            "e4_static:report/bb_e4_evidence_governance_json",
            "2",
            lane_id=None,
            artifact_kind="report",
        ),
        _entry(
            "e4_static:report/bb_e4_atomic_feature_ledger_report_json",
            "3",
            lane_id=None,
            artifact_kind="report",
        ),
        _entry("lane_alpha:capture", "4", artifact_kind="capture"),
        _entry("lane_alpha:support_claim", "5", artifact_kind="support_claim"),
        _entry("lane_alpha:support_claim_ref", "6", artifact_kind="support_claim"),
        _entry("lane_alpha:evidence_manifest", "7", artifact_kind="evidence_manifest"),
        _entry("lane_alpha:evidence_manifest_ref", "8", artifact_kind="evidence_manifest"),
        _entry("lane_alpha:node_gate", "9", artifact_kind="validator_output"),
        _entry("lane_alpha:validator_output", "a", artifact_kind="validator_output"),
    ]
    legacy_binding_subset = [
        entry
        for entry in entries
        if not (
            entry["role_id"].startswith("e4_static:report/")
            or entry["role_id"].rsplit(":", 1)[-1]
            in {
                "support_claim",
                "support_claim_ref",
                "evidence_manifest",
                "evidence_manifest_ref",
                "node_gate",
                "validator_output",
            }
        )
    ]

    assert [entry["role_id"] for entry in legacy_binding_subset] == [
        "e4_static:config/e4_lane_inventory",
        "lane_alpha:capture",
    ]
    assert stable_entries_hash(entries) != stable_entries_hash(legacy_binding_subset)


def test_live_catalog_stable_hash_matches_accepted_v3_binding() -> None:
    """BR-1 guard: current classifier must preserve accepted v3 catalog binding hashes."""

    repo_root = Path(__file__).resolve().parents[2]
    catalog = json.loads((repo_root / "docs/conformance/e4_artifact_catalog.json").read_text(encoding="utf-8"))
    claim = json.loads(
        (
            repo_root
            / "docs/conformance/support_claims/oh_my_pi_p3_1_effective_config_graph_compiler_v1_c4_support_claim.json"
        ).read_text(encoding="utf-8")
    )

    assert stable_entries_hash(catalog["entries"]) == claim["catalog_binding"]["catalog_hash"]
