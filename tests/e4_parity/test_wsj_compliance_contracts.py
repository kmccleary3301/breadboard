from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Mapping

from agentic_coder_prototype.conformance.catalog_binding import CATALOG_PATH, catalog_segment_hash
from scripts.e4_parity import build_artifact_catalog, generate_support_claims, regenerate_evidence, run_lane
from scripts.replay_session_from_records import replay_session_from_records


ROOT = Path(__file__).resolve().parents[2]
WORKSPACE = ROOT.parent
SUPPORT_CLAIMS_DIR = ROOT / "docs" / "conformance" / "support_claims"

EXPECTED_WSJ_LANES: dict[str, str] = {
    "claude_code_north_star_capture_v1": "claude_code_haiku45_north_star_capture_v1",
    "opencode_north_star_capture_v1": "opencode_gpt51mini_north_star_capture_v1",
    "breadboard_self_runtime_records_v1": "breadboard_self_runtime_records_v1",
}


def _load_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert isinstance(payload, dict), f"{path} must contain a JSON object"
    return payload


def _sha256_path(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return "sha256:" + digest.hexdigest()

def _ref_hash(ref_text: str) -> str | None:
    for part in ref_text.replace("#", " ").split():
        if part.startswith("sha256:") and len(part) == 71:
            return part
    return None



def _resolve_artifact_path(path_text: str) -> Path:
    raw_path = path_text.split("#", 1)[0]
    base = WORKSPACE if raw_path.startswith("docs_tmp/") else ROOT
    return base / raw_path


def _catalog_entries(catalog: Mapping[str, Any], lane_id: str) -> list[dict[str, Any]]:
    entries = catalog.get("entries")
    assert isinstance(entries, list), "catalog entries must be a list"
    lane_entries = [entry for entry in entries if isinstance(entry, dict) and entry.get("lane_id") == lane_id]
    assert lane_entries, f"catalog must contain entries for {lane_id}"
    return lane_entries


def _support_claim_path(config_id: str) -> Path:
    return SUPPORT_CLAIMS_DIR / f"{config_id}_c4_support_claim.json"


def _evidence_manifest_path(config_id: str) -> Path:
    return SUPPORT_CLAIMS_DIR / f"{config_id}_c4_evidence_manifest.json"


def test_regeneration_routes_wsj_packets_through_lane_runner() -> None:
    """North-star packet generation must be owned by the data-driven lane runner."""
    stages = {stage.stage_id: stage for stage in regenerate_evidence.STAGES}
    stage = stages["north_star_proof_packets"]

    assert stage.argv[:3] == ("{python}", "scripts/e4_parity/run_lane.py", "--lane")
    assert "north-star" in stage.argv
    assert "--promote-accepted" in stage.argv
    assert "build_north_star_proof_packets.py" not in " ".join(stage.argv)


def test_checked_in_wsj_support_claims_are_v3_and_segment_bound_to_fresh_catalog() -> None:
    """Accepted north-star claims must bind only their lane segment plus shared segment of the live catalog."""
    catalog = _load_json(ROOT / CATALOG_PATH)
    assert catalog.get("schema_version") == "bb.e4.artifact_catalog.v2"
    assert isinstance(catalog.get("revision"), int) and catalog["revision"] >= 1
    assert isinstance(catalog.get("segments"), list) and catalog["segments"]

    for lane_id, config_id in EXPECTED_WSJ_LANES.items():
        claim_path = _support_claim_path(config_id)
        claim = _load_json(claim_path)
        assert claim["schema_version"] == "bb.e4.support_claim.v3", claim_path.as_posix()
        assert claim["lane_id"] == lane_id
        assert claim["scope"]["lane_id"] == lane_id
        assert claim["scope"]["config_id"] == config_id

        binding = claim.get("catalog_binding")
        assert isinstance(binding, dict), claim_path.as_posix()
        assert binding == {
            "catalog_path": CATALOG_PATH,
            "segment_id": lane_id,
            "segment_hash": catalog_segment_hash(catalog, lane_id),
            "shared_segment_hash": catalog_segment_hash(catalog, "shared"),
        }
        assert "catalog_hash" not in binding
        assert "catalog_revision" not in binding


def test_breadboard_self_capture_replays_runtime_records_and_binds_transcript_digest() -> None:
    """Self-capture evidence must be replayed from runtime_records and carry a transcript digest."""
    result = run_lane.run_lane(
        "breadboard_self_runtime_records_v1",
        stage="capture",
        out_dir=None,
        promote_accepted=True,
    )
    assert result["ok"] is True, result
    assert result["stages"][0]["artifact_writer"] == "run_lane"

    build_artifact_catalog.main(["--schema-version", "v2", "--write-bindings"])
    generate_support_claims.generate(dry_run=False)
    build_artifact_catalog.main(["--schema-version", "v2", "--write-bindings"])
    run_dir = ROOT / "docs" / "conformance" / "e4_target_support" / "breadboard_self_runtime_records_v1" / "runtime_records"

    manifest = _load_json(run_dir / "manifest.json")
    assert manifest["session_transcript_digest"].startswith("sha256:")
    assert manifest["quarantine_count"] == 0
    assert manifest["counts_by_schema"]["bb.kernel_event.v2"] >= 3
    assert manifest["counts_by_schema"]["bb.session_transcript.v2"] == 1
    assert (run_dir / "records" / "bb.session_transcript.v2.jsonl").is_file()

    replay = replay_session_from_records(run_dir)
    assert replay["ok"] is True
    assert replay["transcript_digest"] == manifest["session_transcript_digest"]
    assert replay["manifest_transcript_digest"] == manifest["session_transcript_digest"]
    assert replay["final_transcript_digest"] == manifest["session_transcript_digest"]
    assert replay["reconstructed_items"] >= 2


def test_wsj_evidence_manifests_and_catalog_entries_have_fresh_hashes() -> None:
    """Checked-in WS-J manifests and catalog rows must bind current file hashes, with freeze rows scoped by config."""
    catalog = _load_json(ROOT / CATALOG_PATH)

    for lane_id, config_id in EXPECTED_WSJ_LANES.items():
        manifest_path = _evidence_manifest_path(config_id)
        manifest = _load_json(manifest_path)
        support_claim = _load_json(_support_claim_path(config_id))
        assert manifest["lane_id"] == lane_id
        artifacts = manifest.get("artifacts")
        assert isinstance(artifacts, list) and artifacts, manifest_path.as_posix()
        for artifact in artifacts:
            assert isinstance(artifact, dict), manifest_path.as_posix()
            artifact_path = _resolve_artifact_path(str(artifact["path"]))
            assert artifact_path.is_file(), artifact["path"]
            expected_hash = _ref_hash(str(support_claim["freeze_ref"])) if artifact.get("role") == "freeze_manifest" else _sha256_path(artifact_path)
            assert artifact["sha256"] == expected_hash, artifact["path"]

        for entry in _catalog_entries(catalog, lane_id):
            entry_path = _resolve_artifact_path(str(entry["path"]))
            assert entry_path.is_file(), entry["path"]
            assert entry["sha256"] == _sha256_path(entry_path), entry["path"]
