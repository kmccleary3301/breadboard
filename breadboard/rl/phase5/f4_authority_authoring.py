from __future__ import annotations

import copy
import hashlib
import math
import os
import shutil
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Literal

from agentic_coder_prototype.compilation.bundle import (
    ManifestReader,
    build_dependency_closure,
    ingest_member_map,
)
from agentic_coder_prototype.compilation.contracts import (
    ConfigBundleManifest,
    CompiledConfigManifest,
    DependencyClosureManifest,
    canonical_json_bytes,
    canonical_json_loads,
)
from agentic_coder_prototype.compilation.server_compiler import compile_config
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from breadboard.rl.harness import contracts as c
from breadboard.rl.harness.composition import (
    COMPOSITION_MEDIA_TYPE,
    ArtifactFileRefV1,
    AuthorityBundleV1,
    CASConfigRuntimeStore,
    CompositionRefV2,
    HarnessCompositionManifestV1,
    HarnessCompositionManifestV2,
    SelectorCatalogV1,
    _build_authority_graph,
)
from breadboard.rl.phase5.f3_authority_authoring import (
    F3AuthorityBundleManifest,
    F3AuthorityInput,
    _read_signing_key,
    _compiled_identity,
    build_f3_authority,
)
from breadboard.rl.phase5.f3_composition import (
    F3ProductionCompositionInput,
    SourceArtifact,
    build_f3_production_composition,
)
from breadboard.rl.phase5.f4_campaign import (
    OPTIMIZER_RECEIPT_KINDS,
    CampaignInvariantIdentity,
    CompilerVisibleSemanticDelta,
    F4OptimizerAANoiseFacts,
    F4OptimizerConstraintFacts,
    F4OptimizerDispositionFacts,
    F4OptimizerGenerationFacts,
    F4OptimizerHeldOutFacts,
    F4OptimizerObjectiveFacts,
    F4OptimizerPairedABFacts,
    F4OptimizerReceiptBinding,
    F4OptimizerReceiptBody,
    F4OptimizerWorkPacket,
    F4OptimizerWorkPacketBinding,
    F4OptimizerSourceFacts,
    ImmutableRef,
    OptimizerReceiptKind,
)
from breadboard.rl.state.cas import FilesystemCAS
from scripts.rl_phase5.run_f4_target_canaries import (
    F4ProductionBinding,
    F4TargetCanaryInput,
    F4TargetIdentity,
    F4TargetExecutionAuthority,
    F4VariantExecution,
    VARIANT_IDS,
)


_OVERLAY_POINTERS = ("/sampling/temperature",)


class F4AuthorityAuthoringError(RuntimeError):
    pass


class _ExactModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


def _d(raw: bytes) -> str:
    return "sha256:" + hashlib.sha256(raw).hexdigest()


def _digest(value: str) -> str:
    if (
        type(value) is not str
        or len(value) != 71
        or not value.startswith("sha256:")
        or any(ch not in "0123456789abcdef" for ch in value[7:])
    ):
        raise ValueError("lowercase sha256 digest required")
    return value


def _identifier(value: str) -> str:
    if type(value) is not str or not value or value.strip() != value:
        raise ValueError("nonblank identifier required")
    return value


def _absolute(value: str) -> str:
    if (
        type(value) is not str
        or not value.startswith("/")
        or os.path.normpath(value) != value
    ):
        raise ValueError("absolute normalized path required")
    return value


def _iref(label: str, digest: str) -> ImmutableRef:
    return ImmutableRef(reference=f"cas://phase5-f4/{label}@{digest}", digest=digest)


def _fref(path: Path, raw: bytes, media: str) -> ArtifactFileRefV1:
    return ArtifactFileRefV1(
        path=os.fspath(path.resolve()),
        sha256=_d(raw),
        size_bytes=len(raw),
        media_type=media,
    )


def _write(path: Path, raw: bytes) -> None:
    fd = os.open(
        path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_CLOEXEC", 0), 0o600
    )
    try:
        os.write(fd, raw)
        os.fsync(fd)
    finally:
        os.close(fd)


class F4OptimizerReceiptAuthoring(_ExactModel):
    receipt_kind: OptimizerReceiptKind


class F4ConfigVariantAuthoring(_ExactModel):
    variant_id: Literal[
        "codex-like",
        "claude-like",
        "pi-like",
        "opencode",
        "oh-my-opencode",
        "unknown-name",
    ]
    prompt: str = Field(min_length=1, max_length=32768)
    weight: int = Field(gt=0, le=2**53 - 1)
    selection_seed: str
    paired_variant_id: Literal[
        "codex-like",
        "claude-like",
        "pi-like",
        "opencode",
        "oh-my-opencode",
        "unknown-name",
    ]
    paired_weight: int = Field(gt=0, le=2**53 - 1)

    base_temperature: float = Field(ge=0, le=2)
    overlay_temperature: float = Field(ge=0, le=2)
    optimizer_receipts: tuple[
        F4OptimizerReceiptAuthoring,
        F4OptimizerReceiptAuthoring,
        F4OptimizerReceiptAuthoring,
        F4OptimizerReceiptAuthoring,
        F4OptimizerReceiptAuthoring,
        F4OptimizerReceiptAuthoring,
        F4OptimizerReceiptAuthoring,
        F4OptimizerReceiptAuthoring,
    ]
    paired_ab_evaluation_count: int = Field(ge=20, le=1_000_000)
    primary_improvement: float = Field(ge=0)
    aa_noise_upper_bound: float = Field(ge=0)
    secondary_cost_reduction: float = Field(ge=0)
    required_secondary_cost_reduction: float = Field(ge=0)
    held_out_repeat_count: int = Field(ge=1, le=1_000_000)
    acceptance_basis: Literal[
        "improved-beyond-aa-noise", "tie-with-lower-secondary-cost"
    ]
    _seed = field_validator("selection_seed")(_digest)

    @model_validator(mode="after")
    def frozen_pair(self) -> "F4ConfigVariantAuthoring":
        if self.paired_variant_id == self.variant_id:
            raise ValueError("F4 A/B pair must contain two distinct variants")
        if (
            not math.isfinite(self.base_temperature)
            or not math.isfinite(self.overlay_temperature)
            or self.base_temperature == self.overlay_temperature
        ):
            raise ValueError(
                "F4 A/B temperatures must be finite, in range, and distinct"
            )
        if tuple(
            receipt.receipt_kind for receipt in self.optimizer_receipts
        ) != OPTIMIZER_RECEIPT_KINDS:
            raise ValueError(
                "F4 optimizer receipt inventory is missing, extra, or reordered"
            )
        objective_values = (
            self.primary_improvement,
            self.aa_noise_upper_bound,
            self.secondary_cost_reduction,
            self.required_secondary_cost_reduction,
        )
        if not all(math.isfinite(value) for value in objective_values):
            raise ValueError("F4 optimizer objective facts must be finite")
        if self.acceptance_basis == "improved-beyond-aa-noise":
            if self.primary_improvement <= self.aa_noise_upper_bound:
                raise ValueError("F4 accepted improvement does not exceed A/A noise")
        elif (
            self.primary_improvement != 0
            or self.required_secondary_cost_reduction <= 0
            or self.secondary_cost_reduction
            < self.required_secondary_cost_reduction
        ):
            raise ValueError("F4 accepted tie does not reduce secondary cost")
        return self


