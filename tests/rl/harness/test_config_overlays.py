from __future__ import annotations

import copy
import hashlib
import json
import math
import pickle
import unicodedata
from collections.abc import Mapping, Sequence
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from breadboard.rl.harness import contracts as c
from breadboard.rl.harness.config_runtime import CompilerSemanticView, ConfigRuntime
from tests.rl.harness.test_config_selection import ResolutionFixture, _resolution_fixture


FIXTURE_ROOT = Path(__file__).resolve().parents[2] / "fixtures" / "rl" / "config_runtime"
OVERLAY_VECTORS = FIXTURE_ROOT / "overlay_vectors_v1.json"
OVERLAY_VECTORS_FILE_SHA256 = "9e74459b4362d1614972858892d98871e3b4610a4cf4d77bc43b1a26b7b6e2c9"
MAX_SAFE_INTEGER = 2**53 - 1


def _load_vectors() -> dict[str, Any]:
    return json.loads(OVERLAY_VECTORS.read_text(encoding="utf-8"))


def _validate_oracle_value(value: Any) -> None:
    if value is None or type(value) in (bool, str):
        return
    if type(value) is int:
        if not -MAX_SAFE_INTEGER <= value <= MAX_SAFE_INTEGER:
            raise ValueError("integer outside independently frozen JCS domain")
        return
    if type(value) is float:
        if not math.isfinite(value):
            raise ValueError("non-finite JSON number")
        return
    if type(value) is list:
        for item in value:
            _validate_oracle_value(item)
        return
    if type(value) is dict:
        if any(type(key) is not str for key in value):
            raise ValueError("JSON object keys must be strings")
        for item in value.values():
            _validate_oracle_value(item)
        return
    raise TypeError(f"unsupported JSON value {type(value).__name__}")


def _independent_jcs_bytes(value: Any) -> bytes:
    _validate_oracle_value(value)
    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _independent_digest(value: Any) -> str:
    return "sha256:" + hashlib.sha256(_independent_jcs_bytes(value)).hexdigest()


def _semantic_digest(value: Any) -> str:
    return _independent_digest(
        {"schema": "bb.compiled-config-semantic.v1", "config": value}
    )


def _overlay_chain_digest(
    parent_chain_digest: str | None,
    overlay_digest: str,
) -> str:
    return _independent_digest(
        {
            "schema_version": "bb.rl.overlay-chain.v1",
            "parent_chain_digest": parent_chain_digest,
            "overlay_digest": overlay_digest,
        }
    )


def _decode_pointer(path: str) -> list[str]:
    if not path.startswith("/") or path == "/":
        raise ValueError("pointer must be non-root with non-empty tokens")
    result: list[str] = []
    for encoded in path[1:].split("/"):
        if not encoded:
            raise ValueError("empty pointer token")
        decoded: list[str] = []
        index = 0
        while index < len(encoded):
            character = encoded[index]
            if character != "~":
                decoded.append(character)
                index += 1
                continue
            if index + 1 >= len(encoded) or encoded[index + 1] not in {"0", "1"}:
                raise ValueError("malformed pointer escape")
            decoded.append("~" if encoded[index + 1] == "0" else "/")
            index += 2
        token = "".join(decoded)
        canonical = token.replace("~", "~0").replace("/", "~1")
        if encoded != canonical or token != unicodedata.normalize("NFC", token):
            raise ValueError("noncanonical pointer")
        result.append(token)
    return result


def _apply_reference(value: Any, operation: dict[str, Any]) -> Any:
    result = copy.deepcopy(value)
    tokens = _decode_pointer(operation["path"])
    parent = result
    for token in tokens[:-1]:
        parent = parent[int(token)] if isinstance(parent, list) else parent[token]
    token = tokens[-1]
    if operation["op"] == "add":
        assert isinstance(parent, dict) and token not in parent
        parent[token] = copy.deepcopy(operation["value"])
    elif operation["op"] == "replace":
        if isinstance(parent, list):
            parent[int(token)] = copy.deepcopy(operation["value"])
        else:
            assert token in parent
            parent[token] = copy.deepcopy(operation["value"])
    else:
        assert operation["op"] == "remove"
        if isinstance(parent, list):
            parent.pop(int(token))
        else:
            parent.pop(token)
    return result


class OverlayResolutionCompiler:
    def __init__(self, fixture: ResolutionFixture) -> None:
        source = fixture.admission.compiler
        self.view = source.view
        self.manifest_bytes = source.manifest_bytes
        self.base_semantics = copy.deepcopy(source.effective_semantics)
        self.base_semantic_digest = source.semantic_digest
        self.store = fixture.store
        self.calls: list[str] = []
        self.validated_semantics: list[dict[str, Any]] = []
        self.validation_commit_snapshots: list[tuple[int, int, int]] = []
        self.reject_temperatures: set[float] = set()

    def verify_bundle(self, request: c.AdmissionRequest) -> None:
        self.calls.append("verify_bundle")

    def enforce_compile_budget(self, request: c.AdmissionRequest) -> None:
        self.calls.append("enforce_compile_budget")

    def compile(self, request: c.AdmissionRequest) -> CompilerSemanticView:
        self.calls.append("compile")
        roles = copy.deepcopy(self.view.roles)
        roles["semantic_identity"] = {
            "manifest_digest": request.compiled.manifest_digest,
            "semantic_digest": request.compiled.semantic_digest,
        }
        roles["requested_capabilities"] = request.requested_capabilities.to_canonical_obj()
        roles["mutable_pointer_declarations"] = [
            rule.to_canonical_obj()
            for rule in request.requested_capabilities.mutable_pointers
        ]
        return CompilerSemanticView(roles)

    def extract_effective_semantics(
        self, *, canonical_manifest_bytes: bytes
    ) -> dict[str, Any]:
        self.calls.append("extract_effective_semantics")
        if canonical_manifest_bytes != self.manifest_bytes:
            raise ValueError("manifest identity changed")
        return copy.deepcopy(self.base_semantics)

    def normalize_effective_semantics(
        self,
        *,
        canonical_manifest_bytes: bytes,
        effective_semantics: dict[str, Any],
    ) -> dict[str, Any]:
        self.calls.append("normalize_effective_semantics")
        if canonical_manifest_bytes != self.manifest_bytes:
            raise ValueError("manifest identity changed")
        return copy.deepcopy(dict(effective_semantics))


    def validate_effective_semantics(
        self,
        *,
        canonical_manifest_bytes: bytes,
        effective_semantics: dict[str, Any],
    ) -> str:
        self.calls.append("validate_effective_semantics")
        if canonical_manifest_bytes != self.manifest_bytes:
            raise ValueError("manifest identity changed")
        semantics = copy.deepcopy(dict(effective_semantics))
        allowed_keys = {"prompt", "sampling"}
        if "artifacts" in semantics:
            allowed_keys.add("artifacts")
        if set(semantics) != allowed_keys or type(semantics["prompt"]) is not str:
            raise ValueError("closed effective semantics schema rejected")
        if "artifacts" in semantics:
            artifacts = semantics["artifacts"]
            if (
                type(artifacts) is not dict
                or set(artifacts) != {"selected"}
                or type(artifacts["selected"]) is not str
            ):
                raise ValueError("closed artifact selection schema rejected")
        sampling = semantics["sampling"]
        if type(sampling) is not dict or set(sampling) != {"temperature"}:
            raise ValueError("closed sampling schema rejected")
        temperature = sampling["temperature"]
        if type(temperature) not in {int, float} or type(temperature) is bool:
            raise TypeError("temperature must be a JSON number")
        if not 0 <= temperature <= 1:
            raise ValueError("temperature is outside admitted bounds")
        self.validation_commit_snapshots.append(
            (
                len(self.store.bind_calls),
                sum(kind is c.ArtifactKind.SELECTION_RECORD for kind in self.store.kinds.values()),
                sum(
                    kind is c.ArtifactKind.EFFECTIVE_EXECUTION_PLAN
                    for kind in self.store.kinds.values()
                ),
            )
        )
        if temperature in self.reject_temperatures:
            raise ValueError("injected full-schema rejection")
        self.validated_semantics.append(semantics)
        if semantics == self.base_semantics:
            return self.base_semantic_digest
        return _semantic_digest(semantics)


def _overlay_runtime(fixture: ResolutionFixture) -> tuple[ConfigRuntime, OverlayResolutionCompiler]:
    compiler = OverlayResolutionCompiler(fixture)
    admission = fixture.admission
    runtime = ConfigRuntime(
        compiler=compiler,
        policy=admission.policy,
        registries=admission.registries,
        revocations=admission.revocations,
        store=fixture.store,
        clock=admission.clock,
        authenticator=admission.authenticator,
        policy_capabilities=fixture.policy_registry,
    )
    return runtime, compiler


