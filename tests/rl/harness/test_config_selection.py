from __future__ import annotations

import base64
import copy
import hashlib
import json
import math
import multiprocessing
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from breadboard.rl.harness import contracts as c
from breadboard.rl.harness.config_runtime import CompilerSemanticView, ConfigRuntime
from tests.rl.harness.test_config_admission import (
    AdmissionFixture,
    PrivilegedEffectProbe,
    _admission_fixture,
    _base_capability_payload,
    _setup_authority_projection,
)
from tests.rl.harness.test_policy_capability_registry import (
    RecordingPolicyCapabilityRegistry,
    _capability_projection,
    _policy_capabilities,
)


FIXTURE_ROOT = Path(__file__).resolve().parents[2] / "fixtures" / "rl" / "config_runtime"
WEIGHTED_VECTORS = FIXTURE_ROOT / "weighted_v1_vectors.json"
PREDICATE_VECTORS = FIXTURE_ROOT / "predicate_vectors_v1.json"
WEIGHTED_VECTORS_FILE_SHA256 = "195f98ff649335da2f9f54434aa4214076e1d8763f12b3b9b8a53b35cca306a6"
PREDICATE_VECTORS_FILE_SHA256 = "96f3290c623a14bdf9e9a014ddef34b07d52d8336eab9affd6aee1670407240d"
MAX_SAFE_INTEGER = 2**53 - 1
_WEIGHTED_PREFIX = b"bb-weighted-v1\x00"
_CANDIDATE_ID = re.compile(r"[a-z0-9](?:[a-z0-9._-]{0,62}[a-z0-9])?\Z", re.ASCII)
_DIGEST = re.compile(r"sha256:[0-9a-f]{64}\Z", re.ASCII)
_BOOL_FIELDS = frozenset(
    {
        "tool_calling",
        "parallel_tool_calls",
        "token_ids",
        "token_logprobs",
        "routing_metadata",
        "cancellation",
    }
)
_INT_FIELDS = frozenset({"max_context_tokens", "max_output_tokens", "policy_slot_count"})
_SET_FIELDS = frozenset({"modalities", "request_features"})


_MEDIA_TYPES = {
    c.ArtifactKind.ADMISSION_RECEIPT: "application/vnd.breadboard.admission-receipt+json;version=1",
    c.ArtifactKind.POLICY_CAPABILITY_OBSERVATION: "application/vnd.breadboard.policy-capability-observation+json;version=1",
    c.ArtifactKind.COMPILED_MANIFEST: "application/vnd.breadboard.compiled-manifest+json;version=1",
    c.ArtifactKind.ADMITTED_SET: "application/vnd.breadboard.admitted-set+json;version=1",
    c.ArtifactKind.DIRECT_SELECTOR: "application/vnd.breadboard.direct-selector+json;version=1",
    c.ArtifactKind.CONFIG_SET: "application/vnd.breadboard.config-set+json;version=1",
    c.ArtifactKind.MUTATION_OVERLAY: "application/vnd.breadboard.mutation-overlay+json;version=1",
    c.ArtifactKind.SELECTION_RECORD: "application/vnd.breadboard.selection-record+json;version=1",
    c.ArtifactKind.SELECTION_BINDING: "application/vnd.breadboard.selection-binding+json;version=1",
    c.ArtifactKind.EFFECTIVE_EXECUTION_PLAN: "application/vnd.breadboard.effective-execution-plan+json;version=1",
}


class RecordingConfigRuntimeStore:
    def __init__(self) -> None:
        self.records: dict[str, bytes] = {}
        self.kinds: dict[str, c.ArtifactKind] = {}
        self.publish_calls: list[tuple[c.ArtifactKind, bytes]] = []
        self.load_calls: list[tuple[str, c.ArtifactKind, int]] = []
        self.binding_reads: list[str] = []
        self.bind_calls: list[tuple[str, str, str]] = []
        self.bindings: dict[str, c.SelectionBinding] = {}
        self.fail_publish = False
        self.fail_load = False
        self.fail_bind = False
        self.corrupt_readback = False

    def publish(self, *, kind: c.ArtifactKind, canonical_bytes: bytes) -> c.ArtifactRef:
        self.publish_calls.append((kind, canonical_bytes))
        if self.fail_publish:
            raise OSError("control-plane CAS unavailable")
        digest = "sha256:" + hashlib.sha256(canonical_bytes).hexdigest()
        existing = self.records.get(digest)
        if existing is not None and existing != canonical_bytes:
            raise RuntimeError("content-addressed publication conflict")
        self.records.setdefault(digest, canonical_bytes)
        self.kinds.setdefault(digest, kind)
        return c.ArtifactRef(
            artifact_id=digest,
            sha256=digest,
            size_bytes=len(canonical_bytes),
            media_type=_MEDIA_TYPES[kind],
        )

    def load(self, digest: str, *, kind: c.ArtifactKind, max_bytes: int) -> bytes:
        self.load_calls.append((digest, kind, max_bytes))
        if self.fail_load:
            raise OSError("control-plane CAS unavailable")
        if self.kinds[digest] is not kind:
            raise ValueError("artifact kind mismatch")
        payload = self.records[digest]
        if len(payload) > max_bytes:
            raise ValueError("artifact exceeds bounded load")
        if self.corrupt_readback:
            return payload[:-1] + bytes([payload[-1] ^ 1])
        return payload

    def get_selection_binding(self, owner_key: str) -> c.SelectionBinding | None:
        self.binding_reads.append(owner_key)
        binding = self.bindings.get(owner_key)
        if binding is None:
            return None
        return c.SelectionBinding.model_validate(binding.model_dump(mode="json"))

    def bind_selection_once(
        self,
        *,
        owner_key: str,
        request_digest: str,
        selection_record_digest: str,
    ) -> c.SelectionCommitToken:
        self.bind_calls.append((owner_key, request_digest, selection_record_digest))
        if self.fail_bind:
            raise OSError("selection alias unavailable")
        proposed = c.SelectionBinding(
            owner_key=owner_key,
            request_digest=request_digest,
            selection_record_digest=selection_record_digest,
        )
        existing = self.bindings.setdefault(owner_key, proposed)
        if existing != proposed:
            raise RuntimeError("selection idempotency conflict")
        binding_ref = self.publish(
            kind=c.ArtifactKind.SELECTION_BINDING,
            canonical_bytes=existing.canonical_bytes(),
        )
        return c.SelectionCommitToken(
            binding=existing,
            binding_ref=binding_ref,
            verified_at="2026-07-10T12:00:00Z",
        )


@dataclass(frozen=True)
class ResolutionFixture:
    runtime: ConfigRuntime
    request: c.ResolveEpisodeRequest
    admission: AdmissionFixture
    base_receipt_ref: c.AdmissionReceiptRef
    admitted_set: c.AdmittedSetManifest
    selector: c.DirectSelector | c.ConfigSetManifest
    selector_ref: c.DirectSelectorRef | c.WeightedSelectorRef
    policy_observation: c.PolicyCapabilityObservation
    policy_registry: RecordingPolicyCapabilityRegistry
    store: RecordingConfigRuntimeStore
    effects: PrivilegedEffectProbe


class ResolutionCompiler:
    def __init__(
        self,
        *,
        view: CompilerSemanticView,
        manifest_bytes: bytes,
        effective_semantics: Mapping[str, Any],
        semantic_digest: str,
    ) -> None:
        self.view = view
        self.manifest_bytes = manifest_bytes
        self.effective_semantics = copy.deepcopy(dict(effective_semantics))
        self.semantic_digest = semantic_digest
        self.calls: list[str] = []

    def verify_bundle(self, request: c.AdmissionRequest) -> None:
        self.calls.append("verify_bundle")

    def enforce_compile_budget(self, request: c.AdmissionRequest) -> None:
        self.calls.append("enforce_compile_budget")

    def compile(self, request: c.AdmissionRequest) -> CompilerSemanticView:
        self.calls.append("compile")
        return self.view

    def extract_effective_semantics(
        self,
        *,
        canonical_manifest_bytes: bytes,
    ) -> Mapping[str, Any]:
        self.calls.append("extract_effective_semantics")
        if canonical_manifest_bytes != self.manifest_bytes:
            raise ValueError("compiled manifest bytes changed")
        return copy.deepcopy(self.effective_semantics)

    def normalize_effective_semantics(
        self,
        *,
        canonical_manifest_bytes: bytes,
        effective_semantics: Mapping[str, Any],
    ) -> Mapping[str, Any]:
        self.calls.append("normalize_effective_semantics")
        if canonical_manifest_bytes != self.manifest_bytes:
            raise ValueError("compiled manifest bytes changed")
        return copy.deepcopy(dict(effective_semantics))


    def validate_effective_semantics(
        self,
        *,
        canonical_manifest_bytes: bytes,
        effective_semantics: Mapping[str, Any],
    ) -> str:
        self.calls.append("validate_effective_semantics")
        if canonical_manifest_bytes != self.manifest_bytes:
            raise ValueError("compiled manifest bytes changed")
        if dict(effective_semantics) != self.effective_semantics:
            raise ValueError("effective semantics changed without an admitted overlay")
        return self.semantic_digest


