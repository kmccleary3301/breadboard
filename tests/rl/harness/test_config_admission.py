from __future__ import annotations

import copy
import hashlib
import json
import math
from collections.abc import Mapping
from dataclasses import dataclass, fields
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from breadboard.rl.harness.contracts import (
    AdmissionReceipt,
    AdmissionRequest,
    AuthenticatedSubject,
    CapabilityVector,
    ResourceLimits,
    RevocationBinding,
    SetupRegistryRecord,
    ValidityWindow,
)
from breadboard.rl.harness import contracts as c
from breadboard.rl.harness.config_runtime import (
    CompilerSemanticView,
    ConfigRuntime,
)


FIXTURE_ROOT = Path(__file__).resolve().parents[2] / "fixtures" / "rl" / "config_runtime"
CANONICAL_VECTORS = FIXTURE_ROOT / "canonical_artifact_vectors_v1.json"
ADMISSION_DENIALS = FIXTURE_ROOT / "admission_denials_v1.json"
ADMISSION_DENIALS_FILE_SHA256 = "e388adaf972de59c406df7e57707626a1a80a919590f9f7e24b04973066c32b2"
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


def _validate_oracle_value(value: Any) -> None:
    if value is None or type(value) in (bool, str):
        return
    if type(value) is int:
        if not -MAX_SAFE_INTEGER <= value <= MAX_SAFE_INTEGER:
            raise ValueError("integer is outside the independently frozen JCS domain")
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
        # The compact WP3 vectors deliberately use ASCII keys. Python's lexical
        # ordering is then identical to RFC 8785 UTF-16 ordering, without using
        # BreadBoard's canonicalizer as an oracle for BreadBoard itself.
        if any(not key.isascii() for key in value):
            raise ValueError("fixture oracle supports ASCII object keys only")
        for item in value.values():
            _validate_oracle_value(item)
        return
    raise TypeError(f"unsupported JSON value {type(value).__name__}")


def independent_jcs_bytes(value: Any) -> bytes:
    """Canonicalize the fixture's strict JSON subset using only stdlib code."""

    _validate_oracle_value(value)
    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def independent_digest(value: Any) -> str:
    return "sha256:" + hashlib.sha256(independent_jcs_bytes(value)).hexdigest()

