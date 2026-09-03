from __future__ import annotations
import asyncio
import os
from datetime import UTC, datetime
from pathlib import Path
import pytest
from pydantic import ValidationError
from breadboard_engine.compilation.contracts import canonical_json_bytes
from breadboard.rl.harness import contracts as c
from breadboard.rl.harness.composition import (
    CASConfigRuntimeStore,
    HarnessCompositionManifestV1,
    HarnessCompositionManifestV2,
    load_production_composition,
)
from breadboard.rl.phase5.f3_authority_authoring import F3AuthorityInput
from breadboard.rl.phase5.f4_campaign import (
    OPTIMIZER_RECEIPT_KINDS,
    F4OptimizerWorkPacket,
)
from breadboard.rl.phase5.f4_authority_authoring import (
    F4AuthorityAuthoringInput,
    F4ConfigVariantAuthoring,
    F4ExecutionAuthorityAuthoring,
    F4OptimizerReceiptAuthoring,
    build_f4_target_input,
)
from breadboard.artifacts.cas import FilesystemCAS
from scripts.rl_phase5.run_f4_target_canaries import (
    F4TargetCanaryInput,
    F4TargetIdentity,
    ImmutableRef,
    VARIANT_IDS,
)
from tests.rl.phase5.test_f3_authority_authoring import _spec as authority_spec
from tests.rl.phase5.test_f3_composition import _composition_spec


class _FixedClock:
    def now(self) -> datetime:
        return datetime(2026, 7, 13, 0, 30, tzinfo=UTC)

    def current(self) -> datetime:
        return self.now()


@pytest.fixture(autouse=True)
def _fixed_clock(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "breadboard.rl.harness.composition._SystemUTCClock",
        _FixedClock,
    )


def _base(tmp: Path, production: object) -> F3AuthorityInput:
    value = authority_spec(tmp).model_dump(mode="json")
    installed = {item.runtime_id: item for item in production.installed.runtimes}
    for name in ("primary_runtime", "verifier_runtime"):
        runtime = installed[value[name]["runtime_id"]]
        value[name]["driver_implementation_digest"] = (
            runtime.driver_implementation_digest
        )
        value[name]["runtime_binary"] = {
            "immutable_reference": f"file://runtime@{runtime.measured_binary_digest}",
            "digest": runtime.measured_binary_digest,
        }
        value[name]["oci_runtime_binary"] = {
            "immutable_reference": f"file://oci@{runtime.oci_runtime_binary_digest}",
            "digest": runtime.oci_runtime_binary_digest,
        }
    return F3AuthorityInput.model_validate_json(
        canonical_json_bytes(value), strict=True
    )