def _resolution_fixture(
    *,
    algorithm: str = "weighted-v1",
    candidate_count: int = 3,
    candidate_names: tuple[str, str, str] = ("a", "b", "c"),
    observation: c.PolicyCapabilityObservation | None = None,
) -> ResolutionFixture:
    if algorithm not in {"direct-v1", "weighted-v1"}:
        raise ValueError("test fixture supports only frozen selector algorithms")
    if not 1 <= candidate_count <= 3:
        raise ValueError("test fixture candidate_count must be in 1..3")
    if len(candidate_names) != 3:
        raise ValueError("test fixture requires three candidate names")
    if algorithm == "direct-v1" and candidate_count != 1:
        raise ValueError("direct-v1 has exactly one candidate")

    now = datetime(2026, 7, 10, 12, 0, tzinfo=UTC)
    task = c.TaskEligibilityInput(
        task_type="training",
        labels=(c.Label(key="region", value="us-east"), c.Label(key="tier", value="gold")),
        artifacts=(
            c.TaskArtifact(
                role="dataset",
                digest="sha256:" + "d" * 64,
                media_type="application/json",
                size_bytes=100,
            ),
            c.TaskArtifact(
                role="dataset",
                digest="sha256:" + "e" * 64,
                media_type="text/csv",
                size_bytes=200,
            ),
            c.TaskArtifact(
                role="prompt",
                digest="sha256:" + "f" * 64,
                media_type="text/plain",
                size_bytes=50,
            ),
        ),
        parameters_digest="sha256:" + "1" * 64,
    )
    capability_payload = _base_capability_payload()
    capability_payload["secret_handles"][0]["scope_digest"] = "sha256:" + "1" * 64
    slot = capability_payload["policy_slots"][0]
    selection_capabilities = _policy_capabilities(
        parallel_tool_calls=False,
        token_logprobs=False,
    )
    capability_digest = independent_digest(
        _capability_projection(
            protocol_abi=slot["protocol_abi"],
            model_digest=slot["model_digest"],
            tokenizer_digest=slot["tokenizer_digest"],
            checkpoint_digest=slot["checkpoint_digest"],
            capabilities=selection_capabilities,
        )
    )
    slot["required_policy_capabilities_digest"] = capability_digest
    capability_payload["task"]["task_contract_digest"] = task.canonical_digest()
    capability_payload["setup_plans"][0]["plan_digest"] = independent_digest(
        _setup_authority_projection(
            capability_payload["setup_plans"][0], capability_payload["task"]
        )
    )

    store = RecordingConfigRuntimeStore()
    seed = _admission_fixture(
        capability_payload=capability_payload,
        ceiling_capability_payload=copy.deepcopy(capability_payload),
        store=store,
        now=now,
    )
    effective_semantics = {
        "prompt": "selection fixture prompt",
        "sampling": {"temperature": 0},
    }
    manifest_payload = {
        "schema_version": "bb.compiled-config-manifest.test.v1",
        "semantic_config": effective_semantics,
    }
    manifest_bytes = independent_jcs_bytes(manifest_payload)
    manifest_digest = "sha256:" + hashlib.sha256(manifest_bytes).hexdigest()
    semantic_digest = independent_digest(
        {
            "schema": c.COMPILED_CONFIG_SEMANTIC_SCHEMA_ID,
            "config": effective_semantics,
        }
    )
    request_payload = seed.request.model_dump(mode="json")
    request_payload["compiled"]["manifest_digest"] = manifest_digest
    request_payload["compiled"]["semantic_digest"] = semantic_digest
    request_payload["behavior_source"]["manifest_digest"] = manifest_digest
    request_payload["behavior_source"]["semantic_digest"] = semantic_digest
    admission_request = c.AdmissionRequest.model_validate(request_payload)
    roles = copy.deepcopy(seed.compiler.view.roles)
    roles["semantic_identity"] = {
        "manifest_digest": manifest_digest,
        "semantic_digest": semantic_digest,
    }
    compiler = ResolutionCompiler(
        view=CompilerSemanticView(roles),
        manifest_bytes=manifest_bytes,
        effective_semantics=effective_semantics,
        semantic_digest=semantic_digest,
    )

    if observation is None:
        route = admission_request.requested_capabilities.routes[0]
        policy_slot = admission_request.requested_capabilities.policy_slots[0]
        secret = admission_request.requested_capabilities.secret_handles[0]
        observation = c.PolicyCapabilityObservation(
            registry_revision_digest=admission_request.policy_binding_ref.registry_revision_digest,
            route_id=route.route_id,
            route_revision_digest=route.route_revision_digest,
            provider_id="provider-a",
            protocol_abi=policy_slot.protocol_abi,
            bridge_instance_id="bridge-instance-a",
            bridge_build_digest="sha256:" + "a" * 64,
            model_id="model-a",
            model_digest=policy_slot.model_digest,
            tokenizer_digest=policy_slot.tokenizer_digest,
            checkpoint_digest=policy_slot.checkpoint_digest,
            credential_handle_id=secret.handle_id,
            credential_handle_version_digest=secret.handle_version_digest,
            subject_scope_digest=admission_request.subject.authority_scope_digest,
            capabilities=selection_capabilities,
            capability_digest=capability_digest,
            provenance=c.AttestationProvenance(
                kind=c.AttestationKind.STARTUP_PROBE,
                issuer_id="operator-control-plane",
                signer_key_id="startup-key",
                environment_digest="sha256:" + "b" * 64,
                evidence_digest="sha256:" + "c" * 64,
                validity=c.ValidityWindow(
                    issued_at="2026-07-10T11:00:00Z",
                    not_before="2026-07-10T11:00:00Z",
                    expires_at="2026-07-10T13:00:00Z",
                ),
            ),
            revocation=c.RevocationBinding(
                scope_digest=admission_request.subject.authority_scope_digest,
                epoch=seed.policy.revocation.epoch,
                state_digest=seed.policy.revocation.state_digest,
            ),
        )
    policy_registry = RecordingPolicyCapabilityRegistry(observation)
    runtime = ConfigRuntime(
        compiler=compiler,
        policy=seed.policy,
        registries=seed.registries,
        revocations=seed.revocations,
        store=store,
        clock=seed.clock,
        authenticator=seed.authenticator,
        policy_capabilities=policy_registry,
    )
    admission = AdmissionFixture(
        runtime=runtime,
        request=admission_request,
        policy=seed.policy,
        registries=seed.registries,
        compiler=compiler,
        revocations=seed.revocations,
        store=store,
        clock=seed.clock,
        authenticator=seed.authenticator,
    )
    base_receipt_ref = runtime.admit(admission_request)
    base_receipt = c.AdmissionReceipt.model_validate_json(store.records[base_receipt_ref.digest])
    compiled_ref = store.publish(
        kind=c.ArtifactKind.COMPILED_MANIFEST,
        canonical_bytes=manifest_bytes,
    )
    assert compiled_ref.sha256 == base_receipt.compiled.manifest_digest

    admitted_set = c.AdmittedSetManifest(
        compiler_abi=base_receipt.compiled.compiler.semantic_version,
        admission_policy_digest=base_receipt.admission_policy_digest,
        operator_ceiling_digest=base_receipt.operator_ceiling_digest,
        registry_snapshot_digest=base_receipt.registry_snapshot_digest,
        revocation=base_receipt.revocation,
        receipt_digests=(base_receipt_ref.digest,),
        validity=base_receipt.validity,
    )
    admitted_set_ref = store.publish(
        kind=c.ArtifactKind.ADMITTED_SET,
        canonical_bytes=admitted_set.canonical_bytes(),
    )
    candidates = (
        c.ConfigCandidate(
            candidate_id=candidate_names[0],
            receipt_digest=base_receipt_ref.digest,
            predicates=(),
            overlays=(),
        ),
        c.ConfigCandidate(
            candidate_id=candidate_names[1],
            receipt_digest=base_receipt_ref.digest,
            predicates=(c.TaskLabelEq(key="tier", value="gold"),),
            overlays=(),
        ),
        c.ConfigCandidate(
            candidate_id=candidate_names[2],
            receipt_digest=base_receipt_ref.digest,
            predicates=(
                c.PolicyBoolEq(field=c.PolicyBoolField.TOOL_CALLING, value=True),
            ),
            overlays=(),
        ),
    )[:candidate_count]
    if algorithm == "direct-v1":
        selector: c.DirectSelector | c.ConfigSetManifest = c.DirectSelector(
            admitted_set_root=admitted_set_ref.sha256,
            compiler_abi=admitted_set.compiler_abi,
            runtime_abi=base_receipt.compiled.compiler.runtime_abi,
            admission_policy_digest=base_receipt.admission_policy_digest,
            operator_ceiling_digest=base_receipt.operator_ceiling_digest,
            candidate=candidates[0],
            validity=base_receipt.validity,
        )
        published_selector = store.publish(
            kind=c.ArtifactKind.DIRECT_SELECTOR,
            canonical_bytes=selector.canonical_bytes(),
        )
        selector_ref: c.DirectSelectorRef | c.WeightedSelectorRef = c.DirectSelectorRef(
            digest=published_selector.sha256,
            ref=published_selector,
        )
        selection_nonce = None
    else:
        weights = (2, 1, 5)
        selector = c.ConfigSetManifest(
            admitted_set_root=admitted_set_ref.sha256,
            compiler_abi=admitted_set.compiler_abi,
            runtime_abi=base_receipt.compiled.compiler.runtime_abi,
            admission_policy_digest=base_receipt.admission_policy_digest,
            operator_ceiling_digest=base_receipt.operator_ceiling_digest,
            candidates=tuple(
                c.WeightedCandidate(candidate=candidate, weight=weights[index])
                for index, candidate in enumerate(candidates)
            ),
            validity=base_receipt.validity,
        )
        published_selector = store.publish(
            kind=c.ArtifactKind.CONFIG_SET,
            canonical_bytes=selector.canonical_bytes(),
        )
        selector_ref = c.WeightedSelectorRef(
            digest=published_selector.sha256,
            ref=published_selector,
        )
        selection_nonce = "sha256:" + "1" * 64
    resolution_request = c.ResolveEpisodeRequest(
        episode_id="episode-selection-a",
        subject=admission_request.subject,
        selector=selector_ref,
        selection_nonce=selection_nonce,
        task=task,
        policy_binding=admission_request.policy_binding_ref,
        episode_overlays=(),
    )
    return ResolutionFixture(
        runtime=runtime,
        request=resolution_request,
        admission=admission,
        base_receipt_ref=base_receipt_ref,
        admitted_set=admitted_set,
        selector=selector,
        selector_ref=selector_ref,
        policy_observation=observation,
        policy_registry=policy_registry,
        store=store,
        effects=PrivilegedEffectProbe(),
    )


@pytest.mark.parametrize(("algorithm", "candidate_count"), [("direct-v1", 1), ("weighted-v1", 3)])
def test_resolution_fixture_binds_admitted_artifacts_before_resolve(
    algorithm: str,
    candidate_count: int,
) -> None:
    fixture = _resolution_fixture(algorithm=algorithm, candidate_count=candidate_count)

    assert fixture.base_receipt_ref.digest in fixture.admitted_set.receipt_digests
    assert fixture.selector.admitted_set_root in fixture.store.records
    assert fixture.selector_ref.digest in fixture.store.records
    assert fixture.admission.request.requested_capabilities.task.task_contract_digest == (
        fixture.request.task.canonical_digest()
    )
    assert fixture.admission.request.requested_capabilities.policy_slots[0].required_policy_capabilities_digest == (
        fixture.policy_observation.capability_digest
    )
    assert fixture.effects.snapshot() == {
        name: 0
        for name in (
            "lease_open",
            "verifier_lease_open",
            "setup",
            "credential_issue",
            "secret_resolve",
            "dns",
            "network",
            "policy_observe_live",
            "policy_invoke",
            "provider_construct",
            "verifier_start",
        )
    }


def _with_selector(
    fixture: ResolutionFixture,
    selector: c.DirectSelector | c.ConfigSetManifest,
) -> ResolutionFixture:
    if isinstance(selector, c.DirectSelector):
        kind = c.ArtifactKind.DIRECT_SELECTOR
        ref_type = c.DirectSelectorRef
    else:
        kind = c.ArtifactKind.CONFIG_SET
        ref_type = c.WeightedSelectorRef
    artifact_ref = fixture.store.publish(kind=kind, canonical_bytes=selector.canonical_bytes())
    selector_ref = ref_type(digest=artifact_ref.sha256, ref=artifact_ref)
    request_payload = fixture.request.model_dump(mode="json")
    request_payload["selector"] = selector_ref.model_dump(mode="json")
    request = c.ResolveEpisodeRequest.model_validate(request_payload)
    return replace(fixture, selector=selector, selector_ref=selector_ref, request=request)