def _artifact_selection_fixture() -> tuple[ResolutionFixture, OverlayResolutionCompiler]:
    seed = _resolution_fixture(algorithm="direct-v1", candidate_count=1)
    rule = c.MutablePointerRule(
        pointer="/artifacts/selected",
        allowed_operations=(c.MutableOperation.REPLACE,),
        value_schema_digest="sha256:" + "a" * 64,
        authority_effect=c.AuthorityEffect.NONE,
        removable=False,
    )
    policy_payload = seed.admission.policy.model_dump(mode="json")
    policy_payload["ceiling"]["mutable_pointer_rules"] = sorted(
        [
            *policy_payload["ceiling"]["mutable_pointer_rules"],
            rule.to_canonical_obj(),
        ],
        key=lambda item: item["pointer"],
    )
    policy = c.AdmissionPolicySnapshot.model_validate(policy_payload)
    compiler = OverlayResolutionCompiler(seed)
    compiler.base_semantics["artifacts"] = {"selected": "artifact-a"}
    compiler.base_semantic_digest = _semantic_digest(compiler.base_semantics)
    request_payload = seed.admission.request.model_dump(mode="json")
    requested_capabilities = request_payload["requested_capabilities"]
    requested_capabilities["mutable_pointers"] = sorted(
        [*requested_capabilities["mutable_pointers"], rule.to_canonical_obj()],
        key=lambda item: item["pointer"],
    )
    requested_capabilities["artifacts"]["max_each_bytes"] //= 2
    requested_capabilities["artifacts"]["max_total_bytes"] //= 2
    request_payload["requested_capability_digest"] = _independent_digest(
        requested_capabilities
    )
    request_payload["compiled"]["semantic_digest"] = compiler.base_semantic_digest
    request_payload["behavior_source"]["semantic_digest"] = compiler.base_semantic_digest
    request_payload["admission_policy_digest"] = policy.canonical_digest()
    admission_request = c.AdmissionRequest.model_validate(request_payload)
    runtime = ConfigRuntime(
        compiler=compiler,
        policy=policy,
        registries=seed.admission.registries,
        revocations=seed.admission.revocations,
        store=seed.store,
        clock=seed.admission.clock,
        authenticator=seed.admission.authenticator,
        policy_capabilities=seed.policy_registry,
    )
    base_ref = runtime.admit(admission_request)
    base_receipt = c.AdmissionReceipt.model_validate_json(seed.store.records[base_ref.digest])
    admitted_set = c.AdmittedSetManifest(
        compiler_abi=base_receipt.compiled.compiler.semantic_version,
        admission_policy_digest=base_receipt.admission_policy_digest,
        operator_ceiling_digest=base_receipt.operator_ceiling_digest,
        registry_snapshot_digest=base_receipt.registry_snapshot_digest,
        revocation=base_receipt.revocation,
        receipt_digests=(base_ref.digest,),
        validity=base_receipt.validity,
    )
    admitted_ref = seed.store.publish(
        kind=c.ArtifactKind.ADMITTED_SET,
        canonical_bytes=admitted_set.canonical_bytes(),
    )
    assert isinstance(seed.selector, c.DirectSelector)
    selector_payload = seed.selector.model_dump(mode="json")
    selector_payload["admitted_set_root"] = admitted_ref.sha256
    selector_payload["admission_policy_digest"] = base_receipt.admission_policy_digest
    selector_payload["operator_ceiling_digest"] = base_receipt.operator_ceiling_digest
    selector_payload["candidate"]["receipt_digest"] = base_ref.digest
    selector = c.DirectSelector.model_validate(selector_payload)
    selector_artifact = seed.store.publish(
        kind=c.ArtifactKind.DIRECT_SELECTOR,
        canonical_bytes=selector.canonical_bytes(),
    )
    selector_ref = c.DirectSelectorRef(
        digest=selector_artifact.sha256,
        ref=selector_artifact,
    )
    resolution_payload = seed.request.model_dump(mode="json")
    resolution_payload["selector"] = selector_ref.to_canonical_obj()
    admission = replace(
        seed.admission,
        runtime=runtime,
        request=admission_request,
        policy=policy,
        compiler=compiler,
    )
    return (
        ResolutionFixture(
            runtime=runtime,
            request=c.ResolveEpisodeRequest.model_validate(resolution_payload),
            admission=admission,
            base_receipt_ref=base_ref,
            admitted_set=admitted_set,
            selector=selector,
            selector_ref=selector_ref,
            policy_observation=seed.policy_observation,
            policy_registry=seed.policy_registry,
            store=seed.store,
            effects=seed.effects,
        ),
        compiler,
    )


def _admit_overlay_layer(
    fixture: ResolutionFixture,
    runtime: ConfigRuntime,
    *,
    parent_receipt: c.AdmissionReceiptRef,
    before: dict[str, Any],
    after: dict[str, Any],
    source_kind: str,
    source_digest: str,
    capabilities: dict[str, Any] | None = None,
    operation: c.OverlayOperation | None = None,
    validity: c.ValidityWindow | None = None,
    overlay_chain_digest: str | None = None,
) -> tuple[c.AdmittedOverlayRef, c.MutationOverlayManifest]:
    if operation is None:
        operation = c.OverlayOperation(
            op="replace",
            path="/sampling/temperature",
            value=after["sampling"]["temperature"],
        )
    compiler_base = getattr(fixture.admission.compiler, "effective_semantics", None)
    if compiler_base is None:
        compiler_base = fixture.admission.compiler.base_semantics
    before_digest = (
        fixture.admission.request.compiled.semantic_digest
        if before == compiler_base
        else _semantic_digest(before)
    )
    after_digest = _semantic_digest(after)
    manifest = c.MutationOverlayManifest(
        base_compiled_manifest_digest=fixture.admission.request.compiled.manifest_digest,
        parent_receipt_digest=parent_receipt.digest,
        expected_before_semantic_digest=before_digest,
        operations=(operation,),
        expected_transitions=(
            c.OverlayTransition(
                operation_index=0,
                before_semantic_digest=before_digest,
                after_semantic_digest=after_digest,
            ),
        ),
        expected_after_semantic_digest=after_digest,
        provenance=c.OverlayProvenance(
            author_subject_digest=fixture.request.subject.canonical_digest(),
            source_kind=source_kind,
            source_artifact_digest=source_digest,
            rationale_code="bounded-overlay",
        ),
    )
    overlay_ref = fixture.store.publish(
        kind=c.ArtifactKind.MUTATION_OVERLAY,
        canonical_bytes=manifest.canonical_bytes(),
    )
    parent = c.AdmissionReceipt.model_validate_json(
        fixture.store.records[parent_receipt.digest]
    )
    chain_digest = overlay_chain_digest or _overlay_chain_digest(
        parent.overlay_chain_digest,
        overlay_ref.sha256,
    )
    request_payload = fixture.admission.request.model_dump(mode="json")
    request_payload["behavior_source"] = {
        "source_kind": "overlay_derived",
        "base_manifest_digest": fixture.admission.request.compiled.manifest_digest,
        "parent_receipt_digest": parent_receipt.digest,
        "overlay_chain_digest": chain_digest,
        "derived_semantic_digest": after_digest,
    }
    request_payload["compiled"]["semantic_digest"] = after_digest
    request_payload["parent_receipt_digest"] = parent_receipt.digest
    request_payload["overlay_chain_digest"] = chain_digest
    if capabilities is not None:
        request_payload["requested_capabilities"] = copy.deepcopy(capabilities)
        request_payload["requested_capability_digest"] = _independent_digest(capabilities)
    if validity is not None:
        request_payload["validity"] = validity.to_canonical_obj()
    derived_ref = runtime.admit(c.AdmissionRequest.model_validate(request_payload))
    return (
        c.AdmittedOverlayRef(
            overlay_digest=overlay_ref.sha256,
            result_receipt_digest=derived_ref.digest,
        ),
        manifest,
    )


def _resolution_with_candidate_and_episode_overlay() -> tuple[
    ResolutionFixture,
    OverlayResolutionCompiler,
    tuple[c.MutationOverlayManifest, c.MutationOverlayManifest],
]:
    fixture = _resolution_fixture(algorithm="direct-v1", candidate_count=1)
    runtime, compiler = _overlay_runtime(fixture)
    base = copy.deepcopy(fixture.admission.compiler.effective_semantics)
    candidate = copy.deepcopy(base)
    candidate["sampling"]["temperature"] = 0.5
    candidate_ref, candidate_manifest = _admit_overlay_layer(
        fixture,
        runtime,
        parent_receipt=fixture.base_receipt_ref,
        before=base,
        after=candidate,
        source_kind="optimizer",
        source_digest="sha256:" + "6" * 64,
    )
    candidate_receipt_ref = c.AdmissionReceiptRef(
        digest=candidate_ref.result_receipt_digest,
        ref=c.ArtifactRef(
            artifact_id=candidate_ref.result_receipt_digest,
            sha256=candidate_ref.result_receipt_digest,
            size_bytes=len(fixture.store.records[candidate_ref.result_receipt_digest]),
            media_type="application/vnd.breadboard.admission-receipt+json;version=1",
        ),
    )
    episode = copy.deepcopy(candidate)
    episode["sampling"]["temperature"] = 0.25
    episode_ref, episode_manifest = _admit_overlay_layer(
        fixture,
        runtime,
        parent_receipt=candidate_receipt_ref,
        before=candidate,
        after=episode,
        source_kind="experiment",
        source_digest="sha256:" + "7" * 64,
    )
    fixture = ResolutionFixture(
        runtime=runtime,
        request=fixture.request,
        admission=fixture.admission,
        base_receipt_ref=fixture.base_receipt_ref,
        admitted_set=fixture.admitted_set,
        selector=fixture.selector,
        selector_ref=fixture.selector_ref,
        policy_observation=fixture.policy_observation,
        policy_registry=fixture.policy_registry,
        store=fixture.store,
        effects=fixture.effects,
    )
    fixture = _with_overlay_chain(
        fixture,
        candidate_overlays=(candidate_ref,),
        episode_overlays=(episode_ref,),
    )
    return fixture, compiler, (candidate_manifest, episode_manifest)


def test_candidate_and_episode_overlays_are_pre_admitted_as_one_parent_chain() -> None:
    fixture, compiler, manifests = _resolution_with_candidate_and_episode_overlay()
    candidate, episode = manifests
    candidate_ref = fixture.selector.candidate.overlays[0]
    episode_ref = fixture.request.episode_overlays[0]
    assert candidate.parent_receipt_digest == fixture.base_receipt_ref.digest
    assert candidate_ref.overlay_digest == candidate.canonical_digest()
    assert episode.parent_receipt_digest == candidate_ref.result_receipt_digest
    assert episode_ref.overlay_digest == episode.canonical_digest()
    assert episode_ref.result_receipt_digest in fixture.store.records
    assert {
        fixture.base_receipt_ref.digest,
        candidate_ref.result_receipt_digest,
        episode_ref.result_receipt_digest,
    } <= set(fixture.admitted_set.receipt_digests)
    candidate_receipt = c.AdmissionReceipt.model_validate_json(
        fixture.store.records[candidate_ref.result_receipt_digest]
    )
    episode_receipt = c.AdmissionReceipt.model_validate_json(
        fixture.store.records[episode_ref.result_receipt_digest]
    )
    candidate_chain = _overlay_chain_digest(None, candidate_ref.overlay_digest)
    episode_chain = _overlay_chain_digest(candidate_chain, episode_ref.overlay_digest)
    assert candidate_receipt.overlay_chain_digest == candidate_chain
    assert candidate_receipt.behavior_source.overlay_chain_digest == candidate_chain
    assert episode_receipt.overlay_chain_digest == episode_chain
    assert episode_receipt.behavior_source.overlay_chain_digest == episode_chain
    assert compiler.calls.count("compile") == 2
    fixture.effects.assert_zero()


