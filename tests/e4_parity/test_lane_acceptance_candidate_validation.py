from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from breadboard.product.evidence.e4 import lane_acceptance_artifacts as builder
from breadboard.product.evidence.e4.adapters import oh_my_pi_p3_remaining_capture as p3_capture

ROOT = Path(__file__).resolve().parents[2]


def _write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, sort_keys=True) + "\n", encoding="utf-8")


def _candidate_spec() -> dict[str, object]:
    return {
        "assertions": [("candidate_bytes_current", "candidate bytes are current")],
        "behavior_family": "candidate_validation",
        "claim_id": "candidate_validation_v1_c4_support_claim",
        "config_id": "candidate_validation_v1",
        "config_path": "agent_configs/candidate_validation.yaml",
        "ct_id": "ct_candidate_validation",
        "lane_id": "breadboard_self_runtime_records_v1",
        "lane_status": "accepted",
        "package_ref": "config/candidate_source.json",
        "primitive": "candidate_validation",
        "provider_model": "none",
        "run_id": "candidate-validation-run",
        "sandbox_mode": "read-only",
        "semantic_key": "candidate_validation",
        "source_paths": [
            "config/candidate_source.json",
        ],
        "target": "breadboard",
        "target_family": "breadboard",
        "target_version": "candidate",
        "upstream_commit": "a" * 40,
        "upstream_commit_date": "2026-07-11T00:00:00Z",
        "upstream_release_label": "candidate",
        "upstream_repo": "https://example.invalid/candidate",
    }


def _candidate_fixture(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> tuple[dict[str, object], Path, Path, Path, Path]:
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
    (repo_root / "conformance/comparators").mkdir(parents=True)
    shutil.copy2(
        ROOT / "conformance/comparators/registry.json",
        repo_root / "conformance/comparators/registry.json",
    )

    monkeypatch.setattr(builder, "ROOT", repo_root)
    monkeypatch.setattr(builder, "FREEZE_MANIFEST_PATH", freeze_path)
    monkeypatch.setattr(builder, "SUPPORT_DIR", support_dir)
    monkeypatch.setattr(builder, "NODE_GATE_DIR", node_gate_dir)
    monkeypatch.setattr(builder, "CATALOG_PATH", catalog_path)
    monkeypatch.setenv("BB_WORKSPACE_ROOT", str(workspace))

    runtime_relpaths = (
        "docs/conformance/e4_target_support/breadboard_self_runtime_records_v1/runtime_records/manifest.json",
        "docs/conformance/e4_target_support/breadboard_self_runtime_records_v1/runtime_records/records/bb.kernel_event.v2.jsonl",
        "docs/conformance/e4_target_support/breadboard_self_runtime_records_v1/runtime_records/records/bb.session_transcript.v2.jsonl",
    )

    def emit_candidate_runtime_records(
        physical_lane_dir: Path,
        spec: object,
        logical_lane_dir: Path,
    ) -> list[str]:
        del physical_lane_dir
        assert spec
        assert logical_lane_dir == repo_root / "docs/conformance/e4_target_support/breadboard_self_runtime_records_v1"
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

    spec = _candidate_spec()
    ledger_row = {"feature_id": builder.feature_id(spec)}
    _write_json(ledger_path, {"rows": [ledger_row]})
    return spec, repo_root, output_root, freeze_path, support_dir


def test_build_lane_validates_fresh_candidate_bytes_with_real_validator(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    spec, repo_root, output_root, _freeze_path, support_dir = _candidate_fixture(
        tmp_path, monkeypatch
    )
    canonical_registry = repo_root / "conformance/comparators/registry.json"
    canonical_support = support_dir / f"{spec['claim_id']}.json"
    canonical_manifest = support_dir / "candidate_validation_v1_c4_evidence_manifest.json"
    _write_json(canonical_support, {"schema_version": "bb.e4.support_claim.v2", "stale": True})
    _write_json(canonical_manifest, {"schema_version": "bb.e4.evidence_manifest.v1", "stale": True})

    result = builder.build_lane(spec, output_root=output_root)

    assert result["ok"] is True, result["errors"]
    candidate_registry = output_root / "conformance/comparators/registry.json"
    assert candidate_registry.is_file()
    assert candidate_registry.read_bytes() == canonical_registry.read_bytes()
    assert canonical_support.read_text(encoding="utf-8").find('"stale": true') >= 0
    assert canonical_manifest.read_text(encoding="utf-8").find('"stale": true') >= 0


def test_reused_candidate_registry_rejects_stale_bytes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    spec, _repo_root, output_root, _freeze_path, _support_dir = _candidate_fixture(
        tmp_path, monkeypatch
    )
    stale_registry = output_root / "conformance/comparators/registry.json"
    stale_registry.parent.mkdir(parents=True, exist_ok=True)
    stale_bytes = b'{"registry":"stale"}\n'
    stale_registry.write_bytes(stale_bytes)
    with pytest.raises(ValueError, match="differs from the canonical registry"):
        builder.build_lane(spec, output_root=output_root)

    assert stale_registry.read_bytes() == stale_bytes
    outside = tmp_path / "outside-registry.json"
    outside_bytes = b'{"registry":"outside"}\n'
    outside.write_bytes(outside_bytes)
    stale_registry.unlink()
    stale_registry.symlink_to(outside)

    with pytest.raises(ValueError, match="must not be a symlink"):
        builder.build_lane(spec, output_root=output_root)

    assert outside.read_bytes() == outside_bytes


def test_p3_candidate_validation_uses_contained_registry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    spec, repo_root, output_root, freeze_path, support_dir = _candidate_fixture(
        tmp_path, monkeypatch
    )
    builder.build_lane(spec, output_root=output_root)
    candidate_registry = output_root / "conformance/comparators/registry.json"
    candidate_registry.write_text('{"comparators":[]}\n', encoding="utf-8")

    monkeypatch.setattr(p3_capture, "ROOT", repo_root)
    monkeypatch.setattr(p3_capture, "FREEZE_MANIFEST_PATH", freeze_path)
    support_path = output_root / "docs/conformance/support_claims" / f"{spec['claim_id']}.json"
    manifest_path = output_root / "docs/conformance/support_claims/candidate_validation_v1_c4_evidence_manifest.json"
    logical_support_path = support_dir / f"{spec['claim_id']}.json"
    logical_manifest_path = support_dir / "candidate_validation_v1_c4_evidence_manifest.json"

    report = p3_capture.validate_candidate_c4_chain(
        config_id=str(spec["config_id"]),
        physical_support_claim_path=support_path,
        physical_evidence_manifest_path=manifest_path,
        logical_support_claim_path=logical_support_path,
        logical_evidence_manifest_path=logical_manifest_path,
        candidate_root=output_root,
        materialized_sources=(),
        temp_prefix=".candidate-validation-test-",
    )

    assert report["ok"] is True, report["errors"]
    assert report["comparator_rerun"]["registry"] == "conformance/comparators/registry.json"