def _mutable_json(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {key: _mutable_json(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_mutable_json(item) for item in value]
    return value


def _load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _frozen_denial(case_id: str) -> dict[str, Any]:
    cases = _load_json(ADMISSION_DENIALS)["cases"]
    return next(case for case in cases if case["case_id"] == case_id)["expected"]


def _replace_pointer(value: Any, pointer: str, replacement: Any) -> Any:
    assert pointer.startswith("/")
    result = copy.deepcopy(value)
    tokens = [token.replace("~1", "/").replace("~0", "~") for token in pointer[1:].split("/")]
    parent = result
    for token in tokens[:-1]:
        parent = parent[int(token)] if isinstance(parent, list) else parent[token]
    final = tokens[-1]
    if isinstance(parent, list):
        parent[int(final)] = replacement
    else:
        parent[final] = replacement
    return result


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


@pytest.fixture
def privileged_effects() -> PrivilegedEffectProbe:
    return PrivilegedEffectProbe()


def test_canonical_artifact_fixture_bytes_and_digests_are_independently_frozen() -> None:
    corpus = _load_json(CANONICAL_VECTORS)

    assert corpus["schema_version"] == "bb.rl.config-runtime-canonical-vectors.v1"
    assert corpus["canonicalizer_id"] == "rfc8785-jcs-v1"
    assert {vector["artifact_kind"] for vector in corpus["vectors"]} == {
        "authenticated_subject",
        "capability_vector",
        "admission_request",
        "admission_receipt",
        "admission_policy",
    }

    for vector in corpus["vectors"]:
        expected_bytes = bytes.fromhex(vector["canonical_hex"])
        actual_bytes = independent_jcs_bytes(vector["payload"])
        assert actual_bytes == expected_bytes, vector["case_id"]
        assert independent_digest(vector["payload"]) == vector["digest"]
        assert vector["digest"] == "sha256:" + hashlib.sha256(expected_bytes).hexdigest()
        assert b'"digest":"sha256:' not in actual_bytes or vector["artifact_kind"] in {
            "admission_request",
            "admission_receipt",
        }

        for mutation in vector["semantic_mutations"]:
            mutated = _replace_pointer(
                vector["payload"], mutation["pointer"], mutation["replacement"]
            )
            assert independent_digest(mutated) != vector["digest"], (
                vector["case_id"],
                mutation["pointer"],
            )


def test_canonical_artifacts_carry_no_self_digest() -> None:
    corpus = _load_json(CANONICAL_VECTORS)
    for vector in corpus["vectors"]:
        payload = vector["payload"]
        assert "digest" not in payload, vector["case_id"]
        assert vector["digest"] not in independent_jcs_bytes(payload).decode("utf-8")


def test_admission_denial_fixture_binds_schemas_policy_locations_and_effects() -> None:
    corpus = _load_json(ADMISSION_DENIALS)
    assert hashlib.sha256(ADMISSION_DENIALS.read_bytes()).hexdigest() == ADMISSION_DENIALS_FILE_SHA256

    assert corpus["schema_version"] == "bb.rl.admission-denial-vectors.v1"
    assert independent_digest(corpus["policy_schema"]) == corpus["policy_schema_digest"]
    assert independent_digest(corpus["compiled_schema"]) == corpus["compiled_schema_digest"]
    assert independent_digest(corpus["policy_identity_payload"]) == corpus["policy_digest"]

    case_ids = [case["case_id"] for case in corpus["cases"]]
    assert len(case_ids) == len(set(case_ids))
    assert len(case_ids) == 68
    expected_zero = {name: 0 for name in PRIVILEGED_EFFECT_NAMES}
    for case in corpus["cases"]:
        expected = case["expected"]
        assert expected["stage"]
        assert expected["code"]
        assert expected["policy_digest"] == corpus["policy_digest"]
        assert expected["schema_digest"] in {
            corpus["policy_schema_digest"],
            corpus["compiled_schema_digest"],
            corpus["request_schema_digest"],
        }
        pointer = expected["pointer"]
        assert pointer is None or pointer.startswith("/")
        assert case["forbidden_effects"] == expected_zero

    covered = {(case["expected"]["stage"], case["expected"]["code"]) for case in corpus["cases"]}
    assert {
        ("compiled_artifact_verification", "incomplete_capability_vector"),
        ("compiled_artifact_verification", "runner_visible_loss"),
        ("registry_resolution", "registry_snapshot_mismatch"),
        ("registry_resolution", "duplicate_binding"),
        ("registry_resolution", "reserved_tool_shadow"),
        ("registry_resolution", "fallback_cycle"),
        ("capability_intersection", "operator_ceiling_exceeded"),
        ("identity_pinning", "repository_image_mismatch"),
        ("identity_pinning", "model_identity_mismatch"),
        ("identity_pinning", "verifier_identity_mismatch"),
        ("receipt_recheck", "receipt_forged"),
        ("receipt_recheck", "receipt_not_yet_valid"),
        ("receipt_recheck", "receipt_expired"),
        ("receipt_recheck", "receipt_revoked"),
        ("receipt_recheck", "receipt_epoch_rollback"),
        ("receipt_recheck", "receipt_cross_subject"),
    } <= covered


def test_denial_fixture_covers_every_registry_and_capability_dimension() -> None:
    corpus = _load_json(ADMISSION_DENIALS)
    case_ids = {case["case_id"] for case in corpus["cases"]}

    registry_roles = {
        "runner",
        "tool",
        "setup",
        "route",
        "secret",
        "runtime",
        "image",
        "repository",
        "task",
        "dataset",
        "model",
        "verifier",
        "evidence",
        "retention",
    }
    assert {
        f"unknown_{role}_registry_binding" for role in registry_roles
    } <= case_ids

    capability_dimensions = {
        "runner",
        "tools",
        "setup_plans",
        "routes",
        "secret_handles",
        "sandbox",
        "resources",
        "limits",
        "task",
        "policy_slots",
        "verifier",
        "mutable_pointers",
        "artifacts",
        "evidence",
        "retention",
    }
    assert {f"{dimension}_above_ceiling" for dimension in capability_dimensions} <= case_ids


def test_fixture_error_material_contains_no_marker_secret_or_raw_authority() -> None:
    corpus = _load_json(ADMISSION_DENIALS)
    encoded = independent_jcs_bytes(corpus)

    for marker in (
        b"MARKER_SECRET",
        b"Authorization:",
        b"Bearer ",
        b"${env:",
        b"https://user:pass@",
        b"credential.json",
    ):
        assert marker not in encoded


def test_privileged_effect_probe_has_closed_zero_baseline(
    privileged_effects: PrivilegedEffectProbe,
) -> None:
    assert tuple(privileged_effects.snapshot()) == PRIVILEGED_EFFECT_NAMES
    privileged_effects.assert_zero()
    with pytest.raises(AssertionError, match="unknown privileged effect"):
        privileged_effects.record("control_plane_cas_load")


@pytest.mark.parametrize(
    ("artifact_kind", "contract_type"),
    [
        ("authenticated_subject", AuthenticatedSubject),
        ("capability_vector", CapabilityVector),
        ("admission_request", AdmissionRequest),
        ("admission_receipt", AdmissionReceipt),
        ("admission_policy", c.AdmissionPolicySnapshot),
    ],
)
def test_production_contracts_match_independently_frozen_artifact_bytes(
    artifact_kind: str, contract_type: type[Any]
) -> None:
    vector = next(
        item
        for item in _load_json(CANONICAL_VECTORS)["vectors"]
        if item["artifact_kind"] == artifact_kind
    )

    contract = contract_type.model_validate(vector["payload"])

    assert contract.to_canonical_obj() == vector["payload"]
    assert contract.canonical_bytes() == bytes.fromhex(vector["canonical_hex"])
    assert contract.canonical_digest() == vector["digest"]


def test_contracts_are_closed_frozen_deeply_immutable_and_copy_safe() -> None:
    raw = next(
        item["payload"]
        for item in _load_json(CANONICAL_VECTORS)["vectors"]
        if item["artifact_kind"] == "capability_vector"
    )
    mutable_input = copy.deepcopy(raw)
    contract = CapabilityVector.model_validate(mutable_input)
    before = contract.canonical_bytes()

    mutable_input["tools"][0]["capability_ids"].append("zzz-after-validation")
    mutable_input["sandbox"]["egress_route_ids"].clear()
    assert contract.canonical_bytes() == before
    assert isinstance(contract.tools, tuple)
    assert isinstance(contract.tools[0].capability_ids, tuple)
    assert isinstance(contract.sandbox.egress_route_ids, tuple)

    with pytest.raises(ValidationError):
        contract.runner = contract.runner
    with pytest.raises(TypeError, match="cannot be updated by copy"):
        contract.model_copy(update={"runner": {"adapter_id": "attacker"}})
    with pytest.raises(ValidationError):
        CapabilityVector.model_validate({**raw, "unexpected": "forbidden"})


@pytest.mark.parametrize(
    "invalid",
    [True, False, 1.0, "1", 0, -1, MAX_SAFE_INTEGER + 1],
)
def test_resource_limits_reject_coercion_and_unsafe_bounds(invalid: Any) -> None:
    payload = {
        "cpu_millis": 1,
        "memory_bytes": 1,
        "pids": 1,
        "storage_bytes": 1,
        "open_files": 1,
        "wall_time_ms": 1,
    }
    payload["cpu_millis"] = invalid
    with pytest.raises(ValidationError):
        ResourceLimits.model_validate(payload)


@pytest.mark.parametrize("invalid", [True, 1.0, "1", -1, 2**64])
def test_revocation_epoch_is_an_exact_uint64(invalid: Any) -> None:
    with pytest.raises(ValidationError):
        RevocationBinding.model_validate(
            {"scope_digest": "sha256:" + "1" * 64, "epoch": invalid, "state_digest": "sha256:" + "2" * 64}
        )

    maximum = RevocationBinding.model_validate(
        {"scope_digest": "sha256:" + "1" * 64, "epoch": 2**64 - 1, "state_digest": "sha256:" + "2" * 64}
    )
    assert maximum.epoch == 2**64 - 1


@pytest.mark.parametrize(
    "invalid_digest",
    [
        "0" * 64,
        "sha256:" + "A" * 64,
        "sha256:" + "a" * 63,
        "sha512:" + "a" * 64,
        "sha256:" + "a" * 65,
    ],
)
def test_digest_fields_require_exact_lowercase_sha256(invalid_digest: str) -> None:
    with pytest.raises(ValidationError):
        AuthenticatedSubject.model_validate(
            {
                "tenant_id": "tenant-a",
                "principal_id": "principal-a",
                "authority_scope_digest": invalid_digest,
            }
        )


@pytest.mark.parametrize(
    "invalid_id",
    [" cafe", "cafe ", "cafe\u0301", "line\nbreak", "x" * 257],
)
def test_identity_strings_reject_normalization_control_and_length_bypasses(
    invalid_id: str,
) -> None:
    with pytest.raises(ValidationError):
        AuthenticatedSubject.model_validate(
            {
                "tenant_id": invalid_id,
                "principal_id": "principal-a",
                "authority_scope_digest": "sha256:" + "1" * 64,
            }
        )


@pytest.mark.parametrize(
    "invalid_time",
    [
        "2026-07-10T12:00:00+00:00",
        "2026-07-10T12:00:00.000Z",
        "2026-02-30T12:00:00Z",
        "2026-07-10 12:00:00Z",
    ],
)
def test_validity_timestamps_are_real_canonical_utc_seconds(invalid_time: str) -> None:
    with pytest.raises(ValidationError):
        ValidityWindow.model_validate(
            {
                "issued_at": invalid_time,
                "not_before": "2026-07-10T12:00:00Z",
                "expires_at": "2026-07-10T13:00:00Z",
            }
        )


@pytest.mark.parametrize(
    ("contract_type", "payload", "raw_field"),
    [
        (
            AuthenticatedSubject,
            {
                "tenant_id": "tenant-a",
                "principal_id": "principal-a",
                "authority_scope_digest": "sha256:" + "1" * 64,
            },
            "base_url",
        ),
        (
            SetupRegistryRecord,
            {
                "grant": {
                    "setup_id": "setup-a",
                    "implementation_digest": "sha256:" + "1" * 64,
                    "plan_digest": "sha256:" + "2" * 64,
                },
                "route_ids": [],
                "secret_handle_ids": [],
                "timeout_ms": 1,
                "expected_output_roles": [],
            },
            "shell_command",
        ),
    ],
)
def test_contracts_reject_raw_authority_fields(
    contract_type: type[Any], payload: dict[str, Any], raw_field: str
) -> None:
    with pytest.raises(ValidationError):
        contract_type.model_validate({**payload, raw_field: "MARKER_SECRET"})


def _d(character: str) -> str:
    return "sha256:" + character * 64


class RecordingCompiler:
    def __init__(self, view: CompilerSemanticView) -> None:
        self.view = view
        self.calls: list[str] = []

    def verify_bundle(self, request: AdmissionRequest) -> None:
        self.calls.append("verify_bundle")

    def enforce_compile_budget(self, request: AdmissionRequest) -> None:
        self.calls.append("enforce_compile_budget")

    def compile(self, request: AdmissionRequest) -> CompilerSemanticView:
        self.calls.append("compile")
        return self.view

class FailingCompiler(RecordingCompiler):
    def __init__(self, view: CompilerSemanticView, failure_at: str) -> None:
        super().__init__(view)
        self.failure_at = failure_at

    def _raise_if_selected(self, stage: str) -> None:
        if self.failure_at == stage:
            raise RuntimeError("MARKER_SECRET compiler adapter failure")

    def verify_bundle(self, request: AdmissionRequest) -> None:
        super().verify_bundle(request)
        self._raise_if_selected("verify_bundle")

    def enforce_compile_budget(self, request: AdmissionRequest) -> None:
        super().enforce_compile_budget(request)
        self._raise_if_selected("enforce_compile_budget")

    def compile(self, request: AdmissionRequest) -> CompilerSemanticView:
        self._raise_if_selected("compile")
        return super().compile(request)


class FixedClock:
    def __init__(self, now: datetime) -> None:
        self.value = now
        self.calls = 0

    def current(self) -> datetime:
        self.calls += 1
        return self.value


class RecordingRevocations:
    def __init__(self, current: RevocationBinding) -> None:
        self.current = current
        self.loads: list[str] = []

    def load(self, scope_digest: str) -> RevocationBinding:
        self.loads.append(scope_digest)
        return self.current


class RecordingReceiptStore:
    def __init__(self) -> None:
        self.records: dict[str, bytes] = {}
        self.publish_calls: list[tuple[c.ArtifactKind, bytes]] = []
        self.load_calls: list[tuple[str, c.ArtifactKind, int]] = []
        self.fail_publish = False
        self.fail_load = False
        self.conflicting_ref = False
        self.corrupt_readback = False

    def publish(self, *, kind: c.ArtifactKind, canonical_bytes: bytes) -> c.ArtifactRef:
        self.publish_calls.append((kind, canonical_bytes))
        if self.fail_publish:
            raise OSError("control-plane CAS unavailable")
        digest = "sha256:" + hashlib.sha256(canonical_bytes).hexdigest()
        self.records.setdefault(digest, canonical_bytes)
        ref_digest = _d("0") if self.conflicting_ref else digest
        return c.ArtifactRef(
            artifact_id=ref_digest,
            sha256=ref_digest,
            size_bytes=len(canonical_bytes),
            media_type="application/vnd.breadboard.admission-receipt+json;version=1",
        )

    def load(self, digest: str, *, kind: c.ArtifactKind, max_bytes: int) -> bytes:
        self.load_calls.append((digest, kind, max_bytes))
        if self.fail_load:
            raise OSError("control-plane CAS unavailable")
        payload = self.records[digest]
        if self.corrupt_readback:
            return payload[:-1] + bytes([payload[-1] ^ 1])
        return payload

class RecordingReceiptAuthenticator:
    key_id = "test-receipt-key"
    algorithm = "test-sha256-v1"

    def __init__(self) -> None:
        self.sign_calls: list[bytes] = []
        self.verify_calls: list[tuple[bytes, bytes]] = []
        self.fail_sign = False
        self.fail_verify = False
        self.reject_verify = False

    @staticmethod
    def expected_signature(unsigned_canonical_bytes: bytes) -> bytes:
        return hashlib.sha256(b"breadboard-test-issuer-v1\0" + unsigned_canonical_bytes).digest()

    def sign(self, unsigned_canonical_bytes: bytes) -> bytes:
        self.sign_calls.append(unsigned_canonical_bytes)
        if self.fail_sign:
            raise RuntimeError("MARKER_SECRET signing failure")
        return self.expected_signature(unsigned_canonical_bytes)

    def verify(self, unsigned_canonical_bytes: bytes, signature: bytes) -> bool:
        self.verify_calls.append((unsigned_canonical_bytes, signature))
        if self.fail_verify:
            raise RuntimeError("MARKER_SECRET verification failure")
        return not self.reject_verify and signature == self.expected_signature(
            unsigned_canonical_bytes
        )


@dataclass(frozen=True)
class AdmissionFixture:
    runtime: ConfigRuntime
    request: AdmissionRequest
    policy: c.AdmissionPolicySnapshot
    registries: c.RegistrySnapshotSet
    compiler: RecordingCompiler
    revocations: RecordingRevocations
    store: RecordingReceiptStore
    clock: FixedClock
    authenticator: RecordingReceiptAuthenticator


def _setup_authority_projection(grant: dict[str, Any], task: dict[str, Any]) -> dict[str, Any]:
    return {
        "schema_version": "bb.rl.setup-plan.v1",
        "setup_id": grant["setup_id"],
        "implementation_digest": grant["implementation_digest"],
        "argv": ["breadboard-setup", "--prepare-workspace"],
        "input_digests": list(task["input_artifact_digests"]),
        "writable_output_subtrees": ["workspace/output"],
        "writable_output_slots": ["patch"],
        "route_ids": ["policy-route"],
        "secret_handle_ids": ["policy-credential"],
        "timeout_ms": 60_000,
        "expected_outputs": [{"role": "patch", "artifact_id": "patch"}],
    }


def _route_authority_projection(grant: dict[str, Any]) -> dict[str, Any]:
    return {
        "schema_version": "bb.rl.route-authority.v1",
        "route_id": grant["route_id"],
        "protocol_abi": grant["protocol_abi"],
        "credential_handle_id": grant["credential_handle_id"],
        "scheme": "https",
        "authority": "policy.example.test",
        "paths": ["/v1/responses"],
        "methods": ["POST"],
        "ip_policy_digest": _d("2"),
        "dns_policy_digest": _d("3"),
        "request_schema_digest": _d("4"),
        "response_schema_digest": _d("5"),
        "max_request_bytes": 65_536,
        "max_response_bytes": 32_768,
        "max_requests_per_minute": 60,
        "data_classification": "confidential",
        "owner": {"owner_id": "operator", "authority_scope_digest": _d("1")},
    }

def _rebind_registry_payload(payload: dict[str, Any]) -> dict[str, Any]:
    rebound = copy.deepcopy(payload)
    component_digest_fields = {
        "runners": "runner_registry_digest",
        "tools": "tool_registry_digest",
        "setups": "setup_registry_digest",
        "routes": "route_registry_digest",
        "secret_handles": "secret_handle_registry_digest",
        "sandbox_runtimes": "sandbox_runtime_registry_digest",
        "images": "image_registry_digest",
        "repository_bindings": "repository_binding_registry_digest",
        "task_datasets": "task_dataset_registry_digest",
        "models": "model_registry_digest",
        "verifiers": "verifier_registry_digest",
        "evidence_policies": "evidence_policy_registry_digest",
        "retention_policies": "retention_policy_registry_digest",
        "policy_capability_attestations": "policy_capability_registry_digest",
    }
    component_digests = {
        digest_field: independent_digest(
            {
                "schema_version": c.REGISTRY_SNAPSHOT_SCHEMA_VERSION,
                "component": component,
                "records": rebound[component],
            }
        )
        for component, digest_field in component_digest_fields.items()
    }
    rebound["digests"] = {
        **component_digests,
        "snapshot_digest": independent_digest(
            {
                "schema_version": c.REGISTRY_SNAPSHOT_SCHEMA_VERSION,
                "component_digests": component_digests,
            }
        ),
    }
    return rebound


def _base_capability_payload() -> dict[str, Any]:
    payload = copy.deepcopy(
        next(
            item["payload"]
            for item in _load_json(CANONICAL_VECTORS)["vectors"]
            if item["artifact_kind"] == "capability_vector"
        )
    )
    payload["sandbox"]["mounts"].append(
        {
            "source_artifact_digest": _d("b"),
            "target_logical_path": "workspace/output",
            "access": "rw",
            "max_bytes": 1_048_576,
        }
    )
    setup = payload["setup_plans"][0]
    setup["plan_digest"] = independent_digest(
        _setup_authority_projection(setup, payload["task"])
    )
    route = payload["routes"][0]
    route["route_revision_digest"] = independent_digest(
        _route_authority_projection(route)
    )
    return payload


def _admission_fixture(
    *,
    capability_payload: dict[str, Any] | None = None,
    ceiling_capability_payload: dict[str, Any] | None = None,
    store: RecordingReceiptStore | None = None,
    authenticator: RecordingReceiptAuthenticator | None = None,
    now: datetime = datetime(2026, 7, 10, 12, 0, tzinfo=UTC),
) -> AdmissionFixture:
    capability = c.CapabilityVector.model_validate(
        capability_payload or _base_capability_payload()
    )
    ceiling_vector = c.CapabilityVector.model_validate(
        ceiling_capability_payload or _base_capability_payload()
    )
    repository_binding_digest = _d("7")
    retention_grant = c.RetentionPolicyGrant(
        policy=capability.retention,
        minimum_seconds=86_400,
        maximum_seconds=2_592_000,
    )
    setup_projection = _setup_authority_projection(
        capability.setup_plans[0].to_canonical_obj(),
        capability.task.to_canonical_obj(),
    )
    setup_record = c.SetupRegistryRecord(
        grant=capability.setup_plans[0],
        argv=tuple(setup_projection["argv"]),
        input_digests=tuple(setup_projection["input_digests"]),
        writable_output_subtrees=tuple(setup_projection["writable_output_subtrees"]),
        writable_output_slots=tuple(setup_projection["writable_output_slots"]),
        route_ids=tuple(setup_projection["route_ids"]),
        secret_handle_ids=tuple(setup_projection["secret_handle_ids"]),
        timeout_ms=setup_projection["timeout_ms"],
        expected_outputs=tuple(
            c.SetupOutput.model_validate(output)
            for output in setup_projection["expected_outputs"]
        ),
    )
    route_projection = _route_authority_projection(
        capability.routes[0].to_canonical_obj()
    )
    route_record = c.RouteRegistryRecord(
        grant=capability.routes[0],
        scheme=route_projection["scheme"],
        authority=route_projection["authority"],
        paths=tuple(route_projection["paths"]),
        methods=tuple(route_projection["methods"]),
        ip_policy_digest=route_projection["ip_policy_digest"],
        dns_policy_digest=route_projection["dns_policy_digest"],
        request_schema_digest=route_projection["request_schema_digest"],
        response_schema_digest=route_projection["response_schema_digest"],
        max_request_bytes=route_projection["max_request_bytes"],
        max_response_bytes=route_projection["max_response_bytes"],
        max_requests_per_minute=route_projection["max_requests_per_minute"],
        data_classification=route_projection["data_classification"],
        owner=c.RouteOwnerAuthority.model_validate(route_projection["owner"]),
    )
    verifier_runtime = c.SandboxBinding(
        runtime_id="verifier-runtime",
        runtime_class=c.RuntimeClass.HARDENED_DOCKER,
        driver_implementation_digest=_d("7"),
        runtime_binary_digest=_d("8"),
        security_policy_digest=_d("9"),
        image_digest=capability.verifier.image_digest,
        network_policy_digest=capability.verifier.network_policy_digest,
    )
    attestation_projection = {
        "schema_version": "bb.rl.policy-capability-attestation.v1",
        "route_id": capability.routes[0].route_id,
        "route_revision_digest": capability.routes[0].route_revision_digest,
        "model_digest": capability.policy_slots[0].model_digest,
        "tokenizer_digest": capability.policy_slots[0].tokenizer_digest,
        "checkpoint_digest": capability.policy_slots[0].checkpoint_digest,
        "capability_digest": capability.policy_slots[0].required_policy_capabilities_digest,
        "authorized_signer_key_ids": [
            "operator-signing-key",
            "startup-key",
            "startup-probe-key",
        ],
        "signature_verification_policy_digest": _d("d"),
        "validity": {
            "issued_at": "2026-07-10T11:00:00Z",
            "not_before": "2026-07-10T11:00:00Z",
            "expires_at": "2026-07-10T14:00:00Z",
        },
        "revocation": {
            "scope_digest": _d("1"),
            "epoch": 7,
            "state_digest": _d("3"),
        },
    }
    attestation_record = c.PolicyCapabilityAttestationRecord(
        **{
            key: value
            for key, value in attestation_projection.items()
            if key != "schema_version"
        },
        attestation_digest=independent_digest(attestation_projection),
    )
    registry_records: dict[str, tuple[Any, ...]] = {
        "runners": (c.RunnerRegistryRecord(grant=capability.runner),),
        "tools": (
            c.ToolRegistryRecord(
                grant=capability.tools[0],
                argument_schema_digest=_d("1"),
                result_schema_digest=_d("2"),
                reserved=False,
            ),
        ),
        "setups": (setup_record,),
        "routes": (route_record,),
        "secret_handles": (
            c.SecretHandleRegistryRecord(
                grant=capability.secret_handles[0],
                route_ids=(capability.routes[0].route_id,),
            ),
        ),
        "sandbox_runtimes": (
            c.SandboxRuntimeRegistryRecord(
                binding=c.SandboxBinding(
                    runtime_id=capability.sandbox.runtime_id,
                    runtime_class=capability.sandbox.runtime_class,
                    driver_implementation_digest=capability.sandbox.driver_implementation_digest,
                    runtime_binary_digest=capability.sandbox.runtime_binary_digest,
                    security_policy_digest=capability.sandbox.security_policy_digest,
                    image_digest=capability.sandbox.image_digest,
                    network_policy_digest=capability.sandbox.network_policy_digest,
                )
            ),
            c.SandboxRuntimeRegistryRecord(binding=verifier_runtime),
        ),
        "images": (
            c.ImageRegistryRecord(
                image_digest=capability.verifier.image_digest,
                runtime_id=verifier_runtime.runtime_id,
                repository_binding_digests=(),
            ),
            c.ImageRegistryRecord(
                image_digest=capability.sandbox.image_digest,
                runtime_id=capability.sandbox.runtime_id,
                repository_binding_digests=(repository_binding_digest,),
            ),
        ),
        "repository_bindings": (
            c.RepositoryBindingRegistryRecord(
                binding_digest=repository_binding_digest,
                repository_snapshot_digest=capability.task.repository_snapshot_digest,
                image_digest=capability.sandbox.image_digest,
            ),
        ),
        "task_datasets": (
            c.TaskDatasetRegistryRecord(
                task_contract_digest=capability.task.task_contract_digest,
                task_binding_digest=capability.task.task_binding_digest,
                repository_snapshot_digest=capability.task.repository_snapshot_digest,
                dataset_digests=capability.task.dataset_digests,
                input_artifact_digests=capability.task.input_artifact_digests,
            ),
        ),
        "models": (
            c.ModelRegistryRecord(
                identity=c.ModelIdentity(
                    model_id="model-a",
                    model_digest=capability.policy_slots[0].model_digest,
                    tokenizer_digest=capability.policy_slots[0].tokenizer_digest,
                    checkpoint_digest=capability.policy_slots[0].checkpoint_digest,
                )
            ),
        ),
        "verifiers": (
            c.VerifierRegistryRecord(
                grant=capability.verifier,
                runtime_id=verifier_runtime.runtime_id,
                runtime_class=verifier_runtime.runtime_class,
                security_policy_digest=verifier_runtime.security_policy_digest,
            ),
        ),
        "evidence_policies": (
            c.EvidencePolicyRegistryRecord(
                policy=capability.evidence,
                required_roles=("patch", "transcript"),
            ),
        ),
        "retention_policies": (
            c.RetentionPolicyRegistryRecord(grant=retention_grant),
        ),
        "policy_capability_attestations": (attestation_record,),
    }
    component_digest_fields = {
        "runners": "runner_registry_digest",
        "tools": "tool_registry_digest",
        "setups": "setup_registry_digest",
        "routes": "route_registry_digest",
        "secret_handles": "secret_handle_registry_digest",
        "sandbox_runtimes": "sandbox_runtime_registry_digest",
        "images": "image_registry_digest",
        "repository_bindings": "repository_binding_registry_digest",
        "task_datasets": "task_dataset_registry_digest",
        "models": "model_registry_digest",
        "verifiers": "verifier_registry_digest",
        "evidence_policies": "evidence_policy_registry_digest",
        "retention_policies": "retention_policy_registry_digest",
        "policy_capability_attestations": "policy_capability_registry_digest",
    }
    component_digests = {
        component_digest_fields[component]: independent_digest(
            {
                "schema_version": c.REGISTRY_SNAPSHOT_SCHEMA_VERSION,
                "component": component,
                "records": [record.to_canonical_obj() for record in records],
            }
        )
        for component, records in registry_records.items()
    }
    digests = c.RegistryDigestSet(
        **component_digests,
        snapshot_digest=independent_digest(
            {
                "schema_version": c.REGISTRY_SNAPSHOT_SCHEMA_VERSION,
                "component_digests": component_digests,
            }
        ),
    )
    registries = c.RegistrySnapshotSet(digests=digests, **registry_records)
    compiler_payload = next(
        item["payload"]["compiled"]["compiler"]
        for item in _load_json(CANONICAL_VECTORS)["vectors"]
        if item["artifact_kind"] == "admission_request"
    )
    compiler_identity = c.CompilerIdentity.model_validate(compiler_payload)
    ceiling_retention = c.RetentionPolicyGrant(
        policy=ceiling_vector.retention,
        minimum_seconds=86_400,
        maximum_seconds=2_592_000,
    )
    ceiling = c.OperatorCeiling(
        runner_bindings=(ceiling_vector.runner,),
        tool_grants=ceiling_vector.tools,
        setup_grants=ceiling_vector.setup_plans,
        route_grants=ceiling_vector.routes,
        secret_handle_grants=ceiling_vector.secret_handles,
        sandbox_bindings=(
            c.SandboxBinding(
                runtime_id=ceiling_vector.sandbox.runtime_id,
                runtime_class=ceiling_vector.sandbox.runtime_class,
                driver_implementation_digest=ceiling_vector.sandbox.driver_implementation_digest,
                runtime_binary_digest=ceiling_vector.sandbox.runtime_binary_digest,
                security_policy_digest=ceiling_vector.sandbox.security_policy_digest,
                image_digest=ceiling_vector.sandbox.image_digest,
                network_policy_digest=ceiling_vector.sandbox.network_policy_digest,
            ),
        ),
        repository_snapshot_digests=(ceiling_vector.task.repository_snapshot_digest,),
        task_contract_digests=(ceiling_vector.task.task_contract_digest,),
        task_binding_digests=(ceiling_vector.task.task_binding_digest,),
        dataset_digests=ceiling_vector.task.dataset_digests,
        model_bindings=(
            c.ModelIdentity(
                model_id="model-a",
                model_digest=ceiling_vector.policy_slots[0].model_digest,
                tokenizer_digest=ceiling_vector.policy_slots[0].tokenizer_digest,
                checkpoint_digest=ceiling_vector.policy_slots[0].checkpoint_digest,
            ),
        ),
        verifier_grants=(ceiling_vector.verifier,),
        policy_slot_grants=ceiling_vector.policy_slots,
        evidence_policies=(ceiling_vector.evidence,),
        retention_policies=(ceiling_retention,),
        mutable_pointer_rules=ceiling_vector.mutable_pointers,
        resource_maxima=ceiling_vector.resources,
        execution_maxima=ceiling_vector.limits,
        allowed_egress_route_ids=ceiling_vector.sandbox.egress_route_ids,
        mount_grants=ceiling_vector.sandbox.mounts,
        artifact_policy_maximum=ceiling_vector.artifacts,
    )
    policy = c.AdmissionPolicySnapshot(
        policy_id="operator-default",
        revision="2026-07-10.1",
        subject_scope_digest=_d("1"),
        compiler_constraints=c.CompilerConstraints(
            allowed_compilers=(compiler_identity,)
        ),
        registry_digests=digests,
        ceiling=ceiling,
        required_security=c.RequiredSecurityPolicy(
            minimum_isolation_class=c.RuntimeClass.HARDENED_DOCKER,
            required_verifier_isolation_class=c.RuntimeClass.HARDENED_DOCKER,
            required_evidence_roles=("patch", "transcript"),
            prohibited_runtime_classes=(c.RuntimeClass.TRUSTED_PROCESS,),
            minimum_retention_seconds=86_400,
        ),
        receipt_ttl_seconds=3_600,
        validity=c.ValidityWindow(
            issued_at="2026-07-10T11:00:00Z",
            not_before="2026-07-10T11:00:00Z",
            expires_at="2026-07-10T14:00:00Z",
        ),
        revocation=c.RevocationBinding(
            scope_digest=_d("1"), epoch=7, state_digest=_d("3")
        ),
    )
    provenance = {"entries": []}
    diagnostics = {"defaults": [], "notices": []}
    compiled_payload = next(
        item["payload"]["compiled"]
        for item in _load_json(CANONICAL_VECTORS)["vectors"]
        if item["artifact_kind"] == "admission_request"
    )
    compiled_payload = copy.deepcopy(compiled_payload)
    compiled_payload["provenance_digest"] = independent_digest(provenance)
    compiled_payload["diagnostics_digest"] = independent_digest(diagnostics)
    compiled = c.CompiledArtifactIdentity.model_validate(compiled_payload)
    request = c.AdmissionRequest(
        subject=c.AuthenticatedSubject(
            tenant_id="tenant-a",
            principal_id="principal-a",
            authority_scope_digest=_d("1"),
        ),
        behavior_source=c.CompiledBehaviorSource(
            manifest_digest=compiled.manifest_digest,
            semantic_digest=compiled.semantic_digest,
        ),
        compiled=compiled,
        requested_capabilities=capability,
        requested_capability_digest=capability.canonical_digest(),
        task_binding_digest=capability.task.task_binding_digest,
        policy_binding_ref=c.PolicyBindingRef(
            route_id=capability.routes[0].route_id,
            registry_revision_digest=digests.route_registry_digest,
            attestation_digest=attestation_record.attestation_digest,
        ),
        admission_policy_digest=policy.canonical_digest(),
        registry_snapshot_digest=digests.snapshot_digest,
        validity=c.ValidityWindow(
            issued_at="2026-07-10T12:00:00Z",
            not_before="2026-07-10T12:00:00Z",
            expires_at="2026-07-10T13:00:00Z",
        ),
        parent_receipt_digest=None,
        overlay_chain_digest=None,
    )
    roles = {
        "compiler_identity": compiled.compiler.to_canonical_obj(),
        "compile_input_identity": {
            "bundle_digest": compiled.bundle_digest,
            "closure_digest": compiled.closure_digest,
            "compiler_input_digest": compiled.compiler_input_digest,
        },
        "semantic_identity": {
            "manifest_digest": compiled.manifest_digest,
            "semantic_digest": compiled.semantic_digest,
        },
        "requested_capabilities": capability.to_canonical_obj(),
        "task_contract": {
            "task_binding_digest": capability.task.task_binding_digest,
            "task": capability.task.to_canonical_obj(),
        },
        "mutable_pointer_declarations": [
            rule.to_canonical_obj() for rule in capability.mutable_pointers
        ],
        "provenance": provenance,
        "diagnostics": diagnostics,
        "loss_disposition": {"runner_visible_losses": []},
        "authority_disposition": {"forbidden_raw_authority": []},
    }
    compiler = RecordingCompiler(CompilerSemanticView(roles))
    clock = FixedClock(now)
    revocations = RecordingRevocations(policy.revocation)
    receipt_store = store or RecordingReceiptStore()
    receipt_authenticator = authenticator or RecordingReceiptAuthenticator()
    runtime = ConfigRuntime(
        compiler=compiler,
        policy=policy,
        registries=registries,
        revocations=revocations,
        store=receipt_store,
        clock=clock,
        authenticator=receipt_authenticator,
    )
    return AdmissionFixture(
        runtime=runtime,
        request=request,
        policy=policy,
        registries=registries,
        compiler=compiler,
        revocations=revocations,
        store=receipt_store,
        clock=clock,
        authenticator=receipt_authenticator,
    )


def test_complete_semantic_role_admission_publishes_exact_receipt_before_return(
    privileged_effects: PrivilegedEffectProbe,
) -> None:
    fixture = _admission_fixture()

    receipt_ref = fixture.runtime.admit(fixture.request)

    assert fixture.compiler.calls == [
        "verify_bundle",
        "enforce_compile_budget",
        "compile",
    ]
    assert len(fixture.store.publish_calls) == 1
    assert len(fixture.store.load_calls) == 1
    kind, published = fixture.store.publish_calls[0]
    assert kind is c.ArtifactKind.ADMISSION_RECEIPT
    assert receipt_ref.digest == "sha256:" + hashlib.sha256(published).hexdigest()
    assert fixture.store.records[receipt_ref.digest] == published
    parsed = AdmissionReceipt.model_validate_json(published)
    assert parsed.effective_capabilities == fixture.request.requested_capabilities
    assert parsed.requested_capability_digest == parsed.effective_capability_digest
    assert parsed.admission_policy_digest == fixture.policy.canonical_digest()
    assert parsed.registry_snapshot_digest == fixture.registries.digests.snapshot_digest
    assert parsed.capability_deltas == ()
    privileged_effects.assert_zero()


def test_receipt_validity_accepts_exact_ttl_and_denies_one_second_over(
    privileged_effects: PrivilegedEffectProbe,
) -> None:
    exact_fixture = _admission_fixture()
    exact_policy_value = exact_fixture.policy.model_dump(mode="json")
    exact_policy_value["receipt_ttl_seconds"] = 300
    exact_policy = c.AdmissionPolicySnapshot.model_validate(exact_policy_value)
    exact_request_value = exact_fixture.request.model_dump(mode="json")
    exact_request_value["admission_policy_digest"] = exact_policy.canonical_digest()
    exact_request_value["validity"] = {
        "issued_at": "2026-07-10T12:00:00Z",
        "not_before": "2026-07-10T12:00:00Z",
        "expires_at": "2026-07-10T12:05:00Z",
    }
    exact_request = c.AdmissionRequest.model_validate(exact_request_value)
    receipt = _runtime_like(exact_fixture, policy=exact_policy).admit(exact_request)
    assert receipt.digest in exact_fixture.store.records

    over_fixture = _admission_fixture()
    over_policy_value = over_fixture.policy.model_dump(mode="json")
    over_policy_value["receipt_ttl_seconds"] = 300
    over_policy = c.AdmissionPolicySnapshot.model_validate(over_policy_value)
    over_request_value = over_fixture.request.model_dump(mode="json")
    over_request_value["admission_policy_digest"] = over_policy.canonical_digest()
    over_request_value["validity"] = {
        "issued_at": "2026-07-10T12:00:00Z",
        "not_before": "2026-07-10T12:00:00Z",
        "expires_at": "2026-07-10T12:05:01Z",
    }
    over_request = c.AdmissionRequest.model_validate(over_request_value)
    with pytest.raises(c.ConfigRuntimeDenial) as raised:
        _runtime_like(over_fixture, policy=over_policy).admit(over_request)
    _assert_denial(
        raised.value,
        stage="identity_pinning",
        code="unpinned_identity",
        pointer="/validity",
        fixture=over_fixture,
        effects=privileged_effects,
        policy=over_policy,
    )


@pytest.mark.parametrize("checkpoint", list(c.PrivilegedCheckpoint))
def test_every_privileged_checkpoint_rechecks_the_persisted_receipt(
    checkpoint: c.PrivilegedCheckpoint,
    privileged_effects: PrivilegedEffectProbe,
) -> None:
    fixture = _admission_fixture()
    receipt_ref = fixture.runtime.admit(fixture.request)
    fixture.clock.value = datetime(2026, 7, 10, 12, 30, tzinfo=UTC)
    load_count = len(fixture.store.load_calls)

    verified = fixture.runtime.verify_receipt(
        receipt_ref,
        subject=fixture.request.subject,
        checkpoint=checkpoint,
    )

    assert len(fixture.store.load_calls) == load_count + 1
    assert verified.receipt_ref == receipt_ref
    assert verified.checkpoint is checkpoint
    assert verified.currentness.checkpoint is checkpoint
    assert verified.currentness.verified_at == "2026-07-10T12:30:00Z"
    assert verified.currentness.expires_at == "2026-07-10T13:00:00Z"
    privileged_effects.assert_zero()


def _runtime_like(
    fixture: AdmissionFixture,
    *,
    compiler: RecordingCompiler | None = None,
    policy: c.AdmissionPolicySnapshot | None = None,
    registries: c.RegistrySnapshotSet | None = None,
    revocations: RecordingRevocations | None = None,
    store: RecordingReceiptStore | None = None,
    clock: FixedClock | None = None,
    authenticator: RecordingReceiptAuthenticator | None = None,
) -> ConfigRuntime:
    return ConfigRuntime(
        compiler=compiler or fixture.compiler,
        policy=policy or fixture.policy,
        registries=registries or fixture.registries,
        revocations=revocations or fixture.revocations,
        store=store or fixture.store,
        clock=clock or fixture.clock,
        authenticator=authenticator or fixture.authenticator,
    )


def _assert_denial(
    denial: c.ConfigRuntimeDenial,
    *,
    stage: str,
    code: str,
    pointer: str | None,
    fixture: AdmissionFixture,
    effects: PrivilegedEffectProbe,
    policy: c.AdmissionPolicySnapshot | None = None,
) -> None:
    assert denial.stage.value == stage
    assert denial.code.value == code
    assert denial.pointer == pointer
    corpus = _load_json(ADMISSION_DENIALS)
    expected_schema = (
        corpus["compiled_schema_digest"]
        if stage == "compiled_artifact_verification"
        else corpus["request_schema_digest"]
    )
    assert denial.policy_digest == (policy or fixture.policy).canonical_digest()
    assert denial.schema_digest == expected_schema
    assert denial.side_effect_boundary is c.SideEffectBoundary.PRE_ADMISSION
    assert fixture.store.publish_calls == []
    effects.assert_zero()


def test_executable_denials_bind_the_independently_frozen_policy_digest() -> None:
    fixture = _admission_fixture()
    corpus = _load_json(ADMISSION_DENIALS)

    assert fixture.policy.to_canonical_obj() == corpus["policy_identity_payload"]
    assert fixture.policy.canonical_digest() == corpus["policy_digest"]


@pytest.mark.parametrize(
    ("dimension", "pointer", "replacement", "code", "expected_pointer"),
    [
        ("runner", "/runner/implementation_digest", _d("f"), "unsupported_capability", "/requested_capabilities/runner/implementation_digest"),
        ("tools", "/tools/0/implementation_digest", _d("f"), "unsupported_capability", "/requested_capabilities/tools/0/implementation_digest"),
        ("setup_plans", "/setup_plans/0/implementation_digest", _d("f"), "unsupported_capability", "/requested_capabilities/setup_plans/0/implementation_digest"),
        ("routes", "/routes/0/protocol_abi", "responses-v2", "unsupported_capability", "/requested_capabilities/routes/0/protocol_abi"),
        ("secret_handles", "/secret_handles/0/handle_version_digest", _d("f"), "unsupported_capability", "/requested_capabilities/secret_handles/0/handle_version_digest"),
        ("sandbox", "/sandbox/image_digest", _d("f"), "unsupported_capability", "/requested_capabilities/sandbox/image_digest"),
        ("resources", "/resources/cpu_millis", 2_001, "operator_ceiling_exceeded", "/requested_capabilities/resources/cpu_millis"),
        ("limits", "/limits/max_turns", 9, "operator_ceiling_exceeded", "/requested_capabilities/limits/max_turns"),
        ("task", "/task/task_contract_digest", _d("f"), "unsupported_capability", "/requested_capabilities/task/task_contract_digest"),
        ("policy_slots", "/policy_slots/0/model_digest", _d("f"), "unsupported_capability", "/requested_capabilities/policy_slots/0/model_digest"),
        ("verifier", "/verifier/implementation_digest", _d("f"), "unsupported_capability", "/requested_capabilities/verifier/implementation_digest"),
        ("mutable_pointers", "/mutable_pointers/0/value_schema_digest", _d("f"), "unsupported_capability", "/requested_capabilities/mutable_pointers/0/value_schema_digest"),
        ("artifacts", "/artifacts/max_total_bytes", 4_194_305, "operator_ceiling_exceeded", "/requested_capabilities/artifacts/max_total_bytes"),
        ("evidence", "/evidence/revision_digest", _d("f"), "required_security_missing", "/requested_capabilities/evidence/revision_digest"),
        ("retention", "/retention/revision_digest", _d("f"), "retention_out_of_bounds", "/requested_capabilities/retention/revision_digest"),
    ],
)
def test_above_ceiling_denies_every_capability_dimension_without_clamping(
    dimension: str,
    pointer: str,
    replacement: Any,
    code: str,
    expected_pointer: str,
    privileged_effects: PrivilegedEffectProbe,
) -> None:
    requested = _replace_pointer(_base_capability_payload(), pointer, replacement)
    if dimension == "setup_plans":
        requested["setup_plans"][0]["plan_digest"] = independent_digest(
            _setup_authority_projection(
                requested["setup_plans"][0], requested["task"]
            )
        )
    elif dimension == "routes":
        requested["policy_slots"][0]["protocol_abi"] = replacement
        requested["routes"][0]["route_revision_digest"] = independent_digest(
            _route_authority_projection(requested["routes"][0])
        )
        expected_pointer = "/requested_capabilities/routes/0/route_revision_digest"
    fixture = _admission_fixture(capability_payload=requested)

    with pytest.raises(c.ConfigRuntimeDenial) as caught:
        fixture.runtime.admit(fixture.request)

    _assert_denial(
        caught.value,
        stage="capability_intersection",
        code=code,
        pointer=expected_pointer,
        fixture=fixture,
        effects=privileged_effects,
    )


@pytest.mark.parametrize(
    ("changes", "expected_dimension", "expected_pointer", "expected_value"),
    [
        ((('/resources/cpu_millis', 1_999),), "resources", "/resources/cpu_millis", 1_999),
        ((('/limits/max_turns', 7),), "limits", "/limits/max_turns", 7),
        ((('/tools/0/capability_ids', ["read"]),), "tools", "/tools/0/capability_ids", ["read"]),
        ((('/task/dataset_digests', []),), "task", "/task/dataset_digests", []),
        ((('/mutable_pointers', []),), "mutable_pointers", "/mutable_pointers", []),
        ((('/artifacts/allowed_roles', ["patch"]), ('/artifacts/max_each_bytes', 524_288), ('/artifacts/max_total_bytes', 1_048_576)), "artifacts", "/artifacts/max_total_bytes", 1_048_576),
    ],
)
def test_below_ceiling_authority_is_preserved_exactly_without_clamping(
    changes: tuple[tuple[str, Any], ...],
    expected_dimension: str,
    expected_pointer: str,
    expected_value: Any,
    privileged_effects: PrivilegedEffectProbe,
) -> None:
    requested = _base_capability_payload()
    for pointer, replacement in changes:
        requested = _replace_pointer(requested, pointer, replacement)
    fixture = _admission_fixture(capability_payload=requested)

    receipt_ref = fixture.runtime.admit(fixture.request)
    receipt = AdmissionReceipt.model_validate_json(fixture.store.records[receipt_ref.digest])
    effective = receipt.effective_capabilities.to_canonical_obj()

    observed = effective
    for token in expected_pointer[1:].split("/"):
        observed = observed[int(token)] if isinstance(observed, list) else observed[token]
    assert observed == expected_value
    assert receipt.effective_capabilities == fixture.request.requested_capabilities
    assert len(receipt.capability_deltas) == 1
    delta = receipt.capability_deltas[0]
    assert delta.dimension.value == expected_dimension
    assert delta.reason_code == "below_operator_ceiling"
    assert delta.effective_digest == independent_digest(effective[expected_dimension])
    ceiling_payloads = {
        "resources": fixture.policy.ceiling.resource_maxima.to_canonical_obj(),
        "limits": fixture.policy.ceiling.execution_maxima.to_canonical_obj(),
        "tools": [item.to_canonical_obj() for item in fixture.policy.ceiling.tool_grants],
        "sandbox": {
            "bindings": [item.to_canonical_obj() for item in fixture.policy.ceiling.sandbox_bindings],
            "allowed_egress_route_ids": list(fixture.policy.ceiling.allowed_egress_route_ids),
            "mount_grants": [item.to_canonical_obj() for item in fixture.policy.ceiling.mount_grants],
        },
        "task": {
            "repository_snapshot_digests": list(fixture.policy.ceiling.repository_snapshot_digests),
            "task_contract_digests": list(fixture.policy.ceiling.task_contract_digests),
            "task_binding_digests": list(fixture.policy.ceiling.task_binding_digests),
            "dataset_digests": list(fixture.policy.ceiling.dataset_digests),
        },
        "mutable_pointers": [item.to_canonical_obj() for item in fixture.policy.ceiling.mutable_pointer_rules],
        "artifacts": fixture.policy.ceiling.artifact_policy_maximum.to_canonical_obj(),
    }
    assert delta.ceiling_digest == independent_digest(ceiling_payloads[expected_dimension])
    privileged_effects.assert_zero()


@pytest.mark.parametrize(
    ("missing_role", "code", "pointer"),
    [
        ("compiler_identity", "compiled_digest_mismatch", "/compiler_view/compiler_identity"),
        ("compile_input_identity", "compiled_digest_mismatch", "/compiler_view/compile_input_identity"),
        ("semantic_identity", "compiled_digest_mismatch", "/compiler_view/semantic_identity"),
        ("requested_capabilities", "incomplete_capability_vector", "/compiler_view/requested_capabilities"),
        ("task_contract", "incomplete_task_contract", "/compiler_view/task_contract"),
        ("mutable_pointer_declarations", "invalid_mutable_pointer_declaration", "/compiler_view/mutable_pointer_declarations"),
        ("provenance", "compiled_digest_mismatch", "/compiler_view/provenance"),
        ("diagnostics", "compiled_digest_mismatch", "/compiler_view/diagnostics"),
        ("loss_disposition", "runner_visible_loss", "/compiler_view/loss_disposition"),
        ("authority_disposition", "forbidden_raw_authority", "/compiler_view/authority_disposition"),
    ],
)
def test_missing_compiler_semantic_roles_deny_before_publication(
    missing_role: str,
    code: str,
    pointer: str,
    privileged_effects: PrivilegedEffectProbe,
) -> None:
    fixture = _admission_fixture()
    roles = _mutable_json(fixture.compiler.view.roles)
    del roles[missing_role]
    compiler = RecordingCompiler(CompilerSemanticView(roles))
    runtime = _runtime_like(fixture, compiler=compiler)

    with pytest.raises(c.ConfigRuntimeDenial) as caught:
        runtime.admit(fixture.request)

    _assert_denial(
        caught.value,
        stage="compiled_artifact_verification",
        code=code,
        pointer=pointer,
        fixture=fixture,
        effects=privileged_effects,
    )


@pytest.mark.parametrize(
    ("case_id", "role", "pointer", "replacement"),
    [
        ("unsupported_manifest_schema", "compiler_identity", "/manifest_schema_digest", _d("f")),
        ("unsupported_canonicalizer", "compiler_identity", "/canonicalizer_id", "unsupported-jcs"),
        ("compiler_code_digest_mismatch", "compiler_identity", "/code_digest", _d("f")),
        ("compiler_input_digest_mismatch", "compile_input_identity", "/compiler_input_digest", _d("f")),
        ("semantic_digest_mismatch", "semantic_identity", "/semantic_digest", _d("f")),
    ],
)
def test_malformed_compiler_identity_reports_match_frozen_denial_dispositions(
    case_id: str,
    role: str,
    pointer: str,
    replacement: Any,
    privileged_effects: PrivilegedEffectProbe,
) -> None:
    fixture = _admission_fixture()
    roles = _mutable_json(fixture.compiler.view.roles)
    roles[role] = _replace_pointer(roles[role], pointer, replacement)
    runtime = _runtime_like(
        fixture, compiler=RecordingCompiler(CompilerSemanticView(roles))
    )
    expected = _frozen_denial(case_id)

    with pytest.raises(c.ConfigRuntimeDenial) as caught:
        runtime.admit(fixture.request)

    _assert_denial(
        caught.value,
        stage=expected["stage"],
        code=expected["code"],
        pointer=expected["pointer"],
        fixture=fixture,
        effects=privileged_effects,
    )
    assert caught.value.policy_digest == expected["policy_digest"]
    assert caught.value.schema_digest == expected["schema_digest"]


def test_copied_manifest_digest_matches_frozen_first_violated_invariant(
    privileged_effects: PrivilegedEffectProbe,
) -> None:
    fixture = _admission_fixture()
    request_payload = fixture.request.to_canonical_obj()
    request_payload["behavior_source"]["manifest_digest"] = _d("f")
    with pytest.raises(ValidationError):
        AdmissionRequest.model_validate(request_payload)

    source = fixture.request.behavior_source
    source_values = {
        name: getattr(source, name) for name in type(source).model_fields
    }
    source_values["manifest_digest"] = _d("f")
    malformed_source = type(source).model_construct(**source_values)
    request_values = {
        name: getattr(fixture.request, name)
        for name in type(fixture.request).model_fields
    }
    request_values["behavior_source"] = malformed_source
    malformed_request = AdmissionRequest.model_construct(**request_values)
    with pytest.raises(c.ConfigRuntimeDenial) as caught:
        fixture.runtime.admit(malformed_request)

    _assert_denial(
        caught.value,
        stage="subject_authentication",
        code="unauthenticated_subject",
        pointer="/request",
        fixture=fixture,
        effects=privileged_effects,
    )


def test_runtime_abi_downgrade_matches_frozen_first_violated_invariant(
    privileged_effects: PrivilegedEffectProbe,
) -> None:
    fixture = _admission_fixture()
    compiler_identity = fixture.request.compiled.compiler
    compiler_values = {
        name: getattr(compiler_identity, name)
        for name in type(compiler_identity).model_fields
    }
    compiler_values["runtime_abi"] = "breadboard.conductor.v0"
    malformed_compiler = type(compiler_identity).model_construct(**compiler_values)
    compiled = fixture.request.compiled
    compiled_values = {
        name: getattr(compiled, name) for name in type(compiled).model_fields
    }
    compiled_values["compiler"] = malformed_compiler
    malformed_compiled = type(compiled).model_construct(**compiled_values)
    request_values = {
        name: getattr(fixture.request, name)
        for name in type(fixture.request).model_fields
    }
    request_values["compiled"] = malformed_compiled
    malformed_request = AdmissionRequest.model_construct(**request_values)
    roles = _mutable_json(fixture.compiler.view.roles)
    roles["compiler_identity"] = malformed_compiler.to_canonical_obj()
    runtime = _runtime_like(
        fixture, compiler=RecordingCompiler(CompilerSemanticView(roles))
    )
    expected = _frozen_denial("runtime_abi_downgrade")

    with pytest.raises(c.ConfigRuntimeDenial) as caught:
        runtime.admit(malformed_request)

    _assert_denial(
        caught.value,
        stage=expected["stage"],
        code=expected["code"],
        pointer=expected["pointer"],
        fixture=fixture,
        effects=privileged_effects,
    )
    assert caught.value.policy_digest == expected["policy_digest"]
    assert caught.value.schema_digest == expected["schema_digest"]


@pytest.mark.parametrize(
    ("role", "bad_value", "code", "pointer"),
    [
        ("loss_disposition", {"runner_visible_losses": ["lost-runner-field"]}, "runner_visible_loss", "/compiler_view/loss_disposition/runner_visible_losses/0"),
        ("authority_disposition", {"forbidden_raw_authority": [{"raw_secret": "MARKER_SECRET", "url": "https://user:pass@example.test"}]}, "forbidden_raw_authority", "/compiler_view/authority_disposition/forbidden_raw_authority/0"),
    ],
)
def test_loss_and_raw_authority_denials_are_typed_and_secret_free(
    role: str,
    bad_value: dict[str, Any],
    code: str,
    pointer: str,
    privileged_effects: PrivilegedEffectProbe,
) -> None:
    fixture = _admission_fixture()
    roles = _mutable_json(fixture.compiler.view.roles)
    roles[role] = bad_value
    runtime = _runtime_like(
        fixture, compiler=RecordingCompiler(CompilerSemanticView(roles))
    )

    with pytest.raises(c.ConfigRuntimeDenial) as caught:
        runtime.admit(fixture.request)

    _assert_denial(
        caught.value,
        stage="compiled_artifact_verification",
        code=code,
        pointer=pointer,
        fixture=fixture,
        effects=privileged_effects,
    )
    encoded = caught.value.canonical_bytes()
    assert b"MARKER_SECRET" not in encoded
    assert b"user:pass" not in encoded


def test_subject_scope_denial_precedes_compiler_and_publication(
    privileged_effects: PrivilegedEffectProbe,
) -> None:
    fixture = _admission_fixture()
    payload = fixture.request.to_canonical_obj()
    payload["subject"]["authority_scope_digest"] = _d("f")
    request = AdmissionRequest.model_validate(payload)

    with pytest.raises(c.ConfigRuntimeDenial) as caught:
        fixture.runtime.admit(request)

    assert fixture.compiler.calls == []
    _assert_denial(
        caught.value,
        stage="subject_authentication",
        code="subject_scope_mismatch",
        pointer="/subject/authority_scope_digest",
        fixture=fixture,
        effects=privileged_effects,
    )


def test_registry_snapshot_mismatch_matches_frozen_denial_disposition(
    privileged_effects: PrivilegedEffectProbe,
) -> None:
    fixture = _admission_fixture()
    request_payload = fixture.request.to_canonical_obj()
    request_payload["registry_snapshot_digest"] = _d("f")
    request = AdmissionRequest.model_validate(request_payload)
    expected = _frozen_denial("registry_snapshot_mismatch")

    with pytest.raises(c.ConfigRuntimeDenial) as caught:
        fixture.runtime.admit(request)

    _assert_denial(
        caught.value,
        stage=expected["stage"],
        code=expected["code"],
        pointer=expected["pointer"],
        fixture=fixture,
        effects=privileged_effects,
    )
    assert caught.value.policy_digest == expected["policy_digest"]
    assert caught.value.schema_digest == expected["schema_digest"]


def test_registry_component_and_root_digests_match_independent_equations() -> None:
    fixture = _admission_fixture()
    payload = fixture.registries.to_canonical_obj()
    rebound = _rebind_registry_payload(payload)

    assert payload["digests"] == rebound["digests"]
    component_digests = {
        key: value
        for key, value in payload["digests"].items()
        if key != "snapshot_digest"
    }
    assert payload["digests"]["snapshot_digest"] == independent_digest(
        {
            "schema_version": c.REGISTRY_SNAPSHOT_SCHEMA_VERSION,
            "component_digests": component_digests,
        }
    )


@pytest.mark.parametrize(
    "registry_field",
    [
        "runners",
        "tools",
        "setups",
        "routes",
        "secret_handles",
        "sandbox_runtimes",
        "images",
        "repository_bindings",
        "task_datasets",
        "models",
        "verifiers",
        "evidence_policies",
        "retention_policies",
        "policy_capability_attestations",
    ],
)
def test_every_registry_family_rejects_record_mutation_under_stale_digests(
    registry_field: str,
    privileged_effects: PrivilegedEffectProbe,
) -> None:
    fixture = _admission_fixture()
    payload = fixture.registries.to_canonical_obj()
    payload[registry_field] = []

    with pytest.raises(ValidationError, match="registry_digest|exact registry records"):
        c.RegistrySnapshotSet.model_validate(payload)

    assert fixture.store.publish_calls == []
    privileged_effects.assert_zero()


@pytest.mark.parametrize(
    ("case_id", "changes", "code", "pointer"),
    [
        (
            "unknown_route_registry_binding",
            (("/routes", None),),
            "unknown_route",
            "/requested_capabilities/routes/1/route_id",
        ),
        (
            "unknown_secret_registry_binding",
            (("/secret_handles", None),),
            "unknown_secret_handle",
            "/requested_capabilities/secret_handles/1/handle_id",
        ),
        (
            "unknown_dataset_registry_binding",
            (("/task/dataset_digests", None),),
            "unknown_dataset",
            "/requested_capabilities/task/dataset_digests/1",
        ),
    ],
)
def test_unknown_members_in_registered_collections_do_not_fallback(
    case_id: str,
    changes: tuple[tuple[str, None], ...],
    code: str,
    pointer: str,
    privileged_effects: PrivilegedEffectProbe,
) -> None:
    del changes
    requested = _base_capability_payload()
    if case_id == "unknown_route_registry_binding":
        requested["routes"].append(
            {
                "route_id": "unknown-route",
                "route_revision_digest": _d("f"),
                "protocol_abi": "responses-v1",
                "credential_handle_id": "policy-credential",
            }
        )
    elif case_id == "unknown_secret_registry_binding":
        requested["secret_handles"].append(
            {
                "handle_id": "unknown-secret",
                "handle_version_digest": _d("f"),
                "scope_digest": _d("f"),
            }
        )
    else:
        requested["task"]["dataset_digests"].append(_d("f"))
    fixture = _admission_fixture(capability_payload=requested)
    base = _admission_fixture()
    request_payload = fixture.request.to_canonical_obj()
    request_payload["admission_policy_digest"] = base.policy.canonical_digest()
    request_payload["registry_snapshot_digest"] = base.registries.digests.snapshot_digest
    request_payload["policy_binding_ref"]["registry_revision_digest"] = (
        base.registries.digests.route_registry_digest
    )
    request = c.AdmissionRequest.model_validate(request_payload)
    runtime = _runtime_like(
        fixture,
        policy=base.policy,
        registries=base.registries,
    )

    with pytest.raises(c.ConfigRuntimeDenial) as caught:
        runtime.admit(request)

    _assert_denial(
        caught.value,
        stage="registry_resolution",
        code=code,
        pointer=pointer,
        fixture=fixture,
        effects=privileged_effects,
        policy=base.policy,
    )


@pytest.mark.parametrize(
    ("case_id", "code", "pointer"),
    [
        ("repository_image_cross_binding", "repository_image_mismatch", "/requested_capabilities/sandbox/image_digest"),
        ("route_secret_cross_binding", "registry_binding_mismatch", "/requested_capabilities/routes/0/credential_handle_id"),
        ("model_tokenizer_checkpoint_cross_binding", "model_identity_mismatch", "/requested_capabilities/policy_slots/0/checkpoint_digest"),
        ("verifier_schema_cross_binding", "verifier_identity_mismatch", "/requested_capabilities/verifier/image_digest"),
        ("task_dataset_cross_binding", "registry_binding_mismatch", "/requested_capabilities/task/dataset_digests/0"),
    ],
)
def test_registry_cross_bindings_are_revalidated_at_identity_pinning(
    case_id: str,
    code: str,
    pointer: str,
    privileged_effects: PrivilegedEffectProbe,
) -> None:
    fixture = _admission_fixture()
    payload = fixture.registries.to_canonical_obj()
    if case_id == "repository_image_cross_binding":
        payload["repository_bindings"][0]["image_digest"] = _d("f")
    elif case_id == "route_secret_cross_binding":
        payload["secret_handles"][0]["route_ids"] = []
    elif case_id == "model_tokenizer_checkpoint_cross_binding":
        payload["models"][0]["identity"]["checkpoint_digest"] = _d("f")
    elif case_id == "verifier_schema_cross_binding":
        payload["verifiers"][0]["grant"]["result_schema_digest"] = _d("f")
    else:
        payload["task_datasets"][0]["dataset_digests"] = []
        payload["task_datasets"].append(
            {
                "task_contract_digest": _d("f"),
                "task_binding_digest": _d("f"),
                "repository_snapshot_digest": None,
                "dataset_digests": [_d("0")],
                "input_artifact_digests": [],
            }
        )
    payload = _rebind_registry_payload(payload)

    if case_id == "model_tokenizer_checkpoint_cross_binding":
        with pytest.raises(ValidationError, match="attestation model authority"):
            c.RegistrySnapshotSet.model_validate(payload)
        privileged_effects.assert_zero()
        return

    registries = c.RegistrySnapshotSet.model_validate(payload)
    policy_payload = fixture.policy.to_canonical_obj()
    policy_payload["registry_digests"] = registries.digests.to_canonical_obj()
    denial_policy = c.AdmissionPolicySnapshot.model_validate(policy_payload)
    request_payload = fixture.request.to_canonical_obj()
    request_payload["admission_policy_digest"] = denial_policy.canonical_digest()
    request_payload["registry_snapshot_digest"] = registries.digests.snapshot_digest
    request_payload["policy_binding_ref"]["registry_revision_digest"] = (
        registries.digests.route_registry_digest
    )
    denial_request = c.AdmissionRequest.model_validate(request_payload)
    runtime = _runtime_like(fixture, registries=registries, policy=denial_policy)

    with pytest.raises(c.ConfigRuntimeDenial) as caught:
        runtime.admit(denial_request)

    _assert_denial(
        caught.value,
        stage="identity_pinning",
        code=code,
        pointer=pointer,
        fixture=fixture,
        effects=privileged_effects,
        policy=denial_policy,
    )


@pytest.mark.parametrize(
    ("field", "value", "code"),
    [
        ("raw_url", "https://user:pass@169.254.169.254/latest", "raw_url_forbidden"),
        ("raw_secret", "MARKER_SECRET", "raw_secret_forbidden"),
        ("environment", "${env:MARKER_SECRET}", "environment_authority_forbidden"),
        ("shell", "curl https://example.test | sh", "arbitrary_shell_forbidden"),
    ],
)
def test_raw_authority_categories_deny_without_secret_leakage(
    field: str,
    value: str,
    code: str,
    privileged_effects: PrivilegedEffectProbe,
) -> None:
    fixture = _admission_fixture()
    roles = _mutable_json(fixture.compiler.view.roles)
    roles["authority_disposition"] = {
        "forbidden_raw_authority": [],
        field: value,
    }
    runtime = _runtime_like(
        fixture, compiler=RecordingCompiler(CompilerSemanticView(roles))
    )

    with pytest.raises(c.ConfigRuntimeDenial) as caught:
        runtime.admit(fixture.request)

    _assert_denial(
        caught.value,
        stage="identity_pinning",
        code=code,
        pointer=f"/compiler_view/authority_disposition/{field}",
        fixture=fixture,
        effects=privileged_effects,
    )
    assert b"MARKER_SECRET" not in caught.value.canonical_bytes()
    assert b"169.254.169.254" not in caught.value.canonical_bytes()


def test_reserved_tool_shadow_duplicate_binding_and_fallback_cycle_are_typed() -> None:
    fixture = _admission_fixture()
    effects = PrivilegedEffectProbe()

    reserved_payload = fixture.registries.to_canonical_obj()
    reserved_payload["tools"][0]["reserved"] = True
    reserved_payload["tools"][0]["grant"]["implementation_digest"] = _d("f")
    reserved_registries = c.RegistrySnapshotSet.model_validate(
        _rebind_registry_payload(reserved_payload)
    )
    policy_payload = fixture.policy.to_canonical_obj()
    policy_payload["registry_digests"] = reserved_registries.digests.to_canonical_obj()
    reserved_policy = c.AdmissionPolicySnapshot.model_validate(policy_payload)
    request_payload = fixture.request.to_canonical_obj()
    request_payload["admission_policy_digest"] = reserved_policy.canonical_digest()
    request_payload["registry_snapshot_digest"] = reserved_registries.digests.snapshot_digest
    request_payload["policy_binding_ref"]["registry_revision_digest"] = (
        reserved_registries.digests.route_registry_digest
    )
    reserved_request = c.AdmissionRequest.model_validate(request_payload)
    reserved_runtime = _runtime_like(
        fixture, registries=reserved_registries, policy=reserved_policy
    )
    with pytest.raises(c.ConfigRuntimeDenial) as reserved:
        reserved_runtime.admit(reserved_request)
    _assert_denial(
        reserved.value,
        stage="registry_resolution",
        code="reserved_tool_shadow",
        pointer="/requested_capabilities/tools/0/tool_id",
        fixture=fixture,
        effects=effects,
        policy=reserved_policy,
    )

    for marker, code, pointer in (
        ("duplicate_binding", "duplicate_binding", "/requested_capabilities/tools/1/tool_id"),
        ("fallback_cycle", "fallback_cycle", "/compiler_view/requested_capabilities/tools"),
    ):
        roles = _mutable_json(fixture.compiler.view.roles)
        roles["authority_disposition"] = {
            "forbidden_raw_authority": [],
            marker: True,
        }
        runtime = _runtime_like(
            fixture, compiler=RecordingCompiler(CompilerSemanticView(roles))
        )
        with pytest.raises(c.ConfigRuntimeDenial) as caught:
            runtime.admit(fixture.request)
        _assert_denial(
            caught.value,
            stage="registry_resolution",
            code=code,
            pointer=pointer,
            fixture=fixture,
            effects=effects,
        )

    duplicate_snapshot = fixture.registries.to_canonical_obj()
    duplicate_snapshot["tools"].append(copy.deepcopy(duplicate_snapshot["tools"][0]))
    with pytest.raises(ValidationError, match="duplicate"):
        c.RegistrySnapshotSet.model_validate(duplicate_snapshot)


def test_mutable_image_tag_is_constructor_invalid_before_runtime_admission() -> None:
    payload = _base_capability_payload()
    payload["sandbox"]["image_digest"] = "ubuntu:latest"

    with pytest.raises(ValidationError):
        CapabilityVector.model_validate(payload)


@pytest.mark.parametrize(
    ("failure_at", "expected_stage", "expected_code", "expected_calls"),
    [
        ("verify_bundle", "bundle_integrity", "compiled_digest_mismatch", ["verify_bundle"]),
        (
            "enforce_compile_budget",
            "compile_budget",
            "operator_ceiling_exceeded",
            ["verify_bundle", "enforce_compile_budget"],
        ),
        (
            "compile",
            "compilation",
            "compiled_digest_mismatch",
            ["verify_bundle", "enforce_compile_budget"],
        ),
    ],
)
def test_raw_compiler_failures_are_typed_redacted_and_never_published(
    failure_at: str,
    expected_stage: str,
    expected_code: str,
    expected_calls: list[str],
    privileged_effects: PrivilegedEffectProbe,
) -> None:
    fixture = _admission_fixture()
    compiler = FailingCompiler(fixture.compiler.view, failure_at)
    runtime = _runtime_like(fixture, compiler=compiler)

    with pytest.raises(c.ConfigRuntimeDenial) as caught:
        runtime.admit(fixture.request)

    denial = caught.value
    assert denial.stage.value == expected_stage
    assert denial.code.value == expected_code
    assert compiler.calls == expected_calls
    assert "MARKER_SECRET" not in str(denial)
    assert b"MARKER_SECRET" not in denial.canonical_bytes()
    assert fixture.store.publish_calls == []
    assert fixture.store.records == {}
    privileged_effects.assert_zero()


def test_compiler_semantic_roles_are_recursively_immutable() -> None:
    roles = {
        "requested_capabilities": {
            "tools": [{"tool_id": "tool-a"}],
            "authority": {"allowed": True},
        }
    }
    view = CompilerSemanticView(roles)
    roles["requested_capabilities"]["tools"].append({"tool_id": "outside"})

    with pytest.raises((AttributeError, TypeError)):
        view.roles["requested_capabilities"]["tools"].append(
            {"tool_id": "forged"}
        )
    with pytest.raises(TypeError):
        view.roles["requested_capabilities"]["authority"]["allowed"] = False

    assert tuple(view.roles["requested_capabilities"]["tools"]) == (
        {"tool_id": "tool-a"},
    )
    assert view.roles["requested_capabilities"]["authority"]["allowed"] is True


def _setup_record_authority_projection(record: dict[str, Any]) -> dict[str, Any]:
    grant = record["grant"]
    return {
        "schema_version": "bb.rl.setup-plan.v1",
        "setup_id": grant["setup_id"],
        "implementation_digest": grant["implementation_digest"],
        "argv": record["argv"],
        "input_digests": record["input_digests"],
        "writable_output_subtrees": record["writable_output_subtrees"],
        "writable_output_slots": record["writable_output_slots"],
        "route_ids": record["route_ids"],
        "secret_handle_ids": record["secret_handle_ids"],
        "timeout_ms": record["timeout_ms"],
        "expected_outputs": record["expected_outputs"],
    }


def _route_record_authority_projection(record: dict[str, Any]) -> dict[str, Any]:
    grant = record["grant"]
    return {
        "schema_version": "bb.rl.route-authority.v1",
        "route_id": grant["route_id"],
        "protocol_abi": grant["protocol_abi"],
        "credential_handle_id": grant["credential_handle_id"],
        "scheme": record["scheme"],
        "authority": record["authority"],
        "paths": record["paths"],
        "methods": record["methods"],
        "ip_policy_digest": record["ip_policy_digest"],
        "dns_policy_digest": record["dns_policy_digest"],
        "request_schema_digest": record["request_schema_digest"],
        "response_schema_digest": record["response_schema_digest"],
        "max_request_bytes": record["max_request_bytes"],
        "max_response_bytes": record["max_response_bytes"],
        "max_requests_per_minute": record["max_requests_per_minute"],
        "data_classification": record["data_classification"],
        "owner": record["owner"],
    }


@pytest.mark.parametrize(
    ("pointer", "replacement"),
    [
        ("/grant/setup_id", "workspace-prepare-mutated"),
        ("/grant/implementation_digest", _d("f")),
        ("/argv/1", "--forged-workspace"),
        ("/input_digests/0", _d("f")),
        ("/writable_output_subtrees/0", "workspace/forged"),
        ("/writable_output_slots/0", "forged"),
        ("/route_ids/0", "forged-route"),
        ("/secret_handle_ids/0", "forged-secret"),
        ("/timeout_ms", 59_999),
        ("/expected_outputs/0/artifact_id", "forged-patch"),
    ],
)
def test_setup_plan_digest_independently_binds_every_typed_authority_field(
    pointer: str,
    replacement: Any,
) -> None:
    fixture = _admission_fixture()
    record = fixture.registries.setups[0].to_canonical_obj()
    asserted = record["grant"]["plan_digest"]

    assert asserted == independent_digest(_setup_record_authority_projection(record))
    mutated = _replace_pointer(record, pointer, replacement)
    assert independent_digest(_setup_record_authority_projection(mutated)) != asserted
    with pytest.raises(ValidationError):
        c.SetupRegistryRecord.model_validate(mutated)


@pytest.mark.parametrize(
    ("pointer", "replacement"),
    [
        ("/grant/route_id", "policy-route-mutated"),
        ("/grant/protocol_abi", "responses-v2"),
        ("/grant/credential_handle_id", "credential-mutated"),
        ("/scheme", "http"),
        ("/authority", "forged.example.test"),
        ("/paths/0", "/v2/forged"),
        ("/methods/0", "PUT"),
        ("/ip_policy_digest", _d("f")),
        ("/dns_policy_digest", _d("f")),
        ("/request_schema_digest", _d("f")),
        ("/response_schema_digest", _d("f")),
        ("/max_request_bytes", 65_535),
        ("/max_response_bytes", 32_767),
        ("/max_requests_per_minute", 59),
        ("/data_classification", "restricted"),
        ("/owner/owner_id", "other-operator"),
        ("/owner/authority_scope_digest", _d("f")),
    ],
)
def test_route_revision_digest_independently_binds_every_typed_authority_field(
    pointer: str,
    replacement: Any,
) -> None:
    fixture = _admission_fixture()
    record = fixture.registries.routes[0].to_canonical_obj()
    asserted = record["grant"]["route_revision_digest"]

    assert asserted == independent_digest(_route_record_authority_projection(record))
    mutated = _replace_pointer(record, pointer, replacement)
    assert independent_digest(_route_record_authority_projection(mutated)) != asserted
    with pytest.raises(ValidationError):
        c.RouteRegistryRecord.model_validate(mutated)


def test_policy_attestation_digest_is_derived_and_unknown_attestations_deny(
    privileged_effects: PrivilegedEffectProbe,
) -> None:
    fixture = _admission_fixture()
    attestation = fixture.registries.policy_capability_attestations[0].to_canonical_obj()
    projection = {
        "schema_version": "bb.rl.policy-capability-attestation.v1",
        **{
            key: value
            for key, value in attestation.items()
            if key != "attestation_digest"
        },
    }
    assert attestation["attestation_digest"] == independent_digest(projection)

    request_payload = fixture.request.to_canonical_obj()
    request_payload["policy_binding_ref"]["attestation_digest"] = _d("f")
    request = c.AdmissionRequest.model_validate(request_payload)
    with pytest.raises(c.ConfigRuntimeDenial) as caught:
        fixture.runtime.admit(request)

    _assert_denial(
        caught.value,
        stage="registry_resolution",
        code="unknown_policy_binding",
        pointer="/policy_binding_ref/attestation_digest",
        fixture=fixture,
        effects=privileged_effects,
    )


def test_model_construct_cannot_bypass_request_digest_validation(
    privileged_effects: PrivilegedEffectProbe,
) -> None:
    fixture = _admission_fixture()
    fields_by_name = {
        name: getattr(fixture.request, name)
        for name in c.AdmissionRequest.model_fields
    }
    fields_by_name["requested_capability_digest"] = _d("f")
    forged = c.AdmissionRequest.model_construct(**fields_by_name)

    with pytest.raises(c.ConfigRuntimeDenial) as caught:
        fixture.runtime.admit(forged)

    _assert_denial(
        caught.value,
        stage="subject_authentication",
        code="unauthenticated_subject",
        pointer="/request",
        fixture=fixture,
        effects=privileged_effects,
    )
    assert fixture.compiler.calls == []


def test_unknown_task_input_artifact_is_denied_and_registered_inputs_are_pinned(
    privileged_effects: PrivilegedEffectProbe,
) -> None:
    fixture = _admission_fixture()
    receipt_ref = fixture.runtime.admit(fixture.request)
    receipt = c.AdmissionReceipt.model_validate_json(
        fixture.store.records[receipt_ref.digest]
    )
    input_pins = {
        pin.content_digest
        for pin in receipt.pins
        if pin.kind is c.PinKind.INPUT_ARTIFACT
    }
    assert input_pins == set(
        fixture.request.requested_capabilities.task.input_artifact_digests
    )

    request_payload = fixture.request.to_canonical_obj()
    request_payload["requested_capabilities"]["task"]["input_artifact_digests"] = [
        _d("f")
    ]
    request_payload["requested_capability_digest"] = independent_digest(
        request_payload["requested_capabilities"]
    )
    request = c.AdmissionRequest.model_validate(request_payload)
    roles = _mutable_json(fixture.compiler.view.roles)
    roles["requested_capabilities"] = request_payload["requested_capabilities"]
    roles["task_contract"]["task"] = request_payload["requested_capabilities"]["task"]
    runtime = _runtime_like(
        fixture,
        compiler=RecordingCompiler(CompilerSemanticView(roles)),
    )
    publication_count = len(fixture.store.publish_calls)

    with pytest.raises(c.ConfigRuntimeDenial) as caught:
        runtime.admit(request)

    assert caught.value.stage.value == "identity_pinning"
    assert caught.value.code.value == "registry_binding_mismatch"
    assert caught.value.pointer == "/requested_capabilities/task/input_artifact_digests"
    assert len(fixture.store.publish_calls) == publication_count
    privileged_effects.assert_zero()




@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        ("missing_image", "verifier image, runtime, and network authority must resolve"),
        ("missing_runtime", "verifier image, runtime, and network authority must resolve"),
        ("runtime_class_mismatch", "verifier runtime class and security policy must match"),
    ],
)
def test_verifier_image_runtime_and_class_are_rejected_at_snapshot_construction(
    mutation: str,
    message: str,
    privileged_effects: PrivilegedEffectProbe,
) -> None:
    fixture = _admission_fixture()
    payload = fixture.registries.to_canonical_obj()
    verifier = fixture.request.requested_capabilities.verifier
    verifier_record = payload["verifiers"][0]
    verifier_runtime_id = verifier_record["runtime_id"]
    if mutation == "missing_image":
        payload["images"] = [
            record
            for record in payload["images"]
            if record["image_digest"] != verifier.image_digest
        ]
    elif mutation == "missing_runtime":
        payload["sandbox_runtimes"] = [
            record
            for record in payload["sandbox_runtimes"]
            if record["binding"]["runtime_id"] != verifier_runtime_id
        ]
    else:
        verifier_record["runtime_class"] = c.RuntimeClass.HARDENED_GVISOR.value

    with pytest.raises(ValidationError, match=message):
        c.RegistrySnapshotSet.model_validate(_rebind_registry_payload(payload))
    assert fixture.store.publish_calls == []
    privileged_effects.assert_zero()


