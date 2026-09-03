from __future__ import annotations

import base64
import hashlib
import re
import unicodedata
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import Enum
from types import MappingProxyType
from typing import Any, Protocol, runtime_checkable

from breadboard_engine.compilation.contracts import (
    CANONICALIZER_ID,
    canonical_json_bytes,
    canonical_json_loads,
)

from . import contracts as c


_MAX_RECEIPT_BYTES = 4 * 1024 * 1024
_DIGEST_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
_POLICY_SCHEMA_DIGEST = "sha256:702ee6f81a47deb892f023626d4da9ef0c380a863e4c70ac42cf3bce27884c9d"
_COMPILER_VIEW_SCHEMA_DIGEST = "sha256:33f7d93fa7045e4b32454f8981f2f23da1d61b4fff2eb31297a57653526e2963"
_MAX_RUNTIME_ARTIFACT_BYTES = 4 * 1024 * 1024
_ARTIFACT_MEDIA_TYPES = {
    c.ArtifactKind.ADMISSION_RECEIPT: "application/vnd.breadboard.admission-receipt+json;version=1",
    c.ArtifactKind.COMPILED_MANIFEST: "application/vnd.breadboard.compiled-manifest+json;version=1",
    c.ArtifactKind.ADMITTED_SET: "application/vnd.breadboard.admitted-set+json;version=1",
    c.ArtifactKind.DIRECT_SELECTOR: "application/vnd.breadboard.direct-selector+json;version=1",
    c.ArtifactKind.CONFIG_SET: "application/vnd.breadboard.config-set+json;version=1",
    c.ArtifactKind.MUTATION_OVERLAY: "application/vnd.breadboard.mutation-overlay+json;version=1",
    c.ArtifactKind.SELECTION_RECORD: "application/vnd.breadboard.selection-record+json;version=1",
    c.ArtifactKind.EFFECTIVE_EXECUTION_PLAN: "application/vnd.breadboard.effective-execution-plan+json;version=1",
}
_PROTECTED_OVERLAY_ROOTS = frozenset(
    {
        "compiler",
        "config_nodes",
        "evidence",
        "images",
        "image",
        "mounts",
        "network",
        "policy_slots",
        "policy",
        "provenance",
        "repositories",
        "repository",
        "retention",
        "routes",
        "runner",
        "runtime",
        "sandbox",
        "secrets",
        "setup",
        "task",
        "verifier",
    }
)
_PROTECTED_ARTIFACT_PATHS = frozenset(
    {
        ("artifacts", "allowed_roles"),
        ("artifacts", "max_each_bytes"),
        ("artifacts", "max_total_bytes"),
        ("artifacts", "authority"),
        ("artifacts", "policy"),
        ("artifacts", "policy_digest"),
        ("artifacts", "security"),
    }
)
_REQUIRED_COMPILER_ROLES = frozenset(
    {
        "compiler_identity",
        "compile_input_identity",
        "semantic_identity",
        "requested_capabilities",
        "task_contract",
        "mutable_pointer_declarations",
        "provenance",
        "diagnostics",
        "loss_disposition",
        "authority_disposition",
    }
)
_FORBIDDEN_DETAIL_KEYS = frozenset(
    {
        "authorization",
        "credential",
        "credential_bytes",
        "headers",
        "password",
        "raw_secret",
        "secret",
        "secret_bytes",
        "token",
        "url",
    }
)




class _FrozenMapping(Mapping[str, Any]):
    """Read-only mapping whose explicit deep copy is a detached mutable projection."""

    __slots__ = ("_data",)

    def __init__(self, values: Mapping[str, Any]) -> None:
        object.__setattr__(self, "_data", MappingProxyType(dict(values)))

    def __getitem__(self, key: str) -> Any:
        return self._data[key]

    def __iter__(self):
        return iter(self._data)

    def __len__(self) -> int:
        return len(self._data)

    def __setattr__(self, name: str, value: Any) -> None:
        raise TypeError("compiler semantic mappings are immutable")

    def __deepcopy__(self, memo: dict[int, Any]) -> dict[str, Any]:
        del memo
        return {key: _deep_thaw(value) for key, value in self._data.items()}