def _resign_receipt_payload(
    fixture: ResolutionFixture,
    payload: dict[str, Any],
) -> c.AdmissionReceiptRef:
    attestation = payload["issuance_attestation"]
    unsigned_payload = copy.deepcopy(payload)
    unsigned_payload["issuance_attestation"] = {
        "key_id": attestation["key_id"],
        "algorithm": attestation["algorithm"],
    }
    unsigned_bytes = independent_jcs_bytes(unsigned_payload)
    attestation["signed_payload_digest"] = (
        "sha256:" + hashlib.sha256(unsigned_bytes).hexdigest()
    )
    attestation["signature"] = base64.urlsafe_b64encode(
        fixture.admission.authenticator.expected_signature(unsigned_bytes)
    ).decode("ascii").rstrip("=")
    receipt = c.AdmissionReceipt.model_validate(payload)
    artifact_ref = fixture.store.publish(
        kind=c.ArtifactKind.ADMISSION_RECEIPT,
        canonical_bytes=receipt.canonical_bytes(),
    )
    return c.AdmissionReceiptRef(digest=artifact_ref.sha256, ref=artifact_ref)


def _with_admitted_set(
    fixture: ResolutionFixture,
    *,
    receipt_digests: Sequence[str],
    root_validity: c.ValidityWindow | None = None,
    selector_validity: c.ValidityWindow | None = None,
    candidate_receipt_digest: str | None = None,
) -> ResolutionFixture:
    admitted_payload = fixture.admitted_set.model_dump(mode="json")
    admitted_payload["receipt_digests"] = sorted(receipt_digests)
    if root_validity is not None:
        admitted_payload["validity"] = root_validity.model_dump(mode="json")
    admitted_set = c.AdmittedSetManifest.model_validate(admitted_payload)
    admitted_ref = fixture.store.publish(
        kind=c.ArtifactKind.ADMITTED_SET,
        canonical_bytes=admitted_set.canonical_bytes(),
    )
    selector_payload = fixture.selector.model_dump(mode="json")
    selector_payload["admitted_set_root"] = admitted_ref.sha256
    if selector_validity is not None:
        selector_payload["validity"] = selector_validity.model_dump(mode="json")
    if candidate_receipt_digest is not None:
        if isinstance(fixture.selector, c.DirectSelector):
            selector_payload["candidate"]["receipt_digest"] = candidate_receipt_digest
        else:
            for candidate in selector_payload["candidates"]:
                candidate["candidate"]["receipt_digest"] = candidate_receipt_digest
    selector_type = (
        c.DirectSelector
        if isinstance(fixture.selector, c.DirectSelector)
        else c.ConfigSetManifest
    )
    updated = _with_selector(fixture, selector_type.model_validate(selector_payload))
    return replace(updated, admitted_set=admitted_set)


def _root_receipt_mutation(
    fixture: ResolutionFixture,
    field: str,
    *,
    derived: bool,
) -> c.AdmissionReceiptRef:
    payload = c.AdmissionReceipt.model_validate_json(
        fixture.store.records[fixture.base_receipt_ref.digest]
    ).to_canonical_obj()
    if derived:
        chain_digest = "sha256:" + "9" * 64
        payload["behavior_source"] = {
            "source_kind": "overlay_derived",
            "base_manifest_digest": payload["compiled"]["manifest_digest"],
            "parent_receipt_digest": fixture.base_receipt_ref.digest,
            "overlay_chain_digest": chain_digest,
            "derived_semantic_digest": payload["compiled"]["semantic_digest"],
        }
        payload["parent_receipt_digest"] = fixture.base_receipt_ref.digest
        payload["overlay_chain_digest"] = chain_digest
    if field == "compiler_abi":
        payload["compiled"]["compiler"]["semantic_version"] = "compiler-v999"
    elif field == "runtime_abi":
        payload["compiled"]["compiler"]["runtime_abi"] = "runtime-v999"
    elif field == "task_contract":
        previous = payload["effective_capabilities"]["task"]["task_contract_digest"]
        replacement = "sha256:" + "0" * 64
        payload["effective_capabilities"]["task"]["task_contract_digest"] = replacement
        for pin in payload["pins"]:
            if pin["kind"] == "task" and pin["content_digest"] == previous:
                pin["content_digest"] = replacement
        payload["pins"].sort(
            key=lambda pin: (
                pin["kind"],
                pin["logical_id"],
                pin["content_digest"],
                pin["qualifier_digest"] or "",
            )
        )
        capability_digest = independent_digest(payload["effective_capabilities"])
        payload["requested_capability_digest"] = capability_digest
        payload["effective_capability_digest"] = capability_digest
    elif field == "task_binding":
        replacement = "sha256:" + "0" * 64
        previous = payload["task_binding_digest"]
        payload["task_binding_digest"] = replacement
        payload["effective_capabilities"]["task"]["task_binding_digest"] = replacement
        for pin in payload["pins"]:
            if pin["kind"] == "task" and pin["content_digest"] == previous:
                pin["content_digest"] = replacement
        payload["pins"].sort(
            key=lambda pin: (
                pin["kind"],
                pin["logical_id"],
                pin["content_digest"],
                pin["qualifier_digest"] or "",
            )
        )
        capability_digest = independent_digest(payload["effective_capabilities"])
        payload["requested_capability_digest"] = capability_digest
        payload["effective_capability_digest"] = capability_digest
    elif field == "policy":
        payload["admission_policy_digest"] = "sha256:" + "0" * 64
    elif field == "ceiling":
        payload["operator_ceiling_digest"] = "sha256:" + "0" * 64
    elif field == "registry":
        payload["registry_snapshot_digest"] = "sha256:" + "0" * 64
    elif field == "revocation":
        payload["revocation"]["epoch"] += 1
        payload["revocation"]["state_digest"] = "sha256:" + "0" * 64
    else:
        raise AssertionError(field)
    return _resign_receipt_payload(fixture, payload)


def _spawn_resolved_identity(queue: Any) -> None:
    fixture = _resolution_fixture()
    resolved = fixture.runtime.resolve_episode(fixture.request)
    queue.put(
        (
            resolved.selection_record_ref.sha256,
            fixture.store.records[resolved.selection_record_ref.sha256],
            resolved.effective_plan_ref.sha256,
            fixture.store.records[resolved.effective_plan_ref.sha256],
        )
    )


@pytest.mark.parametrize(("algorithm", "candidate_count"), [("direct-v1", 1), ("weighted-v1", 1), ("weighted-v1", 3)])
def test_resolve_episode_commits_selection_before_content_addressed_plan(
    algorithm: str,
    candidate_count: int,
) -> None:
    fixture = _resolution_fixture(algorithm=algorithm, candidate_count=candidate_count)
    now = datetime(2026, 7, 10, 12, 0, tzinfo=UTC)

    resolved = fixture.runtime.resolve_episode(fixture.request)

    record = c.SelectionRecord.model_validate_json(
        fixture.store.records[resolved.selection_record_ref.sha256]
    )
    assert resolved.episode_id == fixture.request.episode_id
    assert resolved.base_receipt_digest == fixture.base_receipt_ref.digest
    assert resolved.final_receipt_digest == fixture.base_receipt_ref.digest
    assert resolved.policy_capability_observation_digest == fixture.policy_observation.canonical_digest()
    assert record.canonical_digest() == resolved.selection_record_ref.sha256
    assert record.algorithm == algorithm
    assert record.selected_receipt_digest == fixture.base_receipt_ref.digest
    assert resolved.selection_commit.binding.selection_record_digest == record.canonical_digest()
    assert resolved.effective_plan.selection_record_digest == record.canonical_digest()
    assert resolved.effective_plan_ref.sha256 == resolved.effective_plan.canonical_digest()
    assert fixture.policy_registry.calls == [
        (fixture.request.policy_binding, fixture.request.subject, now)
    ]
    published_kinds = [kind for kind, _ in fixture.store.publish_calls]
    assert published_kinds.index(c.ArtifactKind.SELECTION_RECORD) < published_kinds.index(
        c.ArtifactKind.SELECTION_BINDING
    ) < published_kinds.index(c.ArtifactKind.EFFECTIVE_EXECUTION_PLAN)
    fixture.effects.assert_zero()

    if algorithm == "direct-v1":
        assert record.draw is None
        assert record.total_weight is None
        assert record.selection_nonce is None
    else:
        assert isinstance(fixture.selector, c.ConfigSetManifest)
        expected = independent_weighted_draw(
            config_set_digest=fixture.selector_ref.digest,
            selection_nonce=fixture.request.selection_nonce or "",
            task_contract_digest=fixture.request.task.canonical_digest(),
            policy_capability_digest=fixture.policy_observation.capability_digest,
            candidates=[
                {
                    "candidate_id": item.candidate.candidate_id,
                    "receipt_digest": item.candidate.receipt_digest,
                    "weight": item.weight,
                }
                for item in fixture.selector.candidates
            ],
        )
        assert record.draw is not None
        assert record.draw.preimage_hex == expected["preimage_hex"]
        assert record.draw.framing == expected["framing"]
        assert record.draw.unsigned_big_endian_hex == expected["unsigned_big_endian_hex"]
        assert int(record.draw.unsigned_big_endian_hex, 16) == expected["unsigned_integer"]
        assert record.draw.total_weight == expected["total_weight"]
        assert record.draw.draw_digest == expected["draw_digest"]
        assert record.draw.modulo == expected["modulo"]
        assert record.draw.selected_interval_start == expected["selected_interval_start"]
        assert record.draw.selected_interval_end_exclusive == expected["selected_interval_end_exclusive"]
        assert record.total_weight == expected["total_weight"]
        assert [item.candidate_id for item in record.eligible_candidates] == expected[
            "eligible_candidate_order"
        ]
        assert record.selected_candidate_id == expected["selected_candidate_id"]


def test_filtered_candidates_are_recorded_but_excluded_before_weighted_draw() -> None:
    fixture = _resolution_fixture()
    assert isinstance(fixture.selector, c.ConfigSetManifest)
    replacements = {
        "b": (c.TaskLabelEq(key="tier", value="silver"),),
        "c": (c.TaskLabelEq(key="region", value="eu"),),
    }
    selector = c.ConfigSetManifest(
        **{
            **fixture.selector.model_dump(mode="json", exclude={"candidates"}),
            "candidates": [
                {
                    "candidate": {
                        **item.candidate.model_dump(mode="json", exclude={"predicates"}),
                        "predicates": [
                            predicate.model_dump(mode="json")
                            for predicate in replacements.get(item.candidate.candidate_id, item.candidate.predicates)
                        ],
                    },
                    "weight": item.weight,
                }
                for item in fixture.selector.candidates
            ],
        }
    )
    fixture = _with_selector(fixture, selector)

    resolved = fixture.runtime.resolve_episode(fixture.request)
    record = c.SelectionRecord.model_validate_json(
        fixture.store.records[resolved.selection_record_ref.sha256]
    )

    assert record.selected_candidate_id == "a"
    assert [item.candidate_id for item in record.eligible_candidates] == ["a"]
    assert [(item.candidate_id, item.eligible, item.exclusion_codes) for item in record.candidate_evaluations] == [
        ("a", True, ()),
        ("b", False, ("task_label_eq_false",)),
        ("c", False, ("task_label_eq_false",)),
    ]
    assert record.total_weight == 2
    fixture.effects.assert_zero()