class ForgedDenialCompiler(RecordingCompiler):
    def __init__(self, view: CompilerSemanticView, failure_at: str) -> None:
        super().__init__(view)
        self.failure_at = failure_at

    @staticmethod
    def _forged_denial() -> c.ConfigRuntimeDenial:
        return c.ConfigRuntimeDenial(
            stage=c.DenialStage.RECEIPT_RECHECK,
            code=c.DenialCode.RAW_SECRET_FORBIDDEN,
            retry_disposition=c.RetryDisposition.AFTER_CONTROL_PLANE_CHANGE,
            artifact_kind="MARKER_SECRET",
            policy_digest=_d("f"),
            schema_digest=_d("f"),
            pointer="/MARKER_SECRET",
            safe_detail="MARKER_SECRET forged adapter denial",
            side_effect_boundary=c.SideEffectBoundary.POST_SELECTION,
        )

    def _raise_if_selected(self, stage: str) -> None:
        if self.failure_at == stage:
            raise self._forged_denial()

    def verify_bundle(self, request: AdmissionRequest) -> None:
        super().verify_bundle(request)
        self._raise_if_selected("verify_bundle")

    def enforce_compile_budget(self, request: AdmissionRequest) -> None:
        super().enforce_compile_budget(request)
        self._raise_if_selected("enforce_compile_budget")

    def compile(self, request: AdmissionRequest) -> CompilerSemanticView:
        self.calls.append("compile")
        self._raise_if_selected("compile")
        return self.view