def _spec(tmp: Path) -> F4AuthorityAuthoringInput:
    fixture = tmp / "fixture"
    fixture.mkdir()
    production, _ = _composition_spec(fixture)
    seed = "sha256:" + "1" * 64
    workspace = tmp / "workspace"
    workspace.mkdir()
    docker_socket = tmp / "docker.sock"
    docker_socket.write_bytes(b"observed-docker-socket")
    return F4AuthorityAuthoringInput(
        schema_version="bb.rl.phase5-f4-authority-authoring-input.v1",
        base_authority=_base(fixture, production),
        production_template=production,
        target=F4TargetIdentity(
            target_run_id="f4-real-run",
            target_job_id="f4-real-job",
            target_node_id="f4-real-node",
        ),
        execution_authority=F4ExecutionAuthorityAuthoring(
            environment_id="f4-test-environment",
            environment_ref=ImmutableRef(
                reference="cas://environment/test@sha256:" + "2" * 64,
                digest="sha256:" + "2" * 64,
            ),
            source_runtime_ref=ImmutableRef(
                reference="cas://runtime/test@sha256:" + "3" * 64,
                digest="sha256:" + "3" * 64,
            ),
            runtime_class="docker",
            python_executable="/usr/bin/python3",
            docker_socket_path=os.fspath(docker_socket.resolve()),
            workspace_root=os.fspath(workspace.resolve()),
            docker_image="f4-target-image@sha256:" + "4" * 64,
            service_factory="production-composition",
        ),
        variants=tuple(
            F4ConfigVariantAuthoring(
                variant_id=name,
                prompt=f"F4 prompt {index}: repair and submit.",
                weight=2**53 - 2,
                selection_seed=seed,
                paired_variant_id=VARIANT_IDS[(index + 1) % len(VARIANT_IDS)],
                paired_weight=1,
                base_temperature=1.0,
                overlay_temperature=(index + 1) / 10,
                optimizer_receipts=tuple(
                    F4OptimizerReceiptAuthoring(receipt_kind=receipt_kind)
                    for receipt_kind in OPTIMIZER_RECEIPT_KINDS
                ),
                paired_ab_evaluation_count=20,
                primary_improvement=2.0,
                aa_noise_upper_bound=1.0,
                secondary_cost_reduction=0.0,
                required_secondary_cost_reduction=0.0,
                held_out_repeat_count=1,
                acceptance_basis="improved-beyond-aa-noise",
            )
            for index, name in enumerate(VARIANT_IDS)
        ),
        task_input={"prompt": "repair admitted repository"},
        run_context={"campaign": "f4-real"},
        target_report_output_dir=os.fspath((tmp / "target-report").resolve()),
    )


def test_v1_canonical_bytes_unchanged_and_v2_bundle_set_is_closed(
    tmp_path: Path,
) -> None:
    fixture = tmp_path / "fixture"
    fixture.mkdir()
    production, _ = _composition_spec(fixture)
    from breadboard.rl.phase5.f3_composition import build_f3_production_composition

    built = build_f3_production_composition(
        production, os.fspath((tmp_path / "v1").resolve())
    )
    raw = Path(built.composition_manifest_path).read_bytes()
    v1 = HarnessCompositionManifestV1.model_validate_json(raw, strict=True)
    assert v1.canonical_bytes() == raw
    payload = v1.model_dump(mode="json")
    payload.pop("config_bundle_ref")
    payload["schema_version"] = "bb.rl.harness-composition.v2"
    ref = v1.config_bundle_ref.model_dump(mode="json")
    payload["config_bundle_refs"] = [ref, ref]
    with pytest.raises(ValidationError, match="sorted and unique"):
        HarnessCompositionManifestV2.model_validate_json(
            canonical_json_bytes(payload), strict=True
        )
    payload["config_bundle_refs"] = []
    with pytest.raises(ValidationError):
        HarnessCompositionManifestV2.model_validate_json(
            canonical_json_bytes(payload), strict=True
        )
    false = dict(ref)
    false["sha256"] = "sha256:" + "f" * 64
    payload["config_bundle_refs"] = [false, ref]
    with pytest.raises(ValidationError, match="sorted and unique"):
        HarnessCompositionManifestV2.model_validate_json(
            canonical_json_bytes(payload), strict=True
        )


