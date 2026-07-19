from __future__ import annotations

import copy
import hashlib
import json
import multiprocessing
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from breadboard.rl.harness import contracts as c
from breadboard.rl.harness.config_runtime import ConfigRuntime
from tests.rl.harness.test_config_admission import (
    PrivilegedEffectProbe,
    RecordingReceiptStore,
    RecordingRevocations,
    _admission_fixture,
    _d,
    _frozen_denial,
    _rebind_registry_payload,
    _runtime_like,
    independent_digest,
    independent_jcs_bytes,
)
from tests.rl.harness.test_config_overlays import (
    _resolution_with_candidate_and_episode_overlay,
)
from tests.rl.harness.test_config_selection import _resolution_fixture


def _ref_for(payload: bytes) -> c.AdmissionReceiptRef:
    digest = "sha256:" + hashlib.sha256(payload).hexdigest()
    return c.AdmissionReceiptRef(
        digest=digest,
        ref=c.ArtifactRef(
            artifact_id=digest,
            sha256=digest,
            size_bytes=len(payload),
            media_type="application/vnd.breadboard.admission-receipt+json;version=1",
        ),
    )


def _store_payload(store: RecordingReceiptStore, payload: bytes) -> c.AdmissionReceiptRef:
    ref = _ref_for(payload)
    store.records[ref.digest] = payload
    return ref


def _denial(
    callable_: Any,
    *,
    stage: str,
    code: str,
    pointer: str | None,
    effects: PrivilegedEffectProbe,
) -> c.ConfigRuntimeDenial:
    with pytest.raises(c.ConfigRuntimeDenial) as caught:
        callable_()
    denial = caught.value
    assert denial.stage.value == stage
    assert denial.code.value == code
    assert denial.pointer == pointer
    effects.assert_zero()
    return denial


def _spawn_receipt(queue: Any) -> None:
    fixture = _admission_fixture()
    receipt_ref = fixture.runtime.admit(fixture.request)
    queue.put((receipt_ref.digest, fixture.store.records[receipt_ref.digest]))


def test_fixed_inputs_publish_identical_receipt_bytes_in_repeated_and_fresh_runtimes() -> None:
    first = _admission_fixture()
    second = _admission_fixture()

    first_ref = first.runtime.admit(first.request)
    first_retry_ref = first.runtime.admit(first.request)
    second_ref = second.runtime.admit(second.request)

    assert first_ref == first_retry_ref == second_ref
    assert first.store.records[first_ref.digest] == second.store.records[second_ref.digest]
    assert first.store.publish_calls[0][1] == first.store.publish_calls[1][1]

    context = multiprocessing.get_context("spawn")
    queue = context.Queue()
    processes = [context.Process(target=_spawn_receipt, args=(queue,)) for _ in range(2)]
    for process in processes:
        process.start()
    results = [queue.get(timeout=20) for _ in processes]
    for process in processes:
        process.join(timeout=20)
        assert process.exitcode == 0
    assert results[0] == results[1] == (
        first_ref.digest,
        first.store.records[first_ref.digest],
    )


@pytest.mark.parametrize(
    ("mode", "code"),
    [
        ("fail_publish", "receipt_store_unavailable"),
        ("conflicting_ref", "receipt_store_conflict"),
        ("fail_load", "receipt_store_unavailable"),
        ("corrupt_readback", "receipt_readback_mismatch"),
    ],
)
def test_receipt_publication_failure_conflict_and_corruption_are_typed(
    mode: str,
    code: str,
) -> None:
    effects = PrivilegedEffectProbe()
    store = RecordingReceiptStore()
    setattr(store, mode, True)
    fixture = _admission_fixture(store=store)

    denial = _denial(
        lambda: fixture.runtime.admit(fixture.request),
        stage="receipt_publication",
        code=code,
        pointer=None,
        effects=effects,
    )

    assert denial.policy_digest == fixture.policy.canonical_digest()
    assert store.publish_calls
    if mode == "fail_publish":
        assert store.load_calls == []
    else:
        assert len(store.publish_calls) == 1


def test_receipt_publication_is_content_addressed_and_read_back_before_return() -> None:
    effects = PrivilegedEffectProbe()
    fixture = _admission_fixture()

    receipt_ref = fixture.runtime.admit(fixture.request)

    kind, payload = fixture.store.publish_calls[0]
    assert kind is c.ArtifactKind.ADMISSION_RECEIPT
    assert fixture.store.load_calls == [
        (receipt_ref.digest, c.ArtifactKind.ADMISSION_RECEIPT, 4 * 1024 * 1024)
    ]
    assert receipt_ref.digest == "sha256:" + hashlib.sha256(payload).hexdigest()
    assert receipt_ref.ref.artifact_id == receipt_ref.ref.sha256 == receipt_ref.digest
    assert receipt_ref.ref.size_bytes == len(payload)
    assert c.AdmissionReceipt.model_validate_json(payload).canonical_bytes() == payload
    effects.assert_zero()


@pytest.mark.parametrize(
    ("now", "code", "pointer"),
    [
        (datetime(2026, 7, 10, 11, 59, 59, tzinfo=UTC), "receipt_not_yet_valid", "/validity/not_before"),
        (datetime(2026, 7, 10, 13, 0, 0, tzinfo=UTC), "receipt_expired", "/validity/expires_at"),
        (datetime(2026, 7, 10, 13, 0, 1, tzinfo=UTC), "receipt_expired", "/validity/expires_at"),
    ],
)
def test_receipt_validity_uses_closed_interval_then_exact_expiry(
    now: datetime,
    code: str,
    pointer: str,
) -> None:
    effects = PrivilegedEffectProbe()
    fixture = _admission_fixture()
    receipt_ref = fixture.runtime.admit(fixture.request)
    fixture.clock.value = now

    denial = _denial(
        lambda: fixture.runtime.verify_receipt(
            receipt_ref,
            subject=fixture.request.subject,
            checkpoint=c.PrivilegedCheckpoint.BEFORE_ALLOCATION,
        ),
        stage="receipt_recheck",
        code=code,
        pointer=pointer,
        effects=effects,
    )

    assert denial.artifact_digest == receipt_ref.digest