def _deep_thaw(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {key: _deep_thaw(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_deep_thaw(item) for item in value]
    return value


def _deep_freeze(value: Any) -> Any:
    if isinstance(value, Mapping):
        return _FrozenMapping({key: _deep_freeze(item) for key, item in value.items()})
    if isinstance(value, (tuple, list)):
        return tuple(_deep_freeze(item) for item in value)
    return value


@dataclass(frozen=True, slots=True)
class CompilerSemanticView:
    """Compiler output projected by stable semantic role, never Python model name."""

    roles: Mapping[str, Any]

    def __post_init__(self) -> None:
        if not isinstance(self.roles, Mapping):
            raise TypeError("roles must be a mapping")
        copied: dict[str, Any] = {}
        for key, value in self.roles.items():
            if type(key) is not str:
                raise TypeError("semantic role names must be strings")
            # Canonical round-trip rejects non-JSON values and severs mutable aliases.
            canonical = canonical_json_loads(canonical_json_bytes(value))
            copied[key] = _deep_freeze(canonical)
        object.__setattr__(self, "roles", _FrozenMapping(copied))


@runtime_checkable
class VerifiedCompilerAdapter(Protocol):
    """Network-disabled compiler boundary exposing canonical semantic roles."""

    def verify_bundle(self, request: c.AdmissionRequest) -> None: ...

    def enforce_compile_budget(self, request: c.AdmissionRequest) -> None: ...

    def compile(self, request: c.AdmissionRequest) -> CompilerSemanticView: ...

    def extract_effective_semantics(
        self, *, canonical_manifest_bytes: bytes
    ) -> Mapping[str, Any]: ...

    def normalize_effective_semantics(
        self,
        *,
        canonical_manifest_bytes: bytes,
        effective_semantics: Mapping[str, Any],
    ) -> Mapping[str, Any]: ...


    def validate_effective_semantics(
        self,
        *,
        canonical_manifest_bytes: bytes,
        effective_semantics: Mapping[str, Any],
    ) -> str: ...


@runtime_checkable
class RevocationStore(Protocol):
    def load(self, scope_digest: str) -> c.RevocationBinding: ...


@runtime_checkable
class PolicyCapabilityRegistry(Protocol):
    """Pure immutable policy-attestation snapshot; observation performs no I/O."""

    def observe(
        self,
        *,
        binding: c.PolicyBindingRef,
        subject: c.AuthenticatedSubject,
        now: datetime,
    ) -> c.PolicyCapabilityObservation: ...


@runtime_checkable
class ConfigRuntimeStore(Protocol):
    def publish(self, *, kind: c.ArtifactKind, canonical_bytes: bytes) -> c.ArtifactRef: ...

    def load(self, digest: str, *, kind: c.ArtifactKind, max_bytes: int) -> bytes: ...

    def get_selection_binding(self, owner_key: str) -> c.SelectionBinding | None: ...

    def bind_selection_once(
        self,
        *,
        owner_key: str,
        request_digest: str,
        selection_record_digest: str,
    ) -> c.SelectionCommitToken: ...

@runtime_checkable
class Clock(Protocol):
    def current(self) -> datetime: ...


@runtime_checkable
class ReceiptAuthenticator(Protocol):
    """Authenticates exact unsigned canonical receipt bytes for server issuance."""

    @property
    def key_id(self) -> str: ...

    @property
    def algorithm(self) -> str: ...

    def sign(self, unsigned_canonical_bytes: bytes) -> bytes: ...

    def verify(self, unsigned_canonical_bytes: bytes, signature: bytes) -> bool: ...


def _obj(value: Any) -> Any:
    canonical_obj = getattr(value, "canonical_obj", None)
    if callable(canonical_obj):
        return canonical_obj()
    model_dump = getattr(value, "model_dump", None)
    if callable(model_dump):
        return model_dump(mode="json")
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, Mapping):
        return {str(key): _obj(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_obj(item) for item in value]
    return value


def _digest(value: Any) -> str:
    payload = canonical_json_bytes(_obj(value))
    return "sha256:" + hashlib.sha256(payload).hexdigest()


def _overlay_chain_digest(parent: c.AdmissionReceipt, overlay_digest: str) -> str:
    return c.derive_overlay_chain_digest(
        parent_chain_digest=parent.overlay_chain_digest,
        overlay_digest=overlay_digest,
    )


def _bytes_digest(value: bytes) -> str:
    return "sha256:" + hashlib.sha256(value).hexdigest()


def _enum(enum_type: type[Any], value: str) -> Any:
    return enum_type(value)


def _safe_detail(value: Any, *, limit: int = 240) -> str:
    """Produce bounded, recursively scrubbed diagnostic text."""

    def scrub(item: Any, depth: int = 0) -> Any:
        if depth > 5:
            return "[bounded]"
        if isinstance(item, Mapping):
            result: dict[str, Any] = {}
            for raw_key, raw_value in list(item.items())[:32]:
                key = str(raw_key)
                lowered = key.casefold()
                if lowered in _FORBIDDEN_DETAIL_KEYS or any(
                    marker in lowered
                    for marker in ("secret", "credential", "password", "authorization")
                ):
                    result[key] = "[redacted]"
                else:
                    result[key] = scrub(raw_value, depth + 1)
            return result
        if isinstance(item, (tuple, list)):
            return [scrub(child, depth + 1) for child in list(item)[:32]]
        if item is None or type(item) in {bool, int, float}:
            return item
        text = str(item)
        # Diagnostic values are never authoritative and never need path/URL payloads.
        if "://" in text or "${env:" in text or "-----BEGIN" in text:
            return "[redacted]"
        return text[:120]

    try:
        rendered = canonical_json_bytes(scrub(value)).decode("utf-8")
    except Exception:
        rendered = "diagnostic unavailable"
    return rendered[:limit]


def _subject_digest(subject: c.AuthenticatedSubject) -> str:
    return _digest(subject)


def _policy_digest(policy: c.AdmissionPolicySnapshot) -> str:
    return _digest(policy)


def _ceiling_digest(policy: c.AdmissionPolicySnapshot) -> str:
    return _digest(policy.ceiling)


def _schema_digest(
    stage: str,
    request: c.AdmissionRequest | None,
    policy: c.AdmissionPolicySnapshot | None,
) -> str:
    if stage == "compiled_artifact_verification":
        return _COMPILER_VIEW_SCHEMA_DIGEST
    if request is not None:
        return request.compiled.compiler.source_schema_digest
    if policy is not None and len(policy.compiler_constraints.allowed_compilers) == 1:
        return policy.compiler_constraints.allowed_compilers[0].source_schema_digest
    return _POLICY_SCHEMA_DIGEST


def _deny(
    stage: str,
    code: str,
    *,
    request: c.AdmissionRequest | None = None,
    subject: c.AuthenticatedSubject | None = None,
    policy: c.AdmissionPolicySnapshot | None = None,
    artifact_kind: str | None = None,
    artifact_digest: str | None = None,
    pointer: str | None = None,
    detail: Any = "denied",
    retry: str | None = None,
) -> None:
    if retry is None:
        retry = (
            "after_control_plane_change"
            if stage == "registry_resolution"
            else "never"
        )
    raise c.ConfigRuntimeDenial(
        stage=_enum(c.DenialStage, stage),
        code=_enum(c.DenialCode, code),
        retry_disposition=_enum(c.RetryDisposition, retry),
        episode_id=None,
        subject_digest=(
            _subject_digest(subject)
            if subject is not None
            else (_subject_digest(request.subject) if request is not None else None)
        ),
        artifact_kind=artifact_kind,
        artifact_digest=artifact_digest,
        policy_digest=_policy_digest(policy) if policy is not None else None,
        schema_digest=_schema_digest(stage, request, policy),
        candidate_id=None,
        pointer=pointer,
        operation_index=None,
        selection_record_digest=None,
        safe_detail=_safe_detail(detail),
        side_effect_boundary=_enum(c.SideEffectBoundary, "pre_admission"),
    )


def _deny_resolution(
    stage: str,
    code: str,
    *,
    request: c.ResolveEpisodeRequest | None = None,
    policy: c.AdmissionPolicySnapshot | None = None,
    artifact_kind: c.ArtifactKind | str | None = None,
    artifact_digest: str | None = None,
    candidate_id: str | None = None,
    pointer: str | None = None,
    operation_index: int | None = None,
    selection_record_digest: str | None = None,
    detail: Any = "denied",
    retry: str = "never",
    post_selection: bool = False,
) -> None:
    kind = artifact_kind.value if isinstance(artifact_kind, c.ArtifactKind) else artifact_kind
    raise c.ConfigRuntimeDenial(
        stage=_enum(c.DenialStage, stage),
        code=_enum(c.DenialCode, code),
        retry_disposition=_enum(c.RetryDisposition, retry),
        episode_id=request.episode_id if request is not None else None,
        subject_digest=_subject_digest(request.subject) if request is not None else None,
        artifact_kind=kind,
        artifact_digest=artifact_digest,
        policy_digest=_policy_digest(policy) if policy is not None else None,
        schema_digest=_POLICY_SCHEMA_DIGEST,
        candidate_id=candidate_id,
        pointer=pointer,
        operation_index=operation_index,
        selection_record_digest=selection_record_digest,
        safe_detail=_safe_detail(detail),
        side_effect_boundary=_enum(
            c.SideEffectBoundary,
            "post_selection" if post_selection else "pre_allocation",
        ),
    )


def _parse_timestamp(value: str) -> datetime:
    return datetime.fromisoformat(value.removesuffix("Z") + "+00:00")


def _utc_now(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("now must be timezone-aware")
    return value.astimezone(UTC)


def _timestamp(value: datetime) -> str:
    return _utc_now(value).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _same(left: Any, right: Any) -> bool:
    return canonical_json_bytes(_obj(left)) == canonical_json_bytes(_obj(right))


def _canonical_model_data(value: Any) -> Any:
    model_dump = getattr(value, "model_dump", None)
    if not callable(model_dump):
        raise TypeError("validated model required")
    dumped = model_dump(mode="json", round_trip=True, warnings="error")
    return canonical_json_loads(canonical_json_bytes(dumped))


def _revalidate_model(model_type: type[Any], value: Any) -> Any:
    return model_type.model_validate(_canonical_model_data(value))


def _find_exact(items: Sequence[Any], expected: Any, projection: Any = None) -> Any | None:
    for item in items:
        candidate = item if projection is None else getattr(item, projection)
        if _same(candidate, expected):
            return item
    return None


def _numeric_leq(requested: Any, ceiling: Any) -> tuple[bool, str | None]:
    requested_obj = _obj(requested)
    ceiling_obj = _obj(ceiling)
    for name, value in requested_obj.items():
        if type(value) is not int or type(ceiling_obj.get(name)) is not int:
            return False, name
        if value > ceiling_obj[name]:
            return False, name
    return True, None


def _mount_allowed(requested: Any, allowed: Sequence[Any]) -> bool:
    for ceiling in allowed:
        if (
            requested.source_artifact_digest == ceiling.source_artifact_digest
            and requested.target_logical_path == ceiling.target_logical_path
            and requested.max_bytes <= ceiling.max_bytes
            and (
                requested.access == ceiling.access
                or (requested.access == "ro" and ceiling.access == "rw")
            )
        ):
            return True
    return False


def _isolation_rank(value: Any) -> int:
    text = value.value if isinstance(value, Enum) else str(value)
    return {
        "trusted_process": 0,
        "hardened_docker": 1,
        "hardened_gvisor": 2,
    }.get(text, -1)


def _identity_pin(kind: str, logical_id: str, digest: str, qualifier: Any = None) -> c.ArtifactIdentity:
    return c.ArtifactIdentity(
        kind=_enum(c.PinKind, kind),
        logical_id=logical_id,
        content_digest=digest,
        qualifier_digest=None if qualifier is None else _digest(qualifier),
    )


def _pins_for(
    request: c.AdmissionRequest,
    policy: c.AdmissionPolicySnapshot,
) -> tuple[c.ArtifactIdentity, ...]:
    vector = request.requested_capabilities
    pins: list[c.ArtifactIdentity] = [
        _identity_pin("bundle", "bundle", request.compiled.bundle_digest),
        _identity_pin("closure", "closure", request.compiled.closure_digest),
        _identity_pin(
            "compiler_code", request.compiled.compiler.compiler_id, request.compiled.compiler.code_digest
        ),
        _identity_pin("compiled_manifest", "manifest", request.compiled.manifest_digest),
        _identity_pin("compiled_semantic", "semantic", request.compiled.semantic_digest),
        _identity_pin(
            "runner_implementation",
            vector.runner.adapter_id,
            vector.runner.implementation_digest,
            {"runtime_abi": vector.runner.runtime_abi},
        ),
        _identity_pin("task", "task_contract", vector.task.task_contract_digest),
        _identity_pin("task", "task_binding", vector.task.task_binding_digest),
        _identity_pin("task", "artifact_policy", _digest(vector.artifacts)),
        _identity_pin("sandbox_driver", vector.sandbox.runtime_id, vector.sandbox.driver_implementation_digest),
        _identity_pin("sandbox_runtime", vector.sandbox.runtime_id, vector.sandbox.runtime_binary_digest),
        _identity_pin("sandbox_image", vector.sandbox.runtime_id, vector.sandbox.image_digest),
        _identity_pin("network_policy", vector.sandbox.runtime_id, vector.sandbox.network_policy_digest),
        _identity_pin("mount_plan", "sandbox_mounts", _digest(vector.sandbox.mounts)),
        _identity_pin("verifier_image", vector.verifier.verifier_id, vector.verifier.image_digest),
        _identity_pin("verifier_executable", vector.verifier.verifier_id, vector.verifier.executable_digest),
        _identity_pin("verifier_code", vector.verifier.verifier_id, vector.verifier.code_digest),
        _identity_pin("verifier_input_schema", vector.verifier.verifier_id, vector.verifier.input_schema_digest),
        _identity_pin("verifier_result_schema", vector.verifier.verifier_id, vector.verifier.result_schema_digest),
        _identity_pin("evidence_policy", vector.evidence.policy_id, vector.evidence.revision_digest),
        _identity_pin("retention_policy", vector.retention.policy_id, vector.retention.revision_digest),
        _identity_pin("mutable_pointer_policy", "mutable_pointers", _digest(vector.mutable_pointers)),
        _identity_pin(
            "policy_capability_attestation",
            request.policy_binding_ref.route_id,
            request.policy_binding_ref.attestation_digest,
            {
                "registry_revision_digest": request.policy_binding_ref.registry_revision_digest,
                "required_policy_capabilities": [
                    slot.required_policy_capabilities_digest for slot in vector.policy_slots
                ],
            },
        ),
    ]
    if vector.task.repository_snapshot_digest is not None:
        pins.append(
            _identity_pin(
                "repository_snapshot", "repository", vector.task.repository_snapshot_digest
            )
        )
    for index, digest in enumerate(vector.task.dataset_digests):
        pins.append(_identity_pin("dataset", f"dataset:{index}", digest))
    for index, digest in enumerate(vector.task.input_artifact_digests):
        pins.append(_identity_pin("input_artifact", f"input:{index}", digest))
    for tool in vector.tools:
        pins.append(_identity_pin("tool_implementation", tool.tool_id, tool.implementation_digest))
    for setup in vector.setup_plans:
        pins.append(
            _identity_pin(
                "setup_implementation", setup.setup_id, setup.implementation_digest, {"plan": setup.plan_digest}
            )
        )
    for route in vector.routes:
        pins.append(
            _identity_pin(
                "route_revision", route.route_id, route.route_revision_digest, {"protocol_abi": route.protocol_abi}
            )
        )
    for secret in vector.secret_handles:
        pins.append(
            _identity_pin(
                "secret_handle_version", secret.handle_id, secret.handle_version_digest, {"scope": secret.scope_digest}
            )
        )
    for slot in vector.policy_slots:
        pins.extend(
            (
                _identity_pin("model", slot.slot_id, slot.model_digest),
                _identity_pin("tokenizer", slot.slot_id, slot.tokenizer_digest),
                _identity_pin("checkpoint", slot.slot_id, slot.checkpoint_digest),
            )
        )
    if vector.verifier.implementation_digest != vector.verifier.code_digest:
        pins.append(
            _identity_pin(
                "verifier_code",
                vector.verifier.verifier_id,
                vector.verifier.implementation_digest,
            )
        )
    return tuple(
        sorted(
            pins,
            key=lambda pin: (
                pin.kind.value if isinstance(pin.kind, Enum) else str(pin.kind),
                pin.logical_id,
                pin.content_digest,
                pin.qualifier_digest or "",
            ),
        )
    )


@dataclass(frozen=True, slots=True)
class _CandidateState:
    candidate: c.ConfigCandidate
    weight: int | None
    admission: c.VerifiedAdmission


def _selector_payload_code(value: Any, kind: c.ArtifactKind, fallback: str) -> str:
    if type(value) is not dict:
        return fallback
    if kind is c.ArtifactKind.CONFIG_SET:
        if value.get("algorithm") != "weighted-v1":
            return "unknown_selector_algorithm"
        weighted = value.get("candidates")
        if type(weighted) is not list:
            return fallback
        if not weighted:
            return "empty_config_set"
        total = 0
        ids: set[str] = set()
        identities: set[bytes] = set()
        for item in weighted:
            if type(item) is not dict:
                return fallback
            weight = item.get("weight")
            if type(weight) is not int:
                return "weight_not_integer"
            if weight <= 0:
                return "weight_nonpositive"
            if weight > 2**53 - 1:
                return "weight_overflow"
            total += weight
            if total > 2**53 - 1:
                return "total_weight_overflow"
            candidate = item.get("candidate")
            if type(candidate) is not dict:
                return fallback
            candidate_id = candidate.get("candidate_id")
            if (
                type(candidate_id) is not str
                or re.fullmatch(r"[a-z0-9](?:[a-z0-9._-]{0,62}[a-z0-9])?", candidate_id)
                is None
            ):
                return "invalid_candidate_id"
            if candidate_id in ids:
                return "duplicate_candidate_id"
            ids.add(candidate_id)
            if type(candidate.get("predicates")) is not list:
                return "invalid_predicate"
            if type(candidate.get("overlays")) is not list:
                return "invalid_overlay_ref"
            identity = canonical_json_bytes(
                {
                    "receipt_digest": candidate.get("receipt_digest"),
                    "predicates": candidate.get("predicates"),
                    "overlays": candidate.get("overlays"),
                }
            )
            if identity in identities:
                return "duplicate_candidate"
            identities.add(identity)
        return fallback
    if kind is c.ArtifactKind.DIRECT_SELECTOR:
        candidate = value.get("candidate")
        if type(candidate) is not dict:
            return fallback
        candidate_id = candidate.get("candidate_id")
        if (
            type(candidate_id) is not str
            or re.fullmatch(r"[a-z0-9](?:[a-z0-9._-]{0,62}[a-z0-9])?", candidate_id)
            is None
        ):
            return "invalid_candidate_id"
        if type(candidate.get("predicates")) is not list:
            return "invalid_predicate"
        if type(candidate.get("overlays")) is not list:
            return "invalid_overlay_ref"
    return fallback


def _load_runtime_artifact(
    runtime: ConfigRuntime,
    request: c.ResolveEpisodeRequest,
    *,
    digest: str,
    kind: c.ArtifactKind,
    model_type: type[Any],
    stage: str,
    code: str,
    expected_ref: c.ArtifactRef | None = None,
    candidate_id: str | None = None,
    post_selection: bool = False,
    selection_record_digest: str | None = None,
) -> tuple[Any, bytes, c.ArtifactRef]:
    try:
        payload = runtime._store.load(
            digest, kind=kind, max_bytes=_MAX_RUNTIME_ARTIFACT_BYTES
        )
    except BaseException:
        _deny_resolution(
            stage,
            code,
            request=request,
            policy=runtime._policy,
            artifact_kind=kind,
            artifact_digest=digest,
            candidate_id=candidate_id,
            selection_record_digest=selection_record_digest,
            detail="canonical artifact load failed",
            retry="same_input_once" if "store" in code else "never",
            post_selection=post_selection,
        )
    if (
        type(payload) is not bytes
        or not payload
        or len(payload) > _MAX_RUNTIME_ARTIFACT_BYTES
        or _bytes_digest(payload) != digest
    ):
        _deny_resolution(
            stage,
            code,
            request=request,
            policy=runtime._policy,
            artifact_kind=kind,
            artifact_digest=digest,
            candidate_id=candidate_id,
            selection_record_digest=selection_record_digest,
            detail="canonical artifact bytes or digest mismatch",
            post_selection=post_selection,
        )
    decoded: Any = None
    try:
        decoded = canonical_json_loads(payload)
        parsed = model_type.model_validate(decoded)
        canonical = parsed.canonical_bytes()
    except BaseException:
        denial_code = _selector_payload_code(decoded, kind, code)
        _deny_resolution(
            stage,
            denial_code,
            request=request,
            policy=runtime._policy,
            artifact_kind=kind,
            artifact_digest=digest,
            candidate_id=candidate_id,
            selection_record_digest=selection_record_digest,
            detail="canonical artifact schema is invalid",
            post_selection=post_selection,
        )
    if canonical != payload or parsed.canonical_digest() != digest:
        _deny_resolution(
            stage,
            code,
            request=request,
            policy=runtime._policy,
            artifact_kind=kind,
            artifact_digest=digest,
            candidate_id=candidate_id,
            selection_record_digest=selection_record_digest,
            detail="artifact is noncanonical or content identity is invalid",
            post_selection=post_selection,
        )
    media_type = _ARTIFACT_MEDIA_TYPES[kind]
    ref = c.ArtifactRef(
        artifact_id=digest,
        sha256=digest,
        size_bytes=len(payload),
        media_type=media_type,
    )
    if expected_ref is not None and not _same(expected_ref, ref):
        _deny_resolution(
            stage,
            code,
            request=request,
            policy=runtime._policy,
            artifact_kind=kind,
            artifact_digest=digest,
            candidate_id=candidate_id,
            detail="artifact reference does not bind loaded canonical bytes",
            post_selection=post_selection,
            selection_record_digest=selection_record_digest,
        )
    return parsed, payload, ref


def _receipt_ref(digest: str, payload: bytes) -> c.AdmissionReceiptRef:
    return c.AdmissionReceiptRef(
        digest=digest,
        ref=c.ArtifactRef(
            artifact_id=digest,
            sha256=digest,
            size_bytes=len(payload),
            media_type=_ARTIFACT_MEDIA_TYPES[c.ArtifactKind.ADMISSION_RECEIPT],
        ),
    )


def _predicate_result(
    predicate: c.EligibilityPredicate,
    task: c.TaskEligibilityInput,
    capabilities: c.PolicyCapabilityVector,
) -> bool:
    labels = {item.key: item.value for item in task.labels}
    if isinstance(predicate, c.AllOf):
        return all(_predicate_result(child, task, capabilities) for child in predicate.children)
    if isinstance(predicate, c.AnyOf):
        return any(_predicate_result(child, task, capabilities) for child in predicate.children)
    if isinstance(predicate, c.TaskLabelEq):
        return labels.get(predicate.key) == predicate.value
    if isinstance(predicate, c.TaskLabelIn):
        return labels.get(predicate.key) in predicate.values
    if isinstance(predicate, c.ArtifactRolePresent):
        matching = tuple(
            item
            for item in task.artifacts
            if item.role == predicate.role
            and (not predicate.media_types or item.media_type in predicate.media_types)
        )
        return len(matching) >= predicate.min_count and (
            predicate.max_count is None or len(matching) <= predicate.max_count
        )
    if isinstance(predicate, c.PolicyBoolEq):
        value = getattr(capabilities, predicate.field.value)
        if type(value) is not bool:
            raise TypeError("policy boolean field is malformed")
        return value is predicate.value
    if isinstance(predicate, c.PolicyIntGte):
        value = getattr(capabilities, predicate.field.value)
        if type(value) is not int:
            raise TypeError("policy integer field is malformed")
        return value >= predicate.value
    if isinstance(predicate, c.PolicySetContainsAll):
        value = getattr(capabilities, predicate.field.value)
        if type(value) is not tuple or any(type(item) is not str for item in value):
            raise TypeError("policy set field is malformed")
        return set(predicate.values).issubset(value)
    raise TypeError("unknown eligibility predicate")


def _predicate_code(predicate: c.EligibilityPredicate) -> str:
    return {
        "all": "all_false",
        "any": "any_false",
        "task_label_eq": "task_label_eq_false",
        "task_label_in": "task_label_in_false",
        "artifact_role_present": "artifact_role_present_false",
        "policy_bool_eq": "policy_bool_eq_false",
        "policy_int_gte": "policy_int_gte_false",
        "policy_set_contains_all": "policy_set_contains_all_false",
    }[predicate.kind]


def _decode_pointer(pointer: str) -> tuple[str, ...]:
    if not pointer or not pointer.startswith("/"):
        raise ValueError("root and relative pointers are forbidden")
    raw_tokens = pointer[1:].split("/")
    if any(token == "" for token in raw_tokens):
        raise ValueError("empty pointer tokens are forbidden")
    tokens: list[str] = []
    for raw in raw_tokens:
        index = 0
        decoded = ""
        while index < len(raw):
            if raw[index] != "~":
                decoded += raw[index]
                index += 1
                continue
            if index + 1 >= len(raw) or raw[index + 1] not in "01":
                raise ValueError("malformed pointer escape")
            decoded += "~" if raw[index + 1] == "0" else "/"
            index += 2
        if unicodedata.normalize("NFC", decoded) != decoded:
            raise ValueError("pointer tokens must be NFC")
        canonical = decoded.replace("~", "~0").replace("/", "~1")
        if canonical != raw:
            raise ValueError("pointer is not canonically escaped")
        tokens.append(decoded)
    return tuple(tokens)


def _array_index(token: str, *, length: int) -> int:
    if token == "0":
        return 0
    if not token or token[0] == "0" or not token.isascii() or not token.isdigit():
        raise ValueError("array index is noncanonical")
    index = int(token)
    if index >= length:
        raise IndexError("array index does not exist")
    return index


def _overlay_value_forbidden(value: Any) -> bool:
    if isinstance(value, Mapping):
        for key, item in value.items():
            lowered = str(key).lower()
            if lowered in _FORBIDDEN_DETAIL_KEYS or _overlay_value_forbidden(item):
                return True
        return False
    if isinstance(value, (tuple, list)):
        return any(_overlay_value_forbidden(item) for item in value)
    if isinstance(value, str):
        lowered = value.casefold()
        return (
            "://" in lowered
            or "${" in lowered
            or "authorization:" in lowered
            or "bearer " in lowered
            or re.search(r"(?:^|[^a-z0-9])sk-[a-z0-9]", lowered) is not None
            or re.search(
                r"(?:^|\s)(?:sh|bash|zsh|dash|ksh|fish)\s+-c(?:\s|$)",
                lowered,
            )
            is not None
            or re.fullmatch(
                r"[a-z_][a-z0-9_]*(?:\.[a-z_][a-z0-9_]*)+"
                r":[a-z_][a-z0-9_]*(?:\.[a-z_][a-z0-9_]*)*",
                lowered,
            )
            is not None
            or lowered.startswith(("/etc/", "/proc/", "/sys/", "file:"))
        )
    return False


def _operation_apply(document: dict[str, Any], operation: c.OverlayOperation) -> dict[str, Any]:
    tokens = _decode_pointer(operation.path)
    updated = _deep_thaw(_deep_freeze(document))
    parent: Any = updated
    for token in tokens[:-1]:
        if isinstance(parent, dict):
            if token not in parent:
                raise KeyError("parent missing")
            parent = parent[token]
        elif isinstance(parent, list):
            parent = parent[_array_index(token, length=len(parent))]
        else:
            raise KeyError("parent is not a container")
    token = tokens[-1]
    value = _deep_thaw(operation.value)
    if isinstance(parent, dict):
        exists = token in parent
        if operation.op == "add":
            if exists:
                raise FileExistsError("add target exists")
            parent[token] = value
        elif operation.op == "replace":
            if not exists:
                raise KeyError("replace target missing")
            parent[token] = value
        else:
            if not exists:
                raise KeyError("remove target missing")
            del parent[token]
        return updated
    if isinstance(parent, list):
        if operation.op == "add":
            raise NotImplementedError("array add forbidden")
        index = _array_index(token, length=len(parent))
        if operation.op == "replace":
            parent[index] = value
        else:
            del parent[index]
        return updated
    raise KeyError("operation parent is not a container")


def _capabilities_do_not_increase(child: c.CapabilityVector, parent: c.CapabilityVector) -> bool:
    if _same(child, parent):
        return True
    child_obj = _obj(child)
    parent_obj = _obj(parent)
    exact_fields = (
        "runner",
        "setup_plans",
        "routes",
        "secret_handles",
        "task",
        "policy_slots",
        "verifier",
        "evidence",
        "retention",
    )
    if any(child_obj[field] != parent_obj[field] for field in exact_fields):
        return False
    for field in ("resources", "limits"):
        ok, _ = _numeric_leq(child_obj[field], parent_obj[field])
        if not ok:
            return False
    parent_tools = {item.tool_id: item for item in parent.tools}
    if any(
        item.tool_id not in parent_tools
        or item.implementation_digest != parent_tools[item.tool_id].implementation_digest
        or not set(item.capability_ids).issubset(parent_tools[item.tool_id].capability_ids)
        for item in child.tools
    ):
        return False
    if not set(child.sandbox.egress_route_ids).issubset(parent.sandbox.egress_route_ids):
        return False
    child_sandbox = _obj(child.sandbox)
    parent_sandbox = _obj(parent.sandbox)
    for name in child_sandbox:
        if name not in {"egress_route_ids", "mounts"} and child_sandbox[name] != parent_sandbox[name]:
            return False
    if any(not _mount_allowed(mount, parent.sandbox.mounts) for mount in child.sandbox.mounts):
        return False
    parent_rules = {_digest(item) for item in parent.mutable_pointers}
    if any(_digest(item) not in parent_rules for item in child.mutable_pointers):
        return False
    if not set(child.artifacts.allowed_roles).issubset(parent.artifacts.allowed_roles):
        return False
    if (
        child.artifacts.max_each_bytes > parent.artifacts.max_each_bytes
        or child.artifacts.max_total_bytes > parent.artifacts.max_total_bytes
    ):
        return False
    return True


def _selection_owner_key(request: c.ResolveEpisodeRequest) -> str:
    return _digest(
        {
            "schema_version": "bb.rl.selection-owner.v1",
            "subject_digest": _subject_digest(request.subject),
            "episode_id": request.episode_id,
        }
    )


def _selection_request_digest(
    request: c.ResolveEpisodeRequest,
    *,
    selector_digest: str,
    config_set_digest: str | None,
    admitted_set_root: str,
    task_digest: str,
    observation: c.PolicyCapabilityObservation,
    revocation_state_digest: str,
) -> str:
    return _digest(
        {
            "schema_version": "bb.rl.selection-request.v1",
            "episode_id": request.episode_id,
            "subject_digest": _subject_digest(request.subject),
            "selector_digest": selector_digest,
            "config_set_digest": config_set_digest,
            "selection_nonce": request.selection_nonce,
            "task_contract_digest": task_digest,
            "policy_capability_observation_digest": observation.canonical_digest(),
            "policy_capability_digest": observation.capability_digest,
            "admitted_set_root": admitted_set_root,
            "revocation_state_digest": revocation_state_digest,
            "episode_overlays": request.episode_overlays,
        }
    )


def _publish_runtime_artifact(
    runtime: ConfigRuntime,
    request: c.ResolveEpisodeRequest,
    value: Any,
    *,
    kind: c.ArtifactKind,
    stage: str,
    unavailable_code: str,
    conflict_code: str,
    readback_code: str,
    selection_record_digest: str | None = None,
    post_selection: bool = False,
) -> c.ArtifactRef:
    payload = value.canonical_bytes()
    digest = value.canonical_digest()
    try:
        raw_ref = runtime._store.publish(kind=kind, canonical_bytes=payload)
        ref = _revalidate_model(c.ArtifactRef, raw_ref)
    except BaseException:
        _deny_resolution(
            stage,
            unavailable_code,
            request=request,
            policy=runtime._policy,
            artifact_kind=kind,
            artifact_digest=digest,
            selection_record_digest=selection_record_digest,
            detail="artifact publication unavailable",
            retry="same_input_once",
            post_selection=post_selection,
        )
    if (
        ref.artifact_id != digest
        or ref.sha256 != digest
        or ref.size_bytes != len(payload)
        or ref.media_type != _ARTIFACT_MEDIA_TYPES[kind]
    ):
        _deny_resolution(
            stage,
            conflict_code,
            request=request,
            policy=runtime._policy,
            artifact_kind=kind,
            artifact_digest=digest,
            selection_record_digest=selection_record_digest,
            detail="artifact store returned a conflicting reference",
            post_selection=post_selection,
        )
    try:
        readback = runtime._store.load(
            digest, kind=kind, max_bytes=_MAX_RUNTIME_ARTIFACT_BYTES
        )
    except BaseException:
        _deny_resolution(
            stage,
            unavailable_code,
            request=request,
            policy=runtime._policy,
            artifact_kind=kind,
            artifact_digest=digest,
            selection_record_digest=selection_record_digest,
            detail="artifact readback unavailable",
            retry="same_input_once",
            post_selection=post_selection,
        )
    if readback != payload or _bytes_digest(readback) != digest:
        _deny_resolution(
            stage,
            readback_code,
            request=request,
            policy=runtime._policy,
            artifact_kind=kind,
            artifact_digest=digest,
            selection_record_digest=selection_record_digest,
            detail="artifact readback mismatch",
            post_selection=post_selection,
        )
    return ref


class ConfigRuntime:
    """Pure WP3 admission and receipt-verification boundary."""

    def __init__(
        self,
        *,
        compiler: VerifiedCompilerAdapter,
        policy: c.AdmissionPolicySnapshot,
        registries: c.RegistrySnapshotSet,
        revocations: RevocationStore,
        store: ConfigRuntimeStore,
        clock: Clock,
        authenticator: ReceiptAuthenticator,
        policy_capabilities: PolicyCapabilityRegistry | None = None,
    ) -> None:
        try:
            validated_policy = _revalidate_model(c.AdmissionPolicySnapshot, policy)
            validated_registries = _revalidate_model(c.RegistrySnapshotSet, registries)
        except Exception:
            _deny(
                "registry_resolution",
                "registry_snapshot_mismatch",
                pointer="/registry_snapshot_digest",
                detail="runtime control-plane model is invalid",
            )
        try:
            identity_probe = c.IssuanceAttestation(
                key_id=authenticator.key_id,
                algorithm=authenticator.algorithm,
                signed_payload_digest=_bytes_digest(b""),
                signature="AA",
            )
            authenticator_key_id = identity_probe.key_id
            authenticator_algorithm = identity_probe.algorithm
        except Exception:
            _deny(
                "receipt_publication",
                "receipt_store_unavailable",
                policy=validated_policy,
                pointer="/issuance_attestation",
                detail="receipt authenticator unavailable",
            )
        self._compiler = compiler
        self._policy = validated_policy
        self._registries = validated_registries
        self._revocations = revocations
        self._store = store
        self._clock = clock
        self._policy_capabilities = policy_capabilities
        self._authenticator = authenticator
        self._authenticator_key_id = authenticator_key_id
        self._authenticator_algorithm = authenticator_algorithm

    def admit(self, request: c.AdmissionRequest) -> c.AdmissionReceiptRef:
        try:
            request = _revalidate_model(c.AdmissionRequest, request)
        except Exception:
            _deny(
                "subject_authentication",
                "unauthenticated_subject",
                policy=self._policy,
                pointer="/request",
                detail="canonical admission request is invalid",
            )
        try:
            now = _utc_now(self._clock.current())
        except Exception:
            _deny(
                "subject_authentication",
                "subject_scope_mismatch",
                request=request,
                policy=self._policy,
                pointer="/validity",
                detail="trusted admission clock unavailable",
            )
        self._authenticate(request, now)
        if request.compiled.compiler.runtime_abi != request.requested_capabilities.runner.runtime_abi:
            _deny(
                "compiled_artifact_verification", "compiled_digest_mismatch",
                request=request, policy=self._policy,
                pointer="/compiled/compiler/runtime_abi",
                detail="compiler and requested runner ABI differ",
            )
        if _find_exact(
            self._policy.compiler_constraints.allowed_compilers,
            request.compiled.compiler,
        ) is None:
            _deny(
                "compiled_artifact_verification", "unsupported_manifest_schema",
                request=request, policy=self._policy,
                pointer="/compiled/compiler/manifest_schema_digest",
                detail="compiler identity is not allowed",
            )
        try:
            self._compiler.verify_bundle(request)
        except BaseException:
            _deny(
                "bundle_integrity", "compiled_digest_mismatch", request=request,
                policy=self._policy, pointer="/compiled/bundle_digest",
                detail="bundle verification failed",
            )
        try:
            self._compiler.enforce_compile_budget(request)
        except BaseException:
            _deny(
                "compile_budget", "operator_ceiling_exceeded", request=request,
                policy=self._policy, pointer="/compiled/compiler_input_digest",
                detail="compile budget enforcement failed",
            )
        try:
            raw_view = self._compiler.compile(request)
            view = CompilerSemanticView(raw_view.roles)
        except BaseException:
            _deny(
                "compilation", "compiled_digest_mismatch", request=request,
                policy=self._policy, pointer="/compiler_view",
                detail="compiler execution failed",
            )
        self._verify_compiled(request, view)
        self._resolve_registry(request, view, now)
        self._prove_ceiling(request)
        self._verify_pins(request, view)
        return self._publish_receipt(request, now)

    def verify_receipt(
        self,
        receipt: c.AdmissionReceiptRef,
        *,
        subject: c.AuthenticatedSubject,
        checkpoint: c.PrivilegedCheckpoint,
        _trusted_now: datetime | None = None,
        _revocation_cache: dict[str, c.RevocationBinding] | None = None,
    ) -> c.VerifiedAdmission:
        policy = self._policy
        try:
            receipt = _revalidate_model(c.AdmissionReceiptRef, receipt)
            subject = _revalidate_model(c.AuthenticatedSubject, subject)
            if type(checkpoint) is not c.PrivilegedCheckpoint:
                raise TypeError("validated checkpoint required")
            checkpoint = c.PrivilegedCheckpoint(checkpoint.value)
        except Exception:
            _deny(
                "receipt_recheck", "receipt_forged", policy=policy,
                artifact_kind=c.ArtifactKind.ADMISSION_RECEIPT.value,
                pointer="/receipt_ref", detail="receipt verification input is invalid",
            )
        try:
            now = _utc_now(
                self._clock.current() if _trusted_now is None else _trusted_now
            )
        except Exception:
            _deny(
                "receipt_recheck", "receipt_forged", subject=subject, policy=policy,
                artifact_kind=c.ArtifactKind.ADMISSION_RECEIPT.value,
                artifact_digest=receipt.digest, pointer="/validity",
                detail="trusted verification clock unavailable",
            )
        try:
            payload = self._store.load(
                receipt.digest,
                kind=c.ArtifactKind.ADMISSION_RECEIPT,
                max_bytes=_MAX_RECEIPT_BYTES,
            )
        except BaseException:
            _deny(
                "receipt_recheck", "receipt_forged", subject=subject, policy=policy,
                artifact_kind=c.ArtifactKind.ADMISSION_RECEIPT.value,
                artifact_digest=receipt.digest, detail="receipt load failed",
            )
        if type(payload) is not bytes or len(payload) > _MAX_RECEIPT_BYTES:
            _deny(
                "receipt_recheck", "receipt_forged", subject=subject, policy=policy,
                artifact_kind=c.ArtifactKind.ADMISSION_RECEIPT.value,
                artifact_digest=receipt.digest, detail="receipt bytes invalid",
            )
        if _bytes_digest(payload) != receipt.digest:
            _deny(
                "receipt_recheck", "receipt_forged", subject=subject, policy=policy,
                artifact_kind=c.ArtifactKind.ADMISSION_RECEIPT.value,
                artifact_digest=receipt.digest, detail="receipt digest mismatch",
            )
        if (
            receipt.ref.artifact_id != receipt.digest
            or receipt.ref.sha256 != receipt.digest
            or receipt.ref.size_bytes != len(payload)
        ):
            _deny(
                "receipt_recheck", "receipt_forged", subject=subject, policy=policy,
                artifact_kind=c.ArtifactKind.ADMISSION_RECEIPT.value,
                artifact_digest=receipt.digest, detail="receipt reference mismatch",
            )
        try:
            decoded = canonical_json_loads(payload)
            parsed = c.AdmissionReceipt.model_validate(decoded)
        except Exception:
            _deny(
                "receipt_recheck", "receipt_forged", subject=subject, policy=policy,
                artifact_kind=c.ArtifactKind.ADMISSION_RECEIPT.value,
                artifact_digest=receipt.digest, detail="receipt schema invalid",
            )
        if parsed.canonical_bytes() != payload or parsed.canonical_digest() != receipt.digest:
            _deny(
                "receipt_recheck", "receipt_forged", subject=subject, policy=policy,
                artifact_kind=c.ArtifactKind.ADMISSION_RECEIPT.value,
                artifact_digest=receipt.digest, detail="receipt canonical identity invalid",
            )
        attestation = parsed.issuance_attestation
        try:
            signature = base64.b64decode(
                attestation.signature + "=" * (-len(attestation.signature) % 4),
                altchars=b"-_",
                validate=True,
            )
            identity_matches = (
                attestation.key_id == self._authenticator_key_id
                and attestation.algorithm == self._authenticator_algorithm
                and self._authenticator.key_id == self._authenticator_key_id
                and self._authenticator.algorithm == self._authenticator_algorithm
            )
            authentic = identity_matches and self._authenticator.verify(
                parsed.unsigned_canonical_bytes(), signature
            )
        except BaseException:
            authentic = False
        if authentic is not True:
            _deny(
                "receipt_recheck", "receipt_forged", subject=subject, policy=policy,
                artifact_kind=c.ArtifactKind.ADMISSION_RECEIPT.value,
                artifact_digest=receipt.digest, pointer="/issuance_attestation",
                detail="receipt issuance authentication failed",
            )
        if not _same(parsed.subject, subject):
            _deny(
                "receipt_recheck", "receipt_cross_subject", subject=subject, policy=policy,
                artifact_digest=receipt.digest, pointer="/subject", detail="receipt subject mismatch",
            )
        if parsed.admission_policy_digest != _policy_digest(policy):
            _deny(
                "receipt_recheck", "receipt_stale_policy", subject=subject, policy=policy,
                artifact_digest=receipt.digest, pointer="/admission_policy_digest",
                detail="receipt policy is stale",
            )
        if (
            parsed.admission_policy_id != policy.policy_id
            or parsed.admission_policy_revision != policy.revision
            or parsed.operator_ceiling_digest != _ceiling_digest(policy)
        ):
            _deny(
                "receipt_recheck", "receipt_stale_policy", subject=subject, policy=policy,
                artifact_digest=receipt.digest, pointer="/operator_ceiling_digest",
                detail="receipt ceiling is stale",
            )
        if parsed.registry_snapshot_digest != self._registries.digests.snapshot_digest:
            _deny(
                "receipt_recheck", "receipt_stale_policy", subject=subject, policy=policy,
                artifact_digest=receipt.digest, pointer="/registry_snapshot_digest",
                detail="receipt registry is stale",
            )
        self._check_validity(
            parsed.validity, now, stage="receipt_recheck",
            not_yet_code="receipt_not_yet_valid", expired_code="receipt_expired",
            subject=subject, artifact_digest=receipt.digest, pointer="/validity",
        )
        current = self._load_revocation(
            parsed.revocation,
            subject=subject,
            artifact_digest=receipt.digest,
            cache=_revocation_cache,
        )
        source = parsed.behavior_source
        source_manifest = getattr(source, "manifest_digest", None) or getattr(source, "base_manifest_digest", None)
        source_semantic = getattr(source, "semantic_digest", None) or getattr(source, "derived_semantic_digest", None)
        if source_manifest != parsed.compiled.manifest_digest or source_semantic != parsed.compiled.semantic_digest:
            _deny(
                "receipt_recheck", "receipt_compiled_mismatch", subject=subject, policy=policy,
                artifact_digest=receipt.digest, pointer="/behavior_source",
                detail="receipt behavior source mismatch",
            )
        if parsed.compiled.compiler.runtime_abi != parsed.effective_capabilities.runner.runtime_abi:
            _deny(
                "receipt_recheck", "receipt_abi_mismatch", subject=subject, policy=policy,
                artifact_digest=receipt.digest, pointer="/compiled/compiler/runtime_abi",
                detail="receipt ABI mismatch",
            )
        if parsed.task_binding_digest != parsed.effective_capabilities.task.task_binding_digest:
            _deny(
                "receipt_recheck", "receipt_task_mismatch", subject=subject, policy=policy,
                artifact_digest=receipt.digest, pointer="/task_binding_digest",
                detail="receipt task binding mismatch",
            )
        if parsed.policy_binding_ref.registry_revision_digest != policy.registry_digests.route_registry_digest:
            _deny(
                "receipt_recheck", "receipt_policy_binding_mismatch", subject=subject, policy=policy,
                artifact_digest=receipt.digest, pointer="/policy_binding_ref/registry_revision_digest",
                detail="receipt policy binding mismatch",
            )
        binding = parsed.policy_binding_ref
        attestation = next(
            (
                record for record in self._registries.policy_capability_attestations
                if record.attestation_digest == binding.attestation_digest
            ),
            None,
        )
        matching_slots = tuple(
            slot for slot in parsed.effective_capabilities.policy_slots
            if slot.route_id == binding.route_id
        )
        route_record = next(
            (
                record for record in self._registries.routes
                if record.grant.route_id == binding.route_id
            ),
            None,
        )
        if (
            attestation is None
            or route_record is None
            or len(matching_slots) != 1
            or attestation.route_id != binding.route_id
            or attestation.route_revision_digest != route_record.grant.route_revision_digest
            or attestation.model_digest != matching_slots[0].model_digest
            or attestation.tokenizer_digest != matching_slots[0].tokenizer_digest
            or attestation.checkpoint_digest != matching_slots[0].checkpoint_digest
            or attestation.capability_digest
            != matching_slots[0].required_policy_capabilities_digest
        ):
            _deny(
                "receipt_recheck", "attestation_invalid",
                subject=subject, policy=policy, artifact_digest=receipt.digest,
                pointer="/policy_binding_ref/attestation_digest",
                detail="receipt policy attestation is stale",
            )
        self._check_validity(
            attestation.validity,
            now,
            stage="receipt_recheck",
            not_yet_code="attestation_not_yet_valid",
            expired_code="attestation_expired",
            subject=subject,
            artifact_digest=receipt.digest,
            pointer="/policy_binding_ref/attestation_digest",
        )
        self._load_revocation(
            attestation.revocation,
            subject=subject,
            artifact_digest=receipt.digest,
            pointer="/policy_binding_ref/attestation_digest",
            invalid_code="attestation_invalid",
            cache=_revocation_cache,
        )
        if (
            parsed.effective_capability_digest != _digest(parsed.effective_capabilities)
            or parsed.requested_capability_digest != parsed.effective_capability_digest
        ):
            _deny(
                "receipt_recheck", "receipt_compiled_mismatch", subject=subject, policy=policy,
                artifact_digest=receipt.digest, pointer="/effective_capability_digest",
                detail="receipt capabilities mismatch",
            )
        if parsed.mutable_pointer_policy_digest != _digest(parsed.effective_capabilities.mutable_pointers):
            _deny(
                "receipt_recheck", "receipt_compiled_mismatch", subject=subject, policy=policy,
                artifact_digest=receipt.digest, pointer="/mutable_pointer_policy_digest",
                detail="receipt mutable pointer policy mismatch",
            )
        if parsed.reason_codes:
            _deny(
                "receipt_recheck", "receipt_compiled_mismatch", subject=subject, policy=policy,
                artifact_digest=receipt.digest, pointer="/reason_codes",
                detail="admitted receipt contains denial reasons",
            )
        if not _same(parsed.capability_deltas, _capability_deltas(parsed.effective_capabilities, policy.ceiling)):
            _deny(
                "receipt_recheck", "receipt_compiled_mismatch", subject=subject, policy=policy,
                artifact_digest=receipt.digest, pointer="/capability_deltas",
                detail="receipt capability deltas mismatch",
            )
        expected_pins = _pins_for_receipt(parsed)
        if not _same(parsed.pins, expected_pins):
            _deny(
                "receipt_recheck", "receipt_compiled_mismatch", subject=subject, policy=policy,
                artifact_digest=receipt.digest, pointer="/pins", detail="receipt pins mismatch",
            )
        token = c.CurrentnessToken(
            receipt_digest=receipt.digest,
            subject_digest=_subject_digest(subject),
            admission_policy_digest=parsed.admission_policy_digest,
            registry_snapshot_digest=parsed.registry_snapshot_digest,
            revocation_scope_digest=current.scope_digest,
            revocation_epoch=current.epoch,
            revocation_state_digest=current.state_digest,
            checkpoint=checkpoint,
            verified_at=_timestamp(now),
            expires_at=parsed.validity.expires_at,
        )
        return c.VerifiedAdmission(
            receipt_ref=receipt,
            receipt=parsed,
            subject_digest=_subject_digest(subject),
            checkpoint=checkpoint,
            currentness=token,
        )

    def resolve_episode(
        self,
        request: c.ResolveEpisodeRequest,
    ) -> c.ResolvedEpisodePlan:
        try:
            request = _revalidate_model(c.ResolveEpisodeRequest, request)
            now = _utc_now(self._clock.current())
        except Exception:
            _deny_resolution(
                "selector_validation",
                "invalid_config_set",
                policy=self._policy,
                detail="canonical resolution request or trusted time is invalid",
            )
        if request.subject.authority_scope_digest != self._policy.subject_scope_digest:
            _deny_resolution(
                "subject_authentication",
                "subject_scope_mismatch",
                request=request,
                policy=self._policy,
                pointer="/subject/authority_scope_digest",
                detail="resolution subject is outside the admission-policy scope",
            )
        revocation_cache: dict[str, c.RevocationBinding] = {}

        selector_kind = (
            c.ArtifactKind.DIRECT_SELECTOR
            if isinstance(request.selector, c.DirectSelectorRef)
            else c.ArtifactKind.CONFIG_SET
        )
        selector_model = (
            c.DirectSelector
            if selector_kind is c.ArtifactKind.DIRECT_SELECTOR
            else c.ConfigSetManifest
        )
        selector, _, _ = _load_runtime_artifact(
            self,
            request,
            digest=request.selector.digest,
            kind=selector_kind,
            model_type=selector_model,
            stage="selector_validation",
            code="invalid_config_set",
            expected_ref=request.selector.ref,
        )
        self._resolution_validity(
            selector.validity,
            now,
            request=request,
            stage="selector_validation",
            code="invalid_config_set",
            pointer="/selector/validity",
        )
        admitted_set, _, _ = _load_runtime_artifact(
            self,
            request,
            digest=selector.admitted_set_root,
            kind=c.ArtifactKind.ADMITTED_SET,
            model_type=c.AdmittedSetManifest,
            stage="selector_validation",
            code="set_root_stale",
        )
        self._validate_resolution_set_headers(
            request, selector, admitted_set, now, revocation_cache
        )

        receipt_cache: dict[str, c.VerifiedAdmission] = {}
        for receipt_digest in admitted_set.receipt_digests:
            receipt_cache[receipt_digest] = self._resolution_receipt(
                request,
                receipt_digest,
                candidate_id=None,
                now=now,
                revocation_cache=revocation_cache,
            )
        task_digest = request.task.canonical_digest()
        root_task_binding_digest = receipt_cache[
            admitted_set.receipt_digests[0]
        ].receipt.task_binding_digest
        root_policy_binding_ref = receipt_cache[
            admitted_set.receipt_digests[0]
        ].receipt.policy_binding_ref
        for receipt_digest, admission in receipt_cache.items():
            self._validate_resolution_receipt_header(
                request,
                selector,
                admitted_set,
                admission.receipt,
                receipt_digest=receipt_digest,
                task_digest=task_digest,
                root_task_binding_digest=root_task_binding_digest,
                root_policy_binding_ref=root_policy_binding_ref,
            )
        self._validate_resolution_windows(
            request,
            selector,
            admitted_set,
            tuple(admission.receipt for admission in receipt_cache.values()),
        )
        self._resolution_validity(
            self._policy.validity,
            now,
            request=request,
            stage="selector_validation",
            code="set_policy_mismatch",
            pointer="/admission_policy/validity",
        )
        self._resolution_revocation(
            request,
            self._policy.revocation,
            stage="selector_validation",
            code="set_policy_mismatch",
            pointer="/admission_policy/revocation",
            cache=revocation_cache,
        )

        weighted_candidates = (
            ((selector.candidate, None),)
            if isinstance(selector, c.DirectSelector)
            else tuple((item.candidate, item.weight) for item in selector.candidates)
        )
        candidate_states: list[_CandidateState] = []
        overlay_cache: dict[
            tuple[str, str], tuple[c.MutationOverlayManifest, c.VerifiedAdmission]
        ] = {}
        for candidate, weight in weighted_candidates:
            if candidate.receipt_digest not in admitted_set.receipt_digests:
                _deny_resolution(
                    "selector_validation",
                    "stale_candidate_receipt",
                    request=request,
                    policy=self._policy,
                    candidate_id=candidate.candidate_id,
                    artifact_digest=candidate.receipt_digest,
                    detail="candidate receipt is absent from the admitted-set root",
                )
            admission = receipt_cache[candidate.receipt_digest]
            candidate_states.append(
                _CandidateState(candidate=candidate, weight=weight, admission=admission)
            )


        observation = self._observe_policy(request, now, revocation_cache)
        every_receipt = tuple(receipt_cache.values())
        for admission in every_receipt:
            self._match_policy_observation(request, observation, admission.receipt)

        evaluations: list[c.CandidateEvaluation] = []
        eligible: list[c.EligibleCandidate] = []
        for state in candidate_states:
            codes: list[str] = []
            try:
                for predicate in state.candidate.predicates:
                    if not _predicate_result(predicate, request.task, observation.capabilities):
                        codes.append(_predicate_code(predicate))
            except Exception:
                _deny_resolution(
                    "policy_observation",
                    "required_policy_capability_missing",
                    request=request,
                    policy=self._policy,
                    candidate_id=state.candidate.candidate_id,
                    detail="policy observation cannot evaluate a closed predicate",
                )
            codes_tuple = tuple(sorted(set(codes)))
            evaluation = c.CandidateEvaluation(
                candidate_id=state.candidate.candidate_id,
                receipt_digest=state.candidate.receipt_digest,
                eligible=not codes_tuple,
                exclusion_codes=codes_tuple,
                weight=state.weight,
            )
            evaluations.append(evaluation)
            if not codes_tuple:
                eligible.append(
                    c.EligibleCandidate(
                        candidate_id=state.candidate.candidate_id,
                        receipt_digest=state.candidate.receipt_digest,
                        weight=state.weight,
                    )
                )
        evaluations.sort(key=lambda item: item.candidate_id.encode("ascii"))
        eligible.sort(key=lambda item: item.candidate_id.encode("ascii"))

        task_digest = request.task.canonical_digest()
        observation_digest = observation.canonical_digest()
        revocation_state_digest = admitted_set.revocation.state_digest
        config_set_digest = (
            request.selector.digest if isinstance(selector, c.ConfigSetManifest) else None
        )
        selection_request_digest = _selection_request_digest(
            request,
            selector_digest=request.selector.digest,
            config_set_digest=config_set_digest,
            admitted_set_root=selector.admitted_set_root,
            task_digest=task_digest,
            observation=observation,
            revocation_state_digest=revocation_state_digest,
        )
        owner_key = _selection_owner_key(request)
        expected_record: c.SelectionRecord | None = None
        if eligible:
            expected_record = self._build_selection_record(
                request,
                selector=selector,
                candidate_states=tuple(candidate_states),
                evaluations=tuple(evaluations),
                eligible=tuple(eligible),
                task_digest=task_digest,
                observation=observation,
                revocation_state_digest=revocation_state_digest,
            )
            self._validate_preexisting_selection_record(
                request,
                expected_record=expected_record,
                owner_key=owner_key,
                selection_request_digest=selection_request_digest,
            )
        for state in candidate_states:
            candidate = state.candidate
            candidate_semantics: dict[str, Any] | None = None
            candidate_manifest_bytes: bytes | None = None
            if candidate.overlays:
                candidate_semantics, candidate_manifest_bytes = self._load_selected_semantics(
                    request,
                    state.admission.receipt,
                    selection_digest=None,
                    candidate_id=candidate.candidate_id,
                )
            current_admission = state.admission
            current_digest = candidate.receipt_digest
            for overlay_ref in candidate.overlays:
                overlay, result = self._prevalidate_overlay_ref(
                    request,
                    overlay_ref,
                    receipt_cache=receipt_cache,
                    admitted_set=admitted_set,
                    candidate_id=candidate.candidate_id,
                )
                overlay_cache[(overlay_ref.overlay_digest, overlay_ref.result_receipt_digest)] = (
                    overlay,
                    result,
                )
                self._validate_overlay_chain_link(
                    request,
                    overlay_ref,
                    overlay,
                    result.receipt,
                    parent=current_admission.receipt,
                    parent_digest=current_digest,
                    candidate_id=candidate.candidate_id,
                )
                assert candidate_semantics is not None and candidate_manifest_bytes is not None
                candidate_semantics, _ = self._evaluate_overlay(
                    request,
                    overlay_ref,
                    overlay,
                    parent=current_admission,
                    semantics=candidate_semantics,
                    manifest_bytes=candidate_manifest_bytes,
                    candidate_id=candidate.candidate_id,
                    selection_record_digest=None,
                    verify_with_compiler=True,
                )
                current_admission = result
                current_digest = overlay_ref.result_receipt_digest
        if not eligible:
            _deny_resolution(
                "eligibility",
                "no_eligible_candidate",
                request=request,
                policy=self._policy,
                detail="no valid candidate satisfies the closed eligibility predicates",
            )
        assert expected_record is not None
        selection_record, selection_ref, selection_commit = self._resolve_selection_commit(
            request,
            now=now,
            expected_record=expected_record,
            owner_key=owner_key,
            selection_request_digest=selection_request_digest,
        )
        selection_digest = selection_record.canonical_digest()
        selected_state = next(
            state
            for state in candidate_states
            if state.candidate.candidate_id == selection_record.selected_candidate_id
        )
        for overlay_ref in request.episode_overlays:
            overlay, result = self._prevalidate_overlay_ref(
                request,
                overlay_ref,
                receipt_cache=receipt_cache,
                admitted_set=admitted_set,
                candidate_id=selected_state.candidate.candidate_id,
                selection_record_digest=selection_digest,
            )
            self._match_policy_observation(request, observation, result.receipt)
            overlay_cache[(overlay_ref.overlay_digest, overlay_ref.result_receipt_digest)] = (
                overlay,
                result,
            )

        semantics, manifest_bytes = self._load_selected_semantics(
            request,
            selected_state.admission.receipt,
            selection_digest=selection_digest,
        )
        current_admission = selected_state.admission
        current_receipt_digest = selected_state.candidate.receipt_digest
        overlay_applications: list[c.OverlayApplicationRecord] = []
        overlay_chain = selected_state.candidate.overlays + request.episode_overlays
        for overlay_ref in overlay_chain:
            overlay, expected_result = overlay_cache[
                (overlay_ref.overlay_digest, overlay_ref.result_receipt_digest)
            ]
            self._validate_overlay_chain_link(
                request,
                overlay_ref,
                overlay,
                expected_result.receipt,
                parent=current_admission.receipt,
                parent_digest=current_receipt_digest,
                candidate_id=selected_state.candidate.candidate_id,
                selection_record_digest=selection_digest,
            )
            semantics, application = self._apply_overlay(
                request,
                overlay_ref,
                overlay,
                parent=current_admission,
                parent_digest=current_receipt_digest,
                expected_result=expected_result,
                semantics=semantics,
                manifest_bytes=manifest_bytes,
                selection_record_digest=selection_digest,
                candidate_id=selected_state.candidate.candidate_id,
            )
            overlay_applications.append(application)
            current_admission = expected_result
            current_receipt_digest = overlay_ref.result_receipt_digest

        final_receipt = current_admission.receipt
        plan = c.EffectiveExecutionPlan(
            subject_digest=_subject_digest(request.subject),
            base_compiled=selected_state.admission.receipt.compiled,
            base_receipt_digest=selected_state.candidate.receipt_digest,
            selector_digest=request.selector.digest,
            config_set_digest=config_set_digest,
            admitted_set_root=selector.admitted_set_root,
            selection_record_digest=selection_digest,
            task_eligibility_digest=task_digest,
            policy_capability_observation_digest=observation_digest,
            policy_capability_digest=observation.capability_digest,
            overlay_applications=tuple(overlay_applications),
            final_receipt_digest=current_receipt_digest,
            final_semantic_digest=final_receipt.compiled.semantic_digest,
            effective_semantics=semantics,
            effective_capabilities=final_receipt.effective_capabilities,
            effective_capability_digest=final_receipt.effective_capability_digest,
            pins=final_receipt.pins,
            runner=final_receipt.effective_capabilities.runner,
            policy_slots=final_receipt.effective_capabilities.policy_slots,
            sandbox=final_receipt.effective_capabilities.sandbox,
            verifier=final_receipt.effective_capabilities.verifier,
            task=final_receipt.effective_capabilities.task,
            artifacts=final_receipt.effective_capabilities.artifacts,
            evidence=final_receipt.effective_capabilities.evidence,
            retention=final_receipt.effective_capabilities.retention,
            revocation=final_receipt.revocation,
        )
        plan_ref = _publish_runtime_artifact(
            self,
            request,
            plan,
            kind=c.ArtifactKind.EFFECTIVE_EXECUTION_PLAN,
            stage="plan_publication",
            unavailable_code="plan_store_unavailable",
            conflict_code="plan_store_conflict",
            readback_code="plan_readback_mismatch",
            selection_record_digest=selection_digest,
            post_selection=True,
        )
        try:
            final_now = _utc_now(self._clock.current())
        except Exception:
            _deny_resolution(
                "pre_allocation_recheck",
                "receipt_expired",
                request=request,
                policy=self._policy,
                selection_record_digest=selection_digest,
                detail="trusted pre-allocation clock is unavailable",
                post_selection=True,
            )
        final_revocation_cache: dict[str, c.RevocationBinding] = {}
        self._validate_resolution_windows(
            request,
            selector,
            admitted_set,
            tuple(admission.receipt for admission in receipt_cache.values()),
            selection_record_digest=selection_digest,
        )
        self._resolution_validity(
            self._policy.validity,
            final_now,
            request=request,
            stage="pre_allocation_recheck",
            code="receipt_expired",
            pointer="/admission_policy/validity",
            selection_record_digest=selection_digest,
        )
        self._resolution_revocation(
            request,
            self._policy.revocation,
            stage="pre_allocation_recheck",
            code="receipt_revoked",
            pointer="/admission_policy/revocation",
            selection_record_digest=selection_digest,
            cache=final_revocation_cache,
        )
        self._resolution_validity(
            selector.validity,
            final_now,
            request=request,
            stage="pre_allocation_recheck",
            code="receipt_expired",
            pointer="/selector/validity",
            selection_record_digest=selection_digest,
        )
        self._resolution_validity(
            admitted_set.validity,
            final_now,
            request=request,
            stage="pre_allocation_recheck",
            code="receipt_expired",
            pointer="/admitted_set/validity",
            selection_record_digest=selection_digest,
        )
        self._resolution_revocation(
            request,
            admitted_set.revocation,
            stage="pre_allocation_recheck",
            code="receipt_revoked",
            pointer="/admitted_set/revocation",
            selection_record_digest=selection_digest,
            cache=final_revocation_cache,
        )
        self._resolution_validity(
            observation.provenance.validity,
            final_now,
            request=request,
            stage="pre_allocation_recheck",
            code="attestation_expired",
            not_yet_code="attestation_not_yet_valid",
            pointer="/policy_observation/provenance/validity",
            selection_record_digest=selection_digest,
        )
        self._resolution_revocation(
            request,
            observation.revocation,
            stage="pre_allocation_recheck",
            code="observation_revoked",
            pointer="/policy_observation/revocation",
            selection_record_digest=selection_digest,
            cache=final_revocation_cache,
        )
        policy_attestation = next(
            (
                item
                for item in self._registries.policy_capability_attestations
                if item.attestation_digest == request.policy_binding.attestation_digest
            ),
            None,
        )
        if policy_attestation is None:
            _deny_resolution(
                "pre_allocation_recheck",
                "attestation_invalid",
                request=request,
                policy=self._policy,
                selection_record_digest=selection_digest,
                detail="policy attestation disappeared before allocation",
                post_selection=True,
            )
        self._resolution_validity(
            policy_attestation.validity,
            final_now,
            request=request,
            stage="pre_allocation_recheck",
            code="attestation_expired",
            not_yet_code="attestation_not_yet_valid",
            pointer="/policy_binding/attestation_digest",
            selection_record_digest=selection_digest,
        )
        self._resolution_revocation(
            request,
            policy_attestation.revocation,
            stage="pre_allocation_recheck",
            code="observation_revoked",
            pointer="/policy_binding/attestation_digest",
            selection_record_digest=selection_digest,
            cache=final_revocation_cache,
        )
        current: c.VerifiedAdmission | None = None
        for receipt_digest in admitted_set.receipt_digests:
            receipt = receipt_cache[receipt_digest].receipt
            self._match_policy_observation(request, observation, receipt)
            verified = self.verify_receipt(
                _receipt_ref(receipt_digest, receipt.canonical_bytes()),
                subject=request.subject,
                checkpoint=c.PrivilegedCheckpoint.BEFORE_ALLOCATION,
                _trusted_now=final_now,
                _revocation_cache=final_revocation_cache,
            )
            if receipt_digest == current_receipt_digest:
                current = verified
        assert current is not None
        return c.ResolvedEpisodePlan(
            episode_id=request.episode_id,
            subject_digest=_subject_digest(request.subject),
            base_receipt_digest=selected_state.candidate.receipt_digest,
            final_receipt_digest=current_receipt_digest,
            policy_capability_observation_digest=observation_digest,
            selection_record_ref=selection_ref,
            selection_commit=selection_commit,
            effective_plan_ref=plan_ref,
            effective_plan=plan,
            currentness=current.currentness,
        )

    def _resolution_validity(
        self,
        validity: c.ValidityWindow,
        now: datetime,
        *,
        request: c.ResolveEpisodeRequest,
        stage: str,
        code: str,
        not_yet_code: str | None = None,
        pointer: str,
        selection_record_digest: str | None = None,
    ) -> None:
        if now < _parse_timestamp(validity.not_before):
            denial_code = not_yet_code or code
        elif now >= _parse_timestamp(validity.expires_at):
            denial_code = code
        else:
            return
        _deny_resolution(
            stage,
            denial_code,
            request=request,
            policy=self._policy,
            pointer=pointer,
            selection_record_digest=selection_record_digest,
            detail="artifact validity window is not current",
            post_selection=selection_record_digest is not None,
        )

    def _resolution_revocation(
        self,
        request: c.ResolveEpisodeRequest,
        expected: c.RevocationBinding,
        *,
        stage: str,
        code: str,
        pointer: str,
        selection_record_digest: str | None = None,
        cache: dict[str, c.RevocationBinding],
    ) -> c.RevocationBinding:
        try:
            current = cache.get(expected.scope_digest)
            if current is None:
                current = _revalidate_model(
                    c.RevocationBinding, self._revocations.load(expected.scope_digest)
                )
                cache[expected.scope_digest] = current
        except BaseException:
            _deny_resolution(
                stage,
                code,
                request=request,
                policy=self._policy,
                pointer=pointer,
                selection_record_digest=selection_record_digest,
                detail="revocation snapshot is unavailable",
                post_selection=selection_record_digest is not None,
            )
        if not _same(current, expected):
            _deny_resolution(
                stage,
                code,
                request=request,
                policy=self._policy,
                pointer=pointer,
                selection_record_digest=selection_record_digest,
                detail="revocation snapshot is stale, rolled back, or revoked",
                post_selection=selection_record_digest is not None,
            )
        return current

    def _validate_resolution_set_headers(
        self,
        request: c.ResolveEpisodeRequest,
        selector: c.DirectSelector | c.ConfigSetManifest,
        admitted_set: c.AdmittedSetManifest,
        now: datetime,
        revocation_cache: dict[str, c.RevocationBinding],
    ) -> None:
        policy_digest = _policy_digest(self._policy)
        ceiling_digest = _ceiling_digest(self._policy)
        if (
            selector.admission_policy_digest != policy_digest
            or admitted_set.admission_policy_digest != policy_digest
            or selector.operator_ceiling_digest != ceiling_digest
            or admitted_set.operator_ceiling_digest != ceiling_digest
        ):
            _deny_resolution(
                "selector_validation",
                "set_policy_mismatch",
                request=request,
                policy=self._policy,
                detail="selector or admitted set does not bind current policy",
            )
        allowed_compiler_abis = {
            item.semantic_version
            for item in self._policy.compiler_constraints.allowed_compilers
        }
        if (
            selector.compiler_abi != admitted_set.compiler_abi
            or selector.compiler_abi not in allowed_compiler_abis
        ):
            _deny_resolution(
                "selector_validation",
                "set_abi_mismatch",
                request=request,
                policy=self._policy,
                pointer="/compiler_abi",
                detail="selector compiler ABI is stale",
            )
        if (
            admitted_set.registry_snapshot_digest
            != self._registries.digests.snapshot_digest
            or not _same(self._policy.registry_digests, self._registries.digests)
        ):
            _deny_resolution(
                "selector_validation",
                "set_root_stale",
                request=request,
                policy=self._policy,
                pointer="/registry_snapshot_digest",
                detail="admitted set registry snapshot is stale",
            )
        self._resolution_validity(
            admitted_set.validity,
            now,
            request=request,
            stage="selector_validation",
            code="set_root_stale",
            pointer="/admitted_set/validity",
        )
        self._resolution_revocation(
            request,
            admitted_set.revocation,
            stage="selector_validation",
            code="set_root_stale",
            pointer="/admitted_set/revocation",
            cache=revocation_cache,
        )

    def _validate_resolution_receipt_header(
        self,
        request: c.ResolveEpisodeRequest,
        selector: c.DirectSelector | c.ConfigSetManifest,
        admitted_set: c.AdmittedSetManifest,
        receipt: c.AdmissionReceipt,
        *,
        receipt_digest: str,
        task_digest: str,
        root_task_binding_digest: str,
        root_policy_binding_ref: c.PolicyBindingRef,
    ) -> None:
        compiler = receipt.compiled.compiler
        if compiler.semantic_version != selector.compiler_abi:
            code = "set_abi_mismatch"
            pointer = "/compiler_abi"
        elif compiler.runtime_abi != selector.runtime_abi:
            code = "set_abi_mismatch"
            pointer = "/runtime_abi"
        elif (
            receipt.effective_capabilities.task.task_contract_digest != task_digest
            or receipt.task_binding_digest != root_task_binding_digest
        ):
            code = "stale_candidate_receipt"
            pointer = "/task_binding_digest"
        elif (
            receipt.admission_policy_digest != admitted_set.admission_policy_digest
            or receipt.operator_ceiling_digest != admitted_set.operator_ceiling_digest
            or receipt.registry_snapshot_digest != admitted_set.registry_snapshot_digest
        ):
            code = "set_policy_mismatch"
            pointer = "/admission_policy_digest"
        elif not _same(receipt.subject, request.subject):
            code = "stale_candidate_receipt"
            pointer = "/subject"
        elif (
            receipt.revocation.scope_digest != request.subject.authority_scope_digest
            or not _same(receipt.revocation, admitted_set.revocation)
        ):
            code = "set_root_stale"
            pointer = "/revocation"
        elif not _same(receipt.policy_binding_ref, root_policy_binding_ref):
            code = "stale_candidate_receipt"
            pointer = "/policy_binding_ref"
        elif not receipt.effective_capabilities.policy_slots:
            code = "stale_candidate_receipt"
            pointer = "/effective_capabilities/policy_slots"
        else:
            return
        _deny_resolution(
            "selector_validation",
            code,
            request=request,
            policy=self._policy,
            artifact_kind=c.ArtifactKind.ADMISSION_RECEIPT,
            artifact_digest=receipt_digest,
            pointer=pointer,
            detail="root receipt header does not share the selector identity envelope",
        )

    def _validate_resolution_windows(
        self,
        request: c.ResolveEpisodeRequest,
        selector: c.DirectSelector | c.ConfigSetManifest,
        admitted_set: c.AdmittedSetManifest,
        receipts: tuple[c.AdmissionReceipt, ...],
        *,
        selection_record_digest: str | None = None,
    ) -> None:
        governing = (self._policy.validity, *(receipt.validity for receipt in receipts))
        for name, validity, code in (
            ("selector", selector.validity, "invalid_config_set"),
            ("admitted_set", admitted_set.validity, "set_root_stale"),
        ):
            bounds = (
                (*governing, admitted_set.validity)
                if name == "selector"
                else governing
            )
            latest_not_before = max(
                _parse_timestamp(item.not_before) for item in bounds
            )
            earliest_expiry = min(
                _parse_timestamp(item.expires_at) for item in bounds
            )
            if (
                _parse_timestamp(validity.not_before) < latest_not_before
                or _parse_timestamp(validity.expires_at) > earliest_expiry
            ):
                _deny_resolution(
                    "pre_allocation_recheck"
                    if selection_record_digest is not None
                    else "selector_validation",
                    "receipt_expired" if selection_record_digest is not None else code,
                    request=request,
                    policy=self._policy,
                    pointer=f"/{name}/validity",
                    selection_record_digest=selection_record_digest,
                    detail="selector and root windows must be contained by policy and every receipt",
                    post_selection=selection_record_digest is not None,
                )

    def _resolution_receipt(
        self,
        request: c.ResolveEpisodeRequest,
        digest: str,
        *,
        candidate_id: str | None,
        now: datetime,
        revocation_cache: dict[str, c.RevocationBinding],
        selection_record_digest: str | None = None,
    ) -> c.VerifiedAdmission:
        post_selection = selection_record_digest is not None
        stage = "overlay_validation" if post_selection else "selector_validation"
        _, payload, _ = _load_runtime_artifact(
            self,
            request,
            digest=digest,
            kind=c.ArtifactKind.ADMISSION_RECEIPT,
            model_type=c.AdmissionReceipt,
            stage=stage,
            code="stale_candidate_receipt",
            candidate_id=candidate_id,
            post_selection=post_selection,
            selection_record_digest=selection_record_digest,
        )
        try:
            return self.verify_receipt(
                _receipt_ref(digest, payload),
                subject=request.subject,
                checkpoint=c.PrivilegedCheckpoint.EPISODE_PREFLIGHT,
                _trusted_now=now,
                _revocation_cache=revocation_cache,
            )
        except c.ConfigRuntimeDenial:
            _deny_resolution(
                stage,
                "stale_candidate_receipt",
                request=request,
                policy=self._policy,
                artifact_kind=c.ArtifactKind.ADMISSION_RECEIPT,
                artifact_digest=digest,
                candidate_id=candidate_id,
                selection_record_digest=selection_record_digest,
                detail="candidate receipt is invalid or non-current",
                post_selection=post_selection,
            )

    def _prevalidate_overlay_ref(
        self,
        request: c.ResolveEpisodeRequest,
        overlay_ref: c.AdmittedOverlayRef,
        *,
        receipt_cache: dict[str, c.VerifiedAdmission],
        admitted_set: c.AdmittedSetManifest,
        candidate_id: str | None,
        selection_record_digest: str | None = None,
    ) -> tuple[c.MutationOverlayManifest, c.VerifiedAdmission]:
        post_selection = selection_record_digest is not None
        overlay, _, _ = _load_runtime_artifact(
            self,
            request,
            digest=overlay_ref.overlay_digest,
            kind=c.ArtifactKind.MUTATION_OVERLAY,
            model_type=c.MutationOverlayManifest,
            stage="overlay_validation" if post_selection else "selector_validation",
            code="invalid_overlay_ref",
            candidate_id=candidate_id,
            post_selection=post_selection,
            selection_record_digest=selection_record_digest,
        )
        if overlay_ref.result_receipt_digest not in admitted_set.receipt_digests:
            _deny_resolution(
                "overlay_validation" if post_selection else "selector_validation",
                "stale_candidate_receipt",
                request=request,
                policy=self._policy,
                artifact_kind=c.ArtifactKind.ADMISSION_RECEIPT,
                artifact_digest=overlay_ref.result_receipt_digest,
                candidate_id=candidate_id,
                selection_record_digest=selection_record_digest,
                detail="overlay result receipt is absent from the pinned admitted-set root",
                post_selection=post_selection,
            )
        return overlay, receipt_cache[overlay_ref.result_receipt_digest]

    def _validate_overlay_chain_link(
        self,
        request: c.ResolveEpisodeRequest,
        overlay_ref: c.AdmittedOverlayRef,
        overlay: c.MutationOverlayManifest,
        result: c.AdmissionReceipt,
        *,
        parent: c.AdmissionReceipt,
        parent_digest: str,
        candidate_id: str | None,
        selection_record_digest: str | None = None,
    ) -> None:
        expected_chain_digest = _overlay_chain_digest(parent, overlay_ref.overlay_digest)
        invalid = (
            overlay.base_compiled_manifest_digest != parent.compiled.manifest_digest
            or overlay.parent_receipt_digest != parent_digest
            or overlay.expected_before_semantic_digest != parent.compiled.semantic_digest
            or result.parent_receipt_digest != parent_digest
            or result.overlay_chain_digest != expected_chain_digest
            or result.compiled.manifest_digest != parent.compiled.manifest_digest
            or result.compiled.semantic_digest != overlay.expected_after_semantic_digest
            or not isinstance(result.behavior_source, c.OverlayDerivedBehaviorSource)
            or result.behavior_source.base_manifest_digest
            != parent.compiled.manifest_digest
            or result.behavior_source.parent_receipt_digest != parent_digest
            or result.behavior_source.overlay_chain_digest != expected_chain_digest
            or result.behavior_source.derived_semantic_digest
            != overlay.expected_after_semantic_digest
        )
        if invalid:
            _deny_resolution(
                "overlay_validation",
                "overlay_receipt_mismatch",
                request=request,
                policy=self._policy,
                artifact_kind=c.ArtifactKind.MUTATION_OVERLAY,
                artifact_digest=overlay_ref.overlay_digest,
                candidate_id=candidate_id,
                selection_record_digest=selection_record_digest,
                detail="overlay, parent, and pre-admitted result receipt do not bind one chain",
                post_selection=selection_record_digest is not None,
            )
        if not _capabilities_do_not_increase(
            result.effective_capabilities, parent.effective_capabilities
        ):
            _deny_resolution(
                "readmission",
                "capability_increase",
                request=request,
                policy=self._policy,
                artifact_digest=overlay_ref.result_receipt_digest,
                candidate_id=candidate_id,
                selection_record_digest=selection_record_digest,
                detail="overlay-derived receipt increases parent authority",
                post_selection=selection_record_digest is not None,
            )

    def _observe_policy(
        self,
        request: c.ResolveEpisodeRequest,
        now: datetime,
        revocation_cache: dict[str, c.RevocationBinding],
    ) -> c.PolicyCapabilityObservation:
        if self._policy_capabilities is None:
            _deny_resolution(
                "policy_observation",
                "observation_unavailable",
                request=request,
                policy=self._policy,
                detail="local policy capability registry is unavailable",
                retry="after_control_plane_change",
            )
        try:
            raw = self._policy_capabilities.observe(
                binding=request.policy_binding,
                subject=request.subject,
                now=now,
            )
            observation = _revalidate_model(c.PolicyCapabilityObservation, raw)
        except BaseException:
            _deny_resolution(
                "policy_observation",
                "attestation_invalid",
                request=request,
                policy=self._policy,
                detail="local policy capability observation is invalid",
            )
        if (
            observation.registry_revision_digest
            != request.policy_binding.registry_revision_digest
            or observation.registry_revision_digest
            != self._registries.digests.route_registry_digest
            or observation.route_id != request.policy_binding.route_id
            or observation.subject_scope_digest != request.subject.authority_scope_digest
        ):
            _deny_resolution(
                "policy_observation",
                "observation_scope_mismatch",
                request=request,
                policy=self._policy,
                detail="policy observation binding or subject scope mismatch",
            )
        if (
            observation.provenance.kind is c.AttestationKind.OPERATOR_ATTESTATION
            and observation.provenance.signer_key_id is None
        ):
            _deny_resolution(
                "policy_observation",
                "attestation_invalid",
                request=request,
                policy=self._policy,
                pointer="/policy_observation/provenance/signer_key_id",
                detail="operator attestation lacks a signer identity",
            )
        self._resolution_validity(
            observation.provenance.validity,
            now,
            request=request,
            stage="policy_observation",
            code="attestation_expired",
            not_yet_code="attestation_not_yet_valid",
            pointer="/policy_observation/provenance/validity",
        )
        self._resolution_revocation(
            request,
            observation.revocation,
            stage="policy_observation",
            code="observation_revoked",
            pointer="/policy_observation/revocation",
            cache=revocation_cache,
        )
        attestation = next(
            (
                item
                for item in self._registries.policy_capability_attestations
                if item.attestation_digest == request.policy_binding.attestation_digest
            ),
            None,
        )
        route = next(
            (
                item.grant
                for item in self._registries.routes
                if item.grant.route_id == observation.route_id
            ),
            None,
        )
        secret = next(
            (
                item.grant
                for item in self._registries.secret_handles
                if item.grant.handle_id == observation.credential_handle_id
            ),
            None,
        )
        model = next(
            (
                item.identity
                for item in self._registries.models
                if item.identity.model_id == observation.model_id
            ),
            None,
        )
        if (
            attestation is None
            or route is None
            or secret is None
            or model is None
            or attestation.route_id != observation.route_id
            or attestation.route_revision_digest != observation.route_revision_digest
            or route.route_revision_digest != observation.route_revision_digest
            or route.credential_handle_id != observation.credential_handle_id
            or secret.handle_version_digest
            != observation.credential_handle_version_digest
        ):
            _deny_resolution(
                "policy_observation",
                "attestation_invalid",
                request=request,
                policy=self._policy,
                detail="policy observation does not bind route or credential authority",
            )
        signer_key_id = observation.provenance.signer_key_id
        if (
            signer_key_id is not None
            and signer_key_id not in attestation.authorized_signer_key_ids
        ):
            _deny_resolution(
                "policy_observation",
                "attestation_invalid",
                request=request,
                policy=self._policy,
                pointer="/policy_observation/provenance/signer_key_id",
                detail="policy observation signer is not authorized by the current attestation",
            )
        if route.protocol_abi != observation.protocol_abi:
            _deny_resolution(
                "policy_observation",
                "protocol_mismatch",
                request=request,
                policy=self._policy,
                detail="policy observation protocol does not bind route authority",
            )
        if (
            attestation.model_digest != observation.model_digest
            or attestation.tokenizer_digest != observation.tokenizer_digest
            or model.model_id != observation.model_id
            or model.model_digest != observation.model_digest
            or model.tokenizer_digest != observation.tokenizer_digest
        ):
            _deny_resolution(
                "policy_observation",
                "model_mismatch",
                request=request,
                policy=self._policy,
                detail="policy observation model or tokenizer identity mismatch",
            )
        if (
            attestation.checkpoint_digest != observation.checkpoint_digest
            or model.checkpoint_digest != observation.checkpoint_digest
        ):
            _deny_resolution(
                "policy_observation",
                "checkpoint_mismatch",
                request=request,
                policy=self._policy,
                detail="policy observation checkpoint identity mismatch",
            )
        if attestation.capability_digest != observation.capability_digest:
            _deny_resolution(
                "policy_observation",
                "capability_digest_mismatch",
                request=request,
                policy=self._policy,
                detail="policy observation capability identity mismatch",
            )
        self._resolution_validity(
            attestation.validity,
            now,
            request=request,
            stage="policy_observation",
            code="attestation_expired",
            not_yet_code="attestation_not_yet_valid",
            pointer="/policy_binding/attestation_digest",
        )
        self._resolution_revocation(
            request,
            attestation.revocation,
            stage="policy_observation",
            code="observation_revoked",
            pointer="/policy_binding/attestation_digest",
            cache=revocation_cache,
        )
        return observation

    def _match_policy_observation(
        self,
        request: c.ResolveEpisodeRequest,
        observation: c.PolicyCapabilityObservation,
        receipt: c.AdmissionReceipt,
    ) -> None:
        slots = receipt.effective_capabilities.policy_slots
        mismatch = observation.capabilities.policy_slot_count != len(slots)
        for slot in slots:
            mismatch = mismatch or (
                slot.route_id != observation.route_id
                or slot.protocol_abi != observation.protocol_abi
                or slot.secret_handle_id != observation.credential_handle_id
                or slot.model_digest != observation.model_digest
                or slot.tokenizer_digest != observation.tokenizer_digest
                or slot.checkpoint_digest != observation.checkpoint_digest
                or slot.required_policy_capabilities_digest
                != observation.capability_digest
            )
        if mismatch:
            _deny_resolution(
                "policy_observation",
                "required_policy_capability_missing",
                request=request,
                policy=self._policy,
                artifact_digest=receipt.canonical_digest(),
                detail="receipt policy slots do not match the trusted observation",
            )

    def _build_selection_record(
        self,
        request: c.ResolveEpisodeRequest,
        *,
        selector: c.DirectSelector | c.ConfigSetManifest,
        candidate_states: tuple[_CandidateState, ...],
        evaluations: tuple[c.CandidateEvaluation, ...],
        eligible: tuple[c.EligibleCandidate, ...],
        task_digest: str,
        observation: c.PolicyCapabilityObservation,
        revocation_state_digest: str,
    ) -> c.SelectionRecord:
        total_weight: int | None = None
        draw: c.OracleDraw | None = None
        if isinstance(selector, c.DirectSelector):
            selected = eligible[0]
            algorithm = "direct-v1"
        else:
            algorithm = "weighted-v1"
            total_weight = sum(item.weight or 0 for item in eligible)
            if total_weight <= 0 or total_weight > (2**53 - 1):
                _deny_resolution(
                    "selection_oracle",
                    "oracle_input_invalid",
                    request=request,
                    policy=self._policy,
                    detail="eligible weight total is outside uint53",
                )
            assert request.selection_nonce is not None
            preimage = (
                b"bb-weighted-v1\x00"
                + request.selector.digest.encode("ascii")
                + request.selection_nonce.encode("ascii")
                + task_digest.encode("ascii")
                + observation.capability_digest.encode("ascii")
            )
            if len(preimage) != 299:
                _deny_resolution(
                    "selection_oracle",
                    "oracle_input_invalid",
                    request=request,
                    policy=self._policy,
                    detail="weighted-v1 framing is not exactly 299 ASCII bytes",
                )
            raw_draw = hashlib.sha256(preimage).digest()
            modulo = int.from_bytes(raw_draw, "big", signed=False) % total_weight
            cursor = 0
            selected = eligible[-1]
            selected_start = 0
            selected_end = total_weight
            for candidate in eligible:
                assert candidate.weight is not None
                end = cursor + candidate.weight
                if cursor <= modulo < end:
                    selected = candidate
                    selected_start, selected_end = cursor, end
                    break
                cursor = end
            draw = c.OracleDraw(
                preimage_hex=preimage.hex(),
                draw_digest="sha256:" + raw_draw.hex(),
                unsigned_big_endian_hex=raw_draw.hex(),
                total_weight=total_weight,
                modulo=modulo,
                selected_interval_start=selected_start,
                selected_interval_end_exclusive=selected_end,
            )
        state = next(
            item
            for item in candidate_states
            if item.candidate.candidate_id == selected.candidate_id
        )
        return c.SelectionRecord(
            algorithm=algorithm,
            episode_id=request.episode_id,
            subject_digest=_subject_digest(request.subject),
            selector_digest=request.selector.digest,
            config_set_digest=(
                request.selector.digest if isinstance(selector, c.ConfigSetManifest) else None
            ),
            admitted_set_root=selector.admitted_set_root,
            selection_nonce=request.selection_nonce,
            task_contract_digest=task_digest,
            policy_capability_observation_digest=observation.canonical_digest(),
            policy_capability_digest=observation.capability_digest,
            revocation_state_digest=revocation_state_digest,
            candidate_evaluations=evaluations,
            eligible_candidates=eligible,
            total_weight=total_weight,
            draw=draw,
            selected_candidate_id=selected.candidate_id,
            selected_receipt_digest=selected.receipt_digest,
            selected_overlays=state.candidate.overlays,
        )

    def _validate_preexisting_selection_record(
        self,
        request: c.ResolveEpisodeRequest,
        *,
        expected_record: c.SelectionRecord,
        owner_key: str,
        selection_request_digest: str,
    ) -> None:
        try:
            existing_raw = self._store.get_selection_binding(owner_key)
            existing = (
                None
                if existing_raw is None
                else _revalidate_model(c.SelectionBinding, existing_raw)
            )
        except BaseException:
            _deny_resolution(
                "selection_persistence",
                "selection_store_unavailable",
                request=request,
                policy=self._policy,
                detail="selection binding read failed",
                retry="same_input_once",
            )
        if existing is None:
            return
        if (
            existing.owner_key != owner_key
            or existing.request_digest != selection_request_digest
        ):
            _deny_resolution(
                "selection_persistence",
                "selection_idempotency_conflict",
                request=request,
                policy=self._policy,
                selection_record_digest=existing.selection_record_digest,
                detail="episode owner already binds a different immutable request",
                post_selection=True,
            )
        record, _, _ = _load_runtime_artifact(
            self,
            request,
            digest=existing.selection_record_digest,
            kind=c.ArtifactKind.SELECTION_RECORD,
            model_type=c.SelectionRecord,
            stage="selection_persistence",
            code="selection_record_corrupt",
            post_selection=True,
            selection_record_digest=existing.selection_record_digest,
        )
        if not _same(record, expected_record):
            _deny_resolution(
                "selection_persistence",
                "selection_record_corrupt",
                request=request,
                policy=self._policy,
                selection_record_digest=existing.selection_record_digest,
                detail="stored selection record differs from complete canonical semantics",
                post_selection=True,
            )

    def _resolve_selection_commit(
        self,
        request: c.ResolveEpisodeRequest,
        *,
        now: datetime,
        expected_record: c.SelectionRecord,
        owner_key: str,
        selection_request_digest: str,
    ) -> tuple[c.SelectionRecord, c.ArtifactRef, c.SelectionCommitToken]:
        try:
            existing_raw = self._store.get_selection_binding(owner_key)
            existing = (
                None
                if existing_raw is None
                else _revalidate_model(c.SelectionBinding, existing_raw)
            )
        except BaseException:
            _deny_resolution(
                "selection_persistence",
                "selection_store_unavailable",
                request=request,
                policy=self._policy,
                detail="selection binding read failed",
                retry="same_input_once",
            )
        if existing is not None:
            if (
                existing.owner_key != owner_key
                or existing.request_digest != selection_request_digest
            ):
                _deny_resolution(
                    "selection_persistence",
                    "selection_idempotency_conflict",
                    request=request,
                    policy=self._policy,
                    selection_record_digest=existing.selection_record_digest,
                    detail="episode owner already binds a different immutable request",
                    post_selection=True,
                )
            record, _, record_ref = _load_runtime_artifact(
                self,
                request,
                digest=existing.selection_record_digest,
                kind=c.ArtifactKind.SELECTION_RECORD,
                model_type=c.SelectionRecord,
                stage="selection_persistence",
                code="selection_record_corrupt",
                post_selection=True,
                selection_record_digest=existing.selection_record_digest,
            )
            if not _same(record, expected_record):
                _deny_resolution(
                    "selection_persistence",
                    "selection_record_corrupt",
                    request=request,
                    policy=self._policy,
                    selection_record_digest=record.canonical_digest(),
                    detail="stored selection record differs from complete canonical semantics",
                    post_selection=True,
                )
            token = self._bind_selection(
                request,
                now=now,
                owner_key=owner_key,
                request_digest=selection_request_digest,
                record_digest=existing.selection_record_digest,
            )
            return record, record_ref, token

        record = expected_record
        record_ref = _publish_runtime_artifact(
            self,
            request,
            record,
            kind=c.ArtifactKind.SELECTION_RECORD,
            stage="selection_persistence",
            unavailable_code="selection_store_unavailable",
            conflict_code="selection_store_conflict",
            readback_code="selection_readback_mismatch",
        )
        token = self._bind_selection(
            request,
            now=now,
            owner_key=owner_key,
            request_digest=selection_request_digest,
            record_digest=record.canonical_digest(),
        )
        return record, record_ref, token

    def _bind_selection(
        self,
        request: c.ResolveEpisodeRequest,
        *,
        now: datetime,
        owner_key: str,
        request_digest: str,
        record_digest: str,
    ) -> c.SelectionCommitToken:
        expected = c.SelectionBinding(
            owner_key=owner_key,
            request_digest=request_digest,
            selection_record_digest=record_digest,
        )
        try:
            raw_token = self._store.bind_selection_once(
                owner_key=owner_key,
                request_digest=request_digest,
                selection_record_digest=record_digest,
            )
            token = _revalidate_model(c.SelectionCommitToken, raw_token)
        except BaseException:
            try:
                readback = self._store.get_selection_binding(owner_key)
                binding = _revalidate_model(c.SelectionBinding, readback)
            except BaseException:
                _deny_resolution(
                    "selection_persistence",
                    "selection_store_unavailable",
                    request=request,
                    policy=self._policy,
                    selection_record_digest=record_digest,
                    detail="selection bind acknowledgment and readback unavailable",
                    retry="same_input_once",
                )
            if not _same(binding, expected):
                _deny_resolution(
                    "selection_persistence",
                    "selection_idempotency_conflict",
                    request=request,
                    policy=self._policy,
                    selection_record_digest=record_digest,
                    detail="selection owner is bound to a different request or record",
                    post_selection=True,
                )
            binding_payload = binding.canonical_bytes()
            token = c.SelectionCommitToken(
                binding=binding,
                binding_ref=c.ArtifactRef(
                    artifact_id=binding.canonical_digest(),
                    sha256=binding.canonical_digest(),
                    size_bytes=len(binding_payload),
                    media_type="application/vnd.breadboard.selection-binding+json;version=1",
                ),
                verified_at=_timestamp(now),
            )
        try:
            readback = _revalidate_model(
                c.SelectionBinding, self._store.get_selection_binding(owner_key)
            )
        except BaseException:
            _deny_resolution(
                "selection_persistence",
                "selection_readback_mismatch",
                request=request,
                policy=self._policy,
                selection_record_digest=record_digest,
                detail="committed selection binding cannot be read back",
                post_selection=True,
            )
        if not _same(readback, expected) or not _same(token.binding, expected):
            _deny_resolution(
                "selection_persistence",
                "selection_idempotency_conflict",
                request=request,
                policy=self._policy,
                selection_record_digest=record_digest,
                detail="selection binding readback conflicts with committed identity",
                post_selection=True,
            )
        return token


    def _load_selected_semantics(
        self,
        request: c.ResolveEpisodeRequest,
        receipt: c.AdmissionReceipt,
        *,
        selection_digest: str | None,
        candidate_id: str | None = None,
    ) -> tuple[dict[str, Any], bytes]:
        digest = receipt.compiled.manifest_digest
        try:
            payload = self._store.load(
                digest,
                kind=c.ArtifactKind.COMPILED_MANIFEST,
                max_bytes=_MAX_RUNTIME_ARTIFACT_BYTES,
            )
            if (
                type(payload) is not bytes
                or not payload
                or len(payload) > _MAX_RUNTIME_ARTIFACT_BYTES
                or _bytes_digest(payload) != digest
                or canonical_json_bytes(canonical_json_loads(payload)) != payload
            ):
                raise ValueError("compiled manifest bytes are invalid")
            raw_semantics = self._compiler.extract_effective_semantics(
                canonical_manifest_bytes=payload
            )
            semantics_obj = canonical_json_loads(canonical_json_bytes(raw_semantics))
            if type(semantics_obj) is not dict:
                raise TypeError("effective semantics must be an object")
            semantic_digest = _digest(
                {
                    "schema": c.COMPILED_CONFIG_SEMANTIC_SCHEMA_ID,
                    "config": semantics_obj,
                }
            )
            if semantic_digest != receipt.compiled.semantic_digest:
                raise ValueError("semantic projection does not bind the receipt")
        except BaseException:
            _deny_resolution(
                "overlay_validation",
                "overlay_base_mismatch",
                request=request,
                policy=self._policy,
                artifact_kind=c.ArtifactKind.COMPILED_MANIFEST,
                artifact_digest=digest,
                candidate_id=candidate_id,
                selection_record_digest=selection_digest,
                detail="compiled manifest or semantic projection is invalid",
                post_selection=selection_digest is not None,
            )
        return semantics_obj, payload

    def _validate_overlay_operations(
        self,
        request: c.ResolveEpisodeRequest,
        overlay_ref: c.AdmittedOverlayRef,
        overlay: c.MutationOverlayManifest,
        *,
        parent: c.VerifiedAdmission,
        candidate_id: str | None,
        selection_record_digest: str | None,
    ) -> None:
        rules = {
            rule.pointer: rule
            for rule in parent.receipt.effective_capabilities.mutable_pointers
        }
        post_selection = selection_record_digest is not None
        for index, operation in enumerate(overlay.operations):
            try:
                tokens = _decode_pointer(operation.path)
            except Exception:
                _deny_resolution(
                    "overlay_validation",
                    "noncanonical_pointer",
                    request=request,
                    policy=self._policy,
                    artifact_digest=overlay_ref.overlay_digest,
                    candidate_id=candidate_id,
                    pointer=operation.path,
                    operation_index=index,
                    selection_record_digest=selection_record_digest,
                    detail="overlay pointer is malformed or noncanonical",
                    post_selection=post_selection,
                )
            if tokens[0] in _PROTECTED_OVERLAY_ROOTS or any(
                tokens[: len(path)] == path for path in _PROTECTED_ARTIFACT_PATHS
            ):
                _deny_resolution(
                    "overlay_validation",
                    "protected_pointer",
                    request=request,
                    policy=self._policy,
                    artifact_digest=overlay_ref.overlay_digest,
                    candidate_id=candidate_id,
                    pointer=operation.path,
                    operation_index=index,
                    selection_record_digest=selection_record_digest,
                    detail="overlay pointer targets protected authority",
                    post_selection=post_selection,
                )
            rule = rules.get(operation.path)
            if rule is None:
                _deny_resolution(
                    "overlay_validation",
                    "pointer_not_mutable",
                    request=request,
                    policy=self._policy,
                    artifact_digest=overlay_ref.overlay_digest,
                    candidate_id=candidate_id,
                    pointer=operation.path,
                    operation_index=index,
                    selection_record_digest=selection_record_digest,
                    detail="overlay pointer lacks an exact admitted mutable rule",
                    post_selection=post_selection,
                )
            if c.MutableOperation(operation.op) not in rule.allowed_operations:
                _deny_resolution(
                    "overlay_validation",
                    "operation_not_allowed",
                    request=request,
                    policy=self._policy,
                    artifact_digest=overlay_ref.overlay_digest,
                    candidate_id=candidate_id,
                    pointer=operation.path,
                    operation_index=index,
                    selection_record_digest=selection_record_digest,
                    detail="overlay operation is not admitted for this exact pointer",
                    post_selection=post_selection,
                )
            if operation.op == "remove" and not rule.removable:
                _deny_resolution(
                    "overlay_application",
                    "remove_not_allowed",
                    request=request,
                    policy=self._policy,
                    artifact_digest=overlay_ref.overlay_digest,
                    candidate_id=candidate_id,
                    pointer=operation.path,
                    operation_index=index,
                    selection_record_digest=selection_record_digest,
                    detail="overlay removal is not explicitly admitted",
                    post_selection=post_selection,
                )
            if operation.op != "remove" and _overlay_value_forbidden(operation.value):
                _deny_resolution(
                    "overlay_validation",
                    "overlay_value_forbidden",
                    request=request,
                    policy=self._policy,
                    artifact_digest=overlay_ref.overlay_digest,
                    candidate_id=candidate_id,
                    pointer=operation.path,
                    operation_index=index,
                    selection_record_digest=selection_record_digest,
                    detail="overlay value contains raw authority",
                    post_selection=post_selection,
                )

    def _evaluate_overlay(
        self,
        request: c.ResolveEpisodeRequest,
        overlay_ref: c.AdmittedOverlayRef,
        overlay: c.MutationOverlayManifest,
        *,
        parent: c.VerifiedAdmission,
        semantics: dict[str, Any],
        manifest_bytes: bytes,
        candidate_id: str | None,
        selection_record_digest: str | None,
        verify_with_compiler: bool,
    ) -> tuple[dict[str, Any], tuple[c.OverlayTransition, ...]]:
        self._validate_overlay_operations(
            request,
            overlay_ref,
            overlay,
            parent=parent,
            candidate_id=candidate_id,
            selection_record_digest=selection_record_digest,
        )
        post_selection = selection_record_digest is not None
        current = semantics
        current_digest = parent.receipt.compiled.semantic_digest
        if overlay.expected_before_semantic_digest != current_digest:
            _deny_resolution(
                "overlay_validation",
                "overlay_transition_mismatch",
                request=request,
                policy=self._policy,
                artifact_digest=overlay_ref.overlay_digest,
                candidate_id=candidate_id,
                selection_record_digest=selection_record_digest,
                detail="overlay before digest does not bind parent semantics",
                post_selection=post_selection,
            )
        transitions: list[c.OverlayTransition] = []
        for index, operation in enumerate(overlay.operations):
            expected_transition = overlay.expected_transitions[index]
            if expected_transition.before_semantic_digest != current_digest:
                _deny_resolution(
                    "overlay_validation",
                    "overlay_transition_mismatch",
                    request=request,
                    policy=self._policy,
                    artifact_digest=overlay_ref.overlay_digest,
                    candidate_id=candidate_id,
                    pointer=operation.path,
                    operation_index=index,
                    selection_record_digest=selection_record_digest,
                    detail="overlay transition before digest mismatch",
                    post_selection=post_selection,
                )
            try:
                updated = _operation_apply(current, operation)
            except FileExistsError:
                code = "add_target_exists"
            except NotImplementedError:
                code = "array_add_forbidden"
            except (KeyError, IndexError, ValueError):
                code = (
                    "remove_target_missing"
                    if operation.op == "remove"
                    else "replace_target_missing"
                    if operation.op == "replace"
                    else "add_parent_missing"
                )
            else:
                code = None
            if code is not None:
                _deny_resolution(
                    "overlay_application",
                    code,
                    request=request,
                    policy=self._policy,
                    artifact_digest=overlay_ref.overlay_digest,
                    candidate_id=candidate_id,
                    pointer=operation.path,
                    operation_index=index,
                    selection_record_digest=selection_record_digest,
                    detail="overlay target shape does not permit the operation",
                    post_selection=post_selection,
                )
            try:
                normalized = self._compiler.normalize_effective_semantics(
                    canonical_manifest_bytes=manifest_bytes,
                    effective_semantics=updated,
                )
                if not isinstance(normalized, Mapping):
                    raise TypeError(
                        "compiler semantic normalization must return a mapping"
                    )
                updated = _deep_thaw(normalized)
            except BaseException:
                _deny_resolution(
                    "overlay_application",
                    "post_overlay_schema_invalid",
                    request=request,
                    policy=self._policy,
                    artifact_digest=overlay_ref.overlay_digest,
                    candidate_id=candidate_id,
                    pointer=operation.path,
                    operation_index=index,
                    selection_record_digest=selection_record_digest,
                    detail="compiler semantic normalization failed",
                    post_selection=post_selection,
                )
            canonical_after_digest = _digest(
                {
                    "schema": c.COMPILED_CONFIG_SEMANTIC_SCHEMA_ID,
                    "config": updated,
                }
            )
            if verify_with_compiler:
                try:
                    after_digest = self._compiler.validate_effective_semantics(
                        canonical_manifest_bytes=manifest_bytes,
                        effective_semantics=updated,
                    )
                    if (
                        _DIGEST_RE.fullmatch(after_digest) is None
                        or after_digest != canonical_after_digest
                    ):
                        raise ValueError("semantic validator returned an invalid digest")
                except TypeError:
                    validation_code = "overlay_type_mismatch"
                except ValueError as exc:
                    description = str(exc).casefold()
                    validation_code = (
                        "overlay_bounds_violation"
                        if "bound" in description
                        else "implicit_default_forbidden"
                        if "default" in description
                        else "post_overlay_invariant_invalid"
                        if "invariant" in description
                        else "post_overlay_schema_invalid"
                    )
                except BaseException:
                    validation_code = "post_overlay_schema_invalid"
                else:
                    validation_code = None
                if validation_code is not None:
                    _deny_resolution(
                        "overlay_application",
                        validation_code,
                        request=request,
                        policy=self._policy,
                        artifact_digest=overlay_ref.overlay_digest,
                        candidate_id=candidate_id,
                        pointer=operation.path,
                        operation_index=index,
                        selection_record_digest=selection_record_digest,
                        detail="full semantic schema or invariant validation failed",
                        post_selection=post_selection,
                    )
            else:
                after_digest = canonical_after_digest
            if after_digest != expected_transition.after_semantic_digest:
                _deny_resolution(
                    "overlay_validation",
                    "overlay_transition_mismatch",
                    request=request,
                    policy=self._policy,
                    artifact_digest=overlay_ref.overlay_digest,
                    candidate_id=candidate_id,
                    pointer=operation.path,
                    operation_index=index,
                    selection_record_digest=selection_record_digest,
                    detail="overlay transition after digest mismatch",
                    post_selection=post_selection,
                )
            transitions.append(
                c.OverlayTransition(
                    operation_index=index,
                    before_semantic_digest=current_digest,
                    after_semantic_digest=after_digest,
                )
            )
            current = updated
            current_digest = after_digest
        if current_digest != overlay.expected_after_semantic_digest:
            _deny_resolution(
                "overlay_validation",
                "overlay_transition_mismatch",
                request=request,
                policy=self._policy,
                artifact_digest=overlay_ref.overlay_digest,
                candidate_id=candidate_id,
                selection_record_digest=selection_record_digest,
                detail="overlay final semantic digest mismatch",
                post_selection=post_selection,
            )
        return current, tuple(transitions)

    def _apply_overlay(
        self,
        request: c.ResolveEpisodeRequest,
        overlay_ref: c.AdmittedOverlayRef,
        overlay: c.MutationOverlayManifest,
        *,
        parent: c.VerifiedAdmission,
        parent_digest: str,
        expected_result: c.VerifiedAdmission,
        semantics: dict[str, Any],
        manifest_bytes: bytes,
        selection_record_digest: str,
        candidate_id: str | None = None,
    ) -> tuple[dict[str, Any], c.OverlayApplicationRecord]:
        current, transitions = self._evaluate_overlay(
            request,
            overlay_ref,
            overlay,
            parent=parent,
            semantics=semantics,
            manifest_bytes=manifest_bytes,
            candidate_id=candidate_id,
            selection_record_digest=selection_record_digest,
            verify_with_compiler=True,
        )
        result_receipt = expected_result.receipt
        expected_chain_digest = _overlay_chain_digest(
            parent.receipt, overlay_ref.overlay_digest
        )
        behavior_source = c.OverlayDerivedBehaviorSource(
            base_manifest_digest=parent.receipt.compiled.manifest_digest,
            parent_receipt_digest=parent_digest,
            overlay_chain_digest=expected_chain_digest,
            derived_semantic_digest=overlay.expected_after_semantic_digest,
        )
        derived_request = c.AdmissionRequest(
            subject=request.subject,
            behavior_source=behavior_source,
            compiled=result_receipt.compiled,
            requested_capabilities=result_receipt.effective_capabilities,
            requested_capability_digest=result_receipt.effective_capability_digest,
            task_binding_digest=result_receipt.task_binding_digest,
            policy_binding_ref=result_receipt.policy_binding_ref,
            admission_policy_digest=result_receipt.admission_policy_digest,
            registry_snapshot_digest=result_receipt.registry_snapshot_digest,
            validity=result_receipt.validity,
            parent_receipt_digest=parent_digest,
            overlay_chain_digest=expected_chain_digest,
        )
        try:
            admitted = self.admit(derived_request)
        except c.ConfigRuntimeDenial:
            _deny_resolution(
                "readmission",
                "derived_receipt_mismatch",
                request=request,
                policy=self._policy,
                artifact_digest=overlay_ref.result_receipt_digest,
                candidate_id=candidate_id,
                selection_record_digest=selection_record_digest,
                detail="overlay-derived behavior failed mandatory re-admission",
                post_selection=True,
            )
        if admitted.digest != overlay_ref.result_receipt_digest:
            _deny_resolution(
                "readmission",
                "derived_receipt_mismatch",
                request=request,
                policy=self._policy,
                artifact_digest=overlay_ref.result_receipt_digest,
                candidate_id=candidate_id,
                selection_record_digest=selection_record_digest,
                detail="mandatory re-admission did not reproduce pre-admitted receipt",
                post_selection=True,
            )
        application = c.OverlayApplicationRecord(
            overlay_digest=overlay_ref.overlay_digest,
            parent_receipt_digest=parent_digest,
            result_receipt_digest=overlay_ref.result_receipt_digest,
            before_semantic_digest=overlay.expected_before_semantic_digest,
            transitions=transitions,
            after_semantic_digest=overlay.expected_after_semantic_digest,
            provenance_digest=overlay.provenance.canonical_digest(),
        )
        return current, application

    def _authenticate(self, request: c.AdmissionRequest, now: datetime) -> None:
        if request.subject.authority_scope_digest != self._policy.subject_scope_digest:
            _deny(
                "subject_authentication", "subject_scope_mismatch", request=request,
                policy=self._policy, pointer="/subject/authority_scope_digest",
                detail="authenticated subject is outside policy scope",
            )
        if request.admission_policy_digest != _policy_digest(self._policy):
            _deny(
                "subject_authentication", "subject_scope_mismatch", request=request,
                policy=self._policy, pointer="/admission_policy_digest",
                detail="admission policy digest mismatch",
            )
        self._check_validity(
            self._policy.validity, now, stage="subject_authentication",
            not_yet_code="subject_scope_mismatch", expired_code="subject_scope_mismatch",
            request=request, pointer="/admission_policy_digest",
        )
        self._check_validity(
            request.validity, now, stage="subject_authentication",
            not_yet_code="subject_scope_mismatch", expired_code="subject_scope_mismatch",
            request=request, pointer="/validity",
        )

    def _verify_compiled(self, request: c.AdmissionRequest, view: CompilerSemanticView) -> None:
        if not isinstance(view, CompilerSemanticView):
            _deny(
                "compiled_artifact_verification", "incomplete_capability_vector", request=request,
                policy=self._policy, pointer="/compiler_view/requested_capabilities",
                detail="compiler semantic view missing",
            )
        roles = view.roles
        missing = sorted(_REQUIRED_COMPILER_ROLES - set(roles))
        if missing:
            role = missing[0]
            missing_code = {
                "requested_capabilities": "incomplete_capability_vector",
                "task_contract": "incomplete_task_contract",
                "mutable_pointer_declarations": "invalid_mutable_pointer_declaration",
                "loss_disposition": "runner_visible_loss",
                "authority_disposition": "forbidden_raw_authority",
            }.get(role, "compiled_digest_mismatch")
            _deny(
                "compiled_artifact_verification", missing_code,
                request=request, policy=self._policy,
                pointer=f"/compiler_view/{role}", detail={"missing_role": role},
            )
        if set(roles) != _REQUIRED_COMPILER_ROLES:
            _deny(
                "compiled_artifact_verification", "forbidden_raw_authority", request=request,
                policy=self._policy, pointer="/compiler_view/authority_disposition",
                detail="unknown semantic role",
            )
        compiler = request.compiled.compiler
        compiler_view = roles["compiler_identity"]
        if not isinstance(compiler_view, Mapping):
            _deny(
                "compiled_artifact_verification", "unsupported_manifest_schema", request=request,
                policy=self._policy, pointer="/compiler_view/compiler_identity/manifest_schema_digest",
                detail="compiler identity is malformed",
            )
        if compiler_view.get("canonicalizer_id") != CANONICALIZER_ID:
            _deny(
                "compiled_artifact_verification", "unsupported_canonicalizer", request=request,
                policy=self._policy, pointer="/compiler_view/compiler_identity/canonicalizer_id",
                detail="unsupported canonicalizer",
            )
        expected_compiler = _obj(compiler)
        if compiler_view.get("manifest_schema_digest") != expected_compiler["manifest_schema_digest"]:
            _deny(
                "compiled_artifact_verification", "unsupported_manifest_schema", request=request,
                policy=self._policy, pointer="/compiler_view/compiler_identity/manifest_schema_digest",
                detail="manifest schema identity mismatch",
            )
        for field in sorted(expected_compiler):
            if compiler_view.get(field) != expected_compiler[field]:
                _deny(
                    "compiled_artifact_verification", "compiled_digest_mismatch", request=request,
                    policy=self._policy, pointer=f"/compiler_view/compiler_identity/{field}",
                    detail="compiler identity mismatch",
                )
        if compiler.runtime_abi != request.requested_capabilities.runner.runtime_abi:
            _deny(
                "compiled_artifact_verification", "compiled_digest_mismatch",
                request=request, policy=self._policy,
                pointer="/compiled/compiler/runtime_abi",
                detail="compiler and requested runner ABI differ",
            )
        if _find_exact(self._policy.compiler_constraints.allowed_compilers, compiler) is None:
            _deny(
                "compiled_artifact_verification", "unsupported_manifest_schema", request=request,
                policy=self._policy, pointer="/compiler_view/compiler_identity/manifest_schema_digest",
                detail="compiler identity is not allowed",
            )
        expected_roles = {
            "compile_input_identity": {
                "bundle_digest": request.compiled.bundle_digest,
                "closure_digest": request.compiled.closure_digest,
                "compiler_input_digest": request.compiled.compiler_input_digest,
            },
            "semantic_identity": {
                "manifest_digest": request.compiled.manifest_digest,
                "semantic_digest": request.compiled.semantic_digest,
            },
            "requested_capabilities": _obj(request.requested_capabilities),
            "task_contract": {
                "task_binding_digest": request.task_binding_digest,
                "task": _obj(request.requested_capabilities.task),
            },
            "mutable_pointer_declarations": _obj(request.requested_capabilities.mutable_pointers),
        }
        for role, expected in expected_roles.items():
            if _same(roles[role], expected):
                continue
            code = "compiled_digest_mismatch"
            pointer = f"/compiler_view/{role}"
            if role == "compile_input_identity" and isinstance(roles[role], Mapping):
                field = next((name for name in expected if roles[role].get(name) != expected[name]), None)
                if field is not None:
                    pointer += f"/{field}"
            elif role == "semantic_identity" and isinstance(roles[role], Mapping):
                field = next((name for name in expected if roles[role].get(name) != expected[name]), None)
                if field is not None:
                    pointer += f"/{field}"
            elif role == "requested_capabilities":
                code = "incomplete_capability_vector"
            elif role == "task_contract":
                code = "incomplete_task_contract"
            elif role == "mutable_pointer_declarations":
                code = "invalid_mutable_pointer_declaration"
                pointer += "/0/pointer"
            _deny(
                "compiled_artifact_verification", code, request=request, policy=self._policy,
                pointer=pointer, detail={"semantic_role": role},
            )
        if _digest(roles["provenance"]) != request.compiled.provenance_digest:
            _deny(
                "compiled_artifact_verification", "compiled_digest_mismatch", request=request,
                policy=self._policy, pointer="/compiler_view/provenance", detail="provenance digest mismatch",
            )
        if _digest(roles["diagnostics"]) != request.compiled.diagnostics_digest:
            pointer = "/compiler_view/diagnostics"
            if isinstance(roles["diagnostics"], Mapping) and "defaults" not in roles["diagnostics"]:
                pointer += "/defaults"
            _deny(
                "compiled_artifact_verification", "compiled_digest_mismatch", request=request,
                policy=self._policy, pointer=pointer, detail="diagnostics digest mismatch",
            )
        loss = roles["loss_disposition"]
        losses = loss.get("runner_visible_losses") if isinstance(loss, Mapping) else loss
        if losses not in ([], ()):
            pointer = (
                "/compiler_view/loss_disposition/runner_visible_losses/0"
                if isinstance(loss, Mapping)
                else "/compiler_view/loss_disposition/0"
            )
            _deny(
                "compiled_artifact_verification", "runner_visible_loss", request=request,
                policy=self._policy, pointer=pointer, detail="runner-visible loss",
            )
        authority = roles["authority_disposition"]
        if not isinstance(authority, Mapping):
            _deny(
                "compiled_artifact_verification", "forbidden_raw_authority", request=request,
                policy=self._policy, pointer="/compiler_view/authority_disposition",
                detail="authority disposition is malformed",
            )
        allowed_authority_keys = {
            "forbidden_raw_authority", "raw_url", "raw_secret", "environment", "shell",
            "duplicate_binding", "fallback_cycle",
        }
        if not set(authority).issubset(allowed_authority_keys) or authority.get("forbidden_raw_authority", []) not in ([], ()):
            _deny(
                "compiled_artifact_verification", "forbidden_raw_authority", request=request,
                policy=self._policy,
                pointer="/compiler_view/authority_disposition/forbidden_raw_authority/0",
                detail="raw authority is forbidden",
            )
        source = request.behavior_source
        source_semantic = getattr(source, "semantic_digest", None) or getattr(source, "derived_semantic_digest", None)
        source_manifest = getattr(source, "manifest_digest", None) or getattr(source, "base_manifest_digest", None)
        if source_semantic != request.compiled.semantic_digest or source_manifest != request.compiled.manifest_digest:
            _deny(
                "compiled_artifact_verification", "compiled_digest_mismatch", request=request,
                policy=self._policy, pointer="/compiled/manifest_digest",
                detail="behavior source mismatch",
            )

    def _resolve_registry(
        self, request: c.AdmissionRequest, view: CompilerSemanticView, now: datetime
    ) -> None:
        registry = self._registries
        policy = self._policy
        vector = request.requested_capabilities
        disposition = view.roles["authority_disposition"]
        if disposition.get("duplicate_binding") not in (None, False, "", [], ()):
            _deny("registry_resolution", "duplicate_binding", request=request, policy=policy, pointer="/requested_capabilities/tools/1/tool_id", detail="duplicate binding")
        if disposition.get("fallback_cycle") not in (None, False, "", [], ()):
            _deny("registry_resolution", "fallback_cycle", request=request, policy=policy, pointer="/compiler_view/requested_capabilities/tools", detail="fallback cycle")
        if (
            request.registry_snapshot_digest != registry.digests.snapshot_digest
            or policy.registry_digests.snapshot_digest != registry.digests.snapshot_digest
            or not _same(policy.registry_digests, registry.digests)
        ):
            _deny(
                "registry_resolution", "registry_snapshot_mismatch", request=request, policy=policy,
                pointer="/registry_snapshot_digest", detail="registry snapshot mismatch",
            )
        runner_record = next(
            (record for record in registry.runners if record.grant.adapter_id == vector.runner.adapter_id and record.grant.runtime_abi == vector.runner.runtime_abi),
            None,
        )
        if runner_record is None:
            _deny("registry_resolution", "unknown_runner", request=request, policy=policy, pointer="/requested_capabilities/runner/adapter_id", detail="runner is not registered")
        if not _same(runner_record.grant, vector.runner):
            _deny("registry_resolution", "registry_binding_mismatch", request=request, policy=policy, pointer="/requested_capabilities/runner/implementation_digest", detail="runner binding mismatch")
        for index, tool in enumerate(vector.tools):
            record = next((item for item in registry.tools if item.grant.tool_id == tool.tool_id), None)
            if record is None:
                _deny("registry_resolution", "unknown_tool", request=request, policy=policy, pointer=f"/requested_capabilities/tools/{index}/tool_id", detail="tool is not registered")
            if record.reserved and record.grant.implementation_digest != tool.implementation_digest:
                _deny("registry_resolution", "reserved_tool_shadow", request=request, policy=policy, pointer=f"/requested_capabilities/tools/{index}/tool_id", detail="reserved tool shadow")
            if not _same(record.grant, tool):
                _deny("registry_resolution", "registry_binding_mismatch", request=request, policy=policy, pointer=f"/requested_capabilities/tools/{index}/implementation_digest", detail="tool binding mismatch")
        for index, setup in enumerate(vector.setup_plans):
            record = next((item for item in registry.setups if item.grant.setup_id == setup.setup_id), None)
            if record is None:
                _deny("registry_resolution", "unknown_setup", request=request, policy=policy, pointer=f"/requested_capabilities/setup_plans/{index}/setup_id", detail="setup is not registered")
            if not _same(record.grant, setup):
                _deny("registry_resolution", "registry_binding_mismatch", request=request, policy=policy, pointer=f"/requested_capabilities/setup_plans/{index}/implementation_digest", detail="setup binding mismatch")
            if record.derived_plan_digest() != setup.plan_digest:
                _deny("registry_resolution", "registry_binding_mismatch", request=request, policy=policy, pointer=f"/requested_capabilities/setup_plans/{index}/plan_digest", detail="setup plan identity mismatch")
            setup_pointer = f"/requested_capabilities/setup_plans/{index}/plan_digest"
            if (
                not set(record.route_ids).issubset(set(vector.sandbox.egress_route_ids))
                or not set(record.secret_handle_ids).issubset({item.handle_id for item in vector.secret_handles})
            ):
                _deny("registry_resolution", "registry_binding_mismatch", request=request, policy=policy, pointer=setup_pointer, detail="setup route or secret authority mismatch")
            allowed_inputs = (
                set(vector.task.input_artifact_digests)
                | set(vector.task.dataset_digests)
                | {mount.source_artifact_digest for mount in vector.sandbox.mounts}
            )
            if not set(record.input_digests).issubset(allowed_inputs):
                _deny("registry_resolution", "registry_binding_mismatch", request=request, policy=policy, pointer=setup_pointer, detail="setup input authority mismatch")
            if record.timeout_ms > vector.limits.setup_timeout_ms:
                _deny("registry_resolution", "registry_binding_mismatch", request=request, policy=policy, pointer=setup_pointer, detail="setup timeout authority mismatch")
            output_ids = {output.artifact_id for output in record.expected_outputs}
            output_roles = {output.role for output in record.expected_outputs}
            if output_ids != set(record.writable_output_slots) or not output_roles.issubset(set(vector.artifacts.allowed_roles)):
                _deny("registry_resolution", "registry_binding_mismatch", request=request, policy=policy, pointer=setup_pointer, detail="setup output authority mismatch")
            writable_mounts = tuple(
                mount for mount in vector.sandbox.mounts
                if mount.access == c.MountAccess.READ_WRITE
            )
            if any(
                not any(
                    subtree == mount.target_logical_path
                    or subtree.startswith(mount.target_logical_path + "/")
                    for mount in writable_mounts
                )
                for subtree in record.writable_output_subtrees
            ):
                _deny("registry_resolution", "registry_binding_mismatch", request=request, policy=policy, pointer=setup_pointer, detail="setup writable subtree authority mismatch")
        route_records: dict[str, Any] = {}
        for index, route in enumerate(vector.routes):
            record = next((item for item in registry.routes if item.grant.route_id == route.route_id), None)
            if record is None:
                _deny("registry_resolution", "unknown_route", request=request, policy=policy, pointer=f"/requested_capabilities/routes/{index}/route_id", detail="route is not registered")
            if not _same(record.grant, route) or record.derived_route_revision_digest() != route.route_revision_digest:
                _deny("registry_resolution", "registry_binding_mismatch", request=request, policy=policy, pointer=f"/requested_capabilities/routes/{index}/route_revision_digest", detail="route authority binding mismatch")
            route_records[route.route_id] = record
        secret_records: dict[str, Any] = {}
        for index, secret in enumerate(vector.secret_handles):
            record = next((item for item in registry.secret_handles if item.grant.handle_id == secret.handle_id), None)
            if record is None:
                _deny("registry_resolution", "unknown_secret_handle", request=request, policy=policy, pointer=f"/requested_capabilities/secret_handles/{index}/handle_id", detail="secret handle is not registered")
            if not _same(record.grant, secret):
                _deny("registry_resolution", "registry_binding_mismatch", request=request, policy=policy, pointer=f"/requested_capabilities/secret_handles/{index}/handle_version_digest", detail="secret binding mismatch")
            secret_records[secret.handle_id] = record
        runtime_record = next((item for item in registry.sandbox_runtimes if item.binding.runtime_id == vector.sandbox.runtime_id), None)
        if runtime_record is None:
            _deny("registry_resolution", "unknown_runtime", request=request, policy=policy, pointer="/requested_capabilities/sandbox/runtime_id", detail="runtime is not registered")
        if not _same(runtime_record.binding, c.SandboxBinding(
            runtime_id=vector.sandbox.runtime_id,
            runtime_class=vector.sandbox.runtime_class,
            driver_implementation_digest=vector.sandbox.driver_implementation_digest,
            runtime_binary_digest=vector.sandbox.runtime_binary_digest,
            security_policy_digest=vector.sandbox.security_policy_digest,
            image_digest=vector.sandbox.image_digest,
            network_policy_digest=vector.sandbox.network_policy_digest,
        )):
            _deny("registry_resolution", "registry_binding_mismatch", request=request, policy=policy, pointer="/requested_capabilities/sandbox", detail="sandbox runtime binding mismatch")
        image_record = next((item for item in registry.images if item.image_digest == vector.sandbox.image_digest), None)
        if image_record is None:
            _deny("registry_resolution", "unknown_image", request=request, policy=policy, pointer="/requested_capabilities/sandbox/image_digest", detail="image is not registered")
        if image_record.runtime_id != vector.sandbox.runtime_id:
            _deny("registry_resolution", "registry_binding_mismatch", request=request, policy=policy, pointer="/requested_capabilities/sandbox/image_digest", detail="image-runtime binding mismatch")
        task_record = next(
            (
                item for item in registry.task_datasets
                if item.task_contract_digest == vector.task.task_contract_digest
                and item.task_binding_digest == vector.task.task_binding_digest
            ),
            None,
        )
        if task_record is None:
            _deny("registry_resolution", "unknown_task", request=request, policy=policy, pointer="/requested_capabilities/task/task_binding_digest", detail="task is not registered")
        if not _same(task_record.repository_snapshot_digest, vector.task.repository_snapshot_digest):
            _deny("registry_resolution", "registry_binding_mismatch", request=request, policy=policy, pointer="/requested_capabilities/task/repository_snapshot_digest", detail="task repository binding mismatch")
        registered_datasets = {digest for record in registry.task_datasets for digest in record.dataset_digests}
        unknown_dataset = next((digest for digest in vector.task.dataset_digests if digest not in registered_datasets), None)
        if unknown_dataset is not None:
            index = vector.task.dataset_digests.index(unknown_dataset)
            _deny("registry_resolution", "unknown_dataset", request=request, policy=policy, pointer=f"/requested_capabilities/task/dataset_digests/{index}", detail="dataset is not registered")
        if vector.task.repository_snapshot_digest is not None:
            repository = next((item for item in registry.repository_bindings if item.repository_snapshot_digest == vector.task.repository_snapshot_digest), None)
            if repository is None:
                _deny("registry_resolution", "unknown_repository_binding", request=request, policy=policy, pointer="/requested_capabilities/task/repository_snapshot_digest", detail="repository is not registered")
        for index, slot in enumerate(vector.policy_slots):
            model = next(
                (item.identity for item in registry.models if item.identity.model_digest == slot.model_digest),
                None,
            )
            if model is None:
                _deny("registry_resolution", "unknown_model", request=request, policy=policy, pointer=f"/requested_capabilities/policy_slots/{index}/model_digest", detail="model is not registered")
            if slot.route_id not in route_records or slot.secret_handle_id not in secret_records:
                _deny("registry_resolution", "registry_binding_mismatch", request=request, policy=policy, pointer=f"/requested_capabilities/policy_slots/{index}", detail="policy slot binding mismatch")
            route = route_records[slot.route_id].grant
            if route.credential_handle_id != slot.secret_handle_id or route.protocol_abi != slot.protocol_abi:
                _deny("registry_resolution", "registry_binding_mismatch", request=request, policy=policy, pointer=f"/requested_capabilities/policy_slots/{index}", detail="policy route-secret binding mismatch")
        verifier_record = next(
            (record for record in registry.verifiers if record.grant.verifier_id == vector.verifier.verifier_id),
            None,
        )
        if verifier_record is None:
            _deny("registry_resolution", "unknown_verifier", request=request, policy=policy, pointer="/requested_capabilities/verifier/verifier_id", detail="verifier is not registered")
        verifier_image = next(
            (record for record in registry.images if record.image_digest == vector.verifier.image_digest),
            None,
        )
        if verifier_image is None:
            _deny("registry_resolution", "unknown_image", request=request, policy=policy, pointer="/requested_capabilities/verifier/image_digest", detail="verifier image is not registered")
        verifier_runtime = next(
            (record for record in registry.sandbox_runtimes if record.binding.runtime_id == verifier_record.runtime_id),
            None,
        )
        if verifier_runtime is None:
            _deny("registry_resolution", "unknown_runtime", request=request, policy=policy, pointer="/requested_capabilities/verifier/image_digest", detail="verifier runtime is not registered")
        if not set(vector.verifier.secret_handle_ids).issubset(set(secret_records)):
            _deny("registry_resolution", "registry_binding_mismatch", request=request, policy=policy, pointer="/requested_capabilities/verifier/secret_handle_ids", detail="verifier secret binding mismatch")
        evidence_record = _find_exact(registry.evidence_policies, vector.evidence, "policy")
        if evidence_record is None:
            _deny("registry_resolution", "unknown_evidence_policy", request=request, policy=policy, pointer="/requested_capabilities/evidence/policy_id", detail="evidence policy is not registered")
        if not any(_same(item.grant.policy, vector.retention) for item in registry.retention_policies):
            _deny("registry_resolution", "unknown_retention_policy", request=request, policy=policy, pointer="/requested_capabilities/retention/policy_id", detail="retention policy is not registered")
        binding = request.policy_binding_ref
        if binding.route_id not in route_records:
            _deny("registry_resolution", "unknown_route", request=request, policy=policy, pointer="/policy_binding_ref/route_id", detail="policy binding route is not registered")
        if binding.registry_revision_digest != registry.digests.route_registry_digest:
            _deny("registry_resolution", "registry_binding_mismatch", request=request, policy=policy, pointer="/policy_binding_ref/registry_revision_digest", detail="policy binding registry mismatch")
        attestation = next(
            (
                record for record in registry.policy_capability_attestations
                if record.attestation_digest == binding.attestation_digest
            ),
            None,
        )
        if attestation is None:
            _deny("registry_resolution", "unknown_policy_binding", request=request, policy=policy, pointer="/policy_binding_ref/attestation_digest", detail="policy attestation is not registered")
        matching_slots = tuple(slot for slot in vector.policy_slots if slot.route_id == binding.route_id)
        route_record = route_records[binding.route_id]
        if (
            len(matching_slots) != 1
            or attestation.route_id != binding.route_id
            or attestation.route_revision_digest != route_record.grant.route_revision_digest
            or attestation.model_digest != matching_slots[0].model_digest
            or attestation.tokenizer_digest != matching_slots[0].tokenizer_digest
            or attestation.checkpoint_digest != matching_slots[0].checkpoint_digest
            or attestation.capability_digest != matching_slots[0].required_policy_capabilities_digest
        ):
            _deny("registry_resolution", "registry_binding_mismatch", request=request, policy=policy, pointer="/policy_binding_ref/attestation_digest", detail="policy attestation authority mismatch")
        self._check_validity(
            attestation.validity,
            now,
            stage="registry_resolution",
            not_yet_code="attestation_not_yet_valid",
            expired_code="attestation_expired",
            request=request,
            pointer="/policy_binding_ref/attestation_digest",
        )
        try:
            attestation_revocation = _revalidate_model(
                c.RevocationBinding,
                self._revocations.load(attestation.revocation.scope_digest),
            )
        except BaseException:
            _deny("registry_resolution", "registry_binding_mismatch", request=request, policy=policy, pointer="/policy_binding_ref/attestation_digest", detail="policy attestation revocation unavailable")
        if not _same(attestation_revocation, attestation.revocation):
            _deny("registry_resolution", "registry_binding_mismatch", request=request, policy=policy, pointer="/policy_binding_ref/attestation_digest", detail="policy attestation is revoked")

    def _prove_ceiling(self, request: c.AdmissionRequest) -> None:
        vector = request.requested_capabilities
        ceiling = self._policy.ceiling

        runner = next(
            (
                item for item in ceiling.runner_bindings
                if item.adapter_id == vector.runner.adapter_id
                and item.runtime_abi == vector.runner.runtime_abi
            ),
            None,
        )
        if runner is None or runner.implementation_digest != vector.runner.implementation_digest:
            field = "implementation_digest" if runner is not None else "adapter_id"
            _deny("capability_intersection", "unsupported_capability", request=request, policy=self._policy, pointer=f"/requested_capabilities/runner/{field}", detail="runner exceeds ceiling")

        for index, tool in enumerate(vector.tools):
            allowed = next((item for item in ceiling.tool_grants if item.tool_id == tool.tool_id), None)
            if allowed is None:
                _deny("capability_intersection", "unsupported_capability", request=request, policy=self._policy, pointer=f"/requested_capabilities/tools/{index}/tool_id", detail="tool exceeds ceiling")
            if allowed.implementation_digest != tool.implementation_digest:
                _deny("capability_intersection", "unsupported_capability", request=request, policy=self._policy, pointer=f"/requested_capabilities/tools/{index}/implementation_digest", detail="tool implementation exceeds ceiling")
            if not set(tool.capability_ids).issubset(set(allowed.capability_ids)):
                _deny("capability_intersection", "unsupported_capability", request=request, policy=self._policy, pointer=f"/requested_capabilities/tools/{index}/capability_ids", detail="tool capabilities exceed ceiling")

        for field, identifier, allowed_items in (
            ("setup_plans", "setup_id", ceiling.setup_grants),
            ("routes", "route_id", ceiling.route_grants),
            ("secret_handles", "handle_id", ceiling.secret_handle_grants),
            ("policy_slots", "slot_id", ceiling.policy_slot_grants),
        ):
            for index, item in enumerate(getattr(vector, field)):
                allowed = next((candidate for candidate in allowed_items if getattr(candidate, identifier) == getattr(item, identifier)), None)
                if allowed is None:
                    _deny("capability_intersection", "unsupported_capability", request=request, policy=self._policy, pointer=f"/requested_capabilities/{field}/{index}/{identifier}", detail=f"{field} exceeds ceiling")
                requested_obj = _obj(item)
                allowed_obj = _obj(allowed)
                difference = next((name for name in requested_obj if requested_obj[name] != allowed_obj[name]), None)
                if difference is not None:
                    _deny("capability_intersection", "unsupported_capability", request=request, policy=self._policy, pointer=f"/requested_capabilities/{field}/{index}/{difference}", detail=f"{field} exceeds ceiling")

        sandbox_base = c.SandboxBinding(
            runtime_id=vector.sandbox.runtime_id,
            runtime_class=vector.sandbox.runtime_class,
            driver_implementation_digest=vector.sandbox.driver_implementation_digest,
            runtime_binary_digest=vector.sandbox.runtime_binary_digest,
            security_policy_digest=vector.sandbox.security_policy_digest,
            image_digest=vector.sandbox.image_digest,
            network_policy_digest=vector.sandbox.network_policy_digest,
        )
        allowed_sandbox = next((item for item in ceiling.sandbox_bindings if item.runtime_id == sandbox_base.runtime_id), None)
        if allowed_sandbox is None:
            _deny("capability_intersection", "unsupported_capability", request=request, policy=self._policy, pointer="/requested_capabilities/sandbox/runtime_id", detail="sandbox exceeds ceiling")
        sandbox_obj = _obj(sandbox_base)
        allowed_sandbox_obj = _obj(allowed_sandbox)
        sandbox_difference = next((name for name in sandbox_obj if sandbox_obj[name] != allowed_sandbox_obj[name]), None)
        if sandbox_difference is not None:
            _deny("capability_intersection", "unsupported_capability", request=request, policy=self._policy, pointer=f"/requested_capabilities/sandbox/{sandbox_difference}", detail="sandbox exceeds ceiling")
        if vector.sandbox.runtime_class in set(self._policy.required_security.prohibited_runtime_classes):
            _deny("capability_intersection", "required_security_missing", request=request, policy=self._policy, pointer="/requested_capabilities/sandbox/runtime_class", detail="runtime class is prohibited")
        if _isolation_rank(vector.sandbox.runtime_class) < _isolation_rank(self._policy.required_security.minimum_isolation_class):
            _deny("capability_intersection", "required_security_missing", request=request, policy=self._policy, pointer="/requested_capabilities/sandbox/runtime_class", detail="sandbox isolation is insufficient")
        for index, route_id in enumerate(vector.sandbox.egress_route_ids):
            if route_id not in set(ceiling.allowed_egress_route_ids):
                _deny("capability_intersection", "unsupported_capability", request=request, policy=self._policy, pointer=f"/requested_capabilities/sandbox/egress_route_ids/{index}", detail="egress route exceeds ceiling")
        for index, mount in enumerate(vector.sandbox.mounts):
            matching_mounts = tuple(
                item for item in ceiling.mount_grants
                if item.source_artifact_digest == mount.source_artifact_digest
                and item.target_logical_path == mount.target_logical_path
            )
            if not matching_mounts:
                _deny("capability_intersection", "unsupported_capability", request=request, policy=self._policy, pointer=f"/requested_capabilities/sandbox/mounts/{index}/source_artifact_digest", detail="mount exceeds ceiling")
            allowed_mount = matching_mounts[0]
            if mount.max_bytes > allowed_mount.max_bytes:
                _deny("capability_intersection", "operator_ceiling_exceeded", request=request, policy=self._policy, pointer=f"/requested_capabilities/sandbox/mounts/{index}/max_bytes", detail="mount bytes exceed ceiling")
            if not _mount_allowed(mount, matching_mounts):
                _deny("capability_intersection", "unsupported_capability", request=request, policy=self._policy, pointer=f"/requested_capabilities/sandbox/mounts/{index}/access", detail="mount access exceeds ceiling")

        for field, maxima in (("resources", ceiling.resource_maxima), ("limits", ceiling.execution_maxima)):
            valid, name = _numeric_leq(getattr(vector, field), maxima)
            if not valid:
                _deny("capability_intersection", "operator_ceiling_exceeded", request=request, policy=self._policy, pointer=f"/requested_capabilities/{field}/{name}", detail=f"{field} exceeds ceiling")

        task = vector.task
        for name, value, allowed in (
            ("task_contract_digest", task.task_contract_digest, ceiling.task_contract_digests),
            ("task_binding_digest", task.task_binding_digest, ceiling.task_binding_digests),
        ):
            if value not in set(allowed):
                _deny("capability_intersection", "unsupported_capability", request=request, policy=self._policy, pointer=f"/requested_capabilities/task/{name}", detail="task exceeds ceiling")
        if task.repository_snapshot_digest is not None and task.repository_snapshot_digest not in set(ceiling.repository_snapshot_digests):
            _deny("capability_intersection", "unsupported_capability", request=request, policy=self._policy, pointer="/requested_capabilities/task/repository_snapshot_digest", detail="repository exceeds ceiling")
        for index, dataset in enumerate(task.dataset_digests):
            if dataset not in set(ceiling.dataset_digests):
                _deny("capability_intersection", "unsupported_capability", request=request, policy=self._policy, pointer=f"/requested_capabilities/task/dataset_digests/{index}", detail="dataset exceeds ceiling")

        for index, slot in enumerate(vector.policy_slots):
            model = next((item.identity for item in self._registries.models if item.identity.model_digest == slot.model_digest), None)
            if model is None or _find_exact(ceiling.model_bindings, model) is None:
                _deny("capability_intersection", "unsupported_capability", request=request, policy=self._policy, pointer=f"/requested_capabilities/policy_slots/{index}/model_digest", detail="model exceeds ceiling")
        allowed_verifier = next((item for item in ceiling.verifier_grants if item.verifier_id == vector.verifier.verifier_id), None)
        if allowed_verifier is None:
            _deny("capability_intersection", "unsupported_capability", request=request, policy=self._policy, pointer="/requested_capabilities/verifier/verifier_id", detail="verifier exceeds ceiling")
        verifier_obj = _obj(vector.verifier)
        allowed_verifier_obj = _obj(allowed_verifier)
        verifier_difference = next((name for name in verifier_obj if verifier_obj[name] != allowed_verifier_obj[name]), None)
        if verifier_difference is not None:
            _deny("capability_intersection", "unsupported_capability", request=request, policy=self._policy, pointer=f"/requested_capabilities/verifier/{verifier_difference}", detail="verifier exceeds ceiling")
        verifier_record = next((item for item in self._registries.verifiers if item.grant.verifier_id == vector.verifier.verifier_id), None)
        if verifier_record is None or _isolation_rank(verifier_record.runtime_class) < _isolation_rank(self._policy.required_security.required_verifier_isolation_class):
            _deny("capability_intersection", "required_security_missing", request=request, policy=self._policy, pointer="/requested_capabilities/verifier/verifier_id", detail="verifier isolation is insufficient")

        for index, rule in enumerate(vector.mutable_pointers):
            allowed = next((item for item in ceiling.mutable_pointer_rules if item.pointer == rule.pointer), None)
            if allowed is None:
                _deny("capability_intersection", "unsupported_capability", request=request, policy=self._policy, pointer=f"/requested_capabilities/mutable_pointers/{index}/pointer", detail="mutable pointer exceeds ceiling")
            requested_rule = _obj(rule)
            allowed_rule = _obj(allowed)
            difference = next((name for name in requested_rule if requested_rule[name] != allowed_rule[name]), None)
            if difference is not None:
                _deny("capability_intersection", "unsupported_capability", request=request, policy=self._policy, pointer=f"/requested_capabilities/mutable_pointers/{index}/{difference}", detail="mutable pointer exceeds ceiling")

        maximum = ceiling.artifact_policy_maximum
        for index, role in enumerate(vector.artifacts.allowed_roles):
            if role not in set(maximum.allowed_roles):
                _deny("capability_intersection", "unsupported_capability", request=request, policy=self._policy, pointer=f"/requested_capabilities/artifacts/allowed_roles/{index}", detail="artifact role exceeds ceiling")
        for name in ("max_each_bytes", "max_total_bytes"):
            if getattr(vector.artifacts, name) > getattr(maximum, name):
                _deny("capability_intersection", "operator_ceiling_exceeded", request=request, policy=self._policy, pointer=f"/requested_capabilities/artifacts/{name}", detail="artifact bytes exceed ceiling")
        if _find_exact(ceiling.evidence_policies, vector.evidence) is None:
            _deny("capability_intersection", "required_security_missing", request=request, policy=self._policy, pointer="/requested_capabilities/evidence/revision_digest", detail="evidence policy is not allowed")
        evidence = _find_exact(self._registries.evidence_policies, vector.evidence, "policy")
        if evidence is None or not set(self._policy.required_security.required_evidence_roles).issubset(set(evidence.required_roles)):
            _deny("capability_intersection", "required_security_missing", request=request, policy=self._policy, pointer="/requested_capabilities/evidence/policy_id", detail="required evidence roles are missing")
        retention = next((item.grant for item in self._registries.retention_policies if _same(item.grant.policy, vector.retention)), None)
        allowed_retention = next((item for item in ceiling.retention_policies if _same(item.policy, vector.retention)), None)
        if retention is None or allowed_retention is None:
            _deny("capability_intersection", "retention_out_of_bounds", request=request, policy=self._policy, pointer="/requested_capabilities/retention/revision_digest", detail="retention policy is outside ceiling")
        if retention.minimum_seconds < self._policy.required_security.minimum_retention_seconds or retention.minimum_seconds < allowed_retention.minimum_seconds or retention.maximum_seconds > allowed_retention.maximum_seconds:
            _deny("capability_intersection", "retention_out_of_bounds", request=request, policy=self._policy, pointer="/requested_capabilities/retention/revision_digest", detail="retention bounds are invalid")

    def _verify_pins(self, request: c.AdmissionRequest, view: CompilerSemanticView) -> None:
        vector = request.requested_capabilities
        if vector.runner.runtime_abi != request.compiled.compiler.runtime_abi:
            _deny("identity_pinning", "unpinned_identity", request=request, policy=self._policy, pointer="/requested_capabilities/runner/runtime_abi", detail="runner/compiler ABI mismatch")
        if _DIGEST_RE.fullmatch(vector.sandbox.image_digest) is None:
            _deny("identity_pinning", "mutable_identity", request=request, policy=self._policy, pointer="/requested_capabilities/sandbox/image_digest", detail="sandbox image is mutable")
        if not all(_DIGEST_RE.fullmatch(pin.content_digest) for pin in _pins_for(request, self._policy)):
            _deny("identity_pinning", "unpinned_identity", request=request, policy=self._policy, pointer="/requested_capabilities", detail="identity digest is not pinned")
        if vector.task.repository_snapshot_digest is not None:
            repository = next(
                (record for record in self._registries.repository_bindings if record.repository_snapshot_digest == vector.task.repository_snapshot_digest),
                None,
            )
            image = next(
                (record for record in self._registries.images if record.image_digest == vector.sandbox.image_digest),
                None,
            )
            if (
                repository is None
                or image is None
                or repository.image_digest != vector.sandbox.image_digest
                or repository.binding_digest not in set(image.repository_binding_digests)
            ):
                _deny("identity_pinning", "repository_image_mismatch", request=request, policy=self._policy, pointer="/requested_capabilities/sandbox/image_digest", detail="repository-image cross-binding mismatch")
        task_record = next(
            (
                record for record in self._registries.task_datasets
                if record.task_contract_digest == vector.task.task_contract_digest
                and record.task_binding_digest == vector.task.task_binding_digest
            ),
            None,
        )
        if task_record is None or not set(vector.task.dataset_digests).issubset(set(task_record.dataset_digests)):
            _deny("identity_pinning", "registry_binding_mismatch", request=request, policy=self._policy, pointer="/requested_capabilities/task/dataset_digests/0", detail="task-dataset cross-binding mismatch")
        if not _same(vector.task.input_artifact_digests, task_record.input_artifact_digests):
            _deny("identity_pinning", "registry_binding_mismatch", request=request, policy=self._policy, pointer="/requested_capabilities/task/input_artifact_digests", detail="task input artifacts do not match the task registry")
        route_by_id = {route.route_id: route for route in vector.routes}
        secret_by_id = {secret.handle_id: secret for secret in vector.secret_handles}
        for index, route in enumerate(vector.routes):
            secret_record = next(
                (record for record in self._registries.secret_handles if record.grant.handle_id == route.credential_handle_id),
                None,
            )
            if secret_record is None or route.route_id not in set(secret_record.route_ids):
                _deny("identity_pinning", "registry_binding_mismatch", request=request, policy=self._policy, pointer=f"/requested_capabilities/routes/{index}/credential_handle_id", detail="route-secret cross-binding mismatch")
        for index, slot in enumerate(vector.policy_slots):
            route = route_by_id.get(slot.route_id)
            model_record = next(
                (record.identity for record in self._registries.models if record.identity.model_digest == slot.model_digest),
                None,
            )
            if (
                route is None
                or route.credential_handle_id != slot.secret_handle_id
                or slot.secret_handle_id not in secret_by_id
                or model_record is None
                or model_record.tokenizer_digest != slot.tokenizer_digest
                or model_record.checkpoint_digest != slot.checkpoint_digest
            ):
                _deny("identity_pinning", "model_identity_mismatch", request=request, policy=self._policy, pointer=f"/requested_capabilities/policy_slots/{index}/checkpoint_digest", detail="policy slot model cross-binding mismatch")
        if request.policy_binding_ref.route_id not in {slot.route_id for slot in vector.policy_slots}:
            _deny("identity_pinning", "model_identity_mismatch", request=request, policy=self._policy, pointer="/policy_binding_ref/route_id", detail="policy binding is not a policy slot route")
        verifier_record = next(
            (record for record in self._registries.verifiers if record.grant.verifier_id == vector.verifier.verifier_id),
            None,
        )
        verifier_image = next(
            (record for record in self._registries.images if record.image_digest == vector.verifier.image_digest),
            None,
        )
        verifier_runtime = None if verifier_record is None else next(
            (
                record for record in self._registries.sandbox_runtimes
                if record.binding.runtime_id == verifier_record.runtime_id
            ),
            None,
        )
        if (
            verifier_record is None
            or not _same(verifier_record.grant, vector.verifier)
            or verifier_image is None
            or verifier_image.runtime_id != verifier_record.runtime_id
            or verifier_runtime is None
            or verifier_runtime.binding.image_digest != vector.verifier.image_digest
            or verifier_runtime.binding.runtime_class != verifier_record.runtime_class
            or verifier_runtime.binding.security_policy_digest != verifier_record.security_policy_digest
            or verifier_runtime.binding.network_policy_digest != vector.verifier.network_policy_digest
            or _isolation_rank(verifier_record.runtime_class)
            < _isolation_rank(self._policy.required_security.required_verifier_isolation_class)
        ):
            _deny("identity_pinning", "verifier_identity_mismatch", request=request, policy=self._policy, pointer="/requested_capabilities/verifier/image_digest", detail="verifier image and runtime cross-binding mismatch")
        authority = view.roles["authority_disposition"]
        for field, code in (
            ("raw_url", "raw_url_forbidden"),
            ("raw_secret", "raw_secret_forbidden"),
            ("environment", "environment_authority_forbidden"),
            ("shell", "arbitrary_shell_forbidden"),
        ):
            if authority.get(field) not in (None, False, "", [], ()):
                _deny("identity_pinning", code, request=request, policy=self._policy, pointer=f"/compiler_view/authority_disposition/{field}", detail=f"{field} authority is forbidden")

    def _publish_receipt(self, request: c.AdmissionRequest, now: datetime) -> c.AdmissionReceiptRef:
        policy = self._policy
        request_not_before = _parse_timestamp(request.validity.not_before)
        request_expires = _parse_timestamp(request.validity.expires_at)
        if (
            _parse_timestamp(request.validity.issued_at) < _parse_timestamp(policy.validity.issued_at)
            or request_not_before < _parse_timestamp(policy.validity.not_before)
            or request_expires > _parse_timestamp(policy.validity.expires_at)
            or (request_expires - request_not_before).total_seconds() > policy.receipt_ttl_seconds
        ):
            _deny("identity_pinning", "unpinned_identity", request=request, policy=policy, pointer="/validity", detail="receipt validity exceeds policy")
        current = self._load_revocation(policy.revocation, request=request)
        receipt_fields = {
            "schema_version": c.ADMISSION_RECEIPT_SCHEMA_VERSION,
            "subject": request.subject,
            "admission_request_digest": _digest(request),
            "behavior_source": request.behavior_source,
            "compiled": request.compiled,
            "admission_policy_id": policy.policy_id,
            "admission_policy_revision": policy.revision,
            "admission_policy_digest": _policy_digest(policy),
            "operator_ceiling_digest": _ceiling_digest(policy),
            "registry_snapshot_digest": self._registries.digests.snapshot_digest,
            "requested_capability_digest": request.requested_capability_digest,
            "effective_capabilities": request.requested_capabilities,
            "effective_capability_digest": _digest(request.requested_capabilities),
            "capability_deltas": _capability_deltas(request.requested_capabilities, policy.ceiling),
            "pins": _pins_for(request, policy),
            "mutable_pointer_policy_digest": _digest(request.requested_capabilities.mutable_pointers),
            "policy_binding_ref": request.policy_binding_ref,
            "task_binding_digest": request.task_binding_digest,
            "decision": "admitted",
            "reason_codes": (),
            "validity": request.validity,
            "revocation": current,
            "parent_receipt_digest": request.parent_receipt_digest,
            "overlay_chain_digest": request.overlay_chain_digest,
        }
        unsigned_obj = _obj(receipt_fields)
        unsigned_obj["issuance_attestation"] = {
            "key_id": self._authenticator_key_id,
            "algorithm": self._authenticator_algorithm,
        }
        unsigned_payload = canonical_json_bytes(unsigned_obj)
        signed_payload_digest = _bytes_digest(unsigned_payload)
        try:
            if (
                self._authenticator.key_id != self._authenticator_key_id
                or self._authenticator.algorithm != self._authenticator_algorithm
            ):
                raise ValueError("authenticator identity changed")
            signature_bytes = self._authenticator.sign(unsigned_payload)
            if type(signature_bytes) is not bytes or not signature_bytes:
                raise TypeError("invalid signature bytes")
            signature = base64.urlsafe_b64encode(signature_bytes).rstrip(b"=").decode("ascii")
            receipt = c.AdmissionReceipt(
                **receipt_fields,
                issuance_attestation=c.IssuanceAttestation(
                    key_id=self._authenticator_key_id,
                    algorithm=self._authenticator_algorithm,
                    signed_payload_digest=signed_payload_digest,
                    signature=signature,
                ),
            )
        except BaseException:
            _deny(
                "receipt_publication", "receipt_store_unavailable", request=request,
                policy=policy, pointer="/issuance_attestation",
                detail="receipt signing failed",
            )
        if (
            receipt.unsigned_canonical_bytes() != unsigned_payload
            or receipt.issuance_attestation.signed_payload_digest != signed_payload_digest
        ):
            _deny(
                "receipt_publication", "receipt_readback_mismatch", request=request,
                policy=policy, pointer="/issuance_attestation/signed_payload_digest",
                detail="receipt signing projection mismatch",
            )
        payload = receipt.canonical_bytes()
        digest = _bytes_digest(payload)
        if len(payload) > _MAX_RECEIPT_BYTES:
            _deny(
                "receipt_publication", "receipt_store_conflict", request=request, policy=policy,
                artifact_kind=c.ArtifactKind.ADMISSION_RECEIPT.value, artifact_digest=digest,
                detail="receipt exceeds bounded publication size",
            )
        if receipt.canonical_digest() != digest:
            _deny("receipt_publication", "receipt_readback_mismatch", request=request, policy=policy, artifact_kind=c.ArtifactKind.ADMISSION_RECEIPT.value, artifact_digest=digest, detail="receipt digest equation failed")
        try:
            raw_ref = self._store.publish(kind=c.ArtifactKind.ADMISSION_RECEIPT, canonical_bytes=payload)
            ref = _revalidate_model(c.ArtifactRef, raw_ref)
        except BaseException:
            _deny("receipt_publication", "receipt_store_unavailable", request=request, policy=policy, artifact_kind=c.ArtifactKind.ADMISSION_RECEIPT.value, artifact_digest=digest, detail="receipt store unavailable", retry="same_input_once")
        if (
            ref.artifact_id != digest
            or ref.sha256 != digest
            or ref.size_bytes != len(payload)
            or ref.media_type != "application/vnd.breadboard.admission-receipt+json;version=1"
        ):
            _deny("receipt_publication", "receipt_store_conflict", request=request, policy=policy, artifact_kind=c.ArtifactKind.ADMISSION_RECEIPT.value, artifact_digest=digest, detail="receipt store returned conflicting reference")
        try:
            readback = self._store.load(digest, kind=c.ArtifactKind.ADMISSION_RECEIPT, max_bytes=_MAX_RECEIPT_BYTES)
        except BaseException:
            _deny("receipt_publication", "receipt_store_unavailable", request=request, policy=policy, artifact_kind=c.ArtifactKind.ADMISSION_RECEIPT.value, artifact_digest=digest, detail="receipt readback unavailable", retry="same_input_once")
        if type(readback) is not bytes or readback != payload or _bytes_digest(readback) != digest:
            _deny("receipt_publication", "receipt_readback_mismatch", request=request, policy=policy, artifact_kind=c.ArtifactKind.ADMISSION_RECEIPT.value, artifact_digest=digest, detail="receipt readback mismatch")
        return c.AdmissionReceiptRef(digest=digest, ref=ref)

    def _check_validity(
        self,
        validity: c.ValidityWindow,
        now: datetime,
        *,
        stage: str,
        not_yet_code: str,
        expired_code: str,
        request: c.AdmissionRequest | None = None,
        subject: c.AuthenticatedSubject | None = None,
        artifact_digest: str | None = None,
        pointer: str,
    ) -> None:
        current = _utc_now(now)
        if current < _parse_timestamp(validity.not_before):
            denial_pointer = f"{pointer}/not_before" if pointer == "/validity" else pointer
            _deny(stage, not_yet_code, request=request, subject=subject, policy=self._policy, artifact_digest=artifact_digest, pointer=denial_pointer, detail="not yet valid")
        if current >= _parse_timestamp(validity.expires_at):
            denial_pointer = f"{pointer}/expires_at" if pointer == "/validity" else pointer
            _deny(stage, expired_code, request=request, subject=subject, policy=self._policy, artifact_digest=artifact_digest, pointer=denial_pointer, detail="expired")

    def _load_revocation(
        self,
        expected: c.RevocationBinding,
        *,
        request: c.AdmissionRequest | None = None,
        subject: c.AuthenticatedSubject | None = None,
        artifact_digest: str | None = None,
        pointer: str = "/revocation",
        invalid_code: str | None = None,
        cache: dict[str, c.RevocationBinding] | None = None,
    ) -> c.RevocationBinding:
        recheck = subject is not None
        stage = "receipt_recheck" if recheck else "identity_pinning"
        default_code = "receipt_revoked" if recheck else "unpinned_identity"
        try:
            current = None if cache is None else cache.get(expected.scope_digest)
            if current is None:
                current = _revalidate_model(
                    c.RevocationBinding,
                    self._revocations.load(expected.scope_digest),
                )
                if cache is not None:
                    cache[expected.scope_digest] = current
        except BaseException:
            _deny(
                stage, invalid_code or default_code, request=request, subject=subject,
                policy=self._policy, artifact_digest=artifact_digest, pointer=pointer,
                detail="revocation snapshot unavailable",
            )
        if current.scope_digest != expected.scope_digest:
            _deny(
                stage, invalid_code or default_code, request=request, subject=subject,
                policy=self._policy, artifact_digest=artifact_digest,
                pointer=pointer if invalid_code is not None else f"{pointer}/scope_digest",
                detail="revocation scope mismatch",
            )
        if current.epoch < expected.epoch:
            _deny(
                stage, invalid_code or ("receipt_epoch_rollback" if recheck else "unpinned_identity"),
                request=request, subject=subject, policy=self._policy,
                artifact_digest=artifact_digest,
                pointer=pointer if invalid_code is not None else f"{pointer}/epoch",
                detail="revocation epoch rollback",
            )
        if current.epoch > expected.epoch:
            _deny(
                stage, invalid_code or default_code, request=request, subject=subject,
                policy=self._policy, artifact_digest=artifact_digest,
                pointer=pointer if invalid_code is not None else f"{pointer}/epoch",
                detail="receipt revoked",
            )
        if current.state_digest != expected.state_digest:
            _deny(
                stage, invalid_code or default_code, request=request, subject=subject,
                policy=self._policy, artifact_digest=artifact_digest,
                pointer=pointer if invalid_code is not None else f"{pointer}/state_digest",
                detail="revocation state mismatch",
            )
        return current


def _pins_for_receipt(receipt: c.AdmissionReceipt) -> tuple[c.ArtifactIdentity, ...]:
    request_like = type("ReceiptProjection", (), {})()
    request_like.compiled = receipt.compiled
    request_like.requested_capabilities = receipt.effective_capabilities
    request_like.policy_binding_ref = receipt.policy_binding_ref
    return _pins_for(request_like, None)  # type: ignore[arg-type]


def _capability_deltas(vector: c.CapabilityVector, ceiling: c.OperatorCeiling) -> tuple[c.CapabilityDelta, ...]:
    """Record only authority dimensions strictly below the operator ceiling."""

    ceiling_by_dimension: dict[str, Any] = {
        "runner": ceiling.runner_bindings,
        "tools": ceiling.tool_grants,
        "setup_plans": ceiling.setup_grants,
        "routes": ceiling.route_grants,
        "secret_handles": ceiling.secret_handle_grants,
        "sandbox": {
            "bindings": ceiling.sandbox_bindings,
            "allowed_egress_route_ids": ceiling.allowed_egress_route_ids,
            "mount_grants": ceiling.mount_grants,
        },
        "resources": ceiling.resource_maxima,
        "limits": ceiling.execution_maxima,
        "task": {
            "repository_snapshot_digests": ceiling.repository_snapshot_digests,
            "task_contract_digests": ceiling.task_contract_digests,
            "task_binding_digests": ceiling.task_binding_digests,
            "dataset_digests": ceiling.dataset_digests,
        },
        "policy_slots": {
            "policy_slot_grants": ceiling.policy_slot_grants,
            "model_bindings": ceiling.model_bindings,
        },
        "verifier": ceiling.verifier_grants,
        "mutable_pointers": ceiling.mutable_pointer_rules,
        "artifacts": ceiling.artifact_policy_maximum,
        "evidence": ceiling.evidence_policies,
        "retention": ceiling.retention_policies,
    }

    sandbox_binding = c.SandboxBinding(
        runtime_id=vector.sandbox.runtime_id,
        runtime_class=vector.sandbox.runtime_class,
        driver_implementation_digest=vector.sandbox.driver_implementation_digest,
        runtime_binary_digest=vector.sandbox.runtime_binary_digest,
        security_policy_digest=vector.sandbox.security_policy_digest,
        image_digest=vector.sandbox.image_digest,
        network_policy_digest=vector.sandbox.network_policy_digest,
    )
    repository_set = (
        set() if vector.task.repository_snapshot_digest is None
        else {vector.task.repository_snapshot_digest}
    )
    task_matches_ceiling = (
        set(ceiling.task_contract_digests) == {vector.task.task_contract_digest}
        and set(ceiling.task_binding_digests) == {vector.task.task_binding_digest}
        and set(ceiling.repository_snapshot_digests) == repository_set
        and set(ceiling.dataset_digests) == set(vector.task.dataset_digests)
    )
    policy_models = {
        (item.model_digest, item.tokenizer_digest, item.checkpoint_digest)
        for item in ceiling.model_bindings
    }
    requested_models = {
        (item.model_digest, item.tokenizer_digest, item.checkpoint_digest)
        for item in vector.policy_slots
    }
    below: dict[str, bool] = {
        # Categorical identities have no lesser valid identity once admitted.
        "runner": False,
        "tools": not _same(vector.tools, ceiling.tool_grants),
        "setup_plans": not _same(vector.setup_plans, ceiling.setup_grants),
        "routes": not _same(vector.routes, ceiling.route_grants),
        "secret_handles": not _same(vector.secret_handles, ceiling.secret_handle_grants),
        "sandbox": not (
            len(ceiling.sandbox_bindings) == 1
            and _same(sandbox_binding, ceiling.sandbox_bindings[0])
            and _same(vector.sandbox.egress_route_ids, ceiling.allowed_egress_route_ids)
            and _same(vector.sandbox.mounts, ceiling.mount_grants)
        ),
        "resources": not _same(vector.resources, ceiling.resource_maxima),
        "limits": not _same(vector.limits, ceiling.execution_maxima),
        "task": not task_matches_ceiling,
        "policy_slots": not (
            _same(vector.policy_slots, ceiling.policy_slot_grants)
            and requested_models == policy_models
        ),
        "verifier": False,
        "mutable_pointers": not _same(vector.mutable_pointers, ceiling.mutable_pointer_rules),
        "artifacts": not _same(vector.artifacts, ceiling.artifact_policy_maximum),
        "evidence": False,
        "retention": False,
    }
    result = tuple(
        c.CapabilityDelta(
            dimension=dimension,
            ceiling_digest=_digest(ceiling_by_dimension[dimension.value]),
            effective_digest=_digest(getattr(vector, dimension.value)),
            reason_code="below_operator_ceiling",
        )
        for dimension in c.CapabilityDimension
        if below[dimension.value]
    )
    return tuple(sorted(result, key=lambda item: item.dimension.value))


__all__ = [
    "Clock",
    "CompilerSemanticView",
    "ConfigRuntime",
    "ConfigRuntimeStore",
    "PolicyCapabilityRegistry",
    "ReceiptAuthenticator",
    "RevocationStore",
    "VerifiedCompilerAdapter",
]
