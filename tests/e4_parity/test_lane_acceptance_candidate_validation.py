from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path
import yaml

from scripts.e4_parity import lane_acceptance_artifacts as builder

ROOT = Path(__file__).resolve().parents[2]
CHECKOUT_FREEZE_PROVENANCE = (
    "config/e4_lanes/evidence_inputs/"
    "oh_my_pi_main_5356713e_freeze_provenance.v1.json"
)


def _write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, sort_keys=True) + "\n", encoding="utf-8")


def test_build_lane_validates_fresh_candidate_bytes_before_promotion(
    tmp_path: Path, monkeypatch
) -> None:
    repo_root = tmp_path / "checkout"
    output_root = tmp_path / "candidate"
    workspace = tmp_path / "workspace"
    config_path = repo_root / "agent_configs/candidate_validation.yaml"
    source_path = repo_root / "config/candidate_source.json"
    freeze_path = repo_root / "config/e4_target_freeze_manifest.yaml"
    support_dir = repo_root / "docs/conformance/support_claims"
    node_gate_dir = repo_root / "artifacts/conformance/node_gate"
    ledger_path = workspace / "docs_tmp/phase_15/BB_E4_ATOMIC_FEATURE_LEDGER_SEED.json"
    catalog_path = repo_root / "docs/conformance/e4_artifact_catalog.json"

    config_path.parent.mkdir(parents=True)
    config_path.write_text("provider: candidate\n", encoding="utf-8")
    _write_json(source_path, {"fresh": True})
    freeze_path.parent.mkdir(parents=True, exist_ok=True)
    freeze_path.write_text("e4_configs: {}\n", encoding="utf-8")
    _write_json(ledger_path, {"rows": []})
    _write_json(catalog_path, {"schema_version": "bb.e4.artifact_catalog.v2"})

    claim_id = "candidate_validation_v1_c4_support_claim"
    canonical_support = support_dir / f"{claim_id}.json"
    canonical_manifest = support_dir / "candidate_validation_v1_c4_evidence_manifest.json"
    _write_json(canonical_support, {"schema_version": "bb.e4.support_claim.v2", "stale": True})
    _write_json(canonical_manifest, {"schema_version": "bb.e4.evidence_manifest.v1", "stale": True})

    monkeypatch.setattr(builder, "ROOT", repo_root)
    monkeypatch.setattr(builder, "WORKSPACE", workspace)
    monkeypatch.setattr(builder, "FREEZE_MANIFEST_PATH", freeze_path)
    monkeypatch.setattr(builder, "LEDGER_PATH", ledger_path)
    monkeypatch.setattr(builder, "SUPPORT_DIR", support_dir)
    monkeypatch.setattr(builder, "NODE_GATE_DIR", node_gate_dir)
    monkeypatch.setattr(builder, "CATALOG_PATH", catalog_path)
    runtime_relpaths = (
        "docs/conformance/e4_target_support/candidate_validation/runtime_records/manifest.json",
        "docs/conformance/e4_target_support/candidate_validation/runtime_records/records/bb.kernel_event.v2.jsonl",
        "docs/conformance/e4_target_support/candidate_validation/runtime_records/records/bb.session_transcript.v2.jsonl",
    )

    def emit_candidate_runtime_records(
        physical_lane_dir: Path,
        spec: object,
        logical_lane_dir: Path,
    ) -> list[str]:
        assert spec
        assert logical_lane_dir == repo_root / "docs/conformance/e4_target_support/candidate_validation"
        for index, relative in enumerate(runtime_relpaths):
            path = output_root / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(
                json.dumps({"candidate_runtime_record": index}) + "\n",
                encoding="utf-8",
            )
        return list(runtime_relpaths)

    monkeypatch.setattr(
        builder, "emit_self_runtime_records", emit_candidate_runtime_records
    )
    monkeypatch.setattr(
        builder,
        "replay_session_from_records",
        lambda _runtime_dir: {"ok": True},
    )
    monkeypatch.setattr(builder, "ledger_row_ref", lambda spec: "ledger.json#feat#sha256:" + "0" * 64)
    monkeypatch.setattr(
        builder,
        "catalog_binding",
        lambda lane_id, prior_binding=None: {
            "catalog_path": "docs/conformance/e4_artifact_catalog.json",
            "catalog_revision": 1,
            "segment_id": lane_id,
            "segment_hash": "sha256:" + "1" * 64,
            "shared_segment_hash": "sha256:" + "2" * 64,
        },
    )

    def validate_candidate(**kwargs):
        support_path = output_root / canonical_support.relative_to(repo_root)
        manifest_path = output_root / canonical_manifest.relative_to(repo_root)
        candidate_freeze = output_root / freeze_path.relative_to(repo_root)
        if kwargs != {
            "repo_root": output_root,
            "freeze_manifest_path": candidate_freeze,
            "config_id": "candidate_validation_v1",
            "support_claim_path": support_path,
            "evidence_manifest_path": manifest_path,
            "enforce_catalog_binding": False,
        }:
            return {"ok": False, "errors": ["PIN_STALE: validation read canonical pre-capture bytes"]}
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        support_artifact = next(
            item
            for item in manifest["artifacts"]
            if item["role"] == "support_claim_ref"
        )
        session_artifact = next(
            item
            for item in manifest["artifacts"]
            if item["role"] == "session_transcript"
        )
        expected_hashes = {
            support_artifact["sha256"]: "sha256:"
            + hashlib.sha256(support_path.read_bytes()).hexdigest(),
            session_artifact["sha256"]: "sha256:"
            + hashlib.sha256(
                (output_root / session_artifact["path"]).read_bytes()
            ).hexdigest(),
        }
        current = all(
            declared == actual for declared, actual in expected_hashes.items()
        )
        return {
            "ok": current,
            "errors": [] if current else ["PIN_STALE"],
        }

    monkeypatch.setattr(builder, "validate_c4_chain", validate_candidate)
    spec = {
        "assertions": [("candidate_bytes_current", "candidate bytes are current")],
        "behavior_family": "candidate_validation",
        "claim_id": claim_id,
        "config_id": "candidate_validation_v1",
        "config_path": "agent_configs/candidate_validation.yaml",
        "ct_id": "ct_candidate_validation",
        "lane_id": "candidate_validation",
        "lane_status": "accepted",
        "package_ref": "config/candidate_source.json",
        "primitive": "candidate_validation",
        "provider_model": "none",
        "run_id": "candidate-validation-run",
        "sandbox_mode": "read-only",
        "semantic_key": "candidate_validation",
        "source_paths": ["config/candidate_source.json"],
        "target": "breadboard",
        "target_family": "breadboard",
        "target_version": "candidate",
        "upstream_commit": "a" * 40,
        "upstream_commit_date": "2026-07-11T00:00:00Z",
        "upstream_release_label": "candidate",
        "upstream_repo": "https://example.invalid/candidate",
    }

    result = builder.build_lane(spec, output_root=output_root)

    assert result["ok"] is True
    assert canonical_support.read_text(encoding="utf-8").find('"stale": true') >= 0
    assert canonical_manifest.read_text(encoding="utf-8").find('"stale": true') >= 0