@pytest.mark.parametrize(
    ("epoch", "state_digest", "code", "pointer"),
    [
        (8, _d("3"), "receipt_revoked", "/revocation/epoch"),
        (6, _d("3"), "receipt_epoch_rollback", "/revocation/epoch"),
        (7, _d("f"), "receipt_revoked", "/revocation/state_digest"),
    ],
)
def test_receipt_revocation_requires_exact_monotonic_epoch_and_state(
    epoch: int,
    state_digest: str,
    code: str,
    pointer: str,
) -> None:
    effects = PrivilegedEffectProbe()
    fixture = _admission_fixture()
    receipt_ref = fixture.runtime.admit(fixture.request)
    revocations = RecordingRevocations(
        c.RevocationBinding(scope_digest=_d("1"), epoch=epoch, state_digest=state_digest)
    )
    runtime = _runtime_like(fixture, revocations=revocations)

    _denial(
        lambda: runtime.verify_receipt(
            receipt_ref,
            subject=fixture.request.subject,
            checkpoint=c.PrivilegedCheckpoint.BEFORE_ALLOCATION,
        ),
        stage="receipt_recheck",
        code=code,
        pointer=pointer,
        effects=effects,
    )


@pytest.mark.parametrize(
    ("mutation", "code", "pointer"),
    [
        ({"admission_policy_digest": _d("f")}, "receipt_stale_policy", "/admission_policy_digest"),
        ({"registry_snapshot_digest": _d("f")}, "receipt_stale_policy", "/registry_snapshot_digest"),
        ({"task_binding_digest": _d("f")}, "receipt_task_mismatch", "/task_binding_digest"),
        ({"operator_ceiling_digest": _d("f")}, "receipt_stale_policy", "/operator_ceiling_digest"),
    ],
)
def test_forged_or_stale_receipt_bindings_never_fallback(
    mutation: dict[str, str],
    code: str,
    pointer: str,
) -> None:
    effects = PrivilegedEffectProbe()
    fixture = _admission_fixture()
    receipt_ref = fixture.runtime.admit(fixture.request)
    payload = c.AdmissionReceipt.model_validate_json(
        fixture.store.records[receipt_ref.digest]
    ).to_canonical_obj()
    payload.update(mutation)
    # Some cross-field mutations are intentionally invalid even as a receipt
    # model. Publish the exact canonical JSON so verify_receipt must reject it
    # as forged rather than trusting a copied digest string.
    try:
        mutated = c.AdmissionReceipt.model_validate(payload).canonical_bytes()
    except Exception:
        import json

        mutated = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
        code = "receipt_forged"
        pointer = None
    mutated_ref = _store_payload(fixture.store, mutated)

    _denial(
        lambda: fixture.runtime.verify_receipt(
            mutated_ref,
            subject=fixture.request.subject,
            checkpoint=c.PrivilegedCheckpoint.BEFORE_ALLOCATION,
        ),
        stage="receipt_recheck",
        code=code,
        pointer=pointer,
        effects=effects,
    )


def test_receipt_abi_downgrade_matches_frozen_recheck_disposition() -> None:
    effects = PrivilegedEffectProbe()
    fixture = _admission_fixture()
    receipt_ref = fixture.runtime.admit(fixture.request)
    payload = c.AdmissionReceipt.model_validate_json(
        fixture.store.records[receipt_ref.digest]
    ).to_canonical_obj()
    payload["compiled"]["compiler"]["runtime_abi"] = "breadboard.conductor.v0"
    mutated = independent_jcs_bytes(payload)
    mutated_ref = _store_payload(fixture.store, mutated)

    denial = _denial(
        lambda: fixture.runtime.verify_receipt(
            mutated_ref,
            subject=fixture.request.subject,
            checkpoint=c.PrivilegedCheckpoint.BEFORE_ALLOCATION,
        ),
        stage="receipt_recheck",
        code="receipt_forged",
        pointer=None,
        effects=effects,
    )
    expected = _frozen_denial("forged_receipt")
    assert denial.policy_digest == expected["policy_digest"]
    assert denial.schema_digest == expected["schema_digest"]


def test_cross_subject_and_missing_or_copied_digest_receipts_are_forged() -> None:
    effects = PrivilegedEffectProbe()
    fixture = _admission_fixture()
    receipt_ref = fixture.runtime.admit(fixture.request)
    other_subject = c.AuthenticatedSubject(
        tenant_id="tenant-b",
        principal_id="principal-b",
        authority_scope_digest=fixture.request.subject.authority_scope_digest,
    )

    _denial(
        lambda: fixture.runtime.verify_receipt(
            receipt_ref,
            subject=other_subject,
            checkpoint=c.PrivilegedCheckpoint.EPISODE_PREFLIGHT,
        ),
        stage="receipt_recheck",
        code="receipt_cross_subject",
        pointer="/subject",
        effects=effects,
    )

    missing = c.AdmissionReceiptRef(
        digest=_d("f"),
        ref=c.ArtifactRef(
            artifact_id=_d("f"),
            sha256=_d("f"),
            size_bytes=1,
            media_type="application/vnd.breadboard.admission-receipt+json;version=1",
        ),
    )
    _denial(
        lambda: fixture.runtime.verify_receipt(
            missing,
            subject=fixture.request.subject,
            checkpoint=c.PrivilegedCheckpoint.EPISODE_PREFLIGHT,
        ),
        stage="receipt_recheck",
        code="receipt_forged",
        pointer=None,
        effects=effects,
    )


def test_receipt_load_is_bounded_before_parsing() -> None:
    effects = PrivilegedEffectProbe()
    fixture = _admission_fixture()
    payload = b"x" * (4 * 1024 * 1024 + 1)
    forged_ref = _store_payload(fixture.store, payload)

    _denial(
        lambda: fixture.runtime.verify_receipt(
            forged_ref,
            subject=fixture.request.subject,
            checkpoint=c.PrivilegedCheckpoint.EPISODE_PREFLIGHT,
        ),
        stage="receipt_recheck",
        code="receipt_forged",
        pointer=None,
        effects=effects,
    )
    assert fixture.store.load_calls[-1][2] == 4 * 1024 * 1024


def test_caller_cannot_backdate_receipt_verification_past_trusted_clock_expiry() -> None:
    effects = PrivilegedEffectProbe()
    fixture = _admission_fixture()
    receipt_ref = fixture.runtime.admit(fixture.request)
    fixture.clock.value = datetime(2026, 7, 10, 13, 0, 1, tzinfo=UTC)
    clock_calls = fixture.clock.calls

    with pytest.raises(TypeError, match="unexpected keyword argument 'now'"):
        fixture.runtime.verify_receipt(
            receipt_ref,
            subject=fixture.request.subject,
            checkpoint=c.PrivilegedCheckpoint.BEFORE_ALLOCATION,
            now=datetime(2026, 7, 10, 12, 30, tzinfo=UTC),
        )
    assert fixture.clock.calls == clock_calls

    _denial(
        lambda: fixture.runtime.verify_receipt(
            receipt_ref,
            subject=fixture.request.subject,
            checkpoint=c.PrivilegedCheckpoint.BEFORE_ALLOCATION,
        ),
        stage="receipt_recheck",
        code="receipt_expired",
        pointer="/validity/expires_at",
        effects=effects,
    )
    assert fixture.clock.calls == clock_calls + 1


