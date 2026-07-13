from __future__ import annotations

from scripts.e4_parity import build_binding_topology_reports as reports


def test_catalog_segments_are_content_addressed_and_revision_informational() -> None:
    payload = reports.build_catalog_segments()

    assert payload["schema_version"] == "bb.e4.artifact_catalog_segments.v2"
    assert payload["revision_semantics"] == "informational_only_content_addressed_segments_are_authoritative"
    assert payload["segments"]
    assert payload["segments_hash"].startswith("sha256:")
    assert all(segment["stable_entries_hash"].startswith("sha256:") for segment in payload["segments"])


def test_support_claim_binding_report_keeps_foreign_claims_read_only() -> None:
    segments = reports.build_catalog_segments()
    payload = reports.build_claim_binding_report(segments)

    assert payload["schema_version"] == "bb.e4.support_claim_v3_binding_report.v1"
    assert payload["claim_count"] > 0
    assert payload["foreign_claim_count"] == sum(1 for claim in payload["claims"] if claim["foreign_claim"])
    assert all(claim["shared_segment"] == "shared" for claim in payload["claims"])


def test_phase15_archive_manifest_is_content_addressed() -> None:
    payload = reports.build_phase15_archive_manifest()

    assert payload["schema_version"] == "bb.e4.phase15_archive_manifest.v1"
    assert payload["entry_count"] > 0
    assert payload["entries_hash"].startswith("sha256:")
    assert all(entry["sha256"].startswith("sha256:") for entry in payload["entries"])


def test_hot_cold_traversal_and_parallel_merge_proof() -> None:
    traversal = reports.build_hot_cold_traversal_report()
    proof = reports.build_parallel_merge_proof()

    assert traversal["schema_version"] == "bb.e4.docs_conformance_traversal.v1"
    assert traversal["file_count"] == traversal["hot_file_count"] + traversal["cold_file_count"]
    assert traversal["hot_file_count"] > 0
    assert proof["schema_version"] == "bb.e4.parallel_lane_merge_proof.v1"
    assert proof["clean_merge"] is True
    assert proof["lane_file_count"] == 2