class F4ExecutionAuthorityAuthoring(_ExactModel):
    environment_id: str
    environment_ref: ImmutableRef
    source_runtime_ref: ImmutableRef
    runtime_class: Literal["docker"]
    python_executable: str
    docker_socket_path: str
    workspace_root: str
    docker_image: str
    service_factory: Literal["production-composition"]

    _environment = field_validator("environment_id")(_identifier)
    _paths = field_validator(
        "python_executable",
        "docker_socket_path",
        "workspace_root",
    )(_absolute)


class F4AuthorityAuthoringInput(_ExactModel):
    schema_version: Literal["bb.rl.phase5-f4-authority-authoring-input.v1"]
    base_authority: F3AuthorityInput
    production_template: F3ProductionCompositionInput
    target: F4TargetIdentity
    execution_authority: F4ExecutionAuthorityAuthoring
    variants: tuple[
        F4ConfigVariantAuthoring,
        F4ConfigVariantAuthoring,
        F4ConfigVariantAuthoring,
        F4ConfigVariantAuthoring,
        F4ConfigVariantAuthoring,
        F4ConfigVariantAuthoring,
    ]
    task_input: dict[str, Any]
    run_context: dict[str, Any]
    target_report_output_dir: str
    _output = field_validator("target_report_output_dir")(_absolute)

    @model_validator(mode="after")
    def exact(self) -> "F4AuthorityAuthoringInput":
        if tuple(item.variant_id for item in self.variants) != VARIANT_IDS:
            raise ValueError("frozen F4 variant order required")
        if len(
            {item.prompt for item in self.variants}
        ) != 6 or self.base_authority.task.prompt in {
            item.prompt for item in self.variants
        }:
            raise ValueError("six distinct compiler-visible prompt deltas required")
        if (
            self.base_authority.composition_id
            != self.production_template.composition_id
        ):
            raise ValueError("authority/composition ID mismatch")
        return self


class F4TargetInputBuildDescriptor(_ExactModel):
    schema_version: Literal["bb.rl.phase5-f4-target-input-build.v1"]
    target_input_path: str
    target_input_sha256: str
    composition_ref_path: str
    composition_descriptor_sha256: str
    composition_manifest_sha256: str
    authority_bundle_sha256: str
    optimizer_work_packet_sha256s: tuple[
        str, str, str, str, str, str
    ]
    _optimizer_digests = field_validator(
        "optimizer_work_packet_sha256s"
    )(lambda values: tuple(_digest(value) for value in values))
    variant_ids: tuple[str, ...]
    payload_ready: Literal[True]
    _paths = field_validator("target_input_path", "composition_ref_path")(_absolute)
    _digests = field_validator(
        "target_input_sha256",
        "composition_descriptor_sha256",
        "composition_manifest_sha256",
        "authority_bundle_sha256",
    )(_digest)


def _clone_authority(
    base: F3AuthorityInput, variant: F4ConfigVariantAuthoring
) -> F3AuthorityInput:
    value = base.model_dump(mode="json")
    value["attempt_id"] = f"f4-{variant.variant_id}"
    value["task"]["prompt"] = variant.prompt
    return F3AuthorityInput.model_validate_json(
        canonical_json_bytes(value), strict=True
    )


def _clone_production(
    template: F3ProductionCompositionInput,
    path: Path,
    manifest: F3AuthorityBundleManifest,
) -> F3ProductionCompositionInput:
    value = template.model_dump(mode="json")
    raw = path.read_bytes()
    value["authority_manifest"] = SourceArtifact(
        path=os.fspath(path.resolve()),
        sha256=_d(raw),
        media_type=template.authority_manifest.media_type,
    ).model_dump(mode="json")
    value["stores"]["cas"] = manifest.cas_root
    return F3ProductionCompositionInput.model_validate_json(
        canonical_json_bytes(value), strict=True
    )


def _model_json_value(value: Any) -> Any:
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json")
    if isinstance(value, tuple):
        return [_model_json_value(item) for item in value]
    return value


def _validated_update(model: BaseModel, **updates: Any) -> Any:
    value = model.model_dump(mode="json")
    value.update(
        {key: _model_json_value(update) for key, update in updates.items()}
    )
    return type(model).model_validate_json(canonical_json_bytes(value), strict=True)