_AUTHORITATIVE_RECEIPT_FIELDS = (
    "schema_version",
    "subject",
    "admission_request_digest",
    "behavior_source",
    "compiled",
    "admission_policy_id",
    "admission_policy_revision",
    "admission_policy_digest",
    "operator_ceiling_digest",
    "registry_snapshot_digest",
    "requested_capability_digest",
    "effective_capabilities",
    "effective_capability_digest",
    "capability_deltas",
    "pins",
    "mutable_pointer_policy_digest",
    "policy_binding_ref",
    "task_binding_digest",
    "decision",
    "reason_codes",
    "validity",
    "revocation",
    "parent_receipt_digest",
    "overlay_chain_digest",
)


def _mutate_first_receipt_leaf(value: Any) -> Any:
    if value is None:
        return _d("f")
    if type(value) is bool:
        return not value
    if type(value) is int:
        return value + 1
    if type(value) is str:
        if value.startswith("sha256:"):
            return _d("f") if value != _d("f") else _d("e")
        if value.endswith("Z"):
            return "2026-07-10T12:59:59Z"
        return value + "-mutated"
    if type(value) is list:
        if not value:
            return ["signed-field-mutation"]
        changed = copy.deepcopy(value)
        changed[0] = _mutate_first_receipt_leaf(changed[0])
        return changed
    if type(value) is dict:
        changed = copy.deepcopy(value)
        key = next(key for key in sorted(changed) if key != "schema_version")
        changed[key] = _mutate_first_receipt_leaf(changed[key])
        return changed
    raise TypeError(f"unsupported receipt mutation value {type(value).__name__}")


def _independent_unsigned_receipt_bytes(payload: dict[str, Any]) -> bytes:
    projection = copy.deepcopy(payload)
    attestation = projection["issuance_attestation"]
    projection["issuance_attestation"] = {
        "key_id": attestation["key_id"],
        "algorithm": attestation["algorithm"],
    }
    return independent_jcs_bytes(projection)


@pytest.mark.parametrize("field", _AUTHORITATIVE_RECEIPT_FIELDS)
def test_signed_issuance_equation_detects_mutation_of_every_authoritative_receipt_field(
    field: str,
) -> None:
    effects = PrivilegedEffectProbe()
    fixture = _admission_fixture()
    receipt_ref = fixture.runtime.admit(fixture.request)
    payload = c.AdmissionReceipt.model_validate_json(
        fixture.store.records[receipt_ref.digest]
    ).to_canonical_obj()
    unsigned = _independent_unsigned_receipt_bytes(payload)
    asserted_digest = "sha256:" + hashlib.sha256(unsigned).hexdigest()
    assert payload["issuance_attestation"]["signed_payload_digest"] == asserted_digest
    assert set(_AUTHORITATIVE_RECEIPT_FIELDS) == (
        set(c.AdmissionReceipt.model_fields) - {"issuance_attestation"}
    )

    mutated = copy.deepcopy(payload)
    mutated[field] = _mutate_first_receipt_leaf(mutated[field])
    mutated_unsigned = _independent_unsigned_receipt_bytes(mutated)
    assert mutated_unsigned != unsigned
    assert "sha256:" + hashlib.sha256(mutated_unsigned).hexdigest() != asserted_digest
    mutated_ref = _store_payload(fixture.store, independent_jcs_bytes(mutated))

    _denial(
        lambda: fixture.runtime.verify_receipt(
            mutated_ref,
            subject=fixture.request.subject,
            checkpoint=c.PrivilegedCheckpoint.BEFORE_ALLOCATION,
        ),
        stage="receipt_recheck",
        code="receipt_forged",
        pointer=None,
        effects=effects,
    )
    assert fixture.authenticator.verify_calls == []


def test_schema_valid_receipt_with_rehashed_payload_but_stale_signature_is_forged() -> None:
    effects = PrivilegedEffectProbe()
    fixture = _admission_fixture()
    receipt_ref = fixture.runtime.admit(fixture.request)
    payload = c.AdmissionReceipt.model_validate_json(
        fixture.store.records[receipt_ref.digest]
    ).to_canonical_obj()
    payload["admission_request_digest"] = _d("f")
    unsigned = _independent_unsigned_receipt_bytes(payload)
    payload["issuance_attestation"]["signed_payload_digest"] = (
        "sha256:" + hashlib.sha256(unsigned).hexdigest()
    )
    mutated = independent_jcs_bytes(payload)
    assert c.AdmissionReceipt.model_validate_json(mutated).canonical_bytes() == mutated
    mutated_ref = _store_payload(fixture.store, mutated)

    _denial(
        lambda: fixture.runtime.verify_receipt(
            mutated_ref,
            subject=fixture.request.subject,
            checkpoint=c.PrivilegedCheckpoint.BEFORE_ALLOCATION,
        ),
        stage="receipt_recheck",
        code="receipt_forged",
        pointer="/issuance_attestation",
        effects=effects,
    )
    assert fixture.authenticator.verify_calls[-1][0] == unsigned


@pytest.mark.parametrize(
    ("field", "replacement", "rehash_unsigned_payload", "pointer"),
    [
        ("key_id", "forged-receipt-key", True, "/issuance_attestation"),
        ("algorithm", "forged-sha256-v1", True, "/issuance_attestation"),
        ("signed_payload_digest", _d("f"), False, None),
        ("signature", "AA", False, "/issuance_attestation"),
    ],
)
def test_every_issuance_attestation_field_is_authenticated(
    field: str,
    replacement: str,
    rehash_unsigned_payload: bool,
    pointer: str | None,
) -> None:
    effects = PrivilegedEffectProbe()
    fixture = _admission_fixture()
    receipt_ref = fixture.runtime.admit(fixture.request)
    payload = c.AdmissionReceipt.model_validate_json(
        fixture.store.records[receipt_ref.digest]
    ).to_canonical_obj()
    payload["issuance_attestation"][field] = replacement
    if rehash_unsigned_payload:
        unsigned = _independent_unsigned_receipt_bytes(payload)
        payload["issuance_attestation"]["signed_payload_digest"] = (
            "sha256:" + hashlib.sha256(unsigned).hexdigest()
        )
    mutated = independent_jcs_bytes(payload)
    if pointer is not None:
        assert c.AdmissionReceipt.model_validate_json(mutated).canonical_bytes() == mutated
    mutated_ref = _store_payload(fixture.store, mutated)

    _denial(
        lambda: fixture.runtime.verify_receipt(
            mutated_ref,
            subject=fixture.request.subject,
            checkpoint=c.PrivilegedCheckpoint.BEFORE_ALLOCATION,
        ),
        stage="receipt_recheck",
        code="receipt_forged",
        pointer=pointer,
        effects=effects,
    )


