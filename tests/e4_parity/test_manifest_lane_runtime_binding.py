from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path

import pytest

from agentic_coder_prototype.conformance import c4_chain
from scripts.e4_parity import compile_lane_lock, lane_definitions
from scripts.e4_parity.adapters import oh_my_pi_compiler_capture as adapter
from scripts.e4_parity.lane_definitions import load_manifest_lane_def
from scripts.e4_parity.path_refs import ReferenceResolutionError, resolve_declared_reference

ROOT = Path(__file__).resolve().parents[2]
LANE_ID = "oh_my_pi_p6_6_task_job_subagent"
LANE_DIR = ROOT / "config" / "e4_lanes"
MANIFEST_PATH = LANE_DIR / f"{LANE_ID}.manifest.yaml"


def test_runtime_projection_requires_physical_canonical_ledger_and_prefers_its_bytes() -> None:
    """The lock-pinned physical ledger overrides the retired embedded ledger payload."""
    canonical_ledger_ref = (
        "config/e4_lanes/evidence_inputs/"
        "oh_my_pi_p6_6_atomic_feature_ledger.v1.json"
    )
    lane = load_manifest_lane_def(MANIFEST_PATH)
    config = lane["normalize"]["config"]
    virtual = config["runtime_payload_inputs"]

    assert virtual
    assert config["roles"]["atomic_feature_ledger"] == canonical_ledger_ref
    assert set(virtual).intersection(lane["capture"]["inputs"]) == {
        canonical_ledger_ref
    }
    assert {builder["source"] for builder in config["record_builders"]} <= set(virtual)

    loaded = adapter._load_projection_inputs(lane)
    canonical_bytes = (ROOT / canonical_ledger_ref).read_bytes()
    assert loaded[canonical_ledger_ref] == {
        "bytes": len(canonical_bytes),
        "path": canonical_ledger_ref,
        "sha256": f"sha256:{hashlib.sha256(canonical_bytes).hexdigest()}",
        "value": json.loads(canonical_bytes),
    }
    assert loaded[canonical_ledger_ref]["value"] != virtual[canonical_ledger_ref]
    virtual_only = set(virtual) - {canonical_ledger_ref}
    assert {
        reference: loaded[reference]["value"] for reference in virtual_only
    } == {reference: virtual[reference] for reference in virtual_only}


def test_runtime_loader_materializes_missing_source_freeze_extraction(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A clean checkout can load the manifest without a separate compiler bootstrap."""
    checkout = tmp_path / "checkout"
    lane_dir = checkout / "config" / "e4_lanes"
    archive_ref = compile_lane_lock.SOURCE_FREEZE_ARCHIVE_REF
    extraction_ref = compile_lane_lock.SOURCE_FREEZE_EXTRACTION_REF
    fixture_refs = (
        f"config/e4_lanes/{LANE_ID}.manifest.yaml",
        f"config/e4_lanes/{LANE_ID}.lock.json",
        f"config/e4_lanes/{LANE_ID}.packet_constants.v1.json",
        "config/e4_target_freeze_manifest.yaml",
        archive_ref,
    )
    for reference in fixture_refs:
        destination = checkout / reference
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / reference, destination)
    shutil.copytree(
        ROOT / "contracts" / "kernel" / "schemas",
        checkout / "contracts" / "kernel" / "schemas",
    )

    extraction_path = checkout / extraction_ref
    assert not extraction_path.exists()
    monkeypatch.setattr(lane_definitions, "ROOT", checkout)
    monkeypatch.setattr(compile_lane_lock, "ROOT", checkout)

    lane = load_manifest_lane_def(lane_dir / f"{LANE_ID}.manifest.yaml")

    assert (extraction_path / "package.json").is_file()
    assert extraction_ref in lane["capture"]["inputs"]




def test_repo_reference_kinds_missing_from_checkout_do_not_resolve_from_ancestor(
    tmp_path: Path,
) -> None:
    """Every repo-reference role is confined to the active checkout."""
    checkout = tmp_path / "nested" / "checkout"
    checkout.mkdir(parents=True)
    references = (
        ("pilot manifest", f"config/e4_lanes/{LANE_ID}.manifest.yaml"),
        ("pilot lock", f"config/e4_lanes/{LANE_ID}.lock.json"),
        (
            "packet constants sidecar",
            f"config/e4_lanes/{LANE_ID}.packet_constants.v1.json",
        ),
        ("target freeze manifest", "config/e4_target_freeze_manifest.yaml"),
        ("legacy lane definition", f"config/e4_lanes/{LANE_ID}.yaml"),
    )

    for label, reference in references:
        ancestor_reference = tmp_path / reference
        ancestor_reference.parent.mkdir(parents=True, exist_ok=True)
        ancestor_reference.write_text(f"{label}\n", encoding="utf-8")
        assert not (checkout / reference).exists()

        with pytest.raises(ReferenceResolutionError) as exc_info:
            resolve_declared_reference(
                reference,
                namespace="repo",
                checkout_root=checkout,
                label=label,
            )

        message = str(exc_info.value)
        assert label in message
        assert reference in message
        assert str(checkout) in message
        assert "missing" in message.lower()


def test_c4_chain_resolves_explicit_workspace_evidence(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = tmp_path / "workspace"
    checkout = tmp_path / "checkout"
    ledger_path = workspace / "docs_tmp" / "phase_15" / "ledger.json"
    checkout.mkdir()
    ledger_path.parent.mkdir(parents=True)
    ledger_path.write_text('{"features":[]}\n', encoding="utf-8")
    monkeypatch.setenv("BB_WORKSPACE_ROOT", str(workspace))

    assert (
        c4_chain._resolve_path(
            checkout,
            "docs_tmp/phase_15/ledger.json#feature-id#sha256:" + "a" * 64,
        )
        == ledger_path
    )


def test_c4_chain_fails_closed_without_workspace_provisioning(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    checkout = tmp_path / "checkout"
    checkout.mkdir()
    monkeypatch.delenv("BB_WORKSPACE_ROOT", raising=False)

    with pytest.raises(c4_chain.C4ChainValidationError, match="BB_WORKSPACE_ROOT is required"):
        c4_chain._resolve_path(checkout, "docs_tmp/phase_15/ledger.json")