def test_resolve_episode_commits_then_applies_candidate_before_episode_and_reads_plan_back() -> None:
    fixture, compiler, manifests = _resolution_with_candidate_and_episode_overlay()
    manifest_bytes = fixture.store.records[fixture.admission.request.compiled.manifest_digest]
    resolved = fixture.runtime.resolve_episode(fixture.request)

    assert isinstance(resolved, c.ResolvedEpisodePlan)
    applications = resolved.effective_plan.overlay_applications
    assert [application.overlay_digest for application in applications] == [
        manifest.canonical_digest() for manifest in manifests
    ]
    assert applications[0].parent_receipt_digest == fixture.base_receipt_ref.digest
    assert applications[1].parent_receipt_digest == applications[0].result_receipt_digest
    assert resolved.final_receipt_digest == applications[1].result_receipt_digest
    assert resolved.effective_plan.effective_semantics["sampling"]["temperature"] == 0.25
    candidate_semantics = {
        "prompt": "selection fixture prompt",
        "sampling": {"temperature": 0.5},
    }
    episode_semantics = {
        "prompt": "selection fixture prompt",
        "sampling": {"temperature": 0.25},
    }
    assert compiler.validated_semantics[0] == candidate_semantics
    assert compiler.validation_commit_snapshots[0] == (0, 0, 0)
    assert compiler.validated_semantics[-1] == episode_semantics
    assert compiler.validation_commit_snapshots[-1][1:] == (1, 0)
    assert all(
        semantics in (candidate_semantics, episode_semantics)
        for semantics in compiler.validated_semantics
    )
    assert fixture.store.records[fixture.admission.request.compiled.manifest_digest] == manifest_bytes
    assert sum(
        kind is c.ArtifactKind.SELECTION_RECORD for kind in fixture.store.kinds.values()
    ) == 1
    assert sum(
        kind is c.ArtifactKind.EFFECTIVE_EXECUTION_PLAN for kind in fixture.store.kinds.values()
    ) == 1
    assert any(
        digest == resolved.effective_plan_ref.sha256
        and kind is c.ArtifactKind.EFFECTIVE_EXECUTION_PLAN
        for digest, kind, _ in fixture.store.load_calls
    )
    assert resolved.currentness.checkpoint is c.PrivilegedCheckpoint.BEFORE_ALLOCATION
    fixture.effects.assert_zero()


def test_selected_overlay_schema_failure_keeps_one_record_and_never_publishes_plan() -> None:
    fixture, compiler, _ = _resolution_with_candidate_and_episode_overlay()
    compiler.reject_temperatures.add(0.25)

    with pytest.raises(c.ConfigRuntimeDenial) as first:
        fixture.runtime.resolve_episode(fixture.request)
    with pytest.raises(c.ConfigRuntimeDenial) as retry:
        fixture.runtime.resolve_episode(fixture.request)

    assert first.value.stage is c.DenialStage.OVERLAY_APPLICATION
    assert first.value.code is c.DenialCode.POST_OVERLAY_SCHEMA_INVALID
    assert first.value.operation_index == 0
    assert first.value.selection_record_digest is not None
    assert retry.value.selection_record_digest == first.value.selection_record_digest
    assert len(fixture.store.bindings) == 1
    assert sum(
        kind is c.ArtifactKind.SELECTION_RECORD for kind in fixture.store.kinds.values()
    ) == 1
    assert not any(
        kind is c.ArtifactKind.EFFECTIVE_EXECUTION_PLAN
        for kind in fixture.store.kinds.values()
    )
    assert compiler.validation_commit_snapshots[0] == (0, 0, 0)
    assert all(snapshot[2] == 0 for snapshot in compiler.validation_commit_snapshots)
    assert any(snapshot[1] == 1 for snapshot in compiler.validation_commit_snapshots)
    fixture.effects.assert_zero()


def _with_overlay_chain(
    fixture: ResolutionFixture,
    *,
    candidate_overlays: tuple[c.AdmittedOverlayRef, ...],
    episode_overlays: tuple[c.AdmittedOverlayRef, ...],
    include_result_receipts: bool = True,
) -> ResolutionFixture:
    assert isinstance(fixture.selector, c.DirectSelector)
    admitted_set = fixture.admitted_set
    if include_result_receipts:
        receipt_digests = tuple(
            sorted(
                {
                    *admitted_set.receipt_digests,
                    *(ref.result_receipt_digest for ref in candidate_overlays),
                    *(ref.result_receipt_digest for ref in episode_overlays),
                }
            )
        )
        receipts = tuple(
            c.AdmissionReceipt.model_validate_json(fixture.store.records[digest])
            for digest in receipt_digests
        )
        validity = c.ValidityWindow(
            issued_at=max(receipt.validity.issued_at for receipt in receipts),
            not_before=max(receipt.validity.not_before for receipt in receipts),
            expires_at=min(receipt.validity.expires_at for receipt in receipts),
        )
        admitted_payload = admitted_set.model_dump(mode="json")
        admitted_payload["receipt_digests"] = list(receipt_digests)
        admitted_payload["validity"] = validity.to_canonical_obj()
        admitted_set = c.AdmittedSetManifest.model_validate(admitted_payload)
        admitted_ref = fixture.store.publish(
            kind=c.ArtifactKind.ADMITTED_SET,
            canonical_bytes=admitted_set.canonical_bytes(),
        )
    else:
        admitted_ref = c.ArtifactRef(
            artifact_id=fixture.selector.admitted_set_root,
            sha256=fixture.selector.admitted_set_root,
            size_bytes=len(fixture.store.records[fixture.selector.admitted_set_root]),
            media_type="application/vnd.breadboard.admitted-set+json;version=1",
        )
        validity = fixture.selector.validity

    selector_payload = fixture.selector.model_dump(mode="json")
    selector_payload["admitted_set_root"] = admitted_ref.sha256
    selector_payload["validity"] = validity.to_canonical_obj()
    selector_payload["candidate"]["overlays"] = [
        overlay.to_canonical_obj() for overlay in candidate_overlays
    ]
    selector = c.DirectSelector.model_validate(selector_payload)
    selector_artifact = fixture.store.publish(
        kind=c.ArtifactKind.DIRECT_SELECTOR,
        canonical_bytes=selector.canonical_bytes(),
    )
    selector_ref = c.DirectSelectorRef(
        digest=selector_artifact.sha256,
        ref=selector_artifact,
    )
    request_payload = fixture.request.model_dump(mode="json")
    request_payload["selector"] = selector_ref.to_canonical_obj()
    request_payload["episode_overlays"] = [
        overlay.to_canonical_obj() for overlay in episode_overlays
    ]
    return ResolutionFixture(
        runtime=fixture.runtime,
        request=c.ResolveEpisodeRequest.model_validate(request_payload),
        admission=fixture.admission,
        base_receipt_ref=fixture.base_receipt_ref,
        admitted_set=admitted_set,
        selector=selector,
        selector_ref=selector_ref,
        policy_observation=fixture.policy_observation,
        policy_registry=fixture.policy_registry,
        store=fixture.store,
        effects=fixture.effects,
    )


def _without_admitted_receipt(
    fixture: ResolutionFixture,
    receipt_digest: str,
) -> ResolutionFixture:
    assert isinstance(fixture.selector, c.DirectSelector)
    admitted_payload = fixture.admitted_set.model_dump(mode="json")
    admitted_payload["receipt_digests"] = [
        digest
        for digest in fixture.admitted_set.receipt_digests
        if digest != receipt_digest
    ]
    admitted_set = c.AdmittedSetManifest.model_validate(admitted_payload)
    admitted_ref = fixture.store.publish(
        kind=c.ArtifactKind.ADMITTED_SET,
        canonical_bytes=admitted_set.canonical_bytes(),
    )
    selector_payload = fixture.selector.model_dump(mode="json")
    selector_payload["admitted_set_root"] = admitted_ref.sha256
    selector = c.DirectSelector.model_validate(selector_payload)
    selector_artifact = fixture.store.publish(
        kind=c.ArtifactKind.DIRECT_SELECTOR,
        canonical_bytes=selector.canonical_bytes(),
    )
    selector_ref = c.DirectSelectorRef(
        digest=selector_artifact.sha256,
        ref=selector_artifact,
    )
    request_payload = fixture.request.model_dump(mode="json")
    request_payload["selector"] = selector_ref.to_canonical_obj()
    return ResolutionFixture(
        runtime=fixture.runtime,
        request=c.ResolveEpisodeRequest.model_validate(request_payload),
        admission=fixture.admission,
        base_receipt_ref=fixture.base_receipt_ref,
        admitted_set=admitted_set,
        selector=selector,
        selector_ref=selector_ref,
        policy_observation=fixture.policy_observation,
        policy_registry=fixture.policy_registry,
        store=fixture.store,
        effects=fixture.effects,
    )


def test_candidate_prevalidation_and_episode_readmission_straddle_commit() -> None:
    fixture, compiler, _ = _resolution_with_candidate_and_episode_overlay()
    admission_publish_count_before = sum(
        kind is c.ArtifactKind.ADMISSION_RECEIPT
        for kind, _ in fixture.store.publish_calls
    )

    resolved = fixture.runtime.resolve_episode(fixture.request)

    assert sum(
        kind is c.ArtifactKind.ADMISSION_RECEIPT
        for kind, _ in fixture.store.publish_calls
    ) - admission_publish_count_before >= 2
    assert len(resolved.effective_plan.overlay_applications) == 2
    assert len(fixture.store.bind_calls) == 1
    assert compiler.validation_commit_snapshots[0] == (0, 0, 0)
    assert compiler.validation_commit_snapshots[-1][1:] == (1, 0)
    assert compiler.validated_semantics[0]["sampling"]["temperature"] == 0.5
    assert compiler.validated_semantics[-1]["sampling"]["temperature"] == 0.25
    fixture.effects.assert_zero()


def test_episode_layer_cannot_skip_its_candidate_parent() -> None:
    fixture, _, _ = _resolution_with_candidate_and_episode_overlay()
    fixture = _with_overlay_chain(
        fixture,
        candidate_overlays=(),
        episode_overlays=fixture.request.episode_overlays,
    )

    with pytest.raises(c.ConfigRuntimeDenial) as caught:
        fixture.runtime.resolve_episode(fixture.request)

    assert caught.value.stage is c.DenialStage.OVERLAY_VALIDATION
    assert caught.value.code is c.DenialCode.OVERLAY_RECEIPT_MISMATCH
    assert caught.value.selection_record_digest is not None
    assert len(fixture.store.bindings) == 1
    assert not any(
        kind is c.ArtifactKind.EFFECTIVE_EXECUTION_PLAN
        for kind in fixture.store.kinds.values()
    )
    fixture.effects.assert_zero()