@pytest.mark.parametrize("mode", ["sign", "verify"])
def test_raw_signer_failures_are_redacted_typed_and_have_zero_effects(mode: str) -> None:
    effects = PrivilegedEffectProbe()
    fixture = _admission_fixture()
    if mode == "sign":
        fixture.authenticator.fail_sign = True
        denial = _denial(
            lambda: fixture.runtime.admit(fixture.request),
            stage="receipt_publication",
            code="receipt_store_unavailable",
            pointer="/issuance_attestation",
            effects=effects,
        )
        assert fixture.store.publish_calls == []
        assert fixture.store.records == {}
    else:
        receipt_ref = fixture.runtime.admit(fixture.request)
        fixture.authenticator.fail_verify = True
        denial = _denial(
            lambda: fixture.runtime.verify_receipt(
                receipt_ref,
                subject=fixture.request.subject,
                checkpoint=c.PrivilegedCheckpoint.BEFORE_ALLOCATION,
            ),
            stage="receipt_recheck",
            code="receipt_forged",
            pointer="/issuance_attestation",
            effects=effects,
        )
        assert denial.schema_digest == _frozen_denial("forged_receipt")["schema_digest"]
    assert denial.policy_digest == fixture.policy.canonical_digest()
    assert "MARKER_SECRET" not in str(denial)
    assert b"MARKER_SECRET" not in denial.canonical_bytes()


@pytest.mark.parametrize(
    ("pointer", "replacement", "message"),
    [
        ("/currentness/admission_policy_digest", _d("f"), "receipt policy"),
        ("/currentness/registry_snapshot_digest", _d("f"), "registry snapshot"),
        ("/currentness/revocation_scope_digest", _d("f"), "revocation state"),
        ("/currentness/revocation_epoch", 8, "revocation state"),
        ("/currentness/revocation_state_digest", _d("f"), "revocation state"),
        ("/currentness/expires_at", "2026-07-10T12:59:59Z", "receipt expiry"),
        ("/currentness/verified_at", "2026-07-10T11:59:59Z", "validity"),
    ],
)
def test_verified_admission_rejects_copied_currentness_cross_fields(
    pointer: str,
    replacement: Any,
    message: str,
) -> None:
    effects = PrivilegedEffectProbe()
    fixture = _admission_fixture()
    receipt_ref = fixture.runtime.admit(fixture.request)
    verified = fixture.runtime.verify_receipt(
        receipt_ref,
        subject=fixture.request.subject,
        checkpoint=c.PrivilegedCheckpoint.BEFORE_ALLOCATION,
    )
    mutated = copy.deepcopy(verified.to_canonical_obj())
    tokens = pointer[1:].split("/")
    target = mutated
    for token in tokens[:-1]:
        target = target[token]
    target[tokens[-1]] = replacement

    with pytest.raises(ValidationError, match=message):
        c.VerifiedAdmission.model_validate(mutated)
    effects.assert_zero()


class ScopedRevocations:
    def __init__(self, bindings: dict[str, c.RevocationBinding]) -> None:
        self.bindings = dict(bindings)
        self.loads: list[str] = []

    def load(self, scope_digest: str) -> c.RevocationBinding:
        self.loads.append(scope_digest)
        return self.bindings[scope_digest]


def _attestation_bound_receipt(
    *,
    attestation_expires_at: str = "2026-07-10T12:45:00Z",
) -> tuple[
    Any,
    Any,
    c.AdmissionRequest,
    c.AdmissionReceiptRef,
    ScopedRevocations,
    c.RevocationBinding,
]:
    fixture = _admission_fixture()
    registry_payload = fixture.registries.to_canonical_obj()
    attestation = registry_payload["policy_capability_attestations"][0]
    attestation_scope = _d("2")
    attestation["validity"]["expires_at"] = attestation_expires_at
    attestation["revocation"] = {
        "scope_digest": attestation_scope,
        "epoch": 7,
        "state_digest": _d("3"),
    }
    attestation_projection = {
        "schema_version": "bb.rl.policy-capability-attestation.v1",
        **{
            key: value
            for key, value in attestation.items()
            if key != "attestation_digest"
        },
    }
    attestation["attestation_digest"] = independent_digest(attestation_projection)
    registries = c.RegistrySnapshotSet.model_validate(
        _rebind_registry_payload(registry_payload)
    )
    policy_payload = fixture.policy.to_canonical_obj()
    policy_payload["registry_digests"] = registries.digests.to_canonical_obj()
    policy = c.AdmissionPolicySnapshot.model_validate(policy_payload)
    request_payload = fixture.request.to_canonical_obj()
    request_payload["policy_binding_ref"]["attestation_digest"] = attestation[
        "attestation_digest"
    ]
    request_payload["policy_binding_ref"]["registry_revision_digest"] = (
        registries.digests.route_registry_digest
    )
    request_payload["registry_snapshot_digest"] = registries.digests.snapshot_digest
    request_payload["admission_policy_digest"] = policy.canonical_digest()
    request = c.AdmissionRequest.model_validate(request_payload)
    attestation_revocation = c.RevocationBinding.model_validate(attestation["revocation"])
    revocations = ScopedRevocations(
        {
            policy.revocation.scope_digest: policy.revocation,
            attestation_scope: attestation_revocation,
        }
    )
    runtime = _runtime_like(
        fixture,
        policy=policy,
        registries=registries,
        revocations=revocations,
    )
    receipt_ref = runtime.admit(request)
    return (
        fixture,
        runtime,
        request,
        receipt_ref,
        revocations,
        attestation_revocation,
    )


@pytest.mark.parametrize("checkpoint", list(c.PrivilegedCheckpoint))
def test_expired_bound_policy_attestation_denies_every_receipt_checkpoint(
    checkpoint: c.PrivilegedCheckpoint,
) -> None:
    effects = PrivilegedEffectProbe()
    fixture, runtime, request, receipt_ref, _, _ = _attestation_bound_receipt(
        attestation_expires_at="2026-07-10T12:15:00Z"
    )
    fixture.clock.value = datetime(2026, 7, 10, 12, 30, tzinfo=UTC)
    receipt = c.AdmissionReceipt.model_validate_json(fixture.store.records[receipt_ref.digest])
    assert receipt.validity.not_before <= "2026-07-10T12:30:00Z" < receipt.validity.expires_at
    publication_count = len(fixture.store.publish_calls)

    _denial(
        lambda: runtime.verify_receipt(
            receipt_ref,
            subject=request.subject,
            checkpoint=checkpoint,
        ),
        stage="receipt_recheck",
        code="attestation_expired",
        pointer="/policy_binding_ref/attestation_digest",
        effects=effects,
    )
    assert len(fixture.store.publish_calls) == publication_count