def test_oh_my_pi_freeze_provenance_is_checkout_local_and_tracked() -> None:
    tracked = {
        line
        for line in subprocess.run(
            ["git", "ls-files"],
            cwd=ROOT,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.splitlines()
    }
    declared_freeze_inputs: set[str] = set()
    declaring_lanes: set[str] = set()
    for lane_path in (ROOT / "config/e4_lanes").glob("oh_my_pi*.yaml"):
        lane = yaml.safe_load(lane_path.read_text(encoding="utf-8"))
        references = [
            *lane.get("capture", {}).get("inputs", []),
            *lane.get("provenance", {}).get("source_paths", []),
        ]
        lane_freeze_inputs = {
            reference
            for reference in references
            if reference.endswith("freeze_provenance.v1.json")
            or reference.endswith("_freeze_provenance.json")
        }
        if lane_freeze_inputs:
            declaring_lanes.add(str(lane["lane_id"]))
            declared_freeze_inputs.update(lane_freeze_inputs)

    assert len(declaring_lanes) == 15
    assert declared_freeze_inputs == {CHECKOUT_FREEZE_PROVENANCE}
    assert CHECKOUT_FREEZE_PROVENANCE in tracked
    assert (ROOT / CHECKOUT_FREEZE_PROVENANCE).is_file()