def _json_value(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {key: _json_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_value(item) for item in value]
    return value



def _difference_paths(left: Any, right: Any, path: str = "") -> tuple[str, ...]:
    if type(left) is dict and type(right) is dict:
        keys = sorted(set(left) | set(right))
        return tuple(
            difference
            for key in keys
            for difference in _difference_paths(
                left.get(key), right.get(key), f"{path}/{key}"
            )
        )
    if type(left) is list and type(right) is list:
        if len(left) != len(right):
            return (path + "/length",)
        return tuple(
            difference
            for index, (left_item, right_item) in enumerate(
                zip(left, right, strict=True)
            )
            for difference in _difference_paths(
                left_item, right_item, f"{path}/{index}"
            )
        )
    return () if left == right else (path or "/",)



def _semantic_digest(value: dict[str, Any]) -> str:
    return _d(
        canonical_json_bytes(
            {"schema": "bb.compiled-config-semantic.v1", "config": value}
        )
    )


def _pointer_parts(pointer: str) -> tuple[str, ...]:
    return tuple(
        part.replace("~1", "/").replace("~0", "~")
        for part in pointer.split("/")[1:]
    )


def _pointer_value(value: Any, pointer: str) -> Any:
    current = value
    for part in _pointer_parts(pointer):
        current = current[int(part)] if isinstance(current, list) else current[part]
    return current


def _replace_pointer(value: dict[str, Any], pointer: str, replacement: Any) -> None:
    parts = _pointer_parts(pointer)
    current: Any = value
    for part in parts[:-1]:
        current = current[int(part)] if isinstance(current, list) else current[part]
    final = parts[-1]
    if isinstance(current, list):
        current[int(final)] = replacement
    else:
        current[final] = replacement


def _mirror_root_semantic_pointer(
    value: dict[str, Any], pointer: str, replacement: Any
) -> None:
    root_id = value["root_config_node_id"]
    root_index = next(
        index
        for index, node in enumerate(value["config_nodes"])
        if node["node_id"] == root_id
    )
    _replace_pointer(
        value,
        f"/config_nodes/{root_index}/semantic_config{pointer}",
        replacement,
    )


def _overlay_values(
    before_semantics: dict[str, Any],
    mutable_pointers: tuple[c.MutablePointerRule, ...],
    replacement: float,
) -> tuple[
    dict[str, Any],
    dict[str, Any],
    tuple[c.OverlayOperation, ...],
    tuple[c.OverlayTransition, ...],
]:
    before = _json_value(before_semantics)
    rules = {
        rule.pointer: rule
        for rule in mutable_pointers
        if c.MutableOperation.REPLACE in rule.allowed_operations
    }
    if set(rules) != set(_OVERLAY_POINTERS):
        raise F4AuthorityAuthoringError(
            "F4 requires the exact compiler sampling-temperature overlay pointer"
        )
    original = _pointer_value(before, _OVERLAY_POINTERS[0])
    if type(original) not in (int, float) or isinstance(original, bool):
        raise F4AuthorityAuthoringError(
            "F4 compiler sampling temperature is not numeric"
        )
    if type(replacement) is not float or not math.isfinite(replacement):
        raise F4AuthorityAuthoringError("F4 overlay temperature is not finite")
    current = copy.deepcopy(before)
    operations: list[c.OverlayOperation] = []
    transitions: list[c.OverlayTransition] = []
    for operation_index, pointer in enumerate(_OVERLAY_POINTERS):
        before_digest = _semantic_digest(current)
        _replace_pointer(current, pointer, replacement)
        _mirror_root_semantic_pointer(current, pointer, replacement)
        after_digest = _semantic_digest(current)
        operations.append(
            c.OverlayOperation(
                op="replace", path=pointer, value=replacement
            )
        )
        transitions.append(
            c.OverlayTransition(
                operation_index=operation_index,
                before_semantic_digest=before_digest,
                after_semantic_digest=after_digest,
            )
        )
    return before, current, tuple(operations), tuple(transitions)




def build_f4_target_input(
    spec: F4AuthorityAuthoringInput, output_dir: str
) -> F4TargetInputBuildDescriptor:
    if type(spec) is not F4AuthorityAuthoringInput:
        raise TypeError("exact F4AuthorityAuthoringInput required")
    root = Path(_absolute(output_dir))
    if os.path.lexists(root):
        raise F4AuthorityAuthoringError("output already exists")
    root.mkdir(mode=0o700, parents=False)
    try:
        authored: list[tuple[F3AuthorityBundleManifest, Path]] = []
        for variant in spec.variants:
            destination = root / "authorities" / variant.variant_id
            destination.parent.mkdir(mode=0o700, exist_ok=True)
            manifest_path = Path(
                build_f3_authority(
                    _clone_authority(spec.base_authority, variant),
                    os.fspath(destination.resolve()),
                )
            )
            authored.append(
                (
                    F3AuthorityBundleManifest.model_validate_json(
                        manifest_path.read_bytes(), strict=True
                    ),
                    manifest_path,
                )
            )
        first, first_path = authored[0]
        Path(first.cas_root).chmod(0o700)
        production = _clone_production(spec.production_template, first_path, first)
        base = build_f3_production_composition(
            production, os.fspath((root / "base-composition").resolve())
        )
        base_manifest = HarnessCompositionManifestV1.model_validate_json(
            Path(base.composition_manifest_path).read_bytes(), strict=True
        )
        base_authority = AuthorityBundleV1.model_validate_json(
            Path(base.authority_bundle_path).read_bytes(), strict=True
        )
        overlay_schema_digest = _d(
            canonical_json_bytes(
                {
                    "schema_version": "bb.rl.f4-overlay-number.v1",
                    "type": "number",
                }
            )
        )
        mutable_rules = tuple(
            c.MutablePointerRule(
                pointer=pointer,
                allowed_operations=(c.MutableOperation.REPLACE,),
                value_schema_digest=overlay_schema_digest,
                authority_effect=c.AuthorityEffect.NONE,
                removable=False,
            )
            for pointer in _OVERLAY_POINTERS
        )
        f4_ceiling = _validated_update(
            base_authority.admission_policy.ceiling,
            mutable_pointer_rules=mutable_rules,
        )
        f4_policy = _validated_update(
            base_authority.admission_policy,
            ceiling=f4_ceiling,
        )
        artifacts = root / "artifacts"
        artifacts.mkdir(mode=0o700)
        f4_policy_raw = f4_policy.canonical_bytes()
        f4_policy_path = artifacts / "admission-policy.json"
        _write(f4_policy_path, f4_policy_raw)
        f4_policy_ref = _fref(
            f4_policy_path,
            f4_policy_raw,
            base_manifest.control_plane.admission_policy_ref.media_type,
        )
        compiled_refs: list[ArtifactFileRefV1] = []
        receipt_refs: list[ArtifactFileRefV1] = []
        bundle_refs: list[ArtifactFileRefV1] = []
        closure_refs: list[ArtifactFileRefV1] = []
        receipts: list[c.AdmissionReceipt] = []
        compiled_payloads: dict[str, bytes] = {}
        receipt_payloads: dict[str, bytes] = {}
        compiled_identities: list[c.CompiledArtifactIdentity] = []
        compiled_manifests: list[CompiledConfigManifest] = []
        source_members: list[tuple[str, str]] = []
        cas = FilesystemCAS(first.cas_root)
        store = CASConfigRuntimeStore(cas)
        try:
            for variant, (manifest, _) in zip(
                spec.variants, authored, strict=True
            ):
                refs = {
                    name: ArtifactFileRefV1.model_validate(
                        ref.model_dump(), strict=True
                    )
                    for name, ref in manifest.artifacts.items()
                }
                source_bundle = ConfigBundleManifest.from_json(
                    Path(refs["config-bundle.json"].path).read_bytes()
                )
                source_closure = DependencyClosureManifest.from_json(
                    Path(refs["config-closure.json"].path).read_bytes()
                )
                source_compiled = CompiledConfigManifest.from_json(
                    Path(refs["compiled-manifest.json"].path).read_bytes()
                )
                source_receipt_raw = Path(
                    refs["admission-receipt.json"].path
                ).read_bytes()
                source_receipt = c.AdmissionReceipt.model_validate_json(
                    source_receipt_raw, strict=True
                )
                source_cas = FilesystemCAS(manifest.cas_root)
                member_bytes: dict[str, bytes] = {}
                try:
                    for entry in source_bundle.entries:
                        source_ref = source_cas.get_ref(entry.artifact_id)
                        payload = source_cas.get_bytes(source_ref)
                        member_bytes[entry.logical_path] = payload
                        if not cas.has(entry.artifact_id):
                            cas.put_bytes(
                                payload,
                                artifact_id=source_ref.artifact_id,
                                media_type=source_ref.media_type,
                                metadata=source_ref.metadata,
                            )
                finally:
                    source_cas.close()
                capability = _validated_update(
                    source_receipt.effective_capabilities,
                    mutable_pointers=mutable_rules,
                )
                main_path = source_closure.root_entrypoint
                root_entrypoint_name = next(
                    entry.name
                    for entry in source_bundle.entrypoints
                    if entry.logical_path == main_path
                )
                source_config = canonical_json_loads(member_bytes[main_path])
                authority = source_config["profile"]["metadata"][
                    "breadboard_rl_authority"
                ]
                if set(authority) != {
                    "requested_capabilities",
                    "task_binding_digest",
                }:
                    raise F4AuthorityAuthoringError(
                        "F3 compiled authority metadata is not source-closed"
                    )
                authority["requested_capabilities"] = capability.model_dump(
                    mode="json"
                )
                source_config["sampling"] = {
                    "temperature": variant.base_temperature
                }
                source_config["optimizer_mutable_pointers"] = [
                    rule.pointer for rule in mutable_rules
                ]
                member_bytes[main_path] = canonical_json_bytes(source_config)
                rebuilt_bundle = ingest_member_map(
                    member_bytes,
                    cas,
                    entrypoints={
                        item.name: item.logical_path
                        for item in source_bundle.entrypoints
                    },
                    limits=source_bundle.limits,
                    source_label=(
                        f"{source_bundle.provenance.source_label}:"
                        f"f4-overlay:{variant.variant_id}"
                    ),
                    media_types={
                        item.logical_path: item.media_type
                        for item in source_bundle.entries
                    },
                    modes={
                        item.logical_path: item.mode
                        for item in source_bundle.entries
                    },
                )
                rebuilt_closure = build_dependency_closure(
                    rebuilt_bundle,
                    root_entrypoint=root_entrypoint_name,
                    edges=source_closure.edges,
                )
                rebuilt_compiled = compile_config(
                    ManifestReader(
                        cas=cas,
                        bundle=rebuilt_bundle,
                        closure=rebuilt_closure,
                    ),
                    rebuilt_closure,
                    source_compiled.inputs.options,
                )
                expected_semantics = source_compiled.semantic.to_canonical_obj()
                rebuilt_semantics = rebuilt_compiled.semantic.to_canonical_obj()
                old_root_id = expected_semantics["root_config_node_id"]
                new_root_id = rebuilt_semantics["root_config_node_id"]
                expected_semantics["root_config_node_id"] = new_root_id
                expected_semantics["optimizer_mutable_pointers"] = [
                    rule.pointer for rule in mutable_rules
                ]
                expected_semantics["sampling"] = {
                    "temperature": variant.base_temperature
                }
                expected_authority = expected_semantics["metadata"][
                    "profile_metadata"
                ]["breadboard_rl_authority"]
                expected_authority["requested_capabilities"] = (
                    capability.model_dump(mode="json")
                )
                for prompt in expected_semantics["prompts"]["variants"]:
                    if prompt["config_node_id"] == old_root_id:
                        prompt["config_node_id"] = new_root_id
                for node in expected_semantics["config_nodes"]:
                    if node["node_id"] != old_root_id:
                        continue
                    node["node_id"] = new_root_id
                    node["semantic_config"]["optimizer_mutable_pointers"] = [
                        rule.pointer for rule in mutable_rules
                    ]
                    node["semantic_config"]["sampling"] = {
                        "temperature": variant.base_temperature
                    }
                    node["semantic_config"]["metadata"]["profile_metadata"][
                        "breadboard_rl_authority"
                    ]["requested_capabilities"] = capability.model_dump(mode="json")
                    for prompt in node["semantic_config"]["prompts"]["variants"]:
                        if prompt["config_node_id"] == old_root_id:
                            prompt["config_node_id"] = new_root_id
                if rebuilt_semantics != expected_semantics:
                    differences = _difference_paths(
                        expected_semantics, rebuilt_semantics
                    )
                    raise F4AuthorityAuthoringError(
                        "F4 recompilation changed non-overlay source semantics at "
                        + ",".join(differences[:8])
                    )
                rebuilt_bundle_raw = rebuilt_bundle.canonical_bytes()
                rebuilt_closure_raw = rebuilt_closure.canonical_bytes()
                rebuilt_compiled_raw = rebuilt_compiled.canonical_bytes()
                rebuilt_bundle_path = (
                    artifacts / f"config-bundle-{variant.variant_id}.json"
                )
                rebuilt_closure_path = (
                    artifacts / f"config-closure-{variant.variant_id}.json"
                )
                rebuilt_compiled_path = (
                    artifacts / f"compiled-manifest-{variant.variant_id}.json"
                )
                _write(rebuilt_bundle_path, rebuilt_bundle_raw)
                _write(rebuilt_closure_path, rebuilt_closure_raw)
                _write(rebuilt_compiled_path, rebuilt_compiled_raw)
                bundle_ref = _fref(
                    rebuilt_bundle_path,
                    rebuilt_bundle_raw,
                    refs["config-bundle.json"].media_type,
                )
                closure_ref = _fref(
                    rebuilt_closure_path,
                    rebuilt_closure_raw,
                    refs["config-closure.json"].media_type,
                )
                compiled_ref = _fref(
                    rebuilt_compiled_path,
                    rebuilt_compiled_raw,
                    refs["compiled-manifest.json"].media_type,
                )
                if (
                    store.publish(
                        kind=c.ArtifactKind.COMPILED_MANIFEST,
                        canonical_bytes=rebuilt_compiled_raw,
                    ).sha256
                    != compiled_ref.sha256
                ):
                    raise F4AuthorityAuthoringError(
                        "recompiled manifest publication mismatch"
                    )
                compiled_refs.append(compiled_ref)
                compiled_payloads[compiled_ref.sha256] = rebuilt_compiled_raw
                compiled_identities.append(
                    _compiled_identity(rebuilt_compiled, compiled_ref.sha256)
                )
                compiled_manifests.append(rebuilt_compiled)
                receipt_refs.append(refs["admission-receipt.json"])
                receipt_payloads[
                    refs["admission-receipt.json"].sha256
                ] = source_receipt_raw
                bundle_refs.append(bundle_ref)
                closure_refs.append(closure_ref)
                receipts.append(source_receipt)
                rebuilt_member = next(
                    entry
                    for entry in rebuilt_bundle.entries
                    if entry.logical_path == main_path
                )
                source_members.append((main_path, rebuilt_member.blob_digest))
            if any(
                len({ref.sha256 for ref in values}) != 6
                for values in (compiled_refs, receipt_refs, bundle_refs, closure_refs)
            ):
                raise F4AuthorityAuthoringError(
                    "F4 config-native identities are not distinct"
                )
            if (
                len(
                    {
                        receipt.compiled.compiler.canonical_digest()
                        for receipt in receipts
                    }
                )
                != 1
            ):
                raise F4AuthorityAuthoringError("compiler identity drift")
            exemplar = receipts[0]
            admitted = c.AdmittedSetManifest(
                compiler_abi=exemplar.compiled.compiler.semantic_version,
                admission_policy_digest=exemplar.admission_policy_digest,
                operator_ceiling_digest=exemplar.operator_ceiling_digest,
                registry_snapshot_digest=exemplar.registry_snapshot_digest,
                revocation=exemplar.revocation,
                receipt_digests=tuple(sorted(ref.sha256 for ref in receipt_refs)),
                validity=exemplar.validity,
            )
            admitted_raw = admitted.canonical_bytes()
            admitted_path = artifacts / "admitted-set.json"
            _write(admitted_path, admitted_raw)
            admitted_ref = _fref(
                admitted_path, admitted_raw, base_manifest.admitted_set_ref.media_type
            )
            store.publish(
                kind=c.ArtifactKind.ADMITTED_SET, canonical_bytes=admitted_raw
            )
        finally:
            cas.close()


        graph_cas = FilesystemCAS(first.cas_root)
        graph = _build_authority_graph(
            cas=graph_cas,
            policy=f4_policy,
            registries=base_authority.registries,
            revocations=base_authority.revocations,
            policy_capabilities=base_authority.policy_capabilities,
            admitted_set=admitted,
            direct_selectors=(),
            weighted_selectors=(),
            compiled_manifests=compiled_payloads,
            admission_receipts=receipt_payloads,
            policy_http=base_authority.policy_http,
            tls_trust=base_authority.tls_trust,
            tls_ca_pem_by_route={
                item.route_id: Path(item.ca_bundle_ref.path).read_bytes()
                for item in base_authority.tls_trust
            },
            receipt_key_id=base_manifest.control_plane.receipt_authenticator.key_id,
            receipt_key=_read_signing_key(
                production.secrets.files[
                    base_manifest.control_plane.receipt_authenticator.secret_handle_id
                ]
            ),
        )
        try:
            f4_receipts: list[c.AdmissionReceipt] = []
            f4_receipt_refs: list[ArtifactFileRefV1] = []
            for index, (variant, source_receipt, compiled_identity) in enumerate(
                zip(spec.variants, receipts, compiled_identities, strict=True)
            ):
                capability = _validated_update(
                    source_receipt.effective_capabilities,
                    mutable_pointers=mutable_rules,
                )
                admission = c.AdmissionRequest(
                    subject=source_receipt.subject,
                    behavior_source=c.CompiledBehaviorSource(
                        manifest_digest=compiled_identity.manifest_digest,
                        semantic_digest=compiled_identity.semantic_digest,
                    ),
                    compiled=compiled_identity,
                    requested_capabilities=capability,
                    requested_capability_digest=capability.canonical_digest(),
                    task_binding_digest=capability.task.task_binding_digest,
                    policy_binding_ref=source_receipt.policy_binding_ref,
                    admission_policy_digest=f4_policy.canonical_digest(),
                    registry_snapshot_digest=source_receipt.registry_snapshot_digest,
                    validity=source_receipt.validity,
                    parent_receipt_digest=None,
                    overlay_chain_digest=None,
                )
                admitted_receipt_ref = graph.config_runtime.admit(admission)
                admitted_receipt_raw = graph.store.load(
                    admitted_receipt_ref.digest,
                    kind=c.ArtifactKind.ADMISSION_RECEIPT,
                    max_bytes=1_000_000,
                )
                admitted_receipt = c.AdmissionReceipt.model_validate_json(
                    admitted_receipt_raw, strict=True
                )
                admitted_receipt_path = (
                    artifacts / f"f4-admission-{variant.variant_id}.json"
                )
                _write(admitted_receipt_path, admitted_receipt_raw)
                f4_receipts.append(admitted_receipt)
                f4_receipt_refs.append(
                    _fref(
                        admitted_receipt_path,
                        admitted_receipt_raw,
                        receipt_refs[index].media_type,
                    )
                )
            receipts = f4_receipts
            receipt_refs = f4_receipt_refs
            optimizer_dir = artifacts / "optimizer-work-packets"
            optimizer_dir.mkdir(mode=0o700)
            optimizer_work_packet_sha256s: list[str] = []
            for index, variant in enumerate(spec.variants):
                acceptance_id = f"f4-authoring-{variant.variant_id}"
                source_member_path, source_member_digest = source_members[index]
                bundle_ref = _iref(
                    f"bundle/{variant.variant_id}", bundle_refs[index].sha256
                )
                closure_ref = _iref(
                    f"closure/{variant.variant_id}", closure_refs[index].sha256
                )
                compiler_ref = _iref(
                    "compiler", receipts[index].compiled.compiler.canonical_digest()
                )
                compiled_ref = _iref(
                    f"compiled/{variant.variant_id}", compiled_refs[index].sha256
                )
                admission_ref = _iref(
                    f"receipt/{variant.variant_id}", receipt_refs[index].sha256
                )
                typed_facts = (
                    F4OptimizerGenerationFacts(
                        schema_version="bb.rl.phase5-f4-optimizer-generation.v1",
                        mutation_axis="sampling",
                        generated_variant_id=variant.variant_id,
                        parent_variant_id=variant.paired_variant_id,
                    ),
                    F4OptimizerSourceFacts(
                        schema_version="bb.rl.phase5-f4-optimizer-source.v1",
                        source_member_path=source_member_path,
                        source_member_digest=source_member_digest,
                        config_bundle_ref=bundle_ref,
                        dependency_closure_ref=closure_ref,
                        compiler_identity_ref=compiler_ref,
                        compiled_config_ref=compiled_ref,
                        admission_receipt_ref=admission_ref,
                    ),
                    F4OptimizerObjectiveFacts(
                        schema_version="bb.rl.phase5-f4-optimizer-objective.v1",
                        primary_objective_frozen=True,
                        secondary_cost_frozen=True,
                        primary_improvement=variant.primary_improvement,
                        secondary_cost_reduction=variant.secondary_cost_reduction,
                        required_secondary_cost_reduction=(
                            variant.required_secondary_cost_reduction
                        ),
                    ),
                    F4OptimizerConstraintFacts(
                        schema_version="bb.rl.phase5-f4-optimizer-constraints.v1",
                        non_config_inputs_identical=True,
                        correctness_regression=False,
                        security_regression=False,
                        isolation_regression=False,
                        evidence_regression=False,
                        cleanup_regression=False,
                    ),
                    F4OptimizerPairedABFacts(
                        schema_version="bb.rl.phase5-f4-optimizer-paired-ab.v1",
                        paired_ab_evaluation_count=(
                            variant.paired_ab_evaluation_count
                        ),
                    ),
                    F4OptimizerAANoiseFacts(
                        schema_version="bb.rl.phase5-f4-optimizer-aa-noise.v1",
                        aa_noise_upper_bound=variant.aa_noise_upper_bound,
                    ),
                    F4OptimizerHeldOutFacts(
                        schema_version="bb.rl.phase5-f4-optimizer-held-out.v1",
                        held_out_repeated=True,
                        repeat_count=variant.held_out_repeat_count,
                    ),
                    F4OptimizerDispositionFacts(
                        schema_version="bb.rl.phase5-f4-optimizer-disposition.v1",
                        optimizer_acceptance_id=acceptance_id,
                        disposition="accepted",
                        acceptance_basis=variant.acceptance_basis,
                    ),
                )
                bindings: list[F4OptimizerReceiptBinding] = []
                for sequence_index, (receipt_input, facts) in enumerate(
                    zip(variant.optimizer_receipts, typed_facts, strict=True)
                ):
                    body = F4OptimizerReceiptBody(
                        schema_version="bb.rl.phase5-f4-optimizer-receipt.v1",
                        receipt_kind=receipt_input.receipt_kind,
                        sequence_index=sequence_index,
                        optimizer_acceptance_id=acceptance_id,
                        variant_id=variant.variant_id,
                        parent_variant_id=variant.paired_variant_id,
                        source_member_path=source_member_path,
                        source_member_digest=source_member_digest,
                        config_bundle_ref=bundle_ref,
                        dependency_closure_ref=closure_ref,
                        compiler_identity_ref=compiler_ref,
                        compiled_config_ref=compiled_ref,
                        admission_receipt_ref=admission_ref,
                        facts=facts,
                    )
                    body_raw = canonical_json_bytes(body.model_dump(mode="json"))
                    body_ref = _iref(
                        f"optimizer/{variant.variant_id}/{receipt_input.receipt_kind}",
                        _d(body_raw),
                    )
                    bindings.append(
                        F4OptimizerReceiptBinding(ref=body_ref, artifact=body)
                    )
                    _write(
                        optimizer_dir
                        / f"{variant.variant_id}-{sequence_index}-{receipt_input.receipt_kind}.json",
                        body_raw,
                    )
                packet = F4OptimizerWorkPacket(
                    schema_version="bb.rl.phase5-f4-optimizer-work-packet.v1",
                    optimizer_acceptance_id=acceptance_id,
                    variant_id=variant.variant_id,
                    parent_variant_id=variant.paired_variant_id,
                    ordered_receipts=tuple(bindings),
                )
                packet_raw = canonical_json_bytes(packet.model_dump(mode="json"))
                packet_digest = _d(packet_raw)
                F4OptimizerWorkPacketBinding(
                    ref=_iref(
                        f"optimizer-work-packet/{variant.variant_id}",
                        packet_digest,
                    ),
                    artifact=packet,
                )
                _write(
                    optimizer_dir / f"{variant.variant_id}-work-packet.json",
                    packet_raw,
                )
                optimizer_work_packet_sha256s.append(packet_digest)
            exemplar = receipts[0]
            admitted = c.AdmittedSetManifest(
                compiler_abi=exemplar.compiled.compiler.semantic_version,
                admission_policy_digest=f4_policy.canonical_digest(),
                operator_ceiling_digest=f4_policy.ceiling.canonical_digest(),
                registry_snapshot_digest=exemplar.registry_snapshot_digest,
                revocation=exemplar.revocation,
                receipt_digests=tuple(sorted(ref.sha256 for ref in receipt_refs)),
                validity=exemplar.validity,
            )
            admitted_raw = admitted.canonical_bytes()
            overlay_base_path = artifacts / "admitted-set-overlay-base.json"
            _write(overlay_base_path, admitted_raw)
            admitted_ref = _fref(
                overlay_base_path,
                admitted_raw,
                base_manifest.admitted_set_ref.media_type,
            )
            graph.store.publish(
                kind=c.ArtifactKind.ADMITTED_SET,
                canonical_bytes=admitted_raw,
            )
            overlays: list[c.AdmittedOverlayRef] = []
            overlay_files: list[ArtifactFileRefV1] = []
            derived_receipt_files: list[ArtifactFileRefV1] = []
            expected_after_semantics: list[dict[str, Any]] = []
            for index, (variant, base_receipt, compiled_manifest) in enumerate(
                zip(spec.variants, receipts, compiled_manifests, strict=True)
            ):
                (
                    before_semantics,
                    after_semantics,
                    operations,
                    transitions,
                ) = _overlay_values(
                    compiled_manifest.semantic.to_canonical_obj(),
                    base_receipt.effective_capabilities.mutable_pointers,
                    variant.overlay_temperature,
                )
                before_digest = _semantic_digest(before_semantics)
                after_digest = _semantic_digest(after_semantics)
                overlay = c.MutationOverlayManifest(
                    base_compiled_manifest_digest=base_receipt.compiled.manifest_digest,
                    parent_receipt_digest=base_receipt.canonical_digest(),
                    expected_before_semantic_digest=before_digest,
                    operations=operations,
                    expected_transitions=transitions,
                    expected_after_semantic_digest=after_digest,
                    provenance=c.OverlayProvenance(
                        author_subject_digest=base_receipt.subject.canonical_digest(),
                        source_kind=c.OverlaySourceKind.EXPERIMENT,
                        source_artifact_digest=_d(
                            canonical_json_bytes(
                                {
                                    "operations": [
                                        operation.to_canonical_obj()
                                        for operation in operations
                                    ],
                                }
                            )
                        ),
                        rationale_code="f4-admitted-config-overlay",
                    ),
                )
                overlay_raw = overlay.canonical_bytes()
                overlay_ref = graph.store.publish(
                    kind=c.ArtifactKind.MUTATION_OVERLAY,
                    canonical_bytes=overlay_raw,
                )
                chain_digest = c.derive_overlay_chain_digest(
                    parent_chain_digest=base_receipt.overlay_chain_digest,
                    overlay_digest=overlay_ref.sha256,
                )
                derived_request = c.AdmissionRequest(
                    subject=base_receipt.subject,
                    behavior_source=c.OverlayDerivedBehaviorSource(
                        base_manifest_digest=base_receipt.compiled.manifest_digest,
                        parent_receipt_digest=base_receipt.canonical_digest(),
                        overlay_chain_digest=chain_digest,
                        derived_semantic_digest=after_digest,
                    ),
                    compiled=_validated_update(
                        base_receipt.compiled,
                        semantic_digest=after_digest,
                    ),
                    requested_capabilities=base_receipt.effective_capabilities,
                    requested_capability_digest=base_receipt.effective_capability_digest,
                    task_binding_digest=base_receipt.task_binding_digest,
                    policy_binding_ref=base_receipt.policy_binding_ref,
                    admission_policy_digest=base_receipt.admission_policy_digest,
                    registry_snapshot_digest=base_receipt.registry_snapshot_digest,
                    validity=base_receipt.validity,
                    parent_receipt_digest=base_receipt.canonical_digest(),
                    overlay_chain_digest=chain_digest,
                )
                derived_ref = graph.config_runtime.admit(derived_request)
                derived_raw = graph.store.load(
                    derived_ref.digest,
                    kind=c.ArtifactKind.ADMISSION_RECEIPT,
                    max_bytes=1_000_000,
                )
                overlay_path = artifacts / f"overlay-{variant.variant_id}.json"
                _write(overlay_path, overlay_raw)
                overlay_files.append(
                    _fref(
                        overlay_path,
                        overlay_raw,
                        "application/vnd.breadboard.mutation-overlay+json;version=1",
                    )
                )
                derived_path = (
                    artifacts / f"overlay-admission-{variant.variant_id}.json"
                )
                _write(derived_path, derived_raw)
                derived_receipt_files.append(
                    _fref(
                        derived_path,
                        derived_raw,
                        receipt_refs[index].media_type,
                    )
                )
                overlays.append(
                    c.AdmittedOverlayRef(
                        overlay_digest=overlay_ref.sha256,
                        result_receipt_digest=derived_ref.digest,
                    )
                )
                expected_after_semantics.append(after_semantics)

            admitted = _validated_update(
                admitted,
                receipt_digests=tuple(
                    sorted(
                        (
                            *(ref.sha256 for ref in receipt_refs),
                            *(ref.sha256 for ref in derived_receipt_files),
                        )
                    )
                ),
            )
            admitted_raw = admitted.canonical_bytes()
            final_admitted_path = artifacts / "admitted-set-final.json"
            _write(final_admitted_path, admitted_raw)
            admitted_ref = _fref(
                final_admitted_path,
                admitted_raw,
                base_manifest.admitted_set_ref.media_type,
            )
            graph.store.publish(
                kind=c.ArtifactKind.ADMITTED_SET,
                canonical_bytes=admitted_raw,
            )

            variants: list[F4VariantExecution] = []
            plans: list[c.EffectiveExecutionPlan] = []
            selectors = []
            for index, (
                variant,
                receipt_file,
                bundle_file,
                closure_file,
                overlay_ref,
                after_semantics,
            ) in enumerate(
                zip(
                    spec.variants,
                    receipt_refs,
                    bundle_refs,
                    closure_refs,
                    overlays,
                    expected_after_semantics,
                    strict=True,
                )
            ):
                partner_index = next(
                    partner_index
                    for partner_index, partner in enumerate(spec.variants)
                    if partner.variant_id == variant.paired_variant_id
                )
                candidate_rows = (
                    c.WeightedCandidate(
                        candidate=c.ConfigCandidate(
                            candidate_id=variant.variant_id,
                            receipt_digest=receipt_file.sha256,
                            predicates=(),
                            overlays=(overlay_ref,),
                        ),
                        weight=variant.weight,
                    ),
                    c.WeightedCandidate(
                        candidate=c.ConfigCandidate(
                            candidate_id=spec.variants[partner_index].variant_id,
                            receipt_digest=receipt_refs[partner_index].sha256,
                            predicates=(),
                            overlays=(),
                        ),
                        weight=variant.paired_weight,
                    ),
                )
                selector = c.ConfigSetManifest(
                    admitted_set_root=admitted_ref.sha256,
                    compiler_abi=admitted.compiler_abi,
                    runtime_abi=exemplar.compiled.compiler.runtime_abi,
                    admission_policy_digest=admitted.admission_policy_digest,
                    operator_ceiling_digest=admitted.operator_ceiling_digest,
                    candidates=tuple(
                        sorted(
                            candidate_rows,
                            key=lambda item: item.candidate.candidate_id,
                        )
                    ),
                    validity=admitted.validity,
                )
                selector_raw = selector.canonical_bytes()
                selector_ref = graph.store.publish(
                    kind=c.ArtifactKind.CONFIG_SET,
                    canonical_bytes=selector_raw,
                )
                request = c.ResolveEpisodeRequest(
                    episode_id=f"f4-target-{index}-{variant.variant_id}",
                    subject=receipts[index].subject,
                    selector=c.WeightedSelectorRef(
                        digest=selector_ref.sha256,
                        ref=selector_ref,
                    ),
                    selection_nonce=variant.selection_seed,
                    task=production.resolution_task,
                    policy_binding=receipts[index].policy_binding_ref,
                    episode_overlays=(),
                )
                selected = graph.config_runtime.resolve_episode(request)
                selected_record = c.SelectionRecord.model_validate_json(
                    graph.store.load(
                        selected.selection_record_ref.sha256,
                        kind=c.ArtifactKind.SELECTION_RECORD,
                        max_bytes=1_000_000,
                    ),
                    strict=True,
                )
                plan = selected.effective_plan
                if (
                    selected_record.selected_candidate_id != variant.variant_id
                    or _json_value(plan.effective_semantics)
                    != after_semantics
                    or not plan.overlay_applications
                ):
                    raise F4AuthorityAuthoringError(
                        "weighted selection or admitted overlay identity drift"
                    )
                selector_raw = selector.canonical_bytes()
                selector_path = (
                    artifacts / f"selector-final-{variant.variant_id}.json"
                )
                _write(selector_path, selector_raw)
                selectors.append(
                    _fref(
                        selector_path,
                        selector_raw,
                        "application/vnd.breadboard.config-set+json;version=1",
                    )
                )
                plans.append(plan)
                compiled_semantics_raw = canonical_json_bytes(
                    compiled_manifests[index].semantic.to_canonical_obj()
                )
                compiled_semantics_ref = graph.store.publish(
                    kind=c.ArtifactKind.COMPILED_MANIFEST,
                    canonical_bytes=compiled_semantics_raw,
                )
                variants.append(
                    F4VariantExecution(
                        variant_id=variant.variant_id,
                        request=request,
                        config_bundle_ref=_iref(
                            f"bundle/{variant.variant_id}", bundle_file.sha256
                        ),
                        dependency_closure_ref=_iref(
                            f"closure/{variant.variant_id}", closure_file.sha256
                        ),
                        compiler_identity_ref=_iref(
                            "compiler", plan.base_compiled.compiler.canonical_digest()
                        ),
                        compiled_config_ref=_iref(
                            f"compiled/{variant.variant_id}",
                            plan.base_compiled.manifest_digest,
                        ),
                        compiled_semantics_ref=compiled_semantics_ref,
                        admission_receipt_ref=_iref(
                            f"receipt/{variant.variant_id}", plan.base_receipt_digest
                        ),
                        selection_record_ref=_iref(
                            f"selection/{variant.variant_id}",
                            selected.selection_record_ref.sha256,
                        ),
                        ordered_overlay_receipt_refs=(
                            _iref(
                                f"overlay-receipt/{variant.variant_id}",
                                overlay_ref.result_receipt_digest,
                            ),
                        ),
                        semantic_delta=CompilerVisibleSemanticDelta(
                            name=f"temperature-{index}",
                            compiler_field_pointer="/sampling/temperature",
                            before_digest=_d(
                                canonical_json_bytes(
                                    _pointer_value(
                                        compiled_manifests[index]
                                        .semantic.to_canonical_obj(),
                                        "/sampling/temperature",
                                    )
                                )
                            ),
                            after_digest=_d(
                                canonical_json_bytes(
                                    _pointer_value(
                                        plan.effective_semantics,
                                        "/sampling/temperature",
                                    )
                                )
                            ),
                        ),
                        requested_security_policy_digest=plan.sandbox.security_policy_digest,
                    )
                )

            authority = AuthorityBundleV1(
                **{
                    **base_authority.model_dump(mode="python"),
                    "admission_policy": f4_policy,
                    "compiled_manifest_refs": tuple(
                        sorted(compiled_refs, key=lambda ref: ref.sha256)
                    ),
                    "admission_receipt_refs": tuple(
                        sorted(
                            (*receipt_refs, *derived_receipt_files),
                            key=lambda ref: ref.sha256,
                        )
                    ),
                }
            )
            authority_raw = authority.canonical_bytes()
            final_authority_path = artifacts / "authority-bundle-final.json"
            _write(final_authority_path, authority_raw)
            authority_ref = _fref(
                final_authority_path, authority_raw, "application/json"
            )
            manifest_value = base_manifest.model_dump(mode="json")
            manifest_value.pop("config_bundle_ref")
            manifest_value["schema_version"] = "bb.rl.harness-composition.v2"
            manifest_value["authority_bundle_ref"] = authority_ref.model_dump(
                mode="json"
            )
            manifest_value["control_plane"]["admission_policy_ref"] = (
                f4_policy_ref.model_dump(mode="json")
            )
            manifest_value["config_bundle_refs"] = [
                ref.model_dump(mode="json")
                for ref in sorted(bundle_refs, key=lambda ref: ref.sha256)
            ]
            manifest_value["admitted_set_ref"] = admitted_ref.model_dump(mode="json")
            manifest_value["selector_catalog"] = SelectorCatalogV1(
                direct=(),
                weighted=tuple(sorted(selectors, key=lambda ref: ref.sha256)),
            ).model_dump(mode="json")
            manifest = HarnessCompositionManifestV2.model_validate_json(
                canonical_json_bytes(manifest_value), strict=True
            )
            manifest_raw = manifest.canonical_bytes()
            final_manifest_path = artifacts / "composition-manifest-final.json"
            _write(final_manifest_path, manifest_raw)
            composition_ref = CompositionRefV2(
                schema_version="bb.rl.harness-composition-ref.v2",
                manifest_path=os.fspath(final_manifest_path.resolve()),
                manifest_sha256=_d(manifest_raw),
                manifest_size_bytes=len(manifest_raw),
                manifest_media_type=COMPOSITION_MEDIA_TYPE,
            )
            composition_raw = composition_ref.canonical_bytes()
            final_composition_path = artifacts / "composition-ref-final.json"
            _write(final_composition_path, composition_raw)
            composition_path = final_composition_path

            first_plan = plans[0]
            slot = first_plan.policy_slots[0]
            invariant = CampaignInvariantIdentity(
                task_id="R-SWE-001",
                task_row_ref=_iref("task-row", first_plan.task.task_binding_digest),
                task_contract_digest=first_plan.task.task_contract_digest,
                repository_snapshot_ref=_iref(
                    "repository", first_plan.task.repository_snapshot_digest
                ),
                model_ref=_iref("model", slot.model_digest),
                checkpoint_ref=_iref("checkpoint", slot.checkpoint_digest),
                task_image_ref=_iref("task-image", first_plan.sandbox.image_digest),
                verifier_image_ref=_iref(
                    "verifier-image", first_plan.verifier.image_digest
                ),
                verifier_ref=_iref(
                    "verifier", first_plan.verifier.implementation_digest
                ),
            )
            target_value = {
                "schema_version": "bb.rl.phase5-f4-target-canary-input.v1",
                "production": F4ProductionBinding(
                    composition_ref_path=os.fspath(composition_path.resolve()),
                    composition_descriptor_ref=_iref(
                        "composition-descriptor", _d(composition_raw)
                    ),
                    composition_manifest_ref=_iref(
                        "composition-manifest", _d(manifest_raw)
                    ),
                    authority_bundle_ref=_iref("authority-bundle", _d(authority_raw)),
                    secret_files=dict(production.secrets.files),
                ),
                "target": spec.target,
                "execution_authority": F4TargetExecutionAuthority(
                    **spec.execution_authority.model_dump(mode="python"),
                    composition_ref=_iref(
                        "composition-descriptor", _d(composition_raw)
                    ),
                ),
                "invariant_identity": invariant,
                "variants": tuple(variants),
                "task_input": spec.task_input,
                "run_context": spec.run_context,
                "output_dir": spec.target_report_output_dir,
            }
            target = F4TargetCanaryInput.model_validate(target_value)
        finally:
            graph.cas.close()
        target_raw = canonical_json_bytes(target.model_dump(mode="json"))
        target_path = root / "target-input.json"
        _write(target_path, target_raw)
        descriptor = F4TargetInputBuildDescriptor(
            schema_version="bb.rl.phase5-f4-target-input-build.v1",
            target_input_path=os.fspath(target_path.resolve()),
            target_input_sha256=_d(target_raw),
            composition_ref_path=os.fspath(composition_path.resolve()),
            composition_descriptor_sha256=_d(composition_raw),
            composition_manifest_sha256=_d(manifest_raw),
            authority_bundle_sha256=_d(authority_raw),
            optimizer_work_packet_sha256s=tuple(
                optimizer_work_packet_sha256s
            ),
            variant_ids=VARIANT_IDS,
            payload_ready=True,
        )
        _write(
            root / "build-descriptor.json",
            canonical_json_bytes(descriptor.model_dump(mode="json")),
        )
        return descriptor
    except BaseException:
        shutil.rmtree(root, ignore_errors=True)
        raise


def read_f4_authoring_input(path: str) -> F4AuthorityAuthoringInput:
    raw = Path(path).resolve(strict=True).read_bytes()
    if canonical_json_bytes(canonical_json_loads(raw)) != raw:
        raise F4AuthorityAuthoringError("authoring input is not canonical JSON")
    return F4AuthorityAuthoringInput.model_validate_json(raw, strict=True)