@pytest.mark.parametrize(
    ("mutation", "current"),
    [
        (
            "higher_epoch",
            c.RevocationBinding(scope_digest=_d("2"), epoch=8, state_digest=_d("3")),
        ),
        (
            "epoch_rollback",
            c.RevocationBinding(scope_digest=_d("2"), epoch=6, state_digest=_d("3")),
        ),
        (
            "scope_mismatch",
            c.RevocationBinding(scope_digest=_d("f"), epoch=7, state_digest=_d("3")),
        ),
    ],
)
def test_bound_policy_attestation_revocation_is_independently_rechecked(
    mutation: str,
    current: c.RevocationBinding,
) -> None:
    del mutation
    effects = PrivilegedEffectProbe()
    fixture, runtime, request, receipt_ref, revocations, admitted = (
        _attestation_bound_receipt()
    )
    revocations.bindings[admitted.scope_digest] = current
    publication_count = len(fixture.store.publish_calls)

    _denial(
        lambda: runtime.verify_receipt(
            receipt_ref,
            subject=request.subject,
            checkpoint=c.PrivilegedCheckpoint.BEFORE_ALLOCATION,
        ),
        stage="receipt_recheck",
        code="attestation_invalid",
        pointer="/policy_binding_ref/attestation_digest",
        effects=effects,
    )
    assert revocations.loads[-1] == admitted.scope_digest
    assert len(fixture.store.publish_calls) == publication_count


_WP4_OVERLAY_VECTORS = (
    Path(__file__).resolve().parents[2]
    / "fixtures"
    / "rl"
    / "config_runtime"
    / "overlay_vectors_v1.json"
)


def _wp4_effective_plan() -> c.EffectiveExecutionPlan:
    corpus = json.loads(_WP4_OVERLAY_VECTORS.read_text(encoding="utf-8"))
    return c.EffectiveExecutionPlan.model_validate(
        corpus["positive_chain"]["final_plan"]["payload"]
    )


def _artifact_ref(digest: str, payload_size: int, media_type: str) -> c.ArtifactRef:
    return c.ArtifactRef(
        artifact_id=digest,
        sha256=digest,
        size_bytes=payload_size,
        media_type=media_type,
    )


def _committed_resolved_plan() -> c.ResolvedEpisodePlan:
    plan = _wp4_effective_plan()
    binding = c.SelectionBinding(
        owner_key=independent_digest(
            {
                "schema_version": "bb.rl.selection-owner.v1",
                "subject_digest": plan.subject_digest,
                "episode_id": "episode-wp4",
            }
        ),
        request_digest=_d("8"),
        selection_record_digest=plan.selection_record_digest,
    )
    binding_bytes = binding.canonical_bytes()
    commit = c.SelectionCommitToken(
        binding=binding,
        binding_ref=_artifact_ref(
            binding.canonical_digest(),
            len(binding_bytes),
            "application/vnd.breadboard.selection-binding+json;version=1",
        ),
        verified_at="2026-07-10T12:00:00Z",
    )
    plan_bytes = plan.canonical_bytes()
    return c.ResolvedEpisodePlan(
        episode_id="episode-wp4",
        subject_digest=plan.subject_digest,
        base_receipt_digest=plan.base_receipt_digest,
        final_receipt_digest=plan.final_receipt_digest,
        policy_capability_observation_digest=plan.policy_capability_observation_digest,
        selection_record_ref=_artifact_ref(
            plan.selection_record_digest,
            1,
            "application/vnd.breadboard.selection-record+json;version=1",
        ),
        selection_commit=commit,
        effective_plan_ref=_artifact_ref(
            plan.canonical_digest(),
            len(plan_bytes),
            "application/vnd.breadboard.effective-execution-plan+json;version=1",
        ),
        effective_plan=plan,
        currentness=c.CurrentnessToken(
            receipt_digest=plan.final_receipt_digest,
            subject_digest=plan.subject_digest,
            admission_policy_digest=_d("5"),
            registry_snapshot_digest=_d("6"),
            revocation_scope_digest=plan.revocation.scope_digest,
            revocation_epoch=plan.revocation.epoch,
            revocation_state_digest=plan.revocation.state_digest,
            checkpoint=c.PrivilegedCheckpoint.BEFORE_ALLOCATION,
            verified_at="2026-07-10T12:00:01Z",
            expires_at="2026-07-10T13:00:00Z",
        ),
    )


def test_selection_owner_and_binding_are_canonical_write_once_identities() -> None:
    resolved = _committed_resolved_plan()
    owner_payload = {
        "schema_version": "bb.rl.selection-owner.v1",
        "subject_digest": resolved.subject_digest,
        "episode_id": resolved.episode_id,
    }
    binding = resolved.selection_commit.binding
    assert binding.owner_key == independent_digest(owner_payload)
    assert binding.selection_record_digest == resolved.selection_record_ref.sha256
    assert resolved.selection_commit.binding_ref.sha256 == independent_digest(
        binding.to_canonical_obj()
    )
    assert resolved.selection_commit.verified_at < resolved.currentness.verified_at


@pytest.mark.parametrize(
    "mutation",
    ["binding_ref", "selection_record_ref", "plan_ref", "subject", "receipt", "checkpoint"],
)
def test_resolved_plan_requires_binding_plan_readback_and_final_currentness(
    mutation: str,
) -> None:
    payload = _committed_resolved_plan().model_dump(mode="json")
    if mutation == "binding_ref":
        payload["selection_commit"]["binding_ref"]["artifact_id"] = _d("0")
        payload["selection_commit"]["binding_ref"]["sha256"] = _d("0")
    elif mutation == "selection_record_ref":
        payload["selection_record_ref"]["artifact_id"] = _d("9")
        payload["selection_record_ref"]["sha256"] = _d("9")
    elif mutation == "plan_ref":
        payload["effective_plan_ref"]["artifact_id"] = _d("0")
        payload["effective_plan_ref"]["sha256"] = _d("0")
    elif mutation == "subject":
        payload["subject_digest"] = _d("0")
    elif mutation == "receipt":
        payload["currentness"]["receipt_digest"] = _d("0")
    else:
        payload["currentness"]["checkpoint"] = c.PrivilegedCheckpoint.EPISODE_PREFLIGHT.value

    with pytest.raises(ValidationError):
        c.ResolvedEpisodePlan.model_validate(payload)