@pytest.mark.parametrize(
    ("mutation", "replacement"),
    [
        ("missing", None),
        ("replace", None),
        ("replace", True),
        ("replace", 0),
        ("replace", -1),
        ("replace", 1.0),
        ("replace", "1"),
        ("replace", MAX_SAFE_INTEGER + 1),
    ],
)
def test_weighted_selection_record_rejects_missing_or_invalid_excluded_candidate_weight(
    mutation: str,
    replacement: Any,
) -> None:
    fixture = _resolution_fixture()
    assert isinstance(fixture.selector, c.ConfigSetManifest)
    selector_payload = fixture.selector.model_dump(mode="json")
    selector_payload["candidates"][1]["candidate"]["predicates"] = [
        {"kind": "task_label_eq", "key": "tier", "value": "silver"}
    ]
    fixture = _with_selector(fixture, c.ConfigSetManifest.model_validate(selector_payload))
    resolved = fixture.runtime.resolve_episode(fixture.request)
    record_payload = json.loads(
        fixture.store.records[resolved.selection_record_ref.sha256]
    )
    excluded = next(
        evaluation
        for evaluation in record_payload["candidate_evaluations"]
        if evaluation["candidate_id"] == "b"
    )
    assert excluded == {
        "candidate_id": "b",
        "eligible": False,
        "exclusion_codes": ["task_label_eq_false"],
        "receipt_digest": fixture.base_receipt_ref.digest,
        "weight": 1,
    }
    if mutation == "missing":
        del excluded["weight"]
    else:
        excluded["weight"] = replacement

    with pytest.raises(ValidationError):
        c.SelectionRecord.model_validate(record_payload)


def test_weighted_selection_record_retains_every_validated_input_weight() -> None:
    fixture = _resolution_fixture()
    assert isinstance(fixture.selector, c.ConfigSetManifest)
    selector_payload = fixture.selector.model_dump(mode="json")
    selector_payload["candidates"][1]["candidate"]["predicates"] = [
        {"kind": "task_label_eq", "key": "tier", "value": "silver"}
    ]
    fixture = _with_selector(fixture, c.ConfigSetManifest.model_validate(selector_payload))

    resolved = fixture.runtime.resolve_episode(fixture.request)
    record = c.SelectionRecord.model_validate_json(
        fixture.store.records[resolved.selection_record_ref.sha256]
    )

    expected_weights = {
        item.candidate.candidate_id: item.weight for item in fixture.selector.candidates
    }
    assert {
        evaluation.candidate_id: evaluation.weight
        for evaluation in record.candidate_evaluations
    } == expected_weights
    assert record.candidate_evaluations[1].eligible is False
    fixture.effects.assert_zero()


def test_maximum_legal_total_matches_live_production_draw_without_saturation() -> None:
    fixture = _resolution_fixture(candidate_count=2)
    assert isinstance(fixture.selector, c.ConfigSetManifest)
    selector = c.ConfigSetManifest(
        **{
            **fixture.selector.model_dump(mode="json", exclude={"candidates"}),
            "candidates": [
                c.WeightedCandidate(
                    candidate=fixture.selector.candidates[0].candidate,
                    weight=MAX_SAFE_INTEGER - 1,
                ).model_dump(mode="json"),
                c.WeightedCandidate(
                    candidate=fixture.selector.candidates[1].candidate,
                    weight=1,
                ).model_dump(mode="json"),
            ],
        }
    )
    fixture = _with_selector(fixture, selector)
    expected = independent_weighted_draw(
        config_set_digest=fixture.selector_ref.digest,
        selection_nonce=fixture.request.selection_nonce or "",
        task_contract_digest=fixture.request.task.canonical_digest(),
        policy_capability_digest=fixture.policy_observation.capability_digest,
        candidates=[
            {
                "candidate_id": item.candidate.candidate_id,
                "receipt_digest": item.candidate.receipt_digest,
                "weight": item.weight,
            }
            for item in selector.candidates
        ],
    )

    resolved = fixture.runtime.resolve_episode(fixture.request)
    record = c.SelectionRecord.model_validate_json(
        fixture.store.records[resolved.selection_record_ref.sha256]
    )

    assert record.total_weight == MAX_SAFE_INTEGER == expected["total_weight"]
    assert record.draw is not None
    assert record.draw.unsigned_big_endian_hex == expected["unsigned_big_endian_hex"]
    assert record.draw.modulo == expected["modulo"]
    assert record.draw.selected_interval_start == expected["selected_interval_start"]
    assert record.draw.selected_interval_end_exclusive == expected[
        "selected_interval_end_exclusive"
    ]
    assert record.selected_candidate_id == expected["selected_candidate_id"]
    fixture.effects.assert_zero()


def test_malformed_false_candidate_rejects_whole_set_before_filtering() -> None:
    fixture = _resolution_fixture()
    assert isinstance(fixture.selector, c.ConfigSetManifest)
    raw = fixture.selector.model_dump(mode="json")
    raw["candidates"][1]["weight"] = "1"
    raw["candidates"][1]["candidate"]["predicates"] = [
        {"kind": "task_label_eq", "key": "tier", "value": "silver"}
    ]
    raw_bytes = independent_jcs_bytes(raw)
    artifact_ref = fixture.store.publish(
        kind=c.ArtifactKind.CONFIG_SET,
        canonical_bytes=raw_bytes,
    )
    selector_ref = c.WeightedSelectorRef(digest=artifact_ref.sha256, ref=artifact_ref)
    request_payload = fixture.request.model_dump(mode="json")
    request_payload["selector"] = selector_ref.model_dump(mode="json")
    request = c.ResolveEpisodeRequest.model_validate(request_payload)

    with pytest.raises(c.ConfigRuntimeDenial) as caught:
        fixture.runtime.resolve_episode(request)

    assert caught.value.stage.value == "selector_validation"
    assert caught.value.code.value == "weight_not_integer"
    assert fixture.policy_registry.calls == []
    assert fixture.store.bindings == {}
    fixture.effects.assert_zero()


def test_noncanonical_false_predicate_rejects_whole_set_before_filtering() -> None:
    fixture = _resolution_fixture()
    assert isinstance(fixture.selector, c.ConfigSetManifest)
    raw = fixture.selector.model_dump(mode="json")
    raw["candidates"][1]["candidate"]["predicates"] = [
        {"kind": "task_label_eq", "key": "tier", "value": "silver"},
        {"kind": "policy_bool_eq", "field": "tool_calling", "value": True},
    ]
    raw_bytes = independent_jcs_bytes(raw)
    artifact_ref = fixture.store.publish(
        kind=c.ArtifactKind.CONFIG_SET,
        canonical_bytes=raw_bytes,
    )
    selector_ref = c.WeightedSelectorRef(digest=artifact_ref.sha256, ref=artifact_ref)
    request_payload = fixture.request.model_dump(mode="json")
    request_payload["selector"] = selector_ref.model_dump(mode="json")
    request = c.ResolveEpisodeRequest.model_validate(request_payload)

    with pytest.raises(c.ConfigRuntimeDenial) as caught:
        fixture.runtime.resolve_episode(request)

    assert caught.value.stage.value == "selector_validation"
    assert caught.value.code.value == "invalid_config_set"
    assert fixture.policy_registry.calls == []
    assert fixture.store.bindings == {}
    fixture.effects.assert_zero()


def test_stale_false_candidate_rejects_whole_set_before_filtering() -> None:
    fixture = _resolution_fixture()
    assert isinstance(fixture.selector, c.ConfigSetManifest)
    payload = fixture.selector.model_dump(mode="json")
    payload["candidates"][1]["candidate"]["receipt_digest"] = "sha256:" + "0" * 64
    payload["candidates"][1]["candidate"]["predicates"] = [
        {"kind": "task_label_eq", "key": "tier", "value": "silver"}
    ]
    selector = c.ConfigSetManifest.model_validate(payload)
    fixture = _with_selector(fixture, selector)

    with pytest.raises(c.ConfigRuntimeDenial) as caught:
        fixture.runtime.resolve_episode(fixture.request)

    assert caught.value.stage.value == "selector_validation"
    assert caught.value.code.value == "stale_candidate_receipt"
    assert caught.value.candidate_id == "b"
    assert fixture.policy_registry.calls == []
    assert fixture.store.bindings == {}
    fixture.effects.assert_zero()


@pytest.mark.parametrize(
    ("field", "replacement", "pointer"),
    [
        ("compiler_abi", "compiler-v999", "/compiler_abi"),
        ("runtime_abi", "runtime-v999", "/runtime_abi"),
    ],
)
def test_false_candidate_abi_mismatch_rejects_before_filtering(
    field: str,
    replacement: str,
    pointer: str,
) -> None:
    fixture = _resolution_fixture(candidate_count=1)
    assert isinstance(fixture.selector, c.ConfigSetManifest)
    payload = fixture.selector.model_dump(mode="json")
    payload[field] = replacement
    payload["candidates"][0]["candidate"]["predicates"] = [
        {"kind": "task_label_eq", "key": "tier", "value": "silver"}
    ]
    selector = c.ConfigSetManifest.model_validate(payload)
    fixture = _with_selector(fixture, selector)

    with pytest.raises(c.ConfigRuntimeDenial) as caught:
        fixture.runtime.resolve_episode(fixture.request)

    assert caught.value.stage.value == "selector_validation"
    assert caught.value.code.value == "set_abi_mismatch"
    assert caught.value.pointer == pointer
    assert fixture.policy_registry.calls == []
    assert fixture.store.bindings == {}
    fixture.effects.assert_zero()


@pytest.mark.parametrize("algorithm", ["direct-v1", "weighted-v1"])
def test_predicate_false_empty_eligibility_denies_without_selection_or_fallback(
    algorithm: str,
) -> None:
    fixture = _resolution_fixture(
        algorithm=algorithm,
        candidate_count=1 if algorithm == "direct-v1" else 3,
    )
    false_by_id: dict[str, tuple[c.EligibilityPredicate, ...]] = {
        "a": (c.TaskLabelEq(key="tier", value="silver"),),
        "b": (c.TaskLabelIn(key="tier", values=("bronze",)),),
        "c": (
            c.ArtifactRolePresent(
                role="missing",
                min_count=1,
                max_count=None,
                media_types=(),
            ),
        ),
    }
    if isinstance(fixture.selector, c.DirectSelector):
        selector: c.DirectSelector | c.ConfigSetManifest = c.DirectSelector(
            **{
                **fixture.selector.model_dump(mode="json", exclude={"candidate"}),
                "candidate": {
                    **fixture.selector.candidate.model_dump(mode="json", exclude={"predicates"}),
                    "predicates": [item.model_dump(mode="json") for item in false_by_id["a"]],
                },
            }
        )
    else:
        selector = c.ConfigSetManifest(
            **{
                **fixture.selector.model_dump(mode="json", exclude={"candidates"}),
                "candidates": [
                    {
                        "candidate": {
                            **item.candidate.model_dump(mode="json", exclude={"predicates"}),
                            "predicates": [
                                predicate.model_dump(mode="json")
                                for predicate in false_by_id[item.candidate.candidate_id]
                            ],
                        },
                        "weight": item.weight,
                    }
                    for item in fixture.selector.candidates
                ],
            }
        )
    fixture = _with_selector(fixture, selector)

    with pytest.raises(c.ConfigRuntimeDenial) as caught:
        fixture.runtime.resolve_episode(fixture.request)

    assert caught.value.stage.value == "eligibility"
    assert caught.value.code.value == "no_eligible_candidate"
    assert fixture.store.bindings == {}
    assert not any(kind is c.ArtifactKind.SELECTION_RECORD for kind, _ in fixture.store.publish_calls)
    fixture.effects.assert_zero()