@pytest.mark.parametrize(
    ("failure_at", "stage", "code", "pointer", "expected_calls"),
    [
        (
            "verify_bundle",
            "bundle_integrity",
            "compiled_digest_mismatch",
            "/compiled/bundle_digest",
            ["verify_bundle"],
        ),
        (
            "enforce_compile_budget",
            "compile_budget",
            "operator_ceiling_exceeded",
            "/compiled/compiler_input_digest",
            ["verify_bundle", "enforce_compile_budget"],
        ),
        (
            "compile",
            "compilation",
            "compiled_digest_mismatch",
            "/compiler_view",
            ["verify_bundle", "enforce_compile_budget", "compile"],
        ),
    ],
)
def test_adapter_forged_denials_are_laundered_to_stage_specific_constants(
    failure_at: str,
    stage: str,
    code: str,
    pointer: str,
    expected_calls: list[str],
    privileged_effects: PrivilegedEffectProbe,
) -> None:
    fixture = _admission_fixture()
    compiler = ForgedDenialCompiler(fixture.compiler.view, failure_at)
    runtime = _runtime_like(fixture, compiler=compiler)

    with pytest.raises(c.ConfigRuntimeDenial) as caught:
        runtime.admit(fixture.request)

    _assert_denial(
        caught.value,
        stage=stage,
        code=code,
        pointer=pointer,
        fixture=fixture,
        effects=privileged_effects,
    )
    assert compiler.calls == expected_calls
    assert caught.value.retry_disposition is c.RetryDisposition.NEVER
    assert caught.value.side_effect_boundary is c.SideEffectBoundary.PRE_ADMISSION
    encoded = caught.value.canonical_bytes()
    assert b"MARKER_SECRET" not in encoded
    assert _d("f").encode() not in encoded
    assert fixture.store.records == {}