@pytest.mark.parametrize("layer", ["candidate", "episode"])
def test_every_overlay_result_receipt_must_belong_to_the_admitted_root(
    layer: str,
) -> None:
    fixture, _, _ = _resolution_with_candidate_and_episode_overlay()
    missing_digest = (
        fixture.selector.candidate.overlays[0].result_receipt_digest
        if layer == "candidate"
        else fixture.request.episode_overlays[0].result_receipt_digest
    )
    fixture = _without_admitted_receipt(fixture, missing_digest)

    with pytest.raises(c.ConfigRuntimeDenial) as caught:
        fixture.runtime.resolve_episode(fixture.request)

    assert caught.value.code is c.DenialCode.STALE_CANDIDATE_RECEIPT
    assert caught.value.artifact_digest == missing_digest
    if layer == "candidate":
        assert caught.value.selection_record_digest is None
    else:
        assert caught.value.selection_record_digest is not None
    assert not any(
        kind is c.ArtifactKind.EFFECTIVE_EXECUTION_PLAN
        for kind in fixture.store.kinds.values()
    )
    fixture.effects.assert_zero()


def test_same_result_overlay_cannot_borrow_receipt_from_different_provenance() -> None:
    fixture = _resolution_fixture(algorithm="direct-v1", candidate_count=1)
    runtime, _ = _overlay_runtime(fixture)
    fixture = replace(fixture, runtime=runtime)
    base = copy.deepcopy(fixture.admission.compiler.effective_semantics)
    after = copy.deepcopy(base)
    after["sampling"]["temperature"] = 0.5
    receipt_a, _ = _admit_overlay_layer(
        fixture,
        runtime,
        parent_receipt=fixture.base_receipt_ref,
        before=base,
        after=after,
        source_kind="optimizer",
        source_digest="sha256:" + "6" * 64,
    )
    receipt_b, _ = _admit_overlay_layer(
        fixture,
        runtime,
        parent_receipt=fixture.base_receipt_ref,
        before=base,
        after=after,
        source_kind="experiment",
        source_digest="sha256:" + "7" * 64,
    )
    assert receipt_a.overlay_digest != receipt_b.overlay_digest
    cross_pair = c.AdmittedOverlayRef(
        overlay_digest=receipt_b.overlay_digest,
        result_receipt_digest=receipt_a.result_receipt_digest,
    )
    fixture = _with_overlay_chain(
        fixture,
        candidate_overlays=(cross_pair,),
        episode_overlays=(),
    )

    with pytest.raises(c.ConfigRuntimeDenial) as caught:
        runtime.resolve_episode(fixture.request)

    assert caught.value.stage is c.DenialStage.OVERLAY_VALIDATION
    assert caught.value.code is c.DenialCode.OVERLAY_RECEIPT_MISMATCH
    assert caught.value.artifact_digest == receipt_b.overlay_digest
    assert caught.value.selection_record_digest is None
    assert fixture.store.bindings == {}
    fixture.effects.assert_zero()


@pytest.mark.parametrize("selected_candidate", ["a", "b"])
def test_overlay_cache_uses_manifest_and_result_receipt_as_the_complete_ref(
    selected_candidate: str,
) -> None:
    fixture = _resolution_fixture(algorithm="weighted-v1", candidate_count=2)
    runtime, _ = _overlay_runtime(fixture)
    fixture = replace(fixture, runtime=runtime)
    base = copy.deepcopy(fixture.admission.compiler.effective_semantics)
    after = copy.deepcopy(base)
    after["sampling"]["temperature"] = 0.5
    first_ref, _ = _admit_overlay_layer(
        fixture,
        runtime,
        parent_receipt=fixture.base_receipt_ref,
        before=base,
        after=after,
        source_kind="optimizer",
        source_digest="sha256:" + "6" * 64,
        validity=c.ValidityWindow(
            issued_at="2026-07-10T12:00:00Z",
            not_before="2026-07-10T12:00:00Z",
            expires_at="2026-07-10T12:45:00Z",
        ),
    )
    second_ref, _ = _admit_overlay_layer(
        fixture,
        runtime,
        parent_receipt=fixture.base_receipt_ref,
        before=base,
        after=after,
        source_kind="optimizer",
        source_digest="sha256:" + "6" * 64,
        validity=c.ValidityWindow(
            issued_at="2026-07-10T12:00:00Z",
            not_before="2026-07-10T12:00:00Z",
            expires_at="2026-07-10T12:50:00Z",
        ),
    )
    assert first_ref.overlay_digest == second_ref.overlay_digest
    assert first_ref.result_receipt_digest != second_ref.result_receipt_digest

    admitted_payload = fixture.admitted_set.model_dump(mode="json")
    admitted_payload["receipt_digests"] = sorted(
        {
            *fixture.admitted_set.receipt_digests,
            first_ref.result_receipt_digest,
            second_ref.result_receipt_digest,
        }
    )
    admitted_payload["validity"]["expires_at"] = "2026-07-10T12:45:00Z"
    admitted_set = c.AdmittedSetManifest.model_validate(admitted_payload)
    admitted_ref = fixture.store.publish(
        kind=c.ArtifactKind.ADMITTED_SET,
        canonical_bytes=admitted_set.canonical_bytes(),
    )
    assert isinstance(fixture.selector, c.ConfigSetManifest)
    selector_payload = fixture.selector.model_dump(mode="json")
    selector_payload["admitted_set_root"] = admitted_ref.sha256
    selector_payload["validity"]["expires_at"] = "2026-07-10T12:45:00Z"
    refs_by_id = {"a": first_ref, "b": second_ref}
    for weighted in selector_payload["candidates"]:
        weighted["weight"] = 1
        weighted["candidate"]["predicates"] = []
        weighted["candidate"]["overlays"] = [
            refs_by_id[weighted["candidate"]["candidate_id"]].to_canonical_obj()
        ]
    selector = c.ConfigSetManifest.model_validate(selector_payload)
    selector_artifact = fixture.store.publish(
        kind=c.ArtifactKind.CONFIG_SET,
        canonical_bytes=selector.canonical_bytes(),
    )
    selector_ref = c.WeightedSelectorRef(
        digest=selector_artifact.sha256,
        ref=selector_artifact,
    )
    target_modulo = 0 if selected_candidate == "a" else 1
    task_digest = fixture.request.task.canonical_digest()
    capability_digest = fixture.policy_observation.capability_digest
    selection_nonce = next(
        "sha256:" + f"{value:064x}"
        for value in range(1, 1_000)
        if int.from_bytes(
            hashlib.sha256(
                b"bb-weighted-v1\x00"
                + selector_ref.digest.encode("ascii")
                + ("sha256:" + f"{value:064x}").encode("ascii")
                + task_digest.encode("ascii")
                + capability_digest.encode("ascii")
            ).digest(),
            "big",
        )
        % 2
        == target_modulo
    )
    request_payload = fixture.request.model_dump(mode="json")
    request_payload["selector"] = selector_ref.to_canonical_obj()
    request_payload["selection_nonce"] = selection_nonce
    request = c.ResolveEpisodeRequest.model_validate(request_payload)

    resolved = runtime.resolve_episode(request)

    selected_ref = refs_by_id[selected_candidate]
    assert resolved.effective_plan.overlay_applications[0].overlay_digest == (
        selected_ref.overlay_digest
    )
    assert resolved.effective_plan.overlay_applications[0].result_receipt_digest == (
        selected_ref.result_receipt_digest
    )
    assert resolved.final_receipt_digest == selected_ref.result_receipt_digest
    record = c.SelectionRecord.model_validate_json(
        fixture.store.records[resolved.selection_record_ref.sha256]
    )
    assert record.selected_candidate_id == selected_candidate
    assert record.selected_overlays == (selected_ref,)
    fixture.effects.assert_zero()


def test_episode_receipt_must_sign_the_exact_cumulative_overlay_chain() -> None:
    fixture, _, _ = _resolution_with_candidate_and_episode_overlay()
    candidate_ref = fixture.selector.candidate.overlays[0]
    episode_ref = fixture.request.episode_overlays[0]
    candidate_receipt = c.AdmissionReceiptRef(
        digest=candidate_ref.result_receipt_digest,
        ref=c.ArtifactRef(
            artifact_id=candidate_ref.result_receipt_digest,
            sha256=candidate_ref.result_receipt_digest,
            size_bytes=len(fixture.store.records[candidate_ref.result_receipt_digest]),
            media_type="application/vnd.breadboard.admission-receipt+json;version=1",
        ),
    )
    candidate = {
        "prompt": "selection fixture prompt",
        "sampling": {"temperature": 0.5},
    }
    episode = copy.deepcopy(candidate)
    episode["sampling"]["temperature"] = 0.25
    layer_local_ref, _ = _admit_overlay_layer(
        fixture,
        fixture.runtime,
        parent_receipt=candidate_receipt,
        before=candidate,
        after=episode,
        source_kind="experiment",
        source_digest="sha256:" + "7" * 64,
        overlay_chain_digest=episode_ref.overlay_digest,
    )
    assert layer_local_ref.overlay_digest == episode_ref.overlay_digest
    assert layer_local_ref.result_receipt_digest != episode_ref.result_receipt_digest
    fixture = _with_overlay_chain(
        fixture,
        candidate_overlays=(candidate_ref,),
        episode_overlays=(layer_local_ref,),
    )

    with pytest.raises(c.ConfigRuntimeDenial) as caught:
        fixture.runtime.resolve_episode(fixture.request)

    assert caught.value.stage is c.DenialStage.OVERLAY_VALIDATION
    assert caught.value.code is c.DenialCode.OVERLAY_RECEIPT_MISMATCH
    assert caught.value.selection_record_digest is not None
    assert len(fixture.store.bindings) == 1
    assert not any(
        kind is c.ArtifactKind.EFFECTIVE_EXECUTION_PLAN
        for kind in fixture.store.kinds.values()
    )
    fixture.effects.assert_zero()