def test_fixed_resolution_is_identical_across_fresh_spawned_processes() -> None:
    local_fixture = _resolution_fixture()
    local = local_fixture.runtime.resolve_episode(local_fixture.request)
    expected = (
        local.selection_record_ref.sha256,
        local_fixture.store.records[local.selection_record_ref.sha256],
        local.effective_plan_ref.sha256,
        local_fixture.store.records[local.effective_plan_ref.sha256],
    )
    context = multiprocessing.get_context("spawn")
    queue = context.Queue()
    processes = [context.Process(target=_spawn_resolved_identity, args=(queue,)) for _ in range(2)]
    for process in processes:
        process.start()
    results = [queue.get(timeout=20) for _ in processes]
    for process in processes:
        process.join(timeout=20)
        assert process.exitcode == 0
    assert results == [expected, expected]


def test_identical_resolution_retry_reuses_exact_write_once_record_and_plan() -> None:
    fixture = _resolution_fixture()
    first = fixture.runtime.resolve_episode(fixture.request)
    first_record_bytes = fixture.store.records[first.selection_record_ref.sha256]
    second = fixture.runtime.resolve_episode(fixture.request)

    assert second == first
    assert fixture.store.records[second.selection_record_ref.sha256] == first_record_bytes
    assert len(fixture.store.bindings) == 1
    assert {call[2] for call in fixture.store.bind_calls} == {first.selection_record_ref.sha256}
    fixture.effects.assert_zero()


def test_selected_manifest_failure_preserves_committed_record_and_never_redraws() -> None:
    fixture = _resolution_fixture()
    manifest_digest = fixture.admission.request.compiled.manifest_digest
    manifest_bytes = fixture.store.records.pop(manifest_digest)
    manifest_kind = fixture.store.kinds.pop(manifest_digest)

    with pytest.raises(c.ConfigRuntimeDenial) as caught:
        fixture.runtime.resolve_episode(fixture.request)

    denial = caught.value
    assert denial.stage.value == "overlay_validation"
    assert denial.code.value == "overlay_base_mismatch"
    assert denial.selection_record_digest is not None
    assert len(fixture.store.bindings) == 1
    binding = next(iter(fixture.store.bindings.values()))
    assert binding.selection_record_digest == denial.selection_record_digest
    record_bytes = fixture.store.records[binding.selection_record_digest]
    record = c.SelectionRecord.model_validate_json(record_bytes)

    fixture.store.records[manifest_digest] = manifest_bytes
    fixture.store.kinds[manifest_digest] = manifest_kind
    recovered = fixture.runtime.resolve_episode(fixture.request)
    assert recovered.selection_record_ref.sha256 == binding.selection_record_digest
    assert fixture.store.records[binding.selection_record_digest] == record_bytes
    assert c.SelectionRecord.model_validate_json(record_bytes) == record
    fixture.effects.assert_zero()



def _contract_candidate(
    candidate_id: str = "a",
    *,
    predicates: tuple[c.EligibilityPredicate, ...] = (),
) -> c.ConfigCandidate:
    return c.ConfigCandidate(
        candidate_id=candidate_id,
        receipt_digest="sha256:" + "4" * 64,
        predicates=predicates,
        overlays=(),
    )


def _contract_set(candidates: tuple[c.WeightedCandidate, ...]) -> c.ConfigSetManifest:
    return c.ConfigSetManifest(
        admitted_set_root="sha256:" + "5" * 64,
        compiler_abi="1.0.0",
        runtime_abi="runtime-v1",
        admission_policy_digest="sha256:" + "6" * 64,
        operator_ceiling_digest="sha256:" + "7" * 64,
        candidates=candidates,
        validity=c.ValidityWindow(
            issued_at="2026-07-10T11:00:00Z",
            not_before="2026-07-10T11:00:00Z",
            expires_at="2026-07-10T13:00:00Z",
        ),
    )


@pytest.mark.parametrize(
    "candidate_id",
    ["café", "A", "-a", "a.", "a" * 65, "a/b", "a b", "a\\b"],
)
def test_production_candidate_id_rejects_unicode_and_malformed_spellings(
    candidate_id: str,
) -> None:
    with pytest.raises(ValidationError):
        _contract_candidate(candidate_id)


@pytest.mark.parametrize("weight", [True, 1.0, "1", 0, -1, MAX_SAFE_INTEGER + 1])
def test_production_weight_rejects_coercion_nonpositive_and_overflow(weight: Any) -> None:
    with pytest.raises(ValidationError):
        c.WeightedCandidate(candidate=_contract_candidate(), weight=weight)


def test_production_config_set_accepts_maximum_legal_total_without_saturation() -> None:
    manifest = _contract_set(
        (
            c.WeightedCandidate(candidate=_contract_candidate("a"), weight=MAX_SAFE_INTEGER - 1),
            c.WeightedCandidate(
                candidate=_contract_candidate(
                    "b",
                    predicates=(c.TaskLabelEq(key="tier", value="gold"),),
                ),
                weight=1,
            ),
        )
    )
    assert sum(candidate.weight for candidate in manifest.candidates) == MAX_SAFE_INTEGER


def test_production_config_set_rejects_empty_duplicate_id_duplicate_effective_and_total_overflow() -> None:
    with pytest.raises(ValidationError):
        _contract_set(())
    with pytest.raises(ValidationError):
        _contract_set(
            (
                c.WeightedCandidate(candidate=_contract_candidate("a"), weight=1),
                c.WeightedCandidate(
                    candidate=c.ConfigCandidate(
                        candidate_id="a",
                        receipt_digest="sha256:" + "5" * 64,
                        predicates=(),
                        overlays=(),
                    ),
                    weight=1,
                ),
            )
        )
    with pytest.raises(ValidationError):
        _contract_set(
            (
                c.WeightedCandidate(candidate=_contract_candidate("a"), weight=1),
                c.WeightedCandidate(candidate=_contract_candidate("b"), weight=1),
            )
        )
    with pytest.raises(ValidationError):
        _contract_set(
            (
                c.WeightedCandidate(candidate=_contract_candidate("a"), weight=MAX_SAFE_INTEGER),
                c.WeightedCandidate(
                    candidate=_contract_candidate(
                        "b",
                        predicates=(c.TaskLabelEq(key="tier", value="gold"),),
                    ),
                    weight=1,
                ),
            )
        )


def test_same_receipt_with_distinct_predicate_semantics_is_valid() -> None:
    manifest = _contract_set(
        (
            c.WeightedCandidate(candidate=_contract_candidate("a"), weight=1),
            c.WeightedCandidate(
                candidate=_contract_candidate(
                    "b",
                    predicates=(c.TaskLabelEq(key="tier", value="gold"),),
                ),
                weight=1,
            ),
            c.WeightedCandidate(
                candidate=c.ConfigCandidate(
                    candidate_id="c",
                    receipt_digest="sha256:" + "4" * 64,
                    predicates=(),
                    overlays=(
                        c.AdmittedOverlayRef(
                            overlay_digest="sha256:" + "8" * 64,
                            result_receipt_digest="sha256:" + "9" * 64,
                        ),
                    ),
                ),
                weight=1,
            ),
        )
    )
    assert len(manifest.candidates) == 3


@pytest.mark.parametrize(
    ("algorithm", "candidate_count", "selection_nonce"),
    [("direct-v1", 1, "sha256:" + "0" * 64), ("weighted-v1", 3, None)],
)
def test_resolve_request_enforces_direct_forbidden_and_weighted_required_nonce(
    algorithm: str,
    candidate_count: int,
    selection_nonce: str | None,
) -> None:
    fixture = _resolution_fixture(algorithm=algorithm, candidate_count=candidate_count)
    payload = fixture.request.model_dump(mode="json")
    payload["selection_nonce"] = selection_nonce

    with pytest.raises(ValidationError):
        c.ResolveEpisodeRequest.model_validate(payload)

