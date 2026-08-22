from __future__ import annotations

import builtins
import hashlib
import json
import os
import random
import secrets
import socket
from dataclasses import dataclass, fields
from datetime import UTC, datetime
from typing import Any

import pytest
from pydantic import ValidationError

from breadboard.rl.harness import contracts as c


MAX_SAFE_INTEGER = 2**53 - 1
PRIVILEGED_EFFECT_NAMES = (
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


def _d(character: str) -> str:
    assert len(character) == 1 and character in "0123456789abcdef"
    return "sha256:" + character * 64


def independent_jcs_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def independent_digest(value: Any) -> str:
    return "sha256:" + hashlib.sha256(independent_jcs_bytes(value)).hexdigest()


def _capability_projection(
    *,
    protocol_abi: str,
    model_digest: str,
    tokenizer_digest: str,
    checkpoint_digest: str,
    capabilities: c.PolicyCapabilityVector,
) -> dict[str, Any]:
    return {
        "schema_version": "bb.rl.policy-selection-capabilities.v1",
        "protocol_abi": protocol_abi,
        "model_digest": model_digest,
        "tokenizer_digest": tokenizer_digest,
        "checkpoint_digest": checkpoint_digest,
        "capabilities": capabilities.model_dump(mode="json"),
    }


def _policy_capabilities(**updates: Any) -> c.PolicyCapabilityVector:
    payload = {
        "responses_protocol": "responses-v1",
        "modalities": ["text", "vision"],
        "tool_calling": True,
        "parallel_tool_calls": True,
        "token_ids": True,
        "token_logprobs": True,
        "routing_metadata": True,
        "cancellation": True,
        "max_context_tokens": 32_768,
        "max_output_tokens": 4_096,
        "policy_slot_count": 1,
        "request_features": ["json_mode", "seed"],
    }
    payload.update(updates)
    return c.PolicyCapabilityVector.model_validate(payload)


def _policy_observation(
    *,
    provenance_kind: str = "startup_probe",
    signer_key_id: str | None = "startup-probe-key",
    capabilities: c.PolicyCapabilityVector | None = None,
    **updates: Any,
) -> c.PolicyCapabilityObservation:
    vector = capabilities or _policy_capabilities()
    payload: dict[str, Any] = {
        "registry_revision_digest": _d("1"),
        "route_id": "policy-route",
        "route_revision_digest": _d("2"),
        "provider_id": "provider-a",
        "protocol_abi": "responses-v1",
        "bridge_instance_id": "bridge-instance-a",
        "bridge_build_digest": _d("3"),
        "model_id": "model-a",
        "model_digest": _d("4"),
        "tokenizer_digest": _d("5"),
        "checkpoint_digest": _d("6"),
        "credential_handle_id": "policy-credential",
        "credential_handle_version_digest": _d("7"),
        "subject_scope_digest": _d("8"),
        "capabilities": vector.model_dump(mode="json"),
        "provenance": {
            "kind": provenance_kind,
            "issuer_id": "operator-control-plane",
            "signer_key_id": signer_key_id,
            "environment_digest": _d("9"),
            "evidence_digest": _d("a"),
            "validity": {
                "issued_at": "2026-07-10T11:00:00Z",
                "not_before": "2026-07-10T11:00:00Z",
                "expires_at": "2026-07-10T13:00:00Z",
            },
        },
        "revocation": {
            "scope_digest": _d("8"),
            "epoch": 7,
            "state_digest": _d("c"),
        },
    }
    payload.update(updates)
    if "subject_scope_digest" in updates and "revocation" not in updates:
        payload["revocation"]["scope_digest"] = payload["subject_scope_digest"]
    capability_projection = _capability_projection(
        protocol_abi=payload["protocol_abi"],
        model_digest=payload["model_digest"],
        tokenizer_digest=payload["tokenizer_digest"],
        checkpoint_digest=payload["checkpoint_digest"],
        capabilities=c.PolicyCapabilityVector.model_validate(payload["capabilities"]),
    )
    payload.setdefault("capability_digest", independent_digest(capability_projection))
    return c.PolicyCapabilityObservation.model_validate(payload)


@dataclass
class PrivilegedEffectProbe:
    lease_open: int = 0
    verifier_lease_open: int = 0
    setup: int = 0
    credential_issue: int = 0
    secret_resolve: int = 0
    dns: int = 0
    network: int = 0
    policy_observe_live: int = 0
    policy_invoke: int = 0
    provider_construct: int = 0
    verifier_start: int = 0

    def record(self, name: str) -> None:
        if name not in PRIVILEGED_EFFECT_NAMES:
            raise AssertionError(f"unknown privileged effect {name!r}")
        setattr(self, name, getattr(self, name) + 1)

    def snapshot(self) -> dict[str, int]:
        return {field.name: getattr(self, field.name) for field in fields(self)}

    def assert_zero(self) -> None:
        assert self.snapshot() == {name: 0 for name in PRIVILEGED_EFFECT_NAMES}


class RecordingPolicyCapabilityRegistry:
    """Pure test registry: immutable local lookup, never a transport or wrapper."""

    def __init__(self, observation: c.PolicyCapabilityObservation) -> None:
        self.observation = observation
        self.calls: list[tuple[c.PolicyBindingRef, c.AuthenticatedSubject, datetime]] = []
        self.failure: BaseException | None = None

    def observe(
        self,
        binding: c.PolicyBindingRef,
        subject: c.AuthenticatedSubject,
        now: datetime,
    ) -> c.PolicyCapabilityObservation:
        self.calls.append((binding, subject, now))
        if self.failure is not None:
            raise self.failure
        # Revalidation returns a detached, frozen value rather than leaking an
        # implementation-owned mutable dictionary.
        return c.PolicyCapabilityObservation.model_validate(
            self.observation.model_dump(mode="json")
        )


def test_startup_probe_and_operator_attestation_are_closed_frozen_observations() -> None:
    startup = _policy_observation(provenance_kind="startup_probe", signer_key_id="startup-key")
    operator = _policy_observation(
        provenance_kind="operator_attestation",
        signer_key_id="operator-signing-key",
    )

    assert startup.provenance.kind == "startup_probe"
    assert operator.provenance.kind == "operator_attestation"
    assert startup.capability_digest == operator.capability_digest
    assert startup.capabilities == operator.capabilities
    assert startup.canonical_digest() != operator.canonical_digest()
    assert isinstance(startup.capabilities.modalities, tuple)
    assert isinstance(startup.capabilities.request_features, tuple)
    with pytest.raises(ValidationError):
        startup.route_id = "other-route"


@pytest.mark.parametrize(
    ("field", "replacement"),
    [
        ("protocol_abi", "responses-v2"),
        ("model_digest", _d("d")),
        ("tokenizer_digest", _d("d")),
        ("checkpoint_digest", _d("d")),
    ],
)
def test_selection_capability_digest_binds_protocol_model_tokenizer_and_checkpoint(
    field: str,
    replacement: str,
) -> None:
    baseline = _policy_observation()
    payload = baseline.model_dump(mode="json")
    payload[field] = replacement
    projection = _capability_projection(
        protocol_abi=payload["protocol_abi"],
        model_digest=payload["model_digest"],
        tokenizer_digest=payload["tokenizer_digest"],
        checkpoint_digest=payload["checkpoint_digest"],
        capabilities=c.PolicyCapabilityVector.model_validate(payload["capabilities"]),
    )
    payload["capability_digest"] = independent_digest(projection)
    changed = c.PolicyCapabilityObservation.model_validate(payload)

    assert changed.capability_digest != baseline.capability_digest
    assert changed.canonical_digest() != baseline.canonical_digest()


def test_every_capability_field_changes_p() -> None:
    baseline = _policy_observation()
    mutations: dict[str, Any] = {
        "responses_protocol": "responses-v2",
        "modalities": ["audio", "text", "vision"],
        "tool_calling": False,
        "parallel_tool_calls": False,
        "token_ids": False,
        "token_logprobs": False,
        "routing_metadata": False,
        "cancellation": False,
        "max_context_tokens": 32_767,
        "max_output_tokens": 4_095,
        "policy_slot_count": 2,
        "request_features": ["json_mode"],
    }

    observed: set[str] = set()
    for field, replacement in mutations.items():
        changed = _policy_observation(capabilities=_policy_capabilities(**{field: replacement}))
        assert changed.capability_digest != baseline.capability_digest, field
        observed.add(changed.capability_digest)
    assert len(observed) == len(mutations)


@pytest.mark.parametrize(
    ("field", "replacement"),
    [
        ("registry_revision_digest", _d("d")),
        ("route_id", "policy-route-renewed"),
        ("route_revision_digest", _d("d")),
        ("provider_id", "provider-b"),
        ("bridge_instance_id", "bridge-instance-b"),
        ("bridge_build_digest", _d("d")),
        ("model_id", "model-display-b"),
        ("credential_handle_id", "policy-credential-b"),
        ("credential_handle_version_digest", _d("d")),
        ("subject_scope_digest", _d("d")),
    ],
)
def test_provenance_and_binding_changes_observation_digest_but_do_not_redraw_p(
    field: str,
    replacement: str,
) -> None:
    baseline = _policy_observation()
    changed = _policy_observation(**{field: replacement})

    assert changed.capability_digest == baseline.capability_digest
    assert changed.canonical_digest() != baseline.canonical_digest()


def test_signer_evidence_validity_and_epoch_are_full_observation_identity_only() -> None:
    baseline = _policy_observation()
    mutations = [
        {"signer_key_id": "renewed-signing-key"},
        {
            "provenance": {
                **baseline.provenance.model_dump(mode="json"),
                "evidence_digest": _d("d"),
            }
        },
        {
            "provenance": {
                **baseline.provenance.model_dump(mode="json"),
                "validity": {
                    "issued_at": "2026-07-10T11:30:00Z",
                    "not_before": "2026-07-10T11:30:00Z",
                    "expires_at": "2026-07-10T13:00:00Z",
                },
            }
        },
        {
            "revocation": {
                "scope_digest": _d("8"),
                "epoch": 8,
                "state_digest": _d("d"),
            }
        },
    ]

    for mutation in mutations:
        changed = _policy_observation(**mutation)
        assert changed.capability_digest == baseline.capability_digest
        assert changed.canonical_digest() != baseline.canonical_digest()


def test_capability_digest_cannot_be_copied_across_semantic_change() -> None:
    baseline = _policy_observation()
    payload = baseline.model_dump(mode="json")
    payload["checkpoint_digest"] = _d("d")

    with pytest.raises(ValidationError):
        c.PolicyCapabilityObservation.model_validate(payload)


def test_observation_revocation_scope_tampering_is_rejected() -> None:
    payload = _policy_observation().model_dump(mode="json")
    payload["revocation"]["scope_digest"] = _d("0")

    with pytest.raises(ValidationError):
        c.PolicyCapabilityObservation.model_validate(payload)


def test_policy_capability_numbers_and_booleans_are_strict() -> None:
    for invalid in (True, 1.0, "1", -1, MAX_SAFE_INTEGER + 1):
        with pytest.raises(ValidationError):
            _policy_capabilities(max_context_tokens=invalid)
    for invalid in (0, 1, "true", None):
        with pytest.raises(ValidationError):
            _policy_capabilities(tool_calling=invalid)


def test_wrapper_claim_tampering_cannot_enter_trusted_observation() -> None:
    payload = _policy_observation().model_dump(mode="json")
    payload.update(
        {
            "model_version_claim": "attacker-selected-latest",
            "routing_claim": {"provider": "attacker-provider"},
            "wrapper_policy_capabilities": {"tool_calling": True},
        }
    )

    with pytest.raises(ValidationError):
        c.PolicyCapabilityObservation.model_validate(payload)


def test_pure_local_registry_observe_has_no_live_or_privileged_effect() -> None:
    observation = _policy_observation()
    registry = RecordingPolicyCapabilityRegistry(observation)
    effects = PrivilegedEffectProbe()
    binding = c.PolicyBindingRef(
        route_id=observation.route_id,
        registry_revision_digest=observation.registry_revision_digest,
        attestation_digest=_d("e"),
    )
    subject = c.AuthenticatedSubject(
        tenant_id="tenant-a",
        principal_id="principal-a",
        authority_scope_digest=observation.subject_scope_digest,
    )
    now = datetime(2026, 7, 10, 12, 0, tzinfo=UTC)

    first = registry.observe(binding, subject, now)
    second = registry.observe(binding, subject, now)

    assert first == second == observation
    assert first is not observation
    assert registry.calls == [(binding, subject, now), (binding, subject, now)]
    effects.assert_zero()


def test_resolution_uses_only_the_injected_local_snapshot(monkeypatch: pytest.MonkeyPatch) -> None:
    from tests.rl.harness.test_config_selection import _resolution_fixture

    fixture = _resolution_fixture()

    def forbidden_live_effect(*args: Any, **kwargs: Any) -> None:
        raise AssertionError(f"unexpected live or ambient effect: {args!r} {kwargs!r}")

    for module, name in (
        (builtins, "open"),
        (os, "getenv"),
        (os, "urandom"),
        (random, "choice"),
        (random, "random"),
        (random, "randrange"),
        (random, "shuffle"),
        (secrets, "token_bytes"),
        (secrets, "token_hex"),
        (socket, "create_connection"),
        (socket, "getaddrinfo"),
    ):
        monkeypatch.setattr(module, name, forbidden_live_effect)

    resolved = fixture.runtime.resolve_episode(fixture.request)

    assert resolved.policy_capability_observation_digest == fixture.policy_observation.canonical_digest()
    assert fixture.policy_registry.calls == [
        (
            fixture.request.policy_binding,
            fixture.request.subject,
            datetime(2026, 7, 10, 12, 0, tzinfo=UTC),
        )
    ]
    fixture.effects.assert_zero()


def _runtime_observation_mutation(
    observation: c.PolicyCapabilityObservation,
    case_id: str,
) -> c.PolicyCapabilityObservation:
    payload = observation.model_dump(mode="json")
    if case_id == "registry_revision":
        payload["registry_revision_digest"] = _d("0")
    elif case_id == "subject_scope":
        payload["subject_scope_digest"] = _d("0")
        payload["revocation"]["scope_digest"] = _d("0")
    elif case_id == "not_yet_valid":
        payload["provenance"]["validity"] = {
            "issued_at": "2026-07-10T12:30:00Z",
            "not_before": "2026-07-10T12:30:00Z",
            "expires_at": "2026-07-10T13:00:00Z",
        }
    elif case_id == "expired":
        payload["provenance"]["validity"] = {
            "issued_at": "2026-07-10T10:00:00Z",
            "not_before": "2026-07-10T10:00:00Z",
            "expires_at": "2026-07-10T12:00:00Z",
        }
    elif case_id == "epoch":
        payload["revocation"]["epoch"] = 8
        payload["revocation"]["state_digest"] = _d("0")
    elif case_id == "route":
        payload["route_revision_digest"] = _d("0")
    elif case_id == "route_id":
        payload["route_id"] = "other-policy-route"
    elif case_id == "credential_handle_id":
        payload["credential_handle_id"] = "other-policy-credential"
    elif case_id == "credential_handle_version":
        payload["credential_handle_version_digest"] = _d("0")
    elif case_id == "model_id":
        payload["model_id"] = "other-model"
    elif case_id == "tokenizer":
        payload["tokenizer_digest"] = _d("0")
    elif case_id == "protocol":
        payload["protocol_abi"] = "responses-v2"
    elif case_id == "model":
        payload["model_digest"] = _d("0")
    elif case_id == "checkpoint":
        payload["checkpoint_digest"] = _d("0")
    elif case_id == "capability":
        payload["capabilities"]["tool_calling"] = False
    else:
        raise AssertionError(case_id)
    if case_id in {"protocol", "model", "tokenizer", "checkpoint", "capability"}:
        vector = c.PolicyCapabilityVector.model_validate(payload["capabilities"])
        payload["capability_digest"] = independent_digest(
            _capability_projection(
                protocol_abi=payload["protocol_abi"],
                model_digest=payload["model_digest"],
                tokenizer_digest=payload["tokenizer_digest"],
                checkpoint_digest=payload["checkpoint_digest"],
                capabilities=vector,
            )
        )
    return c.PolicyCapabilityObservation.model_validate(payload)


def test_operator_attestation_resolves_only_through_injected_local_registry() -> None:
    from tests.rl.harness.test_config_selection import _resolution_fixture

    baseline = _resolution_fixture()
    payload = baseline.policy_observation.model_dump(mode="json")
    payload["provenance"] = {
        **payload["provenance"],
        "kind": "operator_attestation",
        "issuer_id": "offline-security-operator",
        "signer_key_id": "operator-signing-key",
        "evidence_digest": _d("f"),
    }
    observation = c.PolicyCapabilityObservation.model_validate(payload)
    fixture = _resolution_fixture(observation=observation)

    resolved = fixture.runtime.resolve_episode(fixture.request)

    assert resolved.policy_capability_observation_digest == observation.canonical_digest()
    assert fixture.policy_registry.calls == [
        (
            fixture.request.policy_binding,
            fixture.request.subject,
            datetime(2026, 7, 10, 12, 0, tzinfo=UTC),
        )
    ]
    fixture.effects.assert_zero()


def test_unsigned_operator_attestation_denies_before_selection() -> None:
    from tests.rl.harness.test_config_selection import _resolution_fixture

    baseline = _resolution_fixture()
    payload = baseline.policy_observation.model_dump(mode="json")
    payload["provenance"] = {
        **payload["provenance"],
        "kind": "operator_attestation",
        "issuer_id": "offline-security-operator",
        "signer_key_id": None,
        "evidence_digest": _d("f"),
    }
    observation = c.PolicyCapabilityObservation.model_validate(payload)
    fixture = _resolution_fixture(observation=observation)

    with pytest.raises(c.ConfigRuntimeDenial) as caught:
        fixture.runtime.resolve_episode(fixture.request)

    assert caught.value.stage.value == "policy_observation"
    assert caught.value.code.value == "attestation_invalid"
    assert fixture.store.bindings == {}
    fixture.effects.assert_zero()


def test_missing_local_registry_denies_without_live_probe_or_fallback() -> None:
    from breadboard.rl.harness.config_runtime import ConfigRuntime
    from tests.rl.harness.test_config_selection import _resolution_fixture

    fixture = _resolution_fixture()
    runtime = ConfigRuntime(
        compiler=fixture.admission.compiler,
        policy=fixture.admission.policy,
        registries=fixture.admission.registries,
        revocations=fixture.admission.revocations,
        store=fixture.store,
        clock=fixture.admission.clock,
        authenticator=fixture.admission.authenticator,
        policy_capabilities=None,
    )

    with pytest.raises(c.ConfigRuntimeDenial) as caught:
        runtime.resolve_episode(fixture.request)

    assert caught.value.stage.value == "policy_observation"
    assert caught.value.code.value == "observation_unavailable"
    assert fixture.store.bindings == {}
    fixture.effects.assert_zero()


def test_policy_binding_attestation_digest_tampering_denies_before_selection() -> None:
    from tests.rl.harness.test_config_selection import _resolution_fixture

    fixture = _resolution_fixture()
    request_payload = fixture.request.model_dump(mode="json")
    request_payload["policy_binding"]["attestation_digest"] = _d("0")
    request = c.ResolveEpisodeRequest.model_validate(request_payload)

    with pytest.raises(c.ConfigRuntimeDenial) as caught:
        fixture.runtime.resolve_episode(request)

    assert caught.value.stage.value == "policy_observation"
    assert caught.value.code.value == "attestation_invalid"
    assert fixture.store.bindings == {}
    fixture.effects.assert_zero()


@pytest.mark.parametrize(
    ("case_id", "expected_code"),
    [
        ("registry_revision", "observation_scope_mismatch"),
        ("subject_scope", "observation_scope_mismatch"),
        ("not_yet_valid", "attestation_not_yet_valid"),
        ("expired", "attestation_expired"),
        ("epoch", "observation_revoked"),
        ("route", "attestation_invalid"),
        ("route_id", "observation_scope_mismatch"),
        ("credential_handle_id", "attestation_invalid"),
        ("credential_handle_version", "attestation_invalid"),
        ("model_id", "attestation_invalid"),
        ("tokenizer", "model_mismatch"),
        ("protocol", "protocol_mismatch"),
        ("model", "model_mismatch"),
        ("checkpoint", "checkpoint_mismatch"),
        ("capability", "capability_digest_mismatch"),
    ],
)
def test_policy_observation_rejects_scope_validity_epoch_and_semantic_mismatches(
    case_id: str,
    expected_code: str,
) -> None:
    from tests.rl.harness.test_config_selection import _resolution_fixture

    baseline = _resolution_fixture()
    observation = _runtime_observation_mutation(baseline.policy_observation, case_id)
    fixture = _resolution_fixture(observation=observation)

    with pytest.raises(c.ConfigRuntimeDenial) as caught:
        fixture.runtime.resolve_episode(fixture.request)

    assert caught.value.stage.value == "policy_observation"
    assert caught.value.code.value == expected_code
    assert fixture.store.bindings == {}
    fixture.effects.assert_zero()


def test_full_observation_digest_changes_request_binding_while_p_remains_stable() -> None:
    from tests.rl.harness.test_config_selection import _resolution_fixture

    fixture = _resolution_fixture()
    first = fixture.runtime.resolve_episode(fixture.request)
    baseline = fixture.policy_observation
    payload = baseline.model_dump(mode="json")
    payload["provenance"] = {
        **payload["provenance"],
        "evidence_digest": _d("f"),
    }
    renewed = c.PolicyCapabilityObservation.model_validate(payload)
    assert renewed.capability_digest == baseline.capability_digest
    assert renewed.canonical_digest() != baseline.canonical_digest()
    fixture.policy_registry.observation = renewed

    with pytest.raises(c.ConfigRuntimeDenial) as caught:
        fixture.runtime.resolve_episode(fixture.request)

    assert caught.value.stage.value == "selection_persistence"
    assert caught.value.code.value == "selection_idempotency_conflict"
    assert caught.value.selection_record_digest == first.selection_record_ref.sha256
    assert len(fixture.store.bindings) == 1
    fixture.effects.assert_zero()


def test_unknown_operator_signer_differs_only_by_key_and_denies_before_selection() -> None:
    from tests.rl.harness.test_config_selection import _resolution_fixture

    baseline = _resolution_fixture()
    payload = baseline.policy_observation.model_dump(mode="json")
    payload["provenance"] = {
        **payload["provenance"],
        "kind": "operator_attestation",
        "issuer_id": "offline-security-operator",
        "signer_key_id": "operator-signing-key",
    }
    authorized = c.PolicyCapabilityObservation.model_validate(payload)
    authorized_fixture = _resolution_fixture(observation=authorized)
    authorized_fixture.runtime.resolve_episode(authorized_fixture.request)
    authorized_fixture.effects.assert_zero()

    payload["provenance"]["signer_key_id"] = "unknown-operator-key"
    unknown = c.PolicyCapabilityObservation.model_validate(payload)
    assert unknown.model_dump(mode="json") == {
        **authorized.model_dump(mode="json"),
        "provenance": {
            **authorized.provenance.model_dump(mode="json"),
            "signer_key_id": "unknown-operator-key",
        },
    }
    fixture = _resolution_fixture(observation=unknown)

    with pytest.raises(c.ConfigRuntimeDenial) as caught:
        fixture.runtime.resolve_episode(fixture.request)

    assert caught.value.stage.value == "policy_observation"
    assert caught.value.code.value == "attestation_invalid"
    assert caught.value.pointer == "/policy_observation/provenance/signer_key_id"
    assert fixture.store.bindings == {}
    assert not any(
        kind is c.ArtifactKind.SELECTION_RECORD
        for kind, _ in fixture.store.publish_calls
    )
    fixture.effects.assert_zero()


@pytest.mark.parametrize(
    ("validity", "expected_code"),
    [
        (
            {
                "issued_at": "2026-07-10T11:00:00Z",
                "not_before": "2026-07-10T12:00:00Z",
                "expires_at": "2026-07-10T13:00:00Z",
            },
            None,
        ),
        (
            {
                "issued_at": "2026-07-10T11:00:00Z",
                "not_before": "2026-07-10T12:00:01Z",
                "expires_at": "2026-07-10T13:00:00Z",
            },
            "attestation_not_yet_valid",
        ),
        (
            {
                "issued_at": "2026-07-10T11:00:00Z",
                "not_before": "2026-07-10T11:00:00Z",
                "expires_at": "2026-07-10T12:00:01Z",
            },
            None,
        ),
        (
            {
                "issued_at": "2026-07-10T11:00:00Z",
                "not_before": "2026-07-10T11:00:00Z",
                "expires_at": "2026-07-10T12:00:00Z",
            },
            "attestation_expired",
        ),
    ],
)
def test_authorized_signer_currentness_uses_exact_half_open_boundaries(
    validity: dict[str, str],
    expected_code: str | None,
) -> None:
    from tests.rl.harness.test_config_selection import _resolution_fixture

    baseline = _resolution_fixture()
    payload = baseline.policy_observation.model_dump(mode="json")
    payload["provenance"] = {
        **payload["provenance"],
        "kind": "operator_attestation",
        "issuer_id": "offline-security-operator",
        "signer_key_id": "operator-signing-key",
        "validity": validity,
    }
    fixture = _resolution_fixture(
        observation=c.PolicyCapabilityObservation.model_validate(payload)
    )

    if expected_code is None:
        fixture.runtime.resolve_episode(fixture.request)
        assert len(fixture.store.bindings) == 1
    else:
        with pytest.raises(c.ConfigRuntimeDenial) as caught:
            fixture.runtime.resolve_episode(fixture.request)
        assert caught.value.stage.value == "policy_observation"
        assert caught.value.code.value == expected_code
        assert fixture.store.bindings == {}
    fixture.effects.assert_zero()


def _rehash_policy_attestation(payload: dict[str, Any]) -> dict[str, Any]:
    rebound = json.loads(json.dumps(payload))
    projection = {
        "schema_version": "bb.rl.policy-capability-attestation.v1",
        **{
            key: value
            for key, value in rebound.items()
            if key != "attestation_digest"
        },
    }
    rebound["attestation_digest"] = independent_digest(projection)
    return rebound


@pytest.mark.parametrize(
    ("field", "replacement"),
    [
        ("authorized_signer_key_ids", ["other-signer"]),
        ("signature_verification_policy_digest", _d("0")),
        (
            "validity",
            {
                "issued_at": "2026-07-10T12:00:01Z",
                "not_before": "2026-07-10T12:00:01Z",
                "expires_at": "2026-07-10T14:00:00Z",
            },
        ),
    ],
)
def test_registry_signer_set_policy_and_currentness_are_attestation_identity(
    field: str,
    replacement: Any,
) -> None:
    from tests.rl.harness.test_config_selection import _resolution_fixture

    fixture = _resolution_fixture()
    baseline = fixture.admission.registries.policy_capability_attestations[0]
    payload = baseline.model_dump(mode="json")
    payload[field] = replacement

    with pytest.raises(ValidationError):
        c.PolicyCapabilityAttestationRecord.model_validate(payload)

    changed = c.PolicyCapabilityAttestationRecord.model_validate(
        _rehash_policy_attestation(payload)
    )
    assert changed.attestation_digest != baseline.attestation_digest
    assert changed.canonical_digest() != baseline.canonical_digest()


@pytest.mark.parametrize(
    "authorized_signer_key_ids",
    [[], ["startup-key", "operator-signing-key"], ["startup-key", "startup-key"]],
)
def test_registry_authorized_signer_set_is_nonempty_sorted_and_unique(
    authorized_signer_key_ids: list[str],
) -> None:
    from tests.rl.harness.test_config_selection import _resolution_fixture

    fixture = _resolution_fixture()
    payload = fixture.admission.registries.policy_capability_attestations[0].model_dump(
        mode="json"
    )
    payload["authorized_signer_key_ids"] = authorized_signer_key_ids

    with pytest.raises(ValidationError):
        c.PolicyCapabilityAttestationRecord.model_validate(
            _rehash_policy_attestation(payload)
        )