@pytest.mark.parametrize("currentness", ["expiry", "revocation"])
def test_episode_overlay_readmission_rechecks_currentness_after_selection_commit(
    currentness: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixture, _, _ = _resolution_with_candidate_and_episode_overlay()
    admit = fixture.runtime.admit
    readmissions = 0

    def change_currentness_before_episode(
        request: c.AdmissionRequest,
    ) -> c.AdmissionReceiptRef:
        nonlocal readmissions
        if isinstance(request.behavior_source, c.OverlayDerivedBehaviorSource):
            readmissions += 1
            if readmissions == 2:
                if currentness == "expiry":
                    fixture.admission.clock.value = fixture.admission.clock.value.replace(
                        hour=13,
                        minute=0,
                        second=1,
                    )
                else:
                    bound = fixture.admission.revocations.current
                    fixture.admission.revocations.current = c.RevocationBinding(
                        scope_digest=bound.scope_digest,
                        epoch=bound.epoch + 1,
                        state_digest=bound.state_digest,
                    )
        return admit(request)

    monkeypatch.setattr(fixture.runtime, "admit", change_currentness_before_episode)

    with pytest.raises(c.ConfigRuntimeDenial) as caught:
        fixture.runtime.resolve_episode(fixture.request)

    assert readmissions == 2
    assert caught.value.stage is c.DenialStage.READMISSION
    assert caught.value.code is c.DenialCode.DERIVED_RECEIPT_MISMATCH
    assert caught.value.selection_record_digest is not None
    assert len(fixture.store.bindings) == 1
    assert not any(
        kind is c.ArtifactKind.EFFECTIVE_EXECUTION_PLAN
        for kind in fixture.store.kinds.values()
    )
    fixture.effects.assert_zero()


def test_raw_authority_overlay_denies_after_commit_without_partial_plan() -> None:
    fixture, _, _ = _resolution_with_candidate_and_episode_overlay()
    candidate_ref = fixture.selector.candidate.overlays[0]
    candidate_receipt = c.AdmissionReceiptRef(
        digest=candidate_ref.result_receipt_digest,
        ref=c.ArtifactRef(
            artifact_id=candidate_ref.result_receipt_digest,
            sha256=candidate_ref.result_receipt_digest,
            size_bytes=len(fixture.store.records[candidate_ref.result_receipt_digest]),
            media_type="application/vnd.breadboard.admission-receipt+json;version=1",
        ),
    )
    before = {
        "prompt": "selection fixture prompt",
        "sampling": {"temperature": 0.5},
    }
    after = copy.deepcopy(before)
    after["sampling"]["temperature"] = "https://authority.invalid/raw"
    raw_ref, _ = _admit_overlay_layer(
        fixture,
        fixture.runtime,
        parent_receipt=candidate_receipt,
        before=before,
        after=after,
        source_kind="experiment",
        source_digest="sha256:" + "8" * 64,
    )
    fixture = _with_overlay_chain(
        fixture,
        candidate_overlays=(candidate_ref,),
        episode_overlays=(raw_ref,),
    )

    with pytest.raises(c.ConfigRuntimeDenial) as caught:
        fixture.runtime.resolve_episode(fixture.request)

    assert caught.value.stage is c.DenialStage.OVERLAY_VALIDATION
    assert caught.value.code is c.DenialCode.OVERLAY_VALUE_FORBIDDEN
    assert caught.value.pointer == "/sampling/temperature"
    assert caught.value.operation_index == 0
    assert caught.value.selection_record_digest is not None
    assert not any(
        kind is c.ArtifactKind.EFFECTIVE_EXECUTION_PLAN
        for kind in fixture.store.kinds.values()
    )
    fixture.effects.assert_zero()


def test_exact_admitted_non_security_artifact_selection_is_re_admitted() -> None:
    fixture, compiler = _artifact_selection_fixture()
    base = copy.deepcopy(compiler.base_semantics)
    after = copy.deepcopy(base)
    after["artifacts"]["selected"] = "artifact-b"
    overlay_ref, _ = _admit_overlay_layer(
        fixture,
        fixture.runtime,
        parent_receipt=fixture.base_receipt_ref,
        before=base,
        after=after,
        source_kind="operator",
        source_digest="sha256:" + "b" * 64,
        operation=c.OverlayOperation(
            op="replace",
            path="/artifacts/selected",
            value="artifact-b",
        ),
    )
    fixture = _with_overlay_chain(
        fixture,
        candidate_overlays=(overlay_ref,),
        episode_overlays=(),
    )
    admission_publications = sum(
        kind is c.ArtifactKind.ADMISSION_RECEIPT
        for kind, _ in fixture.store.publish_calls
    )

    resolved = fixture.runtime.resolve_episode(fixture.request)

    assert resolved.effective_plan.effective_semantics["artifacts"] == {
        "selected": "artifact-b"
    }
    assert resolved.final_receipt_digest == overlay_ref.result_receipt_digest
    assert resolved.effective_plan.overlay_applications[0].result_receipt_digest == (
        overlay_ref.result_receipt_digest
    )
    assert sum(
        kind is c.ArtifactKind.ADMISSION_RECEIPT
        for kind, _ in fixture.store.publish_calls
    ) == admission_publications + 1
    base_receipt = c.AdmissionReceipt.model_validate_json(
        fixture.store.records[fixture.base_receipt_ref.digest]
    )
    exact_rules = {
        rule.pointer: rule for rule in base_receipt.effective_capabilities.mutable_pointers
    }
    assert exact_rules["/artifacts/selected"].authority_effect is c.AuthorityEffect.NONE
    assert exact_rules["/artifacts/selected"].allowed_operations == (
        c.MutableOperation.REPLACE,
    )
    assert {
        rule.pointer for rule in fixture.admission.policy.ceiling.mutable_pointer_rules
    } >= {"/artifacts/selected"}
    fixture.effects.assert_zero()


def test_artifact_selection_receipt_cannot_increase_artifact_authority() -> None:
    fixture, compiler = _artifact_selection_fixture()
    base = copy.deepcopy(compiler.base_semantics)
    after = copy.deepcopy(base)
    after["artifacts"]["selected"] = "artifact-b"
    base_receipt = c.AdmissionReceipt.model_validate_json(
        fixture.store.records[fixture.base_receipt_ref.digest]
    )
    increased = base_receipt.effective_capabilities.to_canonical_obj()
    maximum = fixture.admission.policy.ceiling.artifact_policy_maximum
    increased["artifacts"]["max_each_bytes"] = maximum.max_each_bytes
    increased["artifacts"]["max_total_bytes"] = maximum.max_total_bytes
    assert increased["artifacts"] != base_receipt.effective_capabilities.artifacts.to_canonical_obj()
    overlay_ref, _ = _admit_overlay_layer(
        fixture,
        fixture.runtime,
        parent_receipt=fixture.base_receipt_ref,
        before=base,
        after=after,
        source_kind="operator",
        source_digest="sha256:" + "e" * 64,
        capabilities=increased,
        operation=c.OverlayOperation(
            op="replace",
            path="/artifacts/selected",
            value="artifact-b",
        ),
    )
    fixture = _with_overlay_chain(
        fixture,
        candidate_overlays=(overlay_ref,),
        episode_overlays=(),
    )

    with pytest.raises(c.ConfigRuntimeDenial) as caught:
        fixture.runtime.resolve_episode(fixture.request)

    assert caught.value.stage is c.DenialStage.READMISSION
    assert caught.value.code is c.DenialCode.CAPABILITY_INCREASE
    assert caught.value.artifact_digest == overlay_ref.result_receipt_digest
    assert caught.value.selection_record_digest is None
    assert fixture.store.bindings == {}
    fixture.effects.assert_zero()


@pytest.mark.parametrize(
    ("pointer", "value"),
    [
        ("/artifacts/allowed_roles", ["unadmitted-role"]),
        ("/artifacts/max_each_bytes", 9_999_999),
        ("/artifacts/max_total_bytes", 9_999_999),
        ("/artifacts/authority", {"grant": "forged"}),
        ("/artifacts/policy_digest", "sha256:" + "f" * 64),
        ("/artifacts/security", {"isolation": "none"}),
    ],
)
def test_artifact_authority_policy_and_security_pointers_remain_protected(
    pointer: str,
    value: Any,
) -> None:
    fixture, compiler = _artifact_selection_fixture()
    base = copy.deepcopy(compiler.base_semantics)
    after = copy.deepcopy(base)
    after["artifacts"][pointer.rsplit("/", 1)[1]] = copy.deepcopy(value)
    overlay_ref, _ = _admit_overlay_layer(
        fixture,
        fixture.runtime,
        parent_receipt=fixture.base_receipt_ref,
        before=base,
        after=after,
        source_kind="operator",
        source_digest="sha256:" + "c" * 64,
        operation=c.OverlayOperation(op="add", path=pointer, value=value),
    )
    fixture = _with_overlay_chain(
        fixture,
        candidate_overlays=(overlay_ref,),
        episode_overlays=(),
    )

    with pytest.raises(c.ConfigRuntimeDenial) as caught:
        fixture.runtime.resolve_episode(fixture.request)

    assert caught.value.stage is c.DenialStage.OVERLAY_VALIDATION
    assert caught.value.code is c.DenialCode.PROTECTED_POINTER
    assert caught.value.pointer == pointer
    assert caught.value.operation_index == 0
    assert caught.value.selection_record_digest is None
    assert fixture.store.bindings == {}
    fixture.effects.assert_zero()


def test_ineligible_candidate_overlay_is_fully_validated_before_filtering() -> None:
    fixture = _resolution_fixture(algorithm="weighted-v1", candidate_count=3)
    runtime, _ = _overlay_runtime(fixture)
    base = copy.deepcopy(fixture.admission.compiler.effective_semantics)
    raw = copy.deepcopy(base)
    raw["sampling"]["temperature"] = "https://authority.invalid/ineligible"
    raw_ref, _ = _admit_overlay_layer(
        fixture,
        runtime,
        parent_receipt=fixture.base_receipt_ref,
        before=base,
        after=raw,
        source_kind="optimizer",
        source_digest="sha256:" + "8" * 64,
    )
    assert isinstance(fixture.selector, c.ConfigSetManifest)
    selector_payload = fixture.selector.model_dump(mode="json")
    admitted_payload = fixture.admitted_set.model_dump(mode="json")
    admitted_payload["receipt_digests"] = sorted(
        {*fixture.admitted_set.receipt_digests, raw_ref.result_receipt_digest}
    )
    admitted_set = c.AdmittedSetManifest.model_validate(admitted_payload)
    admitted_ref = fixture.store.publish(
        kind=c.ArtifactKind.ADMITTED_SET,
        canonical_bytes=admitted_set.canonical_bytes(),
    )
    selector_payload["admitted_set_root"] = admitted_ref.sha256
    candidate = selector_payload["candidates"][1]["candidate"]
    candidate["predicates"] = [
        c.TaskLabelEq(key="tier", value="silver").model_dump(mode="json")
    ]
    candidate["overlays"] = [raw_ref.to_canonical_obj()]
    selector = c.ConfigSetManifest.model_validate(selector_payload)
    selector_artifact = fixture.store.publish(
        kind=c.ArtifactKind.CONFIG_SET,
        canonical_bytes=selector.canonical_bytes(),
    )
    request_payload = fixture.request.model_dump(mode="json")
    request_payload["selector"] = c.WeightedSelectorRef(
        digest=selector_artifact.sha256,
        ref=selector_artifact,
    ).to_canonical_obj()
    request = c.ResolveEpisodeRequest.model_validate(request_payload)

    with pytest.raises(c.ConfigRuntimeDenial) as caught:
        runtime.resolve_episode(request)

    assert caught.value.stage is c.DenialStage.OVERLAY_VALIDATION
    assert caught.value.code is c.DenialCode.OVERLAY_VALUE_FORBIDDEN
    assert caught.value.candidate_id == "b"
    assert caught.value.pointer == "/sampling/temperature"
    assert caught.value.operation_index == 0
    assert caught.value.selection_record_digest is None
    assert fixture.store.bindings == {}
    assert not any(
        kind is c.ArtifactKind.EFFECTIVE_EXECUTION_PLAN
        for kind in fixture.store.kinds.values()
    )
    fixture.effects.assert_zero()


@pytest.mark.parametrize(
    ("failure", "expected_stage", "expected_code"),
    [
        ("malformed", c.DenialStage.SELECTOR_VALIDATION, c.DenialCode.INVALID_OVERLAY_REF),
        ("stale", c.DenialStage.SELECTOR_VALIDATION, c.DenialCode.STALE_CANDIDATE_RECEIPT),
        ("protected", c.DenialStage.OVERLAY_VALIDATION, c.DenialCode.PROTECTED_POINTER),
        ("cross_pair", c.DenialStage.OVERLAY_VALIDATION, c.DenialCode.OVERLAY_RECEIPT_MISMATCH),
    ],
)
def test_all_false_candidates_still_prevalidate_every_overlay_before_no_eligible(
    failure: str,
    expected_stage: c.DenialStage,
    expected_code: c.DenialCode,
) -> None:
    fixture = _resolution_fixture(algorithm="weighted-v1", candidate_count=3)
    runtime, _ = _overlay_runtime(fixture)
    base = copy.deepcopy(fixture.admission.compiler.effective_semantics)
    after = copy.deepcopy(base)
    after["sampling"]["temperature"] = 0.5
    receipt_a, _ = _admit_overlay_layer(
        fixture,
        runtime,
        parent_receipt=fixture.base_receipt_ref,
        before=base,
        after=after,
        source_kind="optimizer",
        source_digest="sha256:" + "6" * 64,
    )
    overlay_ref = receipt_a
    if failure == "protected":
        protected_after = copy.deepcopy(base)
        protected_after["artifacts"] = {"security": {"isolation": "none"}}
        overlay_ref, _ = _admit_overlay_layer(
            fixture,
            runtime,
            parent_receipt=fixture.base_receipt_ref,
            before=base,
            after=protected_after,
            source_kind="operator",
            source_digest="sha256:" + "7" * 64,
            operation=c.OverlayOperation(
                op="add",
                path="/artifacts/security",
                value={"isolation": "none"},
            ),
        )
    elif failure == "cross_pair":
        receipt_b, _ = _admit_overlay_layer(
            fixture,
            runtime,
            parent_receipt=fixture.base_receipt_ref,
            before=base,
            after=after,
            source_kind="experiment",
            source_digest="sha256:" + "7" * 64,
        )
        overlay_ref = c.AdmittedOverlayRef(
            overlay_digest=receipt_b.overlay_digest,
            result_receipt_digest=receipt_a.result_receipt_digest,
        )

    assert isinstance(fixture.selector, c.ConfigSetManifest)
    selector_payload = fixture.selector.model_dump(mode="json")
    for weighted, false_value in zip(
        selector_payload["candidates"],
        ("silver", "bronze", "platinum"),
        strict=True,
    ):
        weighted["candidate"]["predicates"] = [
            c.TaskLabelEq(key="tier", value=false_value).model_dump(mode="json")
        ]
        weighted["candidate"]["overlays"] = []
    selector_payload["candidates"][0]["candidate"]["overlays"] = [
        overlay_ref.to_canonical_obj()
    ]
    admitted_payload = fixture.admitted_set.model_dump(mode="json")
    if failure != "stale":
        admitted_payload["receipt_digests"] = sorted(
            {
                *fixture.admitted_set.receipt_digests,
                overlay_ref.result_receipt_digest,
            }
        )
    admitted_set = c.AdmittedSetManifest.model_validate(admitted_payload)
    admitted_ref = fixture.store.publish(
        kind=c.ArtifactKind.ADMITTED_SET,
        canonical_bytes=admitted_set.canonical_bytes(),
    )
    selector_payload["admitted_set_root"] = admitted_ref.sha256
    selector = c.ConfigSetManifest.model_validate(selector_payload)
    selector_artifact = fixture.store.publish(
        kind=c.ArtifactKind.CONFIG_SET,
        canonical_bytes=selector.canonical_bytes(),
    )
    selector_ref = c.WeightedSelectorRef(
        digest=selector_artifact.sha256,
        ref=selector_artifact,
    )
    request_payload = fixture.request.model_dump(mode="json")
    request_payload["selector"] = selector_ref.to_canonical_obj()
    request = c.ResolveEpisodeRequest.model_validate(request_payload)
    if failure == "malformed":
        fixture.store.records[overlay_ref.overlay_digest] = b"{}"

    with pytest.raises(c.ConfigRuntimeDenial) as caught:
        runtime.resolve_episode(request)

    assert caught.value.stage is expected_stage
    assert caught.value.code is expected_code
    assert caught.value.code is not c.DenialCode.NO_ELIGIBLE_CANDIDATE
    assert caught.value.candidate_id == "a"
    assert caught.value.selection_record_digest is None
    assert fixture.store.bindings == {}
    assert not any(
        kind is c.ArtifactKind.EFFECTIVE_EXECUTION_PLAN
        for kind in fixture.store.kinds.values()
    )
    fixture.effects.assert_zero()


def test_reduced_overlay_capabilities_pass_and_later_increase_is_rejected() -> None:
    fixture = _resolution_fixture(algorithm="direct-v1", candidate_count=1)
    runtime, compiler = _overlay_runtime(fixture)
    base = copy.deepcopy(fixture.admission.compiler.effective_semantics)
    candidate = copy.deepcopy(base)
    candidate["sampling"]["temperature"] = 0.5
    base_receipt = c.AdmissionReceipt.model_validate_json(
        fixture.store.records[fixture.base_receipt_ref.digest]
    )
    original_capabilities = base_receipt.effective_capabilities.to_canonical_obj()
    reduced_capabilities = copy.deepcopy(original_capabilities)
    original_turns = reduced_capabilities["limits"]["max_turns"]
    assert original_turns > 1
    reduced_capabilities["limits"]["max_turns"] = original_turns - 1
    candidate_ref, _ = _admit_overlay_layer(
        fixture,
        runtime,
        parent_receipt=fixture.base_receipt_ref,
        before=base,
        after=candidate,
        source_kind="optimizer",
        source_digest="sha256:" + "6" * 64,
        capabilities=reduced_capabilities,
    )
    reduced_only = _with_overlay_chain(
        fixture,
        candidate_overlays=(candidate_ref,),
        episode_overlays=(),
    )

    resolved = runtime.resolve_episode(reduced_only.request)
    assert resolved.effective_plan.effective_capabilities.limits.max_turns == original_turns - 1
    assert resolved.effective_plan.effective_capability_digest == _independent_digest(
        reduced_capabilities
    )

    candidate_receipt = c.AdmissionReceiptRef(
        digest=candidate_ref.result_receipt_digest,
        ref=c.ArtifactRef(
            artifact_id=candidate_ref.result_receipt_digest,
            sha256=candidate_ref.result_receipt_digest,
            size_bytes=len(fixture.store.records[candidate_ref.result_receipt_digest]),
            media_type="application/vnd.breadboard.admission-receipt+json;version=1",
        ),
    )
    episode = copy.deepcopy(candidate)
    episode["sampling"]["temperature"] = 0.25
    increase_ref, _ = _admit_overlay_layer(
        fixture,
        runtime,
        parent_receipt=candidate_receipt,
        before=candidate,
        after=episode,
        source_kind="experiment",
        source_digest="sha256:" + "7" * 64,
        capabilities=original_capabilities,
    )
    conflict_fixture = _with_overlay_chain(
        fixture,
        candidate_overlays=(candidate_ref,),
        episode_overlays=(increase_ref,),
    )
    conflict_request_payload = conflict_fixture.request.model_dump(mode="json")
    conflict_request_payload["episode_id"] = "episode-capability-increase"

    with pytest.raises(c.ConfigRuntimeDenial) as caught:
        runtime.resolve_episode(c.ResolveEpisodeRequest.model_validate(conflict_request_payload))

    assert caught.value.stage is c.DenialStage.READMISSION
    assert caught.value.code is c.DenialCode.CAPABILITY_INCREASE
    assert not any(
        c.EffectiveExecutionPlan.model_validate_json(payload).final_receipt_digest
        == increase_ref.result_receipt_digest
        for digest, payload in fixture.store.records.items()
        if fixture.store.kinds[digest] is c.ArtifactKind.EFFECTIVE_EXECUTION_PLAN
    )
    fixture.effects.assert_zero()


def test_overlay_fixture_file_and_closed_taxonomy_are_frozen() -> None:
    corpus = _load_vectors()
    assert hashlib.sha256(OVERLAY_VECTORS.read_bytes()).hexdigest() == OVERLAY_VECTORS_FILE_SHA256
    assert corpus["schema_version"] == "bb.rl.overlay-vectors.v1"
    assert corpus["canonicalizer_id"] == "rfc8785-jcs-v1"
    assert corpus["semantic_digest_equation"] == {
        "schema": "bb.compiled-config-semantic.v1",
        "projection": "effective-semantic-config-root",
    }
    assert corpus["overlay_chain_digest_equation"] == {
        "schema_version": "bb.rl.overlay-chain.v1",
        "fields": ["overlay_digest", "parent_chain_digest", "schema_version"],
        "first_parent_chain_digest": None,
    }
    assert len(corpus["denials"]) == 65
    case_ids = [case["case_id"] for case in corpus["denials"]]
    assert len(case_ids) == len(set(case_ids))
    required = {
        "malformed_escape_trailing",
        "malformed_escape_digit",
        "root_pointer",
        "empty_token",
        "non_nfc_token",
        "sibling_pointer",
        "prefix_pointer",
        "ancestor_pointer",
        "descendant_pointer",
        "leading_zero_array_index",
        "array_dash_append",
        "add_existing_member",
        "add_missing_parent",
        "replace_missing_member",
        "remove_missing_member",
        "remove_without_grant",
        "remove_activates_default",
        "duplicate_path",
        "ancestor_overlap",
        "descendant_overlap",
        "wrong_operation",
        "type_bool_for_number",
        "type_string_for_number",
        "temperature_below_bound",
        "turns_above_parent",
        "post_schema_unknown_field",
        "post_invariant_invalid",
        "transition_before_mismatch",
        "transition_after_mismatch",
        "raw_url_value",
        "raw_header_value",
        "raw_secret_value",
        "environment_value",
        "shell_value",
        "import_path_value",
        "raw_host_path_value",
        "nan_raw_json",
        "infinity_raw_json",
        "negative_zero_raw_json",
        "noncanonical_exponent_raw_json",
        "compiler_operator_rule_mismatch",
        "overlay_base_mismatch",
        "overlay_parent_receipt_mismatch",
        "derived_receipt_mismatch",
        "provenance_digest_mismatch",
        "operation_reorder",
        "layer_skip",
    } | {f"protected_{index}" for index in range(11)} | {
        "protected_artifact_allowed_roles",
        "protected_artifact_max_each_bytes",
        "protected_artifact_max_total_bytes",
        "protected_artifact_authority",
        "protected_artifact_policy",
        "protected_artifact_policy_digest",
        "protected_artifact_security",
    }
    assert set(case_ids) == required
    assert {
        stage: sum(case["expected"]["stage"] == stage for case in corpus["denials"])
        for stage in {
            "overlay_validation",
            "overlay_application",
            "capability_intersection",
            "readmission",
        }
    } == {
        "overlay_validation": 51,
        "overlay_application": 12,
        "capability_intersection": 1,
        "readmission": 1,
    }
    nfc_case = next(case for case in corpus["denials"] if case["case_id"] == "non_nfc_token")
    assert nfc_case["mutation"]["path"] == "/cafe\u0301"
    assert unicodedata.normalize("NFC", nfc_case["mutation"]["path"]) == "/caf" + chr(0xE9)


def test_base_semantics_and_compiler_operator_rules_are_exact() -> None:
    base = _load_vectors()["base"]
    assert bytes.fromhex(base["semantic_canonical_hex"]) == _independent_jcs_bytes(base["semantics"])
    assert _semantic_digest(base["semantics"]) == base["semantic_digest"]
    assert base["compiler_mutable_rules"] == base["operator_mutable_rules"]
    assert [rule["pointer"] for rule in base["compiler_mutable_rules"]] == sorted(
        rule["pointer"] for rule in base["compiler_mutable_rules"]
    )
    assert {"/a~1b/value", "/a~0b/value", "/array/1", "/artifacts/selected"} <= {
        rule["pointer"] for rule in base["compiler_mutable_rules"]
    }


def test_mutable_rules_are_exact_and_never_overlap_protected_authority() -> None:
    corpus = _load_vectors()
    rules = {
        rule["pointer"]: rule for rule in corpus["base"]["compiler_mutable_rules"]
    }
    protected_cases = [
        case for case in corpus["denials"] if case["case_id"].startswith("protected_")
    ]
    assert {case["expected"]["pointer"] for case in protected_cases} == {
        "/runtime/abi",
        "/policy/route_id",
        "/secrets/handle_id",
        "/sandbox/network_mode",
        "/runner/implementation_digest",
        "/verifier/implementation_digest",
        "/evidence/policy_id",
        "/retention/policy_id",
        "/task/task_binding_digest",
        "/repository/snapshot_digest",
        "/image/digest",
        "/artifacts/allowed_roles",
        "/artifacts/max_each_bytes",
        "/artifacts/max_total_bytes",
        "/artifacts/authority",
        "/artifacts/policy",
        "/artifacts/policy_digest",
        "/artifacts/security",
    }
    assert not ({case["expected"]["pointer"] for case in protected_cases} & set(rules))
    for layer in corpus["positive_chain"]["layers"]:
        for operation in layer["manifest"]["operations"]:
            rule = rules[operation["path"]]
            assert operation["op"] in rule["allowed_operations"]
            if operation["op"] == "remove":
                assert rule["removable"] is True
                assert rule["authority_effect"] == "reduce_only"
    default_case = next(
        case for case in corpus["denials"] if case["case_id"] == "remove_activates_default"
    )
    assert default_case == {
        "case_id": "remove_activates_default",
        "expected": {
            "code": "implicit_default_forbidden",
            "operation_index": 0,
            "pointer": "/mode",
            "stage": "overlay_application",
        },
        "mutation": {"malicious_rule": True, "op": "remove", "path": "/mode"},
    }


def test_artifact_selection_vector_is_exact_rooted_and_independently_hashed() -> None:
    corpus = _load_vectors()
    vector = corpus["artifact_selection"]
    manifest = vector["manifest"]
    assert manifest["operations"] == [
        {"op": "replace", "path": "/artifacts/selected", "value": "artifact-b"}
    ]
    assert bytes.fromhex(vector["canonical_hex"]) == _independent_jcs_bytes(manifest)
    assert vector["overlay_digest"] == _independent_digest(manifest)
    assert vector["overlay_chain_digest"] == _overlay_chain_digest(
        None,
        vector["overlay_digest"],
    )
    after = _apply_reference(corpus["base"]["semantics"], manifest["operations"][0])
    assert after == vector["result_semantics"]
    assert vector["result_semantic_digest"] == _semantic_digest(after)
    assert vector["derived_receipt_projection"] == {
        "schema_version": "bb.rl.overlay-derived-receipt-identity.v1",
        "base_compiled_manifest_digest": corpus["base"]["compiled_manifest_digest"],
        "parent_receipt_digest": corpus["base"]["receipt_digest"],
        "overlay_chain_digest": vector["overlay_chain_digest"],
        "derived_semantic_digest": vector["result_semantic_digest"],
    }
    assert vector["result_receipt_digest"] == _independent_digest(
        vector["derived_receipt_projection"]
    )
    assert set(vector["admitted_set_receipt_digests"]) == {
        corpus["base"]["receipt_digest"],
        vector["result_receipt_digest"],
    }
    validated = c.MutationOverlayManifest.model_validate(manifest)
    assert validated.canonical_digest() == vector["overlay_digest"]
    rules = {
        rule["pointer"]: rule for rule in corpus["base"]["compiler_mutable_rules"]
    }
    assert rules["/artifacts/selected"] == {
        "allowed_operations": ["replace"],
        "authority_effect": "none",
        "pointer": "/artifacts/selected",
        "removable": False,
        "value_schema_digest": "sha256:" + "7" * 64,
    }


def test_ordered_layers_freeze_every_transition_receipt_and_provenance_digest() -> None:
    corpus = _load_vectors()
    chain = corpus["positive_chain"]
    assert chain["layer_order"] == ["candidate-layer", "episode-layer"]
    current = corpus["base"]["semantics"]
    parent_receipt = corpus["base"]["receipt_digest"]
    parent_chain_digest: str | None = None
    seen_operations: list[str] = []

    for expected_id, layer in zip(chain["layer_order"], chain["layers"], strict=True):
        assert layer["case_id"] == expected_id
        manifest = layer["manifest"]
        assert manifest["parent_receipt_digest"] == parent_receipt
        assert manifest["expected_before_semantic_digest"] == _semantic_digest(current)
        assert bytes.fromhex(layer["canonical_hex"]) == _independent_jcs_bytes(manifest)
        assert layer["overlay_digest"] == _independent_digest(manifest)
        validated_manifest = c.MutationOverlayManifest.model_validate(manifest)
        assert validated_manifest.canonical_bytes() == bytes.fromhex(layer["canonical_hex"])
        assert validated_manifest.canonical_digest() == layer["overlay_digest"]
        assert len(manifest["operations"]) == len(manifest["expected_transitions"])

        for index, (operation, transition) in enumerate(
            zip(manifest["operations"], manifest["expected_transitions"], strict=True)
        ):
            assert transition["operation_index"] == index
            assert transition["before_semantic_digest"] == _semantic_digest(current)
            current = _apply_reference(current, operation)
            assert transition["after_semantic_digest"] == _semantic_digest(current)
            seen_operations.append(operation["path"])

        assert manifest["expected_after_semantic_digest"] == _semantic_digest(current)
        assert layer["result_semantics"] == current
        assert layer["result_semantic_digest"] == _semantic_digest(current)
        expected_chain_digest = _overlay_chain_digest(
            parent_chain_digest,
            layer["overlay_digest"],
        )
        receipt = layer["derived_receipt_projection"]
        assert receipt == {
            "schema_version": "bb.rl.overlay-derived-receipt-identity.v1",
            "base_compiled_manifest_digest": corpus["base"]["compiled_manifest_digest"],
            "parent_receipt_digest": parent_receipt,
            "overlay_chain_digest": expected_chain_digest,
            "derived_semantic_digest": _semantic_digest(current),
        }
        assert layer["result_receipt_digest"] == _independent_digest(receipt)
        assert _independent_digest(manifest["provenance"]) == chain["final_plan"]["payload"][
            "overlay_applications"
        ][chain["layers"].index(layer)]["provenance_digest"]
        application = c.OverlayApplicationRecord.model_validate(
            chain["final_plan"]["payload"]["overlay_applications"][
                chain["layers"].index(layer)
            ]
        )
        assert application.parent_receipt_digest == parent_receipt
        assert application.result_receipt_digest == layer["result_receipt_digest"]
        parent_receipt = layer["result_receipt_digest"]
        parent_chain_digest = expected_chain_digest
    assert set(chain["admitted_set_receipt_digests"]) == {
        corpus["base"]["receipt_digest"],
        *(layer["result_receipt_digest"] for layer in chain["layers"]),
    }

    assert seen_operations == [
        "/sampling/temperature",
        "/tools/enabled/1",
        "/a~1b/value",
        "/limits/max_turns",
        "/optional/note",
        "/array/1",
        "/a~0b/value",
    ]
    assert current == chain["final_semantics"]
    assert _semantic_digest(current) == chain["final_semantic_digest"]
    assert current["tools"]["enabled"] == ["read", "search"]
    assert current["array"] == ["zero", "two"]
    assert current["a/b"]["value"] == 3
    assert current["a~b"]["value"] == 4


def test_operation_reordering_breaks_the_first_frozen_transition() -> None:
    corpus = _load_vectors()
    layer = corpus["positive_chain"]["layers"][0]["manifest"]
    reordered = list(reversed(layer["operations"]))
    assert reordered != layer["operations"]
    current = corpus["base"]["semantics"]
    first_result = _apply_reference(current, reordered[0])
    assert layer["expected_transitions"][0]["before_semantic_digest"] == _semantic_digest(
        current
    )
    assert layer["expected_transitions"][0]["after_semantic_digest"] != _semantic_digest(
        first_result
    )
    reorder_case = next(
        case for case in corpus["denials"] if case["case_id"] == "operation_reorder"
    )
    assert reorder_case["expected"] == {
        "code": "overlay_transition_mismatch",
        "operation_index": 0,
        "pointer": "/operations/0",
        "stage": "overlay_validation",
    }


def test_final_plan_bytes_and_digest_are_content_addressed() -> None:
    final = _load_vectors()["positive_chain"]["final_plan"]
    canonical = _independent_jcs_bytes(final["payload"])
    assert bytes.fromhex(final["canonical_hex"]) == canonical
    assert final["digest"] == "sha256:" + hashlib.sha256(canonical).hexdigest()
    assert "digest" not in final["payload"]
    assert final["payload"]["overlay_applications"][0]["after_semantic_digest"] == final[
        "payload"
    ]["overlay_applications"][1]["before_semantic_digest"]
    assert final["payload"]["final_semantic_digest"] == _semantic_digest(
        final["payload"]["effective_semantics"]
    )
    assert final["payload"]["effective_capability_digest"] == _independent_digest(
        final["payload"]["effective_capabilities"]
    )
    plan = c.EffectiveExecutionPlan.model_validate(final["payload"])
    assert plan.canonical_bytes() == canonical
    assert plan.canonical_digest() == final["digest"]


@pytest.mark.parametrize("path", ["/a~1b/value", "/a~0b/value", "/array/0"])
def test_overlay_contract_accepts_canonical_rfc6901_paths(path: str) -> None:
    operation = c.OverlayOperation(op="replace", path=path, value=1)
    assert operation.path == path


@pytest.mark.parametrize(
    "path",
    ["", "/", "/a~", "/a~2b", "/a//b", "/cafe\u0301"],
)
def test_overlay_contract_rejects_root_empty_malformed_and_non_nfc_paths(path: str) -> None:
    with pytest.raises(ValidationError):
        c.OverlayOperation(op="replace", path=path, value=1)


def test_overlay_operation_value_presence_is_closed() -> None:
    with pytest.raises(ValidationError):
        c.OverlayOperation.model_validate({"op": "add", "path": "/optional/note"})
    with pytest.raises(ValidationError):
        c.OverlayOperation.model_validate(
            {"op": "remove", "path": "/array/1", "value": "forbidden"}
        )


@pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf")])
def test_overlay_contract_rejects_nonfinite_values(value: float) -> None:
    with pytest.raises(ValidationError):
        c.OverlayOperation(op="replace", path="/sampling/temperature", value=value)


@pytest.mark.parametrize(
    "paths",
    [
        ("/sampling/temperature", "/sampling/temperature"),
        ("/sampling", "/sampling/temperature"),
        ("/sampling/temperature", "/sampling"),
    ],
)
def test_overlay_manifest_rejects_duplicate_ancestor_and_descendant_operations(
    paths: tuple[str, str],
) -> None:
    digest = "sha256:" + "1" * 64
    payload = {
        "base_compiled_manifest_digest": "sha256:" + "2" * 64,
        "parent_receipt_digest": "sha256:" + "3" * 64,
        "expected_before_semantic_digest": digest,
        "operations": [
            {"op": "replace", "path": paths[0], "value": 1},
            {"op": "replace", "path": paths[1], "value": 2},
        ],
        "expected_transitions": [
            {
                "operation_index": 0,
                "before_semantic_digest": digest,
                "after_semantic_digest": "sha256:" + "4" * 64,
            },
            {
                "operation_index": 1,
                "before_semantic_digest": "sha256:" + "4" * 64,
                "after_semantic_digest": "sha256:" + "5" * 64,
            },
        ],
        "expected_after_semantic_digest": "sha256:" + "5" * 64,
        "provenance": {
            "author_subject_digest": "sha256:" + "6" * 64,
            "source_kind": "operator",
            "source_artifact_digest": "sha256:" + "7" * 64,
            "rationale_code": "test",
        },
    }
    with pytest.raises(ValidationError):
        c.MutationOverlayManifest.model_validate(payload)


def test_overlay_and_effective_plan_json_are_recursively_immutable() -> None:
    operation = c.OverlayOperation(
        op="replace", path="/optional/note", value={"nested": ["fixed"]}
    )
    with pytest.raises(TypeError):
        operation.value["nested"] = ("changed",)

    plan = c.EffectiveExecutionPlan.model_validate(
        _load_vectors()["positive_chain"]["final_plan"]["payload"]
    )
    with pytest.raises(TypeError):
        plan.effective_semantics["mode"] = "changed"
def test_canonical_json_wrappers_resist_builtin_bypasses_aliases_and_roundtrip() -> None:
    source = {"nested": [{"fixed": True}], "empty": []}
    operation = c.OverlayOperation(op="add", path="/value", value=source)
    frozen = operation.value
    nested_array = frozen["nested"]
    nested_object = nested_array[0]
    before_bytes = operation.canonical_bytes()

    assert not isinstance(frozen, dict)
    assert isinstance(frozen, Mapping)
    assert isinstance(nested_array, Sequence)
    assert frozen == source
    assert nested_array == [{"fixed": True}]
    assert not hasattr(frozen, "__dict__")
    assert not hasattr(frozen, "_FrozenDict__pairs")
    assert all(
        type(pair) is tuple and not isinstance(value, (dict, list))
        for pair in tuple.__iter__(frozen)
        for _key, value in (pair,)
    )
    assert all(type(pair) is tuple for pair in tuple.__iter__(nested_object))

    source["nested"][0]["fixed"] = False
    source["nested"].append({"alias": "mutated"})
    source["empty"].append("mutated")
    assert operation.canonical_bytes() == before_bytes
    assert frozen["nested"] == [{"fixed": True}]
    assert frozen["empty"] == []

    for bypass in (
        lambda: dict.__setitem__(frozen, "nested", None),
        lambda: dict.update(frozen, {"nested": None}),
        lambda: list.append(nested_array, None),
        lambda: list.extend(nested_array, [None]),
        lambda: object.__setattr__(frozen, "_FrozenDict__pairs", (("injected", 1),)),
    ):
        with pytest.raises((TypeError, AttributeError)):
            bypass()
        assert operation.canonical_bytes() == before_bytes

    assert copy.copy(frozen) is frozen
    assert copy.deepcopy(frozen) is frozen
    assert copy.copy(nested_array) is nested_array
    assert copy.deepcopy(nested_array) is nested_array
    assert pickle.loads(pickle.dumps(frozen)) == frozen
    assert pickle.loads(pickle.dumps(nested_array)) == nested_array
    assert operation.model_dump(mode="json") == {
        "op": "add",
        "path": "/value",
        "value": {"nested": [{"fixed": True}], "empty": []},
    }
    assert c.OverlayOperation.model_validate_json(
        operation.model_dump_json()
    ).canonical_bytes() == before_bytes


def test_effective_plan_semantic_digest_survives_every_mutation_attempt() -> None:
    payload = _load_vectors()["positive_chain"]["final_plan"]["payload"]
    constructor_input = copy.deepcopy(payload)
    plan = c.EffectiveExecutionPlan.model_validate(constructor_input)
    semantics = plan.effective_semantics
    nested = semantics["a/b"]
    digest = plan.final_semantic_digest
    canonical = plan.canonical_bytes()

    constructor_input["effective_semantics"]["a/b"]["value"] = 999
    attempts = (
        lambda: dict.__setitem__(semantics, "mode", "changed"),
        lambda: dict.update(semantics, {"mode": "changed"}),
        lambda: dict.__setitem__(nested, "value", 999),
    )
    for attempt in attempts:
        with pytest.raises(TypeError):
            attempt()
        assert plan.final_semantic_digest == digest
        assert plan.canonical_bytes() == canonical

    dumped = plan.model_dump(mode="json")
    assert dumped == payload
    restored = c.EffectiveExecutionPlan.model_validate_json(plan.model_dump_json())
    assert restored == plan
    assert restored.final_semantic_digest == digest
    assert restored.canonical_bytes() == canonical



def test_negative_vectors_pin_exact_stage_code_pointer_and_operation_index() -> None:
    cases = _load_vectors()["denials"]
    closed_stages = {
        "overlay_validation",
        "overlay_application",
        "capability_intersection",
        "readmission",
    }
    closed_codes = {code.value for code in c.DenialCode}
    for case in cases:
        expected = case["expected"]
        assert expected["stage"] in closed_stages
        assert expected["code"] in closed_codes
        assert type(expected["operation_index"]) is int and expected["operation_index"] >= 0
        pointer = expected["pointer"]
        assert type(pointer) is str
        if case["case_id"] not in {"root_pointer"}:
            assert pointer.startswith("/")