def test_effective_plan_is_read_back_by_exact_content_address_before_return() -> None:
    resolved = _committed_resolved_plan()
    canonical = resolved.effective_plan.canonical_bytes()
    assert resolved.effective_plan_ref.size_bytes == len(canonical)
    assert resolved.effective_plan_ref.sha256 == "sha256:" + hashlib.sha256(canonical).hexdigest()
    assert resolved.final_receipt_digest == resolved.effective_plan.final_receipt_digest
    assert resolved.currentness.checkpoint is c.PrivilegedCheckpoint.BEFORE_ALLOCATION


def _resolution_runtime_like(fixture: Any) -> ConfigRuntime:
    return ConfigRuntime(
        compiler=fixture.admission.compiler,
        policy=fixture.admission.policy,
        registries=fixture.admission.registries,
        revocations=fixture.admission.revocations,
        store=fixture.store,
        clock=fixture.admission.clock,
        authenticator=fixture.admission.authenticator,
        policy_capabilities=fixture.policy_registry,
    )


def _expect_resolution_denial(
    callable_: Any,
    *,
    stage: str,
    code: str,
) -> c.ConfigRuntimeDenial:
    with pytest.raises(c.ConfigRuntimeDenial) as caught:
        callable_()
    denial = caught.value
    assert denial.stage.value == stage
    assert denial.code.value == code
    return denial


def _published_count(store: Any, kind: c.ArtifactKind) -> int:
    return sum(published_kind is kind for published_kind, _ in store.publish_calls)