def test_builds_and_real_loader_resolves_six_distinct_config_candidates(
    tmp_path: Path,
) -> None:
    descriptor = build_f4_target_input(
        _spec(tmp_path), os.fspath((tmp_path / "f4").resolve())
    )
    raw = Path(descriptor.target_input_path).read_bytes()
    target = F4TargetCanaryInput.model_validate_json(raw, strict=True)
    assert canonical_json_bytes(target.model_dump(mode="json")) == raw
    assert len(set(descriptor.optimizer_work_packet_sha256s)) == 6
    optimizer_root = Path(descriptor.target_input_path).parent / "artifacts" / (
        "optimizer-work-packets"
    )
    for index, variant_id in enumerate(VARIANT_IDS):
        packet_raw = (
            optimizer_root / f"{variant_id}-work-packet.json"
        ).read_bytes()
        assert (
            "sha256:" + __import__("hashlib").sha256(packet_raw).hexdigest()
            == descriptor.optimizer_work_packet_sha256s[index]
        )
        packet = F4OptimizerWorkPacket.model_validate_json(packet_raw, strict=True)
        assert tuple(
            binding.artifact.receipt_kind
            for binding in packet.ordered_receipts
        ) == OPTIMIZER_RECEIPT_KINDS
    assert tuple(item.variant_id for item in target.variants) == VARIANT_IDS
    for projection in (
        "config_bundle_ref",
        "dependency_closure_ref",
        "compiled_config_ref",
        "admission_receipt_ref",
        "selection_record_ref",
    ):
        assert len({getattr(item, projection).digest for item in target.variants}) == 6
    assert len({item.compiler_identity_ref.digest for item in target.variants}) == 1
    composition_descriptor = __import__("json").loads(
        Path(descriptor.composition_ref_path).read_bytes()
    )
    manifest = HarnessCompositionManifestV2.model_validate_json(
        Path(composition_descriptor["manifest_path"]).read_bytes(),
        strict=True,
    )
    assert len(manifest.config_bundle_refs) == 6
    if Path("/proc/self/mountinfo").exists():
        composition = load_production_composition(
            target.production.composition_ref_path,
            target.production.secret_files,
        )
        try:
            for index, item in enumerate(target.variants):
                resolved = (
                    composition.authority_graph.config_runtime.resolve_episode(
                        item.request
                    )
                )
                plan = resolved.effective_plan
                assert len(plan.overlay_applications) == 1
                assert plan.effective_semantics["sampling"]["temperature"] == pytest.approx(
                    (index + 1) / 10
                )
                root_id = plan.effective_semantics["root_config_node_id"]
                root_node = next(
                    node
                    for node in plan.effective_semantics["config_nodes"]
                    if node["node_id"] == root_id
                )
                assert (
                    root_node["semantic_config"]["sampling"]["temperature"]
                    == plan.effective_semantics["sampling"]["temperature"]
                )
        finally:
            asyncio.run(composition.close())
    cas = FilesystemCAS(manifest.stores.cas.path)
    store = CASConfigRuntimeStore(cas)
    try:
        for item in target.variants:
            selection_raw = store.load(
                item.selection_record_ref.digest,
                kind=c.ArtifactKind.SELECTION_RECORD,
                max_bytes=4 * 1024 * 1024,
            )
            selection = c.SelectionRecord.model_validate_json(
                selection_raw, strict=True
            )
            assert selection.selected_candidate_id == item.variant_id
            assert selection.canonical_digest() == item.selection_record_ref.digest
            receipt_raw = store.load(
                item.admission_receipt_ref.digest,
                kind=c.ArtifactKind.ADMISSION_RECEIPT,
                max_bytes=4 * 1024 * 1024,
            )
            receipt = c.AdmissionReceipt.model_validate_json(receipt_raw, strict=True)
            assert receipt.compiled.manifest_digest == item.compiled_config_ref.digest
    finally:
        cas.close()


def test_v2_loader_rejects_manifest_bundle_mismatch(tmp_path: Path) -> None:
    descriptor = build_f4_target_input(
        _spec(tmp_path), os.fspath((tmp_path / "f4").resolve())
    )
    target = F4TargetCanaryInput.model_validate_json(
        Path(descriptor.target_input_path).read_bytes(), strict=True
    )
    composition_ref = __import__("json").loads(
        Path(descriptor.composition_ref_path).read_bytes()
    )
    manifest_path = Path(composition_ref["manifest_path"])
    value = __import__("json").loads(manifest_path.read_bytes())
    value["config_bundle_refs"] = value["config_bundle_refs"][:-1]
    replacement = canonical_json_bytes(value)
    manifest_path.chmod(0o600)
    manifest_path.write_bytes(replacement)
    with pytest.raises(
        ValueError, match="size mismatch|digest mismatch"
    ):
        load_production_composition(
            target.production.composition_ref_path, target.production.secret_files
        )