def _load(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _validate_json(value: Any) -> None:
    if value is None or type(value) in (bool, str):
        return
    if type(value) is int:
        if not -MAX_SAFE_INTEGER <= value <= MAX_SAFE_INTEGER:
            raise ValueError("integer outside independently frozen JCS domain")
        return
    if type(value) is float:
        if not math.isfinite(value):
            raise ValueError("non-finite number")
        return
    if type(value) is list:
        for item in value:
            _validate_json(item)
        return
    if type(value) is dict:
        if any(type(key) is not str for key in value):
            raise TypeError("JSON keys must be strings")
        if any(not key.isascii() for key in value):
            raise ValueError("independent fixture canonicalizer has an ASCII-key domain")
        for item in value.values():
            _validate_json(item)
        return
    raise TypeError(f"unsupported JSON value: {type(value).__name__}")


def independent_jcs_bytes(value: Any) -> bytes:
    """Canonicalize the fixture subset without importing BreadBoard code."""

    _validate_json(value)
    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def independent_digest(value: Any) -> str:
    return "sha256:" + hashlib.sha256(independent_jcs_bytes(value)).hexdigest()


def independent_weighted_draw(
    *,
    config_set_digest: str,
    selection_nonce: str,
    task_contract_digest: str,
    policy_capability_digest: str,
    candidates: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """The standalone standard-library weighted-v1 reference oracle."""

    framed: list[bytes] = [_WEIGHTED_PREFIX]
    for name, value in (
        ("S", config_set_digest),
        ("N", selection_nonce),
        ("T", task_contract_digest),
        ("P", policy_capability_digest),
    ):
        if type(value) is not str or _DIGEST.fullmatch(value) is None:
            raise ValueError(f"{name} is not a fixed-width lowercase SHA-256 spelling")
        encoded = value.encode("ascii")
        assert len(encoded) == 71
        framed.append(encoded)
    preimage = b"".join(framed)
    assert len(_WEIGHTED_PREFIX) == 15
    assert len(preimage) == 299

    canonical: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    for candidate in candidates:
        candidate_id = candidate.get("candidate_id")
        weight = candidate.get("weight")
        receipt_digest = candidate.get("receipt_digest")
        if type(candidate_id) is not str or _CANDIDATE_ID.fullmatch(candidate_id) is None:
            raise ValueError("invalid candidate id")
        if candidate_id in seen_ids:
            raise ValueError("duplicate candidate id")
        seen_ids.add(candidate_id)
        if type(weight) is not int:
            raise TypeError("weight is not an integer")
        if weight <= 0:
            raise ValueError("weight is nonpositive")
        if weight > MAX_SAFE_INTEGER:
            raise OverflowError("weight overflow")
        if type(receipt_digest) is not str or _DIGEST.fullmatch(receipt_digest) is None:
            raise ValueError("invalid receipt digest")
        canonical.append(
            {
                "candidate_id": candidate_id,
                "receipt_digest": receipt_digest,
                "weight": weight,
            }
        )
    if not canonical:
        raise ValueError("empty candidate set")
    canonical.sort(key=lambda item: item["candidate_id"].encode("ascii"))
    total = sum(item["weight"] for item in canonical)
    if total > MAX_SAFE_INTEGER:
        raise OverflowError("total weight overflow")

    draw_bytes = hashlib.sha256(preimage).digest()
    unsigned = int.from_bytes(draw_bytes, "big", signed=False)
    modulo = unsigned % total
    cursor = 0
    intervals: list[dict[str, Any]] = []
    selected: Mapping[str, Any] | None = None
    selected_start = -1
    selected_end = -1
    for candidate in canonical:
        end = cursor + candidate["weight"]
        intervals.append(
            {
                "candidate_id": candidate["candidate_id"],
                "start": cursor,
                "end_exclusive": end,
            }
        )
        if selected is None and modulo < end:
            selected = candidate
            selected_start = cursor
            selected_end = end
        cursor = end
    assert selected is not None
    return {
        "framing": "ascii-sha256-digests-v1",
        "preimage_hex": preimage.hex(),
        "preimage_length": len(preimage),
        "draw_digest": "sha256:" + draw_bytes.hex(),
        "unsigned_big_endian_hex": draw_bytes.hex(),
        "unsigned_integer": unsigned,
        "total_weight": total,
        "modulo": modulo,
        "selected_interval_start": selected_start,
        "selected_interval_end_exclusive": selected_end,
        "selected_candidate_id": selected["candidate_id"],
        "selected_receipt_digest": selected["receipt_digest"],
        "intervals": intervals,
        "eligible_candidate_order": [item["candidate_id"] for item in canonical],
    }


def _assert_oracle_vector(vector: Mapping[str, Any]) -> dict[str, Any]:
    excluded = set(vector.get("excluded_candidate_ids", ()))
    eligible = [
        candidate
        for candidate in vector["input_candidates"]
        if candidate["candidate_id"] not in excluded
    ]
    actual = independent_weighted_draw(
        config_set_digest=vector["S"],
        selection_nonce=vector["N"],
        task_contract_digest=vector["T"],
        policy_capability_digest=vector["P"],
        candidates=eligible,
    )
    expected = vector["expected"]
    for key in (
        "framing",
        "preimage_hex",
        "preimage_length",
        "draw_digest",
        "unsigned_big_endian_hex",
        "unsigned_integer",
        "total_weight",
        "modulo",
        "selected_interval_start",
        "selected_interval_end_exclusive",
        "selected_candidate_id",
        "selected_receipt_digest",
    ):
        assert actual[key] == expected[key], (vector["case_id"], key)
    assert actual["intervals"] == vector["intervals"]
    assert actual["eligible_candidate_order"] == vector["eligible_candidate_order"]
    return actual


def _spawn_oracle_result(queue: Any) -> None:
    vector = _load(WEIGHTED_VECTORS)["golden_vectors"][0]
    actual = _assert_oracle_vector(vector)
    queue.put(
        (
            actual["preimage_hex"],
            actual["draw_digest"],
            actual["unsigned_integer"],
            actual["modulo"],
            actual["selected_candidate_id"],
        )
    )


def test_weighted_fixture_is_frozen_and_has_the_exact_architecture_rows() -> None:
    corpus = _load(WEIGHTED_VECTORS)

    assert hashlib.sha256(WEIGHTED_VECTORS.read_bytes()).hexdigest() == WEIGHTED_VECTORS_FILE_SHA256
    assert corpus["schema_version"] == "bb.rl.weighted-v1-vectors.v1"
    assert corpus["framing"] == "ascii-sha256-digests-v1"
    assert corpus["preimage_length"] == 299
    assert corpus["max_safe_integer"] == MAX_SAFE_INTEGER
    assert len(corpus["golden_vectors"]) == 4
    assert [row["case_id"] for row in corpus["golden_vectors"]] == [
        "weighted_ascii_primary_b2",
        "weighted_ascii_a0",
        "weighted_ascii_c3",
        "weighted_ascii_c7",
    ]
    assert [row["expected"]["draw_digest"] for row in corpus["golden_vectors"]] == [
        "sha256:66d0955bde3ff6c592071c04a0c61344466d423a2101dab393d9efa1d99dc3da",
        "sha256:0de0974a5d9e3bf799abba0f50c8da68562403c14b2b0e05a567d8e720e794f8",
        "sha256:39bd9f394f3d39e9176d82f64333f1b3c8254eb88aacc9ae3a4ac2563136e073",
        "sha256:bfdd5f6a82df24313a151e86d72260ccc0d4a05007b50fd64d1841c66100cde7",
    ]
    assert [row["expected"]["modulo"] for row in corpus["golden_vectors"]] == [2, 0, 3, 7]
    assert [row["expected"]["selected_candidate_id"] for row in corpus["golden_vectors"]] == [
        "b",
        "a",
        "c",
        "c",
    ]


@pytest.mark.parametrize("vector_index", range(4))
def test_weighted_golden_preimage_draw_intervals_and_record_are_independent(
    vector_index: int,
) -> None:
    vector = _load(WEIGHTED_VECTORS)["golden_vectors"][vector_index]
    actual = _assert_oracle_vector(vector)
    expected = vector["expected"]

    preimage = bytes.fromhex(expected["preimage_hex"])
    assert len(preimage) == 299
    assert preimage == (
        _WEIGHTED_PREFIX
        + vector["S"].encode("ascii")
        + vector["N"].encode("ascii")
        + vector["T"].encode("ascii")
        + vector["P"].encode("ascii")
    )
    assert "sha256:" + hashlib.sha256(preimage).hexdigest() == expected["draw_digest"]
    assert int(expected["unsigned_big_endian_hex"], 16) == expected["unsigned_integer"]
    assert int.from_bytes(bytes.fromhex(expected["draw_digest"].removeprefix("sha256:")), "big") == actual["unsigned_integer"]

    record_bytes = bytes.fromhex(expected["selection_record_canonical_hex"])
    assert independent_jcs_bytes(expected["selection_record"]) == record_bytes
    assert independent_digest(expected["selection_record"]) == expected["selection_record_digest"]
    assert expected["selection_record_digest"] == "sha256:" + hashlib.sha256(record_bytes).hexdigest()
    assert expected["selection_record"]["draw"]["preimage_hex"] == expected["preimage_hex"]
    assert expected["selection_record"]["selected_candidate_id"] == actual["selected_candidate_id"]


@pytest.mark.parametrize("vector_index", range(4))
def test_production_selection_record_matches_independent_golden_bytes(
    vector_index: int,
) -> None:
    expected = _load(WEIGHTED_VECTORS)["golden_vectors"][vector_index]["expected"]

    record = c.SelectionRecord.model_validate(expected["selection_record"])

    assert record.canonical_bytes() == bytes.fromhex(expected["selection_record_canonical_hex"])
    assert record.canonical_digest() == expected["selection_record_digest"]
    assert record.draw is not None
    assert record.draw.preimage_hex == expected["preimage_hex"]
    assert record.draw.modulo == expected["modulo"]
    assert record.selected_candidate_id == expected["selected_candidate_id"]


@pytest.mark.parametrize("vector_index", range(5))
def test_weighted_positive_filtered_singleton_permutation_and_maximum_vectors(
    vector_index: int,
) -> None:
    vectors = _load(WEIGHTED_VECTORS)["positive_vectors"]
    _assert_oracle_vector(vectors[vector_index])


def test_candidate_input_permutations_have_identical_oracle_result() -> None:
    vectors = {
        vector["case_id"]: vector
        for vector in _load(WEIGHTED_VECTORS)["positive_vectors"]
    }
    left = _assert_oracle_vector(vectors["permutation_cab"])
    right = _assert_oracle_vector(vectors["permutation_abc"])

    for key in (
        "preimage_hex",
        "draw_digest",
        "unsigned_integer",
        "modulo",
        "intervals",
        "selected_candidate_id",
        "selected_receipt_digest",
    ):
        assert left[key] == right[key]


def test_weighted_oracle_is_identical_in_repeated_and_fresh_processes() -> None:
    vector = _load(WEIGHTED_VECTORS)["golden_vectors"][0]
    local = _assert_oracle_vector(vector)
    expected = (
        local["preimage_hex"],
        local["draw_digest"],
        local["unsigned_integer"],
        local["modulo"],
        local["selected_candidate_id"],
    )
    assert _assert_oracle_vector(vector)["draw_digest"] == local["draw_digest"]

    context = multiprocessing.get_context("spawn")
    queue = context.Queue()
    processes = [context.Process(target=_spawn_oracle_result, args=(queue,)) for _ in range(2)]
    for process in processes:
        process.start()
    results = [queue.get(timeout=20) for _ in processes]
    for process in processes:
        process.join(timeout=20)
        assert process.exitcode == 0
    assert results == [expected, expected]


def test_draw_boundaries_use_half_open_intervals() -> None:
    rows = _load(WEIGHTED_VECTORS)["golden_vectors"]
    observed = {
        row["expected"]["modulo"]: (
            row["expected"]["selected_candidate_id"],
            row["expected"]["selected_interval_start"],
            row["expected"]["selected_interval_end_exclusive"],
        )
        for row in rows
    }
    assert observed == {
        0: ("a", 0, 2),
        2: ("b", 2, 3),
        3: ("c", 3, 8),
        7: ("c", 3, 8),
    }


def test_direct_v1_record_has_no_nonce_draw_or_weight() -> None:
    vector = _load(WEIGHTED_VECTORS)["direct_vector"]
    expected = vector["expected"]
    record = expected["selection_record"]

    assert vector["algorithm"] == "direct-v1"
    assert vector["selection_nonce"] is None
    assert record["config_set_digest"] is None
    assert record["selection_nonce"] is None
    assert record["total_weight"] is None
    assert record["draw"] is None
    assert record["candidate_evaluations"][0]["weight"] is None
    assert record["eligible_candidates"][0]["weight"] is None
    canonical = bytes.fromhex(expected["selection_record_canonical_hex"])
    assert independent_jcs_bytes(record) == canonical
    assert independent_digest(record) == expected["selection_record_digest"]


def test_invalid_vector_corpus_covers_no_repair_and_numeric_edges() -> None:
    corpus = _load(WEIGHTED_VECTORS)
    cases = {vector["case_id"]: vector for vector in corpus["invalid_vectors"]}
    assert len(cases) == 20
    assert {
        "weighted_nonce_missing",
        "direct_nonce_forbidden",
        "empty_config_set",
        "unicode_candidate_id",
        "uppercase_candidate_id",
        "leading_punctuation_id",
        "trailing_punctuation_id",
        "overlong_candidate_id",
        "duplicate_candidate_id",
        "bool_weight",
        "float_weight",
        "string_weight",
        "zero_weight",
        "negative_weight",
        "weight_overflow",
        "total_weight_overflow",
        "malformed_candidate_not_repaired_by_false_predicate",
        "stale_ineligible_candidate_not_filtered",
        "no_eligible_candidate",
        "exact_duplicate_effective_candidate",
    } == set(cases)
    assert cases["malformed_candidate_not_repaired_by_false_predicate"]["code"] == "weight_not_integer"
    assert cases["stale_ineligible_candidate_not_filtered"]["code"] == "stale_candidate_receipt"
    assert cases["no_eligible_candidate"]["stage"] == "eligibility"
    assert cases["exact_duplicate_effective_candidate"]["code"] == "duplicate_candidate"
    assert corpus["same_receipt_distinct_semantics"]["expected_valid"] is True


@pytest.mark.parametrize(
    ("case_id", "error_type"),
    [
        ("unicode_candidate_id", ValueError),
        ("uppercase_candidate_id", ValueError),
        ("leading_punctuation_id", ValueError),
        ("trailing_punctuation_id", ValueError),
        ("overlong_candidate_id", ValueError),
        ("bool_weight", TypeError),
        ("float_weight", TypeError),
        ("string_weight", TypeError),
        ("zero_weight", ValueError),
        ("negative_weight", ValueError),
        ("weight_overflow", OverflowError),
    ],
)
def test_independent_oracle_rejects_malformed_candidate_before_any_draw(
    case_id: str,
    error_type: type[Exception],
) -> None:
    corpus = _load(WEIGHTED_VECTORS)
    case = next(vector for vector in corpus["invalid_vectors"] if vector["case_id"] == case_id)
    with pytest.raises(error_type):
        independent_weighted_draw(
            config_set_digest="sha256:" + "0" * 64,
            selection_nonce="sha256:" + "1" * 64,
            task_contract_digest="sha256:" + "2" * 64,
            policy_capability_digest="sha256:" + "3" * 64,
            candidates=[case["candidate"]],
        )


def test_independent_oracle_rejects_total_overflow_without_saturation() -> None:
    corpus = _load(WEIGHTED_VECTORS)
    case = next(
        vector
        for vector in corpus["invalid_vectors"]
        if vector["case_id"] == "total_weight_overflow"
    )
    with pytest.raises(OverflowError, match="total weight overflow"):
        independent_weighted_draw(
            config_set_digest="sha256:" + "0" * 64,
            selection_nonce="sha256:" + "1" * 64,
            task_contract_digest="sha256:" + "2" * 64,
            policy_capability_digest="sha256:" + "3" * 64,
            candidates=case["candidates"],
        )


def _strict_string(value: Any, name: str) -> None:
    if type(value) is not str or not value:
        raise ValueError(f"{name} must be a non-empty string")


def _strict_uint53(value: Any, name: str) -> None:
    if type(value) is not int or not 0 <= value <= MAX_SAFE_INTEGER:
        raise ValueError(f"{name} must be uint53")


def _sorted_unique_strings(values: Any, name: str, *, nonempty: bool) -> None:
    if type(values) is not list:
        raise ValueError(f"{name} must be a list")
    if nonempty and not values:
        raise ValueError(f"{name} must not be empty")
    if any(type(value) is not str for value in values):
        raise ValueError(f"{name} must contain strings")
    if values != sorted(set(values)):
        raise ValueError(f"{name} must be sorted and unique")


def independent_validate_predicates(predicates: Any) -> None:
    if type(predicates) is not list:
        raise ValueError("predicates must be a list")
    node_count = 0

    def visit(node: Any, depth: int) -> None:
        nonlocal node_count
        node_count += 1
        if node_count > 64 or depth > 8 or type(node) is not dict:
            raise ValueError("predicate structural bound exceeded")
        kind = node.get("kind")
        if kind in {"all", "any"}:
            if set(node) != {"kind", "children"}:
                raise ValueError("invalid commutative node shape")
            children = node["children"]
            if type(children) is not list or not children:
                raise ValueError("commutative children must be non-empty")
            for child in children:
                visit(child, depth + 1)
            return
        if kind == "task_label_eq":
            if set(node) != {"kind", "key", "value"}:
                raise ValueError("invalid label equality shape")
            _strict_string(node["key"], "key")
            _strict_string(node["value"], "value")
            return
        if kind == "task_label_in":
            if set(node) != {"kind", "key", "values"}:
                raise ValueError("invalid label membership shape")
            _strict_string(node["key"], "key")
            _sorted_unique_strings(node["values"], "values", nonempty=True)
            return
        if kind == "artifact_role_present":
            if set(node) != {"kind", "role", "min_count", "max_count", "media_types"}:
                raise ValueError("invalid artifact role shape")
            _strict_string(node["role"], "role")
            _strict_uint53(node["min_count"], "min_count")
            maximum = node["max_count"]
            if maximum is not None:
                _strict_uint53(maximum, "max_count")
                if maximum < node["min_count"]:
                    raise ValueError("max_count is below min_count")
            _sorted_unique_strings(node["media_types"], "media_types", nonempty=False)
            return
        if kind == "policy_bool_eq":
            if set(node) != {"kind", "field", "value"}:
                raise ValueError("invalid policy bool shape")
            if node["field"] not in _BOOL_FIELDS or type(node["value"]) is not bool:
                raise ValueError("policy bool field/type mismatch")
            return
        if kind == "policy_int_gte":
            if set(node) != {"kind", "field", "value"}:
                raise ValueError("invalid policy int shape")
            if node["field"] not in _INT_FIELDS:
                raise ValueError("policy int field mismatch")
            _strict_uint53(node["value"], "value")
            return
        if kind == "policy_set_contains_all":
            if set(node) != {"kind", "field", "values"}:
                raise ValueError("invalid policy set shape")
            if node["field"] not in _SET_FIELDS:
                raise ValueError("policy set field mismatch")
            _sorted_unique_strings(node["values"], "values", nonempty=True)
            return
        raise ValueError("unknown predicate kind")

    for predicate in predicates:
        visit(predicate, 1)


def independent_canonical_predicate(node: Mapping[str, Any]) -> dict[str, Any]:
    result = dict(node)
    if node["kind"] in {"all", "any"}:
        children = [independent_canonical_predicate(child) for child in node["children"]]
        result["children"] = sorted(children, key=independent_jcs_bytes)
    return result


def independent_canonical_predicates(predicates: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    canonical = [independent_canonical_predicate(predicate) for predicate in predicates]
    return sorted(canonical, key=independent_jcs_bytes)


def independent_evaluate_predicate(node: Mapping[str, Any], facts: Mapping[str, Any]) -> bool:
    kind = node["kind"]
    if kind == "all":
        return all(independent_evaluate_predicate(child, facts) for child in node["children"])
    if kind == "any":
        return any(independent_evaluate_predicate(child, facts) for child in node["children"])
    task = facts["task"]
    policy = facts["policy"]
    if kind == "task_label_eq":
        return task["labels"].get(node["key"]) == node["value"]
    if kind == "task_label_in":
        return task["labels"].get(node["key"]) in node["values"]
    if kind == "artifact_role_present":
        matching = [
            artifact
            for artifact in task["artifacts"]
            if artifact["role"] == node["role"]
            and (not node["media_types"] or artifact["media_type"] in node["media_types"])
        ]
        return len(matching) >= node["min_count"] and (
            node["max_count"] is None or len(matching) <= node["max_count"]
        )
    if kind == "policy_bool_eq":
        return policy[node["field"]] is node["value"]
    if kind == "policy_int_gte":
        return policy[node["field"]] >= node["value"]
    if kind == "policy_set_contains_all":
        return set(node["values"]).issubset(policy[node["field"]])
    raise AssertionError(f"unvalidated predicate kind: {kind}")


def test_predicate_fixture_is_frozen_and_complete() -> None:
    corpus = _load(PREDICATE_VECTORS)

    assert hashlib.sha256(PREDICATE_VECTORS.read_bytes()).hexdigest() == PREDICATE_VECTORS_FILE_SHA256
    assert corpus["schema_version"] == "bb.rl.predicate-vectors.v1"
    assert corpus["limits"] == {"maximum_depth": 8, "maximum_nodes_per_candidate": 64}
    assert len(corpus["valid_vectors"]) == 23
    assert len(corpus["canonical_order_vectors"]) == 2
    assert len(corpus["invalid_vectors"]) == 25
    assert len(corpus["missing_policy_fact_vectors"]) == 3
    valid_ids = {case["case_id"] for case in corpus["valid_vectors"]}
    assert {
        "all_true",
        "all_false",
        "any_true",
        "any_false",
        "task_label_eq_true",
        "task_label_eq_false",
        "task_label_eq_missing",
        "task_label_in_true",
        "task_label_in_false",
        "task_label_in_missing",
        "artifact_role_min_true",
        "artifact_role_missing_false",
        "artifact_role_max_false",
        "artifact_role_media_true",
        "artifact_role_media_false",
        "policy_bool_eq_true",
        "policy_bool_eq_false",
        "policy_int_gte_true",
        "policy_int_gte_false",
        "policy_set_contains_all_true",
        "policy_set_contains_all_false",
        "maximum_depth_8",
        "maximum_nodes_64",
    } == valid_ids


@pytest.mark.parametrize("vector_index", range(23))
def test_every_valid_predicate_matches_independent_fixture(vector_index: int) -> None:
    corpus = _load(PREDICATE_VECTORS)
    case = corpus["valid_vectors"][vector_index]
    independent_validate_predicates(case["predicates"])

    canonical = independent_canonical_predicates(case["predicates"])
    canonical_bytes = independent_jcs_bytes(canonical)
    assert canonical == case["expected"]["canonical_predicates"], case["case_id"]
    assert canonical_bytes.hex() == case["expected"]["canonical_hex"], case["case_id"]
    assert independent_digest(canonical) == case["expected"]["digest"], case["case_id"]
    assert independent_evaluate_predicate(case["predicates"][0], corpus["facts"]) is case["expected"]["result"]


@pytest.mark.parametrize("vector_index", range(23))
def test_runtime_predicate_evaluation_matches_every_independent_vector(
    vector_index: int,
) -> None:
    case = _load(PREDICATE_VECTORS)["valid_vectors"][vector_index]
    fixture = _resolution_fixture(candidate_count=2)
    assert isinstance(fixture.selector, c.ConfigSetManifest)
    target = c.ConfigCandidate.model_validate(
        {
            "candidate_id": "a",
            "receipt_digest": fixture.base_receipt_ref.digest,
            "predicates": case["expected"]["canonical_predicates"],
            "overlays": [],
        }
    )
    control = c.ConfigCandidate(
        candidate_id="b",
        receipt_digest=fixture.base_receipt_ref.digest,
        predicates=(
            c.PolicyBoolEq(field=c.PolicyBoolField.CANCELLATION, value=True),
        ),
        overlays=(),
    )
    selector = c.ConfigSetManifest(
        **{
            **fixture.selector.model_dump(mode="json", exclude={"candidates"}),
            "candidates": [
                c.WeightedCandidate(candidate=target, weight=2).model_dump(mode="json"),
                c.WeightedCandidate(candidate=control, weight=1).model_dump(mode="json"),
            ],
        }
    )
    fixture = _with_selector(fixture, selector)

    resolved = fixture.runtime.resolve_episode(fixture.request)
    record = c.SelectionRecord.model_validate_json(
        fixture.store.records[resolved.selection_record_ref.sha256]
    )
    evaluation = next(item for item in record.candidate_evaluations if item.candidate_id == "a")
    expected_result = case["expected"]["result"]
    assert evaluation.eligible is expected_result, case["case_id"]
    if expected_result:
        assert evaluation.exclusion_codes == ()
        assert "a" in {item.candidate_id for item in record.eligible_candidates}
    else:
        root_kind = case["predicates"][0]["kind"]
        expected_code = {
            "all": "all_false",
            "any": "any_false",
            "task_label_eq": "task_label_eq_false",
            "task_label_in": "task_label_in_false",
            "artifact_role_present": "artifact_role_present_false",
            "policy_bool_eq": "policy_bool_eq_false",
            "policy_int_gte": "policy_int_gte_false",
            "policy_set_contains_all": "policy_set_contains_all_false",
        }[root_kind]
        assert evaluation.exclusion_codes == (expected_code,)
        assert "a" not in {item.candidate_id for item in record.eligible_candidates}
    fixture.effects.assert_zero()


@pytest.mark.parametrize("vector_index", range(23))
def test_production_candidate_accepts_every_independently_canonical_predicate(
    vector_index: int,
) -> None:
    case = _load(PREDICATE_VECTORS)["valid_vectors"][vector_index]
    candidate = c.ConfigCandidate.model_validate(
        {
            "candidate_id": "candidate-a",
            "receipt_digest": "sha256:" + "4" * 64,
            "predicates": case["expected"]["canonical_predicates"],
            "overlays": [],
        }
    )

    assert candidate.to_canonical_obj()["predicates"] == case["expected"]["canonical_predicates"]


@pytest.mark.parametrize("vector_index", range(25))
def test_every_malformed_predicate_rejects_the_entire_set(vector_index: int) -> None:
    case = _load(PREDICATE_VECTORS)["invalid_vectors"][vector_index]
    assert case["expected"] == {"stage": "selector_validation", "code": "invalid_predicate"}
    with pytest.raises(ValueError):
        independent_validate_predicates(case["predicates"])


@pytest.mark.parametrize("vector_index", range(25))
def test_production_candidate_rejects_every_malformed_predicate(
    vector_index: int,
) -> None:
    case = _load(PREDICATE_VECTORS)["invalid_vectors"][vector_index]
    with pytest.raises(ValidationError):
        c.ConfigCandidate.model_validate(
            {
                "candidate_id": "candidate-a",
                "receipt_digest": "sha256:" + "4" * 64,
                "predicates": case["predicates"],
                "overlays": [],
            }
        )


@pytest.mark.parametrize("vector_index", range(2))
def test_commutative_predicate_order_has_one_canonical_identity(vector_index: int) -> None:
    case = _load(PREDICATE_VECTORS)["canonical_order_vectors"][vector_index]
    if case["scope"] == "children":
        left = independent_canonical_predicate(case["left"])
        right = independent_canonical_predicate(case["right"])
    else:
        left = independent_canonical_predicates(case["left"])
        right = independent_canonical_predicates(case["right"])
    assert left == right == case["expected_canonical"]
    canonical = independent_jcs_bytes(left)
    assert canonical.hex() == case["canonical_hex"]
    assert "sha256:" + hashlib.sha256(canonical).hexdigest() == case["digest"]


@pytest.mark.parametrize(
    ("vector_index", "ordering"),
    [(0, "left"), (0, "right"), (1, "left"), (1, "right")],
)
def test_production_rejects_noncanonical_predicate_order(
    vector_index: int,
    ordering: str,
) -> None:
    case = _load(PREDICATE_VECTORS)["canonical_order_vectors"][vector_index]
    raw = case[ordering]
    predicates = [raw] if case["scope"] == "children" else raw

    with pytest.raises(ValidationError):
        c.ConfigCandidate.model_validate(
            {
                "candidate_id": "candidate-a",
                "receipt_digest": "sha256:" + "4" * 64,
                "predicates": predicates,
                "overlays": [],
            }
        )


@pytest.mark.parametrize(
    "predicates",
    [
        [
            {"kind": "task_label_eq", "key": "tier", "value": "gold"},
            {"kind": "task_label_eq", "key": "tier", "value": "gold"},
        ],
        [
            {
                "kind": "all",
                "children": [
                    {"kind": "task_label_eq", "key": "tier", "value": "gold"},
                    {"kind": "task_label_eq", "key": "tier", "value": "gold"},
                ],
            }
        ],
    ],
)
def test_production_rejects_duplicate_predicate_nodes(predicates: list[dict[str, Any]]) -> None:
    with pytest.raises(ValidationError):
        c.ConfigCandidate.model_validate(
            {
                "candidate_id": "candidate-a",
                "receipt_digest": "sha256:" + "4" * 64,
                "predicates": predicates,
                "overlays": [],
            }
        )


@pytest.mark.parametrize("vector_index", range(3))
def test_missing_policy_fact_is_malformed_observation_not_false(vector_index: int) -> None:
    corpus = _load(PREDICATE_VECTORS)
    case = corpus["missing_policy_fact_vectors"][vector_index]
    facts = json.loads(json.dumps(corpus["facts"]))
    del facts["policy"][case["remove_policy_field"]]
    independent_validate_predicates(case["predicates"])

    with pytest.raises(KeyError):
        independent_evaluate_predicate(case["predicates"][0], facts)
    assert case["expected"]["stage"] == "policy_observation"
    assert case["expected"]["code"] == "observation_unavailable"



@pytest.mark.parametrize("derived", [False, True], ids=["base", "derived"])
@pytest.mark.parametrize(
    "field",
    [
        "compiler_abi",
        "runtime_abi",
        "task_contract",
        "task_binding",
        "policy",
        "ceiling",
        "registry",
        "revocation",
    ],
)
def test_unused_heterogeneous_root_receipt_rejects_whole_selector_before_binding(
    field: str,
    derived: bool,
) -> None:
    fixture = _resolution_fixture()
    mismatched = _root_receipt_mutation(fixture, field, derived=derived)
    fixture = _with_admitted_set(
        fixture,
        receipt_digests=(fixture.base_receipt_ref.digest, mismatched.digest),
    )

    with pytest.raises(c.ConfigRuntimeDenial) as caught:
        fixture.runtime.resolve_episode(fixture.request)

    assert caught.value.stage.value == "selector_validation"
    assert caught.value.artifact_digest == mismatched.digest
    assert fixture.store.bindings == {}
    assert fixture.policy_registry.calls == []
    assert not any(
        kind is c.ArtifactKind.SELECTION_RECORD
        for kind, _ in fixture.store.publish_calls
    )
    fixture.effects.assert_zero()


def _format_utc_second(value: datetime) -> str:
    return value.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _window_matrix_fixture(
    reference: str,
) -> tuple[ResolutionFixture, c.ValidityWindow]:
    fixture = _resolution_fixture()
    base = c.AdmissionReceipt.model_validate_json(
        fixture.store.records[fixture.base_receipt_ref.digest]
    )
    if reference == "policy":
        governing = fixture.admission.policy.validity
        payload = base.to_canonical_obj()
        payload["validity"] = governing.model_dump(mode="json")
        widened = _resign_receipt_payload(fixture, payload)
        fixture = _with_admitted_set(
            fixture,
            receipt_digests=(widened.digest,),
            root_validity=governing,
            selector_validity=governing,
            candidate_receipt_digest=widened.digest,
        )
        return fixture, governing
    if reference == "base_receipt":
        fixture = _with_admitted_set(
            fixture,
            receipt_digests=(fixture.base_receipt_ref.digest,),
            root_validity=base.validity,
            selector_validity=base.validity,
        )
        return fixture, base.validity
    if reference == "every_receipt":
        governing = c.ValidityWindow(
            issued_at="2026-07-10T12:00:00Z",
            not_before="2026-07-10T12:00:00Z",
            expires_at="2026-07-10T12:59:59Z",
        )
        payload = base.to_canonical_obj()
        payload["validity"] = governing.model_dump(mode="json")
        narrower = _resign_receipt_payload(fixture, payload)
        fixture = _with_admitted_set(
            fixture,
            receipt_digests=(fixture.base_receipt_ref.digest, narrower.digest),
            root_validity=governing,
            selector_validity=governing,
        )
        return fixture, governing
    raise AssertionError(reference)


@pytest.mark.parametrize(
    "reference",
    ["policy", "base_receipt", "every_receipt"],
)
@pytest.mark.parametrize("artifact", ["selector", "root"])
@pytest.mark.parametrize("edge", ["not_before", "expires_at"])
@pytest.mark.parametrize("overrun_seconds", [0, 1], ids=["exact", "one_second_overrun"])
def test_selector_and_root_windows_are_contained_by_policy_and_every_receipt(
    reference: str,
    artifact: str,
    edge: str,
    overrun_seconds: int,
) -> None:
    fixture, governing = _window_matrix_fixture(reference)
    selector_validity = governing
    root_validity = governing
    if overrun_seconds:
        payload = governing.model_dump(mode="json")
        current = datetime.fromisoformat(payload[edge].replace("Z", "+00:00"))
        delta = timedelta(seconds=-1 if edge == "not_before" else 1)
        payload[edge] = _format_utc_second(current + delta)
        if edge == "not_before":
            issued = datetime.fromisoformat(payload["issued_at"].replace("Z", "+00:00"))
            if issued > current + delta:
                payload["issued_at"] = _format_utc_second(current + delta)
        changed = c.ValidityWindow.model_validate(payload)
        if artifact == "selector":
            selector_validity = changed
        else:
            root_validity = changed
    fixture = _with_admitted_set(
        fixture,
        receipt_digests=fixture.admitted_set.receipt_digests,
        root_validity=root_validity,
        selector_validity=selector_validity,
        candidate_receipt_digest=(
            fixture.selector.candidate.receipt_digest
            if isinstance(fixture.selector, c.DirectSelector)
            else fixture.selector.candidates[0].candidate.receipt_digest
        ),
    )

    if overrun_seconds == 0:
        fixture.runtime.resolve_episode(fixture.request)
        assert len(fixture.store.bindings) == 1
    else:
        with pytest.raises(c.ConfigRuntimeDenial) as caught:
            fixture.runtime.resolve_episode(fixture.request)
        assert caught.value.stage.value == "selector_validation"
        assert caught.value.pointer == f"/{'selector' if artifact == 'selector' else 'admitted_set'}/validity"
        assert fixture.store.bindings == {}
    fixture.effects.assert_zero()