def test_resolution_publishes_reads_and_binds_record_before_overlay_and_plan(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixture, compiler, _ = _resolution_with_candidate_and_episode_overlay()
    events: list[tuple[str, c.ArtifactKind | None]] = []
    publish = fixture.store.publish
    load = fixture.store.load
    bind = fixture.store.bind_selection_once

    def recording_publish(*, kind: c.ArtifactKind, canonical_bytes: bytes) -> c.ArtifactRef:
        if kind in {
            c.ArtifactKind.SELECTION_RECORD,
            c.ArtifactKind.SELECTION_BINDING,
            c.ArtifactKind.EFFECTIVE_EXECUTION_PLAN,
        }:
            events.append(("publish", kind))
        return publish(kind=kind, canonical_bytes=canonical_bytes)

    def recording_load(
        digest: str,
        *,
        kind: c.ArtifactKind,
        max_bytes: int,
    ) -> bytes:
        if kind in {
            c.ArtifactKind.SELECTION_RECORD,
            c.ArtifactKind.SELECTION_BINDING,
            c.ArtifactKind.EFFECTIVE_EXECUTION_PLAN,
        }:
            events.append(("load", kind))
        return load(digest, kind=kind, max_bytes=max_bytes)

    def recording_bind(
        *,
        owner_key: str,
        request_digest: str,
        selection_record_digest: str,
    ) -> c.SelectionCommitToken:
        events.append(("bind", None))
        return bind(
            owner_key=owner_key,
            request_digest=request_digest,
            selection_record_digest=selection_record_digest,
        )

    monkeypatch.setattr(fixture.store, "publish", recording_publish)
    monkeypatch.setattr(fixture.store, "load", recording_load)
    monkeypatch.setattr(fixture.store, "bind_selection_once", recording_bind)

    resolved = fixture.runtime.resolve_episode(fixture.request)

    assert events == [
        ("publish", c.ArtifactKind.SELECTION_RECORD),
        ("load", c.ArtifactKind.SELECTION_RECORD),
        ("bind", None),
        ("publish", c.ArtifactKind.SELECTION_BINDING),
        ("publish", c.ArtifactKind.EFFECTIVE_EXECUTION_PLAN),
        ("load", c.ArtifactKind.EFFECTIVE_EXECUTION_PLAN),
    ]
    assert compiler.validation_commit_snapshots[0] == (0, 0, 0)
    assert compiler.validation_commit_snapshots[-1][1:] == (1, 0)
    assert resolved.selection_commit.binding.selection_record_digest == (
        resolved.selection_record_ref.sha256
    )
    fixture.effects.assert_zero()


def test_concurrent_retry_and_fresh_runtime_reuse_exact_selection_record() -> None:
    fixture = _resolution_fixture()
    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [
            executor.submit(fixture.runtime.resolve_episode, fixture.request)
            for _ in range(2)
        ]
        concurrent = [future.result(timeout=20) for future in futures]

    assert concurrent[0] == concurrent[1]
    first = concurrent[0]
    record_bytes = fixture.store.records[first.selection_record_ref.sha256]
    selection_publications = _published_count(fixture.store, c.ArtifactKind.SELECTION_RECORD)
    retry = fixture.runtime.resolve_episode(fixture.request)
    restarted = _resolution_runtime_like(fixture).resolve_episode(fixture.request)

    assert retry == restarted == first
    assert fixture.store.records[first.selection_record_ref.sha256] == record_bytes
    assert _published_count(fixture.store, c.ArtifactKind.SELECTION_RECORD) == selection_publications
    assert len(fixture.store.bindings) == 1
    assert {call[2] for call in fixture.store.bind_calls} == {
        first.selection_record_ref.sha256
    }
    fixture.effects.assert_zero()


def test_crash_after_record_publish_retries_same_record_without_partial_plan(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixture = _resolution_fixture()
    bind = fixture.store.bind_selection_once
    failed = False

    def crash_before_bind(
        *,
        owner_key: str,
        request_digest: str,
        selection_record_digest: str,
    ) -> c.SelectionCommitToken:
        nonlocal failed
        if not failed:
            failed = True
            raise OSError("crash before atomic owner bind")
        return bind(
            owner_key=owner_key,
            request_digest=request_digest,
            selection_record_digest=selection_record_digest,
        )

    monkeypatch.setattr(fixture.store, "bind_selection_once", crash_before_bind)
    denial = _expect_resolution_denial(
        lambda: fixture.runtime.resolve_episode(fixture.request),
        stage="selection_persistence",
        code="selection_store_unavailable",
    )
    assert denial.selection_record_digest is not None
    record_bytes = fixture.store.records[denial.selection_record_digest]
    assert fixture.store.bindings == {}
    assert _published_count(fixture.store, c.ArtifactKind.EFFECTIVE_EXECUTION_PLAN) == 0

    recovered = _resolution_runtime_like(fixture).resolve_episode(fixture.request)
    assert recovered.selection_record_ref.sha256 == denial.selection_record_digest
    assert fixture.store.records[recovered.selection_record_ref.sha256] == record_bytes
    assert len(fixture.store.bindings) == 1
    fixture.effects.assert_zero()


def test_lost_bind_ack_restart_loads_committed_record_without_redraw(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixture = _resolution_fixture()
    bind = fixture.store.bind_selection_once
    lost = False

    def bind_then_lose_ack(
        *,
        owner_key: str,
        request_digest: str,
        selection_record_digest: str,
    ) -> c.SelectionCommitToken:
        nonlocal lost
        token = bind(
            owner_key=owner_key,
            request_digest=request_digest,
            selection_record_digest=selection_record_digest,
        )
        if not lost:
            lost = True
            raise TimeoutError("commit succeeded but acknowledgement was lost")
        return token

    monkeypatch.setattr(fixture.store, "bind_selection_once", bind_then_lose_ack)
    recovered_from_lost_ack = fixture.runtime.resolve_episode(fixture.request)
    binding = next(iter(fixture.store.bindings.values()))
    assert binding.selection_record_digest == recovered_from_lost_ack.selection_record_ref.sha256
    record_bytes = fixture.store.records[binding.selection_record_digest]
    selection_publications = _published_count(fixture.store, c.ArtifactKind.SELECTION_RECORD)

    recovered_after_restart = _resolution_runtime_like(fixture).resolve_episode(fixture.request)
    assert recovered_after_restart == recovered_from_lost_ack
    assert fixture.store.records[binding.selection_record_digest] == record_bytes
    assert _published_count(fixture.store, c.ArtifactKind.SELECTION_RECORD) == selection_publications
    fixture.effects.assert_zero()


@pytest.mark.parametrize("change", ["nonce", "selector", "observation", "overlays"])
def test_bound_owner_rejects_changed_resolution_inputs_without_alternate(
    change: str,
) -> None:
    if change == "overlays":
        fixture, _, _ = _resolution_with_candidate_and_episode_overlay()
    else:
        fixture = _resolution_fixture()
    first = fixture.runtime.resolve_episode(fixture.request)
    request = fixture.request

    if change == "nonce":
        payload = request.model_dump(mode="json")
        payload["selection_nonce"] = _d("2")
        request = c.ResolveEpisodeRequest.model_validate(payload)
    elif change == "selector":
        assert isinstance(fixture.selector, c.ConfigSetManifest)
        selector_payload = fixture.selector.model_dump(mode="json")
        selector_payload["candidates"][0]["weight"] += 1
        selector = c.ConfigSetManifest.model_validate(selector_payload)
        selector_ref = fixture.store.publish(
            kind=c.ArtifactKind.CONFIG_SET,
            canonical_bytes=selector.canonical_bytes(),
        )
        payload = request.model_dump(mode="json")
        payload["selector"] = c.WeightedSelectorRef(
            digest=selector_ref.sha256,
            ref=selector_ref,
        ).to_canonical_obj()
        request = c.ResolveEpisodeRequest.model_validate(payload)
    elif change == "observation":
        observation_payload = fixture.policy_observation.model_dump(mode="json")
        observation_payload["provenance"]["evidence_digest"] = _d("0")
        fixture.policy_registry.observation = c.PolicyCapabilityObservation.model_validate(
            observation_payload
        )
    else:
        payload = request.model_dump(mode="json")
        payload["episode_overlays"] = []
        request = c.ResolveEpisodeRequest.model_validate(payload)

    denial = _expect_resolution_denial(
        lambda: fixture.runtime.resolve_episode(request),
        stage="selection_persistence",
        code="selection_idempotency_conflict",
    )
    assert denial.selection_record_digest == first.selection_record_ref.sha256
    assert len(fixture.store.bindings) == 1
    assert _published_count(fixture.store, c.ArtifactKind.SELECTION_RECORD) == 1
    fixture.effects.assert_zero()


def test_changed_task_digest_conflicts_at_atomic_owner_binding() -> None:
    fixture = _resolution_fixture()
    resolved = fixture.runtime.resolve_episode(fixture.request)
    binding = resolved.selection_commit.binding
    changed_task_payload = fixture.request.task.model_dump(mode="json")
    changed_task_payload["labels"][1]["value"] = "platinum"
    changed_task = c.TaskEligibilityInput.model_validate(changed_task_payload)
    changed_request_digest = independent_digest(
        {
            "schema_version": "bb.rl.selection-request-test.v1",
            "original_request_digest": binding.request_digest,
            "task_contract_digest": changed_task.canonical_digest(),
        }
    )

    with pytest.raises(RuntimeError, match="idempotency conflict"):
        fixture.store.bind_selection_once(
            owner_key=binding.owner_key,
            request_digest=changed_request_digest,
            selection_record_digest=binding.selection_record_digest,
        )
    assert len(fixture.store.bindings) == 1
    assert next(iter(fixture.store.bindings.values())) == binding
    fixture.effects.assert_zero()


@pytest.mark.parametrize("corruption", ["record_bytes", "record_pointer", "plan_readback"])
def test_committed_selection_corruption_denies_without_alternate(
    corruption: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixture = _resolution_fixture()
    first = fixture.runtime.resolve_episode(fixture.request)
    binding = first.selection_commit.binding

    if corruption == "record_bytes":
        payload = fixture.store.records[binding.selection_record_digest]
        fixture.store.records[binding.selection_record_digest] = payload[:-1] + b"!"
        expected_stage = "selection_persistence"
        expected_code = "selection_record_corrupt"
    elif corruption == "record_pointer":
        fixture.store.bindings[binding.owner_key] = c.SelectionBinding(
            owner_key=binding.owner_key,
            request_digest=binding.request_digest,
            selection_record_digest=_d("f"),
        )
        expected_stage = "selection_persistence"
        expected_code = "selection_record_corrupt"
    else:
        load = fixture.store.load

        def corrupt_plan_load(
            digest: str,
            *,
            kind: c.ArtifactKind,
            max_bytes: int,
        ) -> bytes:
            payload = load(digest, kind=kind, max_bytes=max_bytes)
            if kind is c.ArtifactKind.EFFECTIVE_EXECUTION_PLAN:
                return payload[:-1] + b"!"
            return payload

        monkeypatch.setattr(fixture.store, "load", corrupt_plan_load)
        expected_stage = "plan_publication"
        expected_code = "plan_readback_mismatch"

    denial = _expect_resolution_denial(
        lambda: _resolution_runtime_like(fixture).resolve_episode(fixture.request),
        stage=expected_stage,
        code=expected_code,
    )
    assert denial.selection_record_digest in {
        binding.selection_record_digest,
        _d("f"),
    }
    assert len(fixture.store.bindings) == 1
    fixture.effects.assert_zero()


@pytest.mark.parametrize(
    "corruption",
    [
        "selected_overlays",
        "selected_candidate",
        "selected_receipt",
        "draw",
        "weight",
        "evaluation",
    ],
)
def test_rehashed_bound_record_rejects_each_poisoned_selection_projection(
    corruption: str,
) -> None:
    if corruption == "selected_overlays":
        fixture, compiler, _ = _resolution_with_candidate_and_episode_overlay()
    else:
        fixture = _resolution_fixture(algorithm="weighted-v1", candidate_count=3)
        compiler = None
    first = fixture.runtime.resolve_episode(fixture.request)
    binding = first.selection_commit.binding
    record_payload = json.loads(
        fixture.store.records[binding.selection_record_digest].decode("utf-8")
    )
    validation_calls = (
        len(compiler.validated_semantics) if compiler is not None else None
    )
    plan_publications = _published_count(
        fixture.store,
        c.ArtifactKind.EFFECTIVE_EXECUTION_PLAN,
    )

    if corruption == "selected_overlays":
        assert record_payload["selected_overlays"]
        record_payload["selected_overlays"][0]["result_receipt_digest"] = (
            fixture.base_receipt_ref.digest
        )
    elif corruption == "selected_candidate":
        replacement = next(
            candidate["candidate_id"]
            for candidate in record_payload["eligible_candidates"]
            if candidate["candidate_id"] != record_payload["selected_candidate_id"]
        )
        record_payload["selected_candidate_id"] = replacement
    elif corruption == "selected_receipt":
        record_payload["selected_receipt_digest"] = _d("f")
    elif corruption == "draw":
        draw = record_payload["draw"]
        draw["modulo"] = (draw["modulo"] + 1) % draw["total_weight"]
    elif corruption == "weight":
        record_payload["candidate_evaluations"][0]["weight"] += 1
    else:
        evaluation = record_payload["candidate_evaluations"][0]
        evaluation["eligible"] = False
        evaluation["exclusion_codes"] = ["forged-evaluation"]

    poison_ref = fixture.store.publish(
        kind=c.ArtifactKind.SELECTION_RECORD,
        canonical_bytes=independent_jcs_bytes(record_payload),
    )
    fixture.store.bindings[binding.owner_key] = c.SelectionBinding(
        owner_key=binding.owner_key,
        request_digest=binding.request_digest,
        selection_record_digest=poison_ref.sha256,
    )

    denial = _expect_resolution_denial(
        lambda: _resolution_runtime_like(fixture).resolve_episode(fixture.request),
        stage="selection_persistence",
        code="selection_record_corrupt",
    )

    assert denial.selection_record_digest == poison_ref.sha256
    assert fixture.store.bindings[binding.owner_key].selection_record_digest == (
        poison_ref.sha256
    )
    assert _published_count(
        fixture.store,
        c.ArtifactKind.EFFECTIVE_EXECUTION_PLAN,
    ) == plan_publications
    if compiler is not None:
        assert len(compiler.validated_semantics) == validation_calls
    fixture.effects.assert_zero()


def test_plan_failure_retains_selection_and_retry_never_selects_alternate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixture = _resolution_fixture()
    publish = fixture.store.publish
    fail_plan = True

    def publish_with_plan_crash(
        *,
        kind: c.ArtifactKind,
        canonical_bytes: bytes,
    ) -> c.ArtifactRef:
        nonlocal fail_plan
        if kind is c.ArtifactKind.EFFECTIVE_EXECUTION_PLAN and fail_plan:
            fail_plan = False
            raise OSError("plan CAS unavailable")
        return publish(kind=kind, canonical_bytes=canonical_bytes)

    monkeypatch.setattr(fixture.store, "publish", publish_with_plan_crash)
    denial = _expect_resolution_denial(
        lambda: fixture.runtime.resolve_episode(fixture.request),
        stage="plan_publication",
        code="plan_store_unavailable",
    )
    assert denial.selection_record_digest is not None
    binding = next(iter(fixture.store.bindings.values()))
    assert binding.selection_record_digest == denial.selection_record_digest
    record_bytes = fixture.store.records[binding.selection_record_digest]

    recovered = _resolution_runtime_like(fixture).resolve_episode(fixture.request)
    assert recovered.selection_record_ref.sha256 == binding.selection_record_digest
    assert fixture.store.records[binding.selection_record_digest] == record_bytes
    assert len(fixture.store.bindings) == 1
    fixture.effects.assert_zero()


def test_revocation_after_selection_commit_retains_record_and_returns_no_plan(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixture = _resolution_fixture()
    publish = fixture.store.publish
    advanced = False

    def publish_then_revoke(
        *,
        kind: c.ArtifactKind,
        canonical_bytes: bytes,
    ) -> c.ArtifactRef:
        nonlocal advanced
        artifact = publish(kind=kind, canonical_bytes=canonical_bytes)
        if kind is c.ArtifactKind.EFFECTIVE_EXECUTION_PLAN and not advanced:
            advanced = True
            admitted = fixture.admission.revocations.current
            fixture.admission.revocations.current = c.RevocationBinding(
                scope_digest=admitted.scope_digest,
                epoch=admitted.epoch + 1,
                state_digest=admitted.state_digest,
            )
        return artifact

    monkeypatch.setattr(fixture.store, "publish", publish_then_revoke)
    with pytest.raises(c.ConfigRuntimeDenial) as caught:
        fixture.runtime.resolve_episode(fixture.request)

    assert caught.value.code is c.DenialCode.RECEIPT_REVOKED
    assert len(fixture.store.bindings) == 1
    binding = next(iter(fixture.store.bindings.values()))
    assert binding.selection_record_digest in fixture.store.records
    assert _published_count(fixture.store, c.ArtifactKind.SELECTION_RECORD) == 1
    fixture.effects.assert_zero()


def test_expiry_after_selection_commit_retains_record_and_returns_no_plan(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixture = _resolution_fixture()
    publish = fixture.store.publish
    advanced = False

    def publish_then_expire(
        *,
        kind: c.ArtifactKind,
        canonical_bytes: bytes,
    ) -> c.ArtifactRef:
        nonlocal advanced
        artifact = publish(kind=kind, canonical_bytes=canonical_bytes)
        if kind is c.ArtifactKind.EFFECTIVE_EXECUTION_PLAN and not advanced:
            advanced = True
            fixture.admission.clock.value = datetime(
                2026,
                7,
                10,
                13,
                0,
                1,
                tzinfo=UTC,
            )
        return artifact

    monkeypatch.setattr(fixture.store, "publish", publish_then_expire)
    denial = _expect_resolution_denial(
        lambda: fixture.runtime.resolve_episode(fixture.request),
        stage="pre_allocation_recheck",
        code="receipt_expired",
    )

    assert denial.pointer == "/selector/validity"
    assert denial.selection_record_digest is not None
    assert len(fixture.store.bindings) == 1
    binding = next(iter(fixture.store.bindings.values()))
    assert binding.selection_record_digest == denial.selection_record_digest
    assert _published_count(fixture.store, c.ArtifactKind.SELECTION_RECORD) == 1
    fixture.effects.assert_zero()
