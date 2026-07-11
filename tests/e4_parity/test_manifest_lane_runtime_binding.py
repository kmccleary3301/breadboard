from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from scripts.e4_parity import compile_lane_lock, lane_definitions
from scripts.e4_parity.adapters import oh_my_pi_compiler_capture as adapter
from scripts.e4_parity.lane_definitions import load_manifest_lane_def
from scripts.e4_parity.path_refs import ReferenceResolutionError, resolve_declared_reference

ROOT = Path(__file__).resolve().parents[2]
LANE_ID = "oh_my_pi_p6_6_task_job_subagent"
LANE_DIR = ROOT / "config" / "e4_lanes"
MANIFEST_PATH = LANE_DIR / f"{LANE_ID}.manifest.yaml"


def test_runtime_projection_consumes_sidecar_payloads_without_legacy_physical_inputs() -> None:
    """Every record-builder source is supplied virtually even though runtime capture declares sources only."""
    lane = load_manifest_lane_def(MANIFEST_PATH)
    config = lane["normalize"]["config"]
    virtual = config["runtime_payload_inputs"]

    assert virtual
    assert set(virtual).isdisjoint(lane["capture"]["inputs"])
    assert {builder["source"] for builder in config["record_builders"]} <= set(virtual)

    loaded = adapter._load_projection_inputs(lane)
    assert {reference: loaded[reference]["value"] for reference in virtual} == virtual


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
