from __future__ import annotations

import hashlib
import json
import math
import re
import unicodedata
from collections.abc import Iterator, Mapping, Sequence
from enum import Enum
from typing import Annotated, Any, Literal

from pydantic import (
    AfterValidator,
    BaseModel,
    ConfigDict,
    Field,
    StrictBool,
    StringConstraints,
    field_serializer,
    field_validator,
    model_serializer,
    model_validator,
)

__all__: list[str] = []
MAX_UINT53 = 9_007_199_254_740_991
MAX_UINT64 = 18_446_744_073_709_551_615

ARTIFACT_REF_SCHEMA_VERSION = "bb.rl.artifact-ref.v1"
REGISTRY_SNAPSHOT_SCHEMA_VERSION = "bb.rl.registry-snapshot.v1"
ADMISSION_POLICY_SCHEMA_VERSION = "bb.rl.admission-policy.v1"
ADMISSION_REQUEST_SCHEMA_VERSION = "bb.rl.admission-request.v1"
ADMISSION_RECEIPT_SCHEMA_VERSION = "bb.rl.admission-receipt.v1"
CURRENTNESS_TOKEN_SCHEMA_VERSION = "bb.rl.admission-currentness.v1"
VERIFIED_ADMISSION_SCHEMA_VERSION = "bb.rl.verified-admission.v1"
CONFIG_RUNTIME_DENIAL_SCHEMA_VERSION = "bb.rl.config-runtime-denial.v1"
POLICY_CAPABILITY_OBSERVATION_SCHEMA_VERSION = "bb.rl.policy-capability-observation.v1"
ADMITTED_SET_SCHEMA_VERSION = "bb.rl.admitted-set.v1"
DIRECT_SELECTOR_SCHEMA_VERSION = "bb.rl.direct-selector.v1"
CONFIG_SET_SCHEMA_VERSION = "bb.rl.config-set.v1"
TASK_ELIGIBILITY_SCHEMA_VERSION = "bb.rl.task-eligibility.v1"
SELECTION_RECORD_SCHEMA_VERSION = "bb.rl.selection-record.v1"
SELECTION_BINDING_SCHEMA_VERSION = "bb.rl.selection-binding.v1"
SELECTION_COMMIT_SCHEMA_VERSION = "bb.rl.selection-commit.v1"
MUTATION_OVERLAY_SCHEMA_VERSION = "bb.rl.mutation-overlay.v1"
EFFECTIVE_EXECUTION_PLAN_SCHEMA_VERSION = "bb.rl.effective-execution-plan.v1"
RESOLVE_EPISODE_REQUEST_SCHEMA_VERSION = "bb.rl.config-resolution-request.v1"
RESOLVED_EPISODE_PLAN_SCHEMA_VERSION = "bb.rl.resolved-episode-plan.v1"
COMPILED_CONFIG_SEMANTIC_SCHEMA_ID = "bb.compiled-config-semantic.v1"
OVERLAY_CHAIN_SCHEMA_VERSION = "bb.rl.overlay-chain.v1"

_DIGEST_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
_UTC_SECOND_RE = re.compile(r"^(?!0000)\d{4}-(?:0[1-9]|1[0-2])-(?:0[1-9]|[12]\d|3[01])T(?:[01]\d|2[0-3]):[0-5]\d:[0-5]\dZ$")
_MEDIA_TYPE_RE = re.compile(
    r"^application/vnd\.breadboard\.[a-z0-9](?:[a-z0-9.-]*[a-z0-9])?\+json;version=1$"
)
_CANDIDATE_ID_RE = re.compile(r"^[a-z0-9](?:[a-z0-9._-]{0,62}[a-z0-9])?$")
_LOWER_HEX_64_RE = re.compile(r"^[0-9a-f]{64}$")
_PREIMAGE_HEX_RE = re.compile(r"^[0-9a-f]{598}$")

_IMMUTABLE_IMAGE_DIGEST_RE = re.compile(
    r"^(?:[a-zA-Z0-9._/:+-]+@)?sha256:[0-9a-f]{64}$"
)

def _validate_text(value: str, *, field_name: str, max_bytes: int = 256) -> str:
    if type(value) is not str or not value:
        raise ValueError(f"{field_name} must be a non-empty string")
    if value != unicodedata.normalize("NFC", value):
        raise ValueError(f"{field_name} must be NFC-normalized")
    if value != value.strip():
        raise ValueError(f"{field_name} must not have surrounding whitespace")
    if any(ord(character) < 0x20 or ord(character) == 0x7F for character in value):
        raise ValueError(f"{field_name} must not contain control characters")
    try:
        encoded = value.encode("utf-8", errors="strict")
    except UnicodeEncodeError as exc:
        raise ValueError(f"{field_name} must be valid Unicode") from exc
    if len(encoded) > max_bytes:
        raise ValueError(f"{field_name} exceeds {max_bytes} UTF-8 bytes")
    return value


def _identifier(value: str) -> str:
    value = _validate_text(value, field_name="identifier")
    if any(character.isspace() for character in value):
        raise ValueError("identifier must not contain whitespace")
    if "/" in value or "\\" in value or "://" in value or "${" in value:
        raise ValueError("identifier must not contain raw authority or path syntax")
    return value


def _digest(value: str) -> str:
    if type(value) is not str or _DIGEST_RE.fullmatch(value) is None:
        raise ValueError("digest must be a full lowercase sha256:<64 hex> value")
    return value


def _immutable_image_digest(value: str) -> str:
    if type(value) is not str or _IMMUTABLE_IMAGE_DIGEST_RE.fullmatch(value) is None:
        raise ValueError("image identity must be an immutable sha256 digest reference")
    return value


def _candidate_id(value: str) -> str:
    if type(value) is not str or _CANDIDATE_ID_RE.fullmatch(value) is None:
        raise ValueError(
            "candidate_id must match [a-z0-9](?:[a-z0-9._-]{0,62}[a-z0-9])?"
        )
    return value


def _timestamp(value: str) -> str:
    if type(value) is not str or _UTC_SECOND_RE.fullmatch(value) is None:
        raise ValueError("timestamp must be canonical RFC3339 UTC at whole-second precision")
    from datetime import datetime

    try:
        datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ")
    except ValueError as exc:
        raise ValueError("timestamp is not a real UTC calendar instant") from exc
    return value


def _logical_path(value: str) -> str:
    value = _validate_text(value, field_name="logical path", max_bytes=1024)
    if value.startswith("/") or "\\" in value:
        raise ValueError("logical path must be POSIX-relative")
    parts = value.split("/")
    if any(part in {"", ".", ".."} for part in parts):
        raise ValueError("logical path contains an empty, dot, or parent component")
    return value


def _json_pointer(value: str) -> str:
    value = _validate_text(value, field_name="JSON pointer", max_bytes=1024)
    if not value.startswith("/"):
        raise ValueError("JSON pointer must be non-root and begin with '/'")
    for token in value[1:].split("/"):
        if not token:
            raise ValueError("JSON pointer tokens must not be empty")
        index = 0
        decoded: list[str] = []
        while index < len(token):
            character = token[index]
            if character != "~":
                decoded.append(character)
                index += 1
                continue
            if index + 1 >= len(token) or token[index + 1] not in {"0", "1"}:
                raise ValueError("JSON pointer contains a malformed escape")
            decoded.append("~" if token[index + 1] == "0" else "/")
            index += 2
        decoded_token = "".join(decoded)
        canonical = decoded_token.replace("~", "~0").replace("/", "~1")
        if canonical != token or decoded_token != unicodedata.normalize("NFC", decoded_token):
            raise ValueError("JSON pointer is not canonically encoded")
    return value


def _route_authority(value: str) -> str:
    value = _validate_text(value, field_name="route authority", max_bytes=253)
    if any(character.isspace() for character in value):
        raise ValueError("route authority must not contain whitespace")
    if any(token in value for token in ("/", "\\", "@", "?", "#", "://")):
        raise ValueError("route authority must contain only host and optional port authority")
    try:
        value.encode("ascii", errors="strict")
    except UnicodeEncodeError as exc:
        raise ValueError("route authority must be canonical ASCII") from exc
    if value != value.lower():
        raise ValueError("route authority must be lowercase canonical authority")

    host = value
    port: str | None = None
    if value.startswith("["):
        closing = value.find("]")
        if closing < 0:
            raise ValueError("IPv6 route authority must be bracketed")
        host = value[1:closing]
        suffix = value[closing + 1 :]
        if suffix:
            if not suffix.startswith(":"):
                raise ValueError("route authority has malformed IPv6 port syntax")
            port = suffix[1:]
        import ipaddress

        try:
            ipaddress.IPv6Address(host)
        except ValueError as exc:
            raise ValueError("route authority has malformed IPv6 syntax") from exc
    else:
        if value.count(":") > 1:
            raise ValueError("IPv6 route authority must be bracketed")
        if ":" in value:
            host, port = value.rsplit(":", 1)
        labels = host.split(".")
        if (
            not host
            or any(
                not label
                or len(label) > 63
                or re.fullmatch(r"[a-z0-9](?:[a-z0-9-]*[a-z0-9])?", label) is None
                for label in labels
            )
        ):
            raise ValueError("route authority host is not canonical")
    if port is not None and (
        not port.isascii()
        or not port.isdigit()
        or str(int(port)) != port
        or not 1 <= int(port) <= 65535
    ):
        raise ValueError("route authority port must be canonical and within 1..65535")
    return value


def _route_path(value: str) -> str:
    value = _validate_text(value, field_name="route path", max_bytes=1024)
    if not value.startswith("/") or value.startswith("//") or "\\" in value:
        raise ValueError("route path must be a single-rooted absolute POSIX path")
    if "?" in value or "#" in value or any(part in {".", ".."} for part in value.split("/")):
        raise ValueError("route path must not contain query, fragment, or dot components")
    return value


def _base64url(value: str) -> str:
    value = _validate_text(value, field_name="base64url value", max_bytes=16384)
    if re.fullmatch(r"[A-Za-z0-9_-]+", value) is None:
        raise ValueError("base64url value must be unpadded canonical base64url")
    import base64

    padding = "=" * (-len(value) % 4)
    try:
        decoded = base64.urlsafe_b64decode(value + padding)
    except ValueError as exc:
        raise ValueError("base64url value is malformed") from exc
    if base64.urlsafe_b64encode(decoded).rstrip(b"=").decode("ascii") != value:
        raise ValueError("base64url value is not canonical")
    return value


Identifier = Annotated[
    str,
    StringConstraints(strict=True),
    AfterValidator(_identifier),
]
Digest = Annotated[
    str,
    StringConstraints(strict=True),
    AfterValidator(_digest),
]
UtcSecond = Annotated[
    str,
    StringConstraints(strict=True),
    AfterValidator(_timestamp),
]
LogicalPath = Annotated[
    str,
    StringConstraints(strict=True),
    AfterValidator(_logical_path),
]
JsonPointer = Annotated[
    str,
    StringConstraints(strict=True),
    AfterValidator(_json_pointer),
]
RouteAuthority = Annotated[
    str,
    StringConstraints(strict=True),
    AfterValidator(_route_authority),
]
RoutePath = Annotated[
    str,
    StringConstraints(strict=True),
    AfterValidator(_route_path),
]
Base64Url = Annotated[
    str,
    StringConstraints(strict=True),
    AfterValidator(_base64url),
]
PositiveUInt53 = Annotated[int, Field(strict=True, ge=1, le=MAX_UINT53)]
UInt53 = Annotated[int, Field(strict=True, ge=0, le=MAX_UINT53)]
UInt64 = Annotated[int, Field(strict=True, ge=0, le=MAX_UINT64)]
CandidateId = Annotated[
    str,
    StringConstraints(strict=True),
    AfterValidator(_candidate_id),
]
ImmutableImageDigest = Annotated[
    str,
    StringConstraints(strict=True),
    AfterValidator(_immutable_image_digest),
]


def _enum_value(value: Any) -> Any:
    return value.value if isinstance(value, Enum) else value


def _canonical_projection(value: Any) -> Any:
    if isinstance(value, BaseModel):
        return _canonical_projection(value.model_dump(mode="python"))
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, Mapping):
        return {
            key: _canonical_projection(item)
            for key, item in value.items()
        }
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return [_canonical_projection(item) for item in value]
    return value


def _canonical_bytes(value: Any) -> bytes:
    from breadboard_engine.compilation.contracts import canonical_json_bytes

    return canonical_json_bytes(_canonical_projection(value))


def _canonical_digest(value: Any) -> str:
    from breadboard_engine.compilation.contracts import canonical_sha256

    return canonical_sha256(_canonical_projection(value))


def derive_overlay_chain_digest(
    *, parent_chain_digest: Digest | None, overlay_digest: Digest
) -> str:
    """Derive the cumulative ordered overlay-chain identity without a receipt cycle."""
    if parent_chain_digest is not None:
        _digest(parent_chain_digest)
    _digest(overlay_digest)
    return _canonical_digest(
        {
            "schema_version": OVERLAY_CHAIN_SCHEMA_VERSION,
            "parent_chain_digest": parent_chain_digest,
            "overlay_digest": overlay_digest,
        }
    )


def _require_sorted_unique(
    values: tuple[Any, ...], *, key: Any, field_name: str, nonempty: bool = False
) -> None:
    if type(values) is not tuple:
        raise ValueError(f"{field_name} must be a tuple")
    keys = tuple(key(value) for value in values)
    if nonempty and not keys:
        raise ValueError(f"{field_name} must not be empty")
    if keys != tuple(sorted(keys)):
        raise ValueError(f"{field_name} must be sorted")
    if len(keys) != len(set(keys)):
        raise ValueError(f"{field_name} must not contain duplicates")


class _FrozenDict(tuple[tuple[str, Any], ...], Mapping[str, Any]):
    """Immutable JSON object whose tuple contents are ordered key/value pairs."""

    __slots__ = ()

    def __new__(
        cls, pairs: tuple[tuple[str, Any], ...]
    ) -> _FrozenDict:
        if type(pairs) is not tuple or any(type(pair) is not tuple for pair in pairs):
            raise TypeError("frozen JSON object storage must be tuple pairs")
        return tuple.__new__(cls, pairs)

    def __getitem__(self, key: str) -> Any:
        for candidate, value in tuple.__iter__(self):
            if candidate == key:
                return value
        raise KeyError(key)

    def __iter__(self) -> Iterator[str]:
        return (key for key, _value in tuple.__iter__(self))

    def __eq__(self, other: object) -> bool:
        if isinstance(other, Mapping):
            return dict(self.items()) == dict(other.items())
        return NotImplemented

    __hash__ = None

    def __copy__(self) -> _FrozenDict:
        return self

    def __deepcopy__(self, _memo: dict[int, Any]) -> _FrozenDict:
        return self

    def __reduce__(self) -> tuple[Any, tuple[tuple[tuple[str, Any], ...]]]:
        return type(self), (tuple(tuple.__iter__(self)),)


class _FrozenList(tuple[Any, ...]):
    """Immutable JSON array with tuple storage and Sequence behavior."""

    __slots__ = ()

    def __new__(cls, values: Any = ()) -> _FrozenList:
        return tuple.__new__(cls, values)
    def __eq__(self, other: object) -> bool:
        if isinstance(other, Sequence) and not isinstance(other, (str, bytes, bytearray)):
            return tuple(self) == tuple(other)
        return NotImplemented

    __hash__ = None

    def __copy__(self) -> _FrozenList:
        return self

    def __deepcopy__(self, _memo: dict[int, Any]) -> _FrozenList:
        return self


def _freeze_json(value: Any, *, field_name: str = "canonical JSON") -> Any:
    if value is None or type(value) is str or type(value) is bool:
        if type(value) is str:
            _validate_text(value, field_name=field_name, max_bytes=1_048_576)
        return value
    if type(value) is int:
        if not -MAX_UINT53 <= value <= MAX_UINT53:
            raise ValueError(f"{field_name} integer is outside the JCS-safe domain")
        return value
    if type(value) is float:
        if not math.isfinite(value):
            raise ValueError(f"{field_name} must not contain NaN or infinity")
        return value
    if type(value) is _FrozenList:
        return _FrozenList(_freeze_json(item, field_name=field_name) for item in value)
    if type(value) is _FrozenDict:
        return _FrozenDict(
            tuple(
                (key, _freeze_json(item, field_name=field_name))
                for key, item in value.items()
            )
        )
    if type(value) is list:
        return _FrozenList(_freeze_json(item, field_name=field_name) for item in value)
    if type(value) is dict:
        frozen: list[tuple[str, Any]] = []
        for key, item in value.items():
            if type(key) is not str:
                raise ValueError(f"{field_name} object keys must be strings")
            _validate_text(key, field_name=f"{field_name} object key", max_bytes=1024)
            frozen.append((key, _freeze_json(item, field_name=field_name)))
        return _FrozenDict(tuple(frozen))
    raise ValueError(f"{field_name} contains a non-JSON value")
def _validate_compiled_semantic_text(value: str) -> str:
    if value != unicodedata.normalize("NFC", value):
        raise ValueError("effective semantics must be NFC-normalized")
    if any(
        (ord(character) < 0x20 and character not in "\t\n\r")
        or 0x7F <= ord(character) <= 0x9F
        for character in value
    ):
        raise ValueError("effective semantics must not contain unsafe control characters")
    try:
        encoded = value.encode("utf-8", errors="strict")
    except UnicodeEncodeError as exc:
        raise ValueError("effective semantics must be valid Unicode") from exc
    if len(encoded) > 1_048_576:
        raise ValueError("effective semantics exceeds 1048576 UTF-8 bytes")
    return value


def _freeze_compiled_semantics(value: Any) -> Any:
    if value is None or type(value) is bool:
        return value
    if type(value) is str:
        return _validate_compiled_semantic_text(value)
    if type(value) is int:
        if not -MAX_UINT53 <= value <= MAX_UINT53:
            raise ValueError("effective semantics integer is outside the JCS-safe domain")
        return value
    if type(value) is float:
        if not math.isfinite(value):
            raise ValueError("effective semantics must not contain NaN or infinity")
        return value
    if type(value) in {_FrozenList, list}:
        return _FrozenList(_freeze_compiled_semantics(item) for item in value)
    if type(value) in {_FrozenDict, dict}:
        frozen: list[tuple[str, Any]] = []
        for key, item in value.items():
            if type(key) is not str:
                raise ValueError("effective semantics object keys must be strings")
            _validate_text(
                key,
                field_name="effective semantics object key",
                max_bytes=1024,
            )
            frozen.append((key, _freeze_compiled_semantics(item)))
        return _FrozenDict(tuple(frozen))
    raise ValueError("effective semantics contains a non-JSON value")




def _require_tuple_input(value: Any, *, field_name: str) -> Any:
    if type(value) is not tuple:
        raise TypeError(f"{field_name} must be a tuple")
    return value


def _require_tuple_or_json_array(
    value: Any, *, field_name: str, mode: str
) -> Any:
    if mode == "json" and type(value) is list:
        return value
    return _require_tuple_input(value, field_name=field_name)


def _require_sorted_canonical(
    values: tuple[Any, ...], *, field_name: str, nonempty: bool = False
) -> None:
    _require_sorted_unique(
        values,
        key=lambda item: _canonical_bytes(item),
        field_name=field_name,
        nonempty=nonempty,
    )


class _ConfigRuntimeContract(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        strict=False,
        revalidate_instances="always",
    )

    def model_copy(self, *, update: dict[str, Any] | None = None, deep: bool = False) -> Any:
        if update:
            raise TypeError("validated config-runtime contracts cannot be updated by copy")
        return super().model_copy(deep=deep)

    def model_dump(self, *, mode: str = "python", **kwargs: Any) -> dict[str, Any]:
        if mode == "json":
            kwargs.pop("fallback", None)
            return _canonical_projection(super().model_dump(mode="python", **kwargs))
        return super().model_dump(mode=mode, **kwargs)

    def model_dump_json(self, *, indent: int | None = None, **kwargs: Any) -> str:
        payload = self.model_dump(mode="json", **kwargs)
        return json.dumps(
            payload,
            ensure_ascii=False,
            indent=indent,
            separators=None if indent is not None else (",", ":"),
        )

    @classmethod
    def from_dict(cls, value: Any) -> Any:
        if type(value) is not dict:
            raise TypeError(f"{cls.__name__} wire value must be an object")
        return cls.model_validate(value)

    def to_canonical_obj(self) -> dict[str, Any]:
        return _canonical_projection(self.model_dump(mode="python"))

    def canonical_bytes(self) -> bytes:
        return _canonical_bytes(self)

    def canonical_digest(self) -> str:
        return _canonical_digest(self)


class PinKind(str, Enum):
    BUNDLE = "bundle"
    CLOSURE = "closure"
    COMPILER_CODE = "compiler_code"
    COMPILED_MANIFEST = "compiled_manifest"
    COMPILED_SEMANTIC = "compiled_semantic"
    RUNNER_IMPLEMENTATION = "runner_implementation"
    TASK = "task"
    DATASET = "dataset"
    INPUT_ARTIFACT = "input_artifact"
    REPOSITORY_SNAPSHOT = "repository_snapshot"
    SANDBOX_IMAGE = "sandbox_image"
    MODEL = "model"
    TOKENIZER = "tokenizer"
    CHECKPOINT = "checkpoint"
    TOOL_IMPLEMENTATION = "tool_implementation"
    SETUP_IMPLEMENTATION = "setup_implementation"
    ROUTE_REVISION = "route_revision"
    SECRET_HANDLE_VERSION = "secret_handle_version"
    SANDBOX_DRIVER = "sandbox_driver"
    SANDBOX_RUNTIME = "sandbox_runtime"
    NETWORK_POLICY = "network_policy"
    MOUNT_PLAN = "mount_plan"
    VERIFIER_IMAGE = "verifier_image"
    VERIFIER_EXECUTABLE = "verifier_executable"
    VERIFIER_CODE = "verifier_code"
    VERIFIER_INPUT_SCHEMA = "verifier_input_schema"
    VERIFIER_RESULT_SCHEMA = "verifier_result_schema"
    EVIDENCE_POLICY = "evidence_policy"
    RETENTION_POLICY = "retention_policy"
    MUTABLE_POINTER_POLICY = "mutable_pointer_policy"
    POLICY_CAPABILITY_ATTESTATION = "policy_capability_attestation"


class RouteScheme(str, Enum):
    HTTPS = "https"


class RouteMethod(str, Enum):
    DELETE = "DELETE"
    GET = "GET"
    PATCH = "PATCH"
    POST = "POST"
    PUT = "PUT"


class DataClassification(str, Enum):
    PUBLIC = "public"
    INTERNAL = "internal"
    CONFIDENTIAL = "confidential"
    RESTRICTED = "restricted"


class RuntimeClass(str, Enum):
    TRUSTED_PROCESS = "trusted_process"
    HARDENED_DOCKER = "hardened_docker"
    HARDENED_GVISOR = "hardened_gvisor"


class MountAccess(str, Enum):
    READ_ONLY = "ro"
    READ_WRITE = "rw"


class AuthorityEffect(str, Enum):
    NONE = "none"
    REDUCE_ONLY = "reduce_only"


class MutableOperation(str, Enum):
    ADD = "add"
    REMOVE = "remove"
    REPLACE = "replace"


class ArtifactKind(str, Enum):
    ADMISSION_POLICY = "admission_policy"
    REGISTRY_SNAPSHOT = "registry_snapshot"
    ADMISSION_RECEIPT = "admission_receipt"
    CONFIG_RUNTIME_DENIAL = "config_runtime_denial"
    POLICY_CAPABILITY_OBSERVATION = "policy_capability_observation"
    COMPILED_MANIFEST = "compiled_manifest"
    ADMITTED_SET = "admitted_set"
    DIRECT_SELECTOR = "direct_selector"
    CONFIG_SET = "config_set"
    MUTATION_OVERLAY = "mutation_overlay"
    SELECTION_RECORD = "selection_record"
    SELECTION_BINDING = "selection_binding"
    EFFECTIVE_EXECUTION_PLAN = "effective_execution_plan"


class AuthenticatedSubject(_ConfigRuntimeContract):
    tenant_id: Identifier
    principal_id: Identifier
    authority_scope_digest: Digest


class ArtifactIdentity(_ConfigRuntimeContract):
    kind: PinKind
    logical_id: Identifier
    content_digest: Digest
    qualifier_digest: Digest | None = None


class CompilerIdentity(_ConfigRuntimeContract):
    compiler_id: Identifier
    semantic_version: Identifier
    code_digest: Digest
    source_schema_id: Identifier
    source_schema_digest: Digest
    manifest_schema_digest: Digest
    canonicalizer_id: Identifier
    runtime_abi: Identifier


class CompiledArtifactIdentity(_ConfigRuntimeContract):
    manifest_digest: Digest
    bundle_digest: Digest
    closure_digest: Digest
    compiler_input_digest: Digest
    semantic_digest: Digest
    compiler: CompilerIdentity
    provenance_digest: Digest
    diagnostics_digest: Digest


class ValidityWindow(_ConfigRuntimeContract):
    issued_at: UtcSecond
    not_before: UtcSecond
    expires_at: UtcSecond

    @model_validator(mode="after")
    def _ordered(self) -> ValidityWindow:
        if not self.issued_at <= self.not_before < self.expires_at:
            raise ValueError("validity requires issued_at <= not_before < expires_at")
        return self


class RevocationBinding(_ConfigRuntimeContract):
    scope_digest: Digest
    epoch: UInt64
    state_digest: Digest


class RunnerGrant(_ConfigRuntimeContract):
    adapter_id: Identifier
    runtime_abi: Identifier
    implementation_digest: Digest


class ToolGrant(_ConfigRuntimeContract):
    tool_id: Identifier
    implementation_digest: Digest
    capability_ids: tuple[Identifier, ...]

    @model_validator(mode="after")
    def _capabilities_sorted(self) -> ToolGrant:
        _require_sorted_unique(
            self.capability_ids, key=lambda value: value, field_name="capability_ids"
        )
        return self


class SetupGrant(_ConfigRuntimeContract):
    setup_id: Identifier
    implementation_digest: Digest
    plan_digest: Digest


class RouteGrant(_ConfigRuntimeContract):
    route_id: Identifier
    route_revision_digest: Digest
    protocol_abi: Identifier
    credential_handle_id: Identifier


class SecretHandleGrant(_ConfigRuntimeContract):
    handle_id: Identifier
    handle_version_digest: Digest
    scope_digest: Digest


class MountGrant(_ConfigRuntimeContract):
    source_artifact_digest: Digest
    target_logical_path: LogicalPath
    access: MountAccess
    max_bytes: PositiveUInt53


class SandboxGrant(_ConfigRuntimeContract):
    runtime_id: Identifier
    runtime_class: RuntimeClass
    driver_implementation_digest: Digest
    runtime_binary_digest: Digest
    security_policy_digest: Digest
    image_digest: Digest
    network_policy_digest: Digest
    egress_route_ids: tuple[Identifier, ...]
    mounts: tuple[MountGrant, ...]

    @model_validator(mode="after")
    def _sets_are_canonical(self) -> SandboxGrant:
        _require_sorted_unique(
            self.egress_route_ids,
            key=lambda value: value,
            field_name="egress_route_ids",
        )
        _require_sorted_unique(
            self.mounts,
            key=lambda value: (
                value.target_logical_path,
                value.source_artifact_digest,
                value.access.value,
            ),
            field_name="mounts",
        )
        return self


class ResourceLimits(_ConfigRuntimeContract):
    cpu_millis: PositiveUInt53
    memory_bytes: PositiveUInt53
    pids: PositiveUInt53
    storage_bytes: PositiveUInt53
    open_files: PositiveUInt53
    wall_time_ms: PositiveUInt53


class ExecutionLimits(_ConfigRuntimeContract):
    max_turns: PositiveUInt53
    action_timeout_ms: PositiveUInt53
    observation_bytes: PositiveUInt53
    response_bytes: PositiveUInt53
    artifact_bytes_each: PositiveUInt53
    artifact_bytes_total: PositiveUInt53
    transcript_bytes: PositiveUInt53
    setup_timeout_ms: PositiveUInt53
    verifier_timeout_ms: PositiveUInt53

    @model_validator(mode="after")
    def _artifact_limits_ordered(self) -> ExecutionLimits:
        if self.artifact_bytes_each > self.artifact_bytes_total:
            raise ValueError("artifact_bytes_each must not exceed artifact_bytes_total")
        return self


class TaskGrant(_ConfigRuntimeContract):
    task_contract_digest: Digest
    task_binding_digest: Digest
    repository_snapshot_digest: Digest | None
    dataset_digests: tuple[Digest, ...]
    input_artifact_digests: tuple[Digest, ...]

    @model_validator(mode="after")
    def _sets_are_canonical(self) -> TaskGrant:
        _require_sorted_unique(
            self.dataset_digests,
            key=lambda value: value,
            field_name="dataset_digests",
        )
        _require_sorted_unique(
            self.input_artifact_digests,
            key=lambda value: value,
            field_name="input_artifact_digests",
        )
        return self


class PolicySlotGrant(_ConfigRuntimeContract):
    slot_id: Identifier
    protocol_abi: Identifier
    route_id: Identifier
    secret_handle_id: Identifier
    model_digest: Digest
    tokenizer_digest: Digest
    checkpoint_digest: Digest
    required_policy_capabilities_digest: Digest


class VerifierGrant(_ConfigRuntimeContract):
    verifier_id: Identifier
    implementation_digest: Digest
    image_digest: Digest
    executable_digest: Digest
    code_digest: Digest
    input_schema_digest: Digest
    result_schema_digest: Digest
    network_policy_digest: Digest
    secret_handle_ids: tuple[Identifier, ...]

    @model_validator(mode="after")
    def _secrets_sorted(self) -> VerifierGrant:
        _require_sorted_unique(
            self.secret_handle_ids,
            key=lambda value: value,
            field_name="secret_handle_ids",
        )
        return self


class MutablePointerRule(_ConfigRuntimeContract):
    pointer: JsonPointer
    allowed_operations: tuple[MutableOperation, ...]
    value_schema_digest: Digest
    authority_effect: AuthorityEffect
    removable: StrictBool

    @model_validator(mode="after")
    def _operations_sorted(self) -> MutablePointerRule:
        _require_sorted_unique(
            self.allowed_operations,
            key=lambda value: value.value,
            field_name="allowed_operations",
            nonempty=True,
        )
        if self.removable != (MutableOperation.REMOVE in self.allowed_operations):
            raise ValueError("removable must exactly reflect the remove operation grant")
        return self


class ArtifactPolicyGrant(_ConfigRuntimeContract):
    allowed_roles: tuple[Identifier, ...]
    max_each_bytes: PositiveUInt53
    max_total_bytes: PositiveUInt53

    @model_validator(mode="after")
    def _roles_and_limits(self) -> ArtifactPolicyGrant:
        _require_sorted_unique(
            self.allowed_roles,
            key=lambda value: value,
            field_name="allowed_roles",
        )
        if self.max_each_bytes > self.max_total_bytes:
            raise ValueError("max_each_bytes must not exceed max_total_bytes")
        return self


class PolicyRef(_ConfigRuntimeContract):
    policy_id: Identifier
    revision_digest: Digest


class CapabilityVector(_ConfigRuntimeContract):
    runner: RunnerGrant
    tools: tuple[ToolGrant, ...]
    setup_plans: tuple[SetupGrant, ...]
    routes: tuple[RouteGrant, ...]
    secret_handles: tuple[SecretHandleGrant, ...]
    sandbox: SandboxGrant
    resources: ResourceLimits
    limits: ExecutionLimits
    task: TaskGrant
    policy_slots: tuple[PolicySlotGrant, ...]
    verifier: VerifierGrant
    mutable_pointers: tuple[MutablePointerRule, ...]
    artifacts: ArtifactPolicyGrant
    evidence: PolicyRef
    retention: PolicyRef

    @model_validator(mode="after")
    def _complete_and_cross_bound(self) -> CapabilityVector:
        collections = (
            (self.tools, lambda item: item.tool_id, "tools"),
            (self.setup_plans, lambda item: item.setup_id, "setup_plans"),
            (self.routes, lambda item: item.route_id, "routes"),
            (self.secret_handles, lambda item: item.handle_id, "secret_handles"),
            (self.policy_slots, lambda item: item.slot_id, "policy_slots"),
            (self.mutable_pointers, lambda item: item.pointer, "mutable_pointers"),
        )
        for values, key, field_name in collections:
            _require_sorted_unique(values, key=key, field_name=field_name)
        route_ids = {route.route_id for route in self.routes}
        secret_ids = {secret.handle_id for secret in self.secret_handles}
        if not set(self.sandbox.egress_route_ids) <= route_ids:
            raise ValueError("sandbox egress routes must be declared route grants")
        if any(route.credential_handle_id not in secret_ids for route in self.routes):
            raise ValueError("every route credential handle must be declared")
        if any(
            slot.route_id not in route_ids or slot.secret_handle_id not in secret_ids
            for slot in self.policy_slots
        ):
            raise ValueError("policy slots must bind declared routes and secret handles")
        if not set(self.verifier.secret_handle_ids) <= secret_ids:
            raise ValueError("verifier secret handles must be declared")
        return self


class SandboxBinding(_ConfigRuntimeContract):
    runtime_id: Identifier
    runtime_class: RuntimeClass
    driver_implementation_digest: Digest
    runtime_binary_digest: Digest
    security_policy_digest: Digest
    image_digest: Digest
    network_policy_digest: Digest


class ModelIdentity(_ConfigRuntimeContract):
    model_id: Identifier
    model_digest: Digest
    tokenizer_digest: Digest
    checkpoint_digest: Digest


class RetentionPolicyGrant(_ConfigRuntimeContract):
    policy: PolicyRef
    minimum_seconds: UInt53
    maximum_seconds: PositiveUInt53

    @model_validator(mode="after")
    def _bounds_ordered(self) -> RetentionPolicyGrant:
        if self.minimum_seconds > self.maximum_seconds:
            raise ValueError("minimum_seconds must not exceed maximum_seconds")
        return self


class OperatorCeiling(_ConfigRuntimeContract):
    runner_bindings: tuple[RunnerGrant, ...]
    tool_grants: tuple[ToolGrant, ...]
    setup_grants: tuple[SetupGrant, ...]
    route_grants: tuple[RouteGrant, ...]
    secret_handle_grants: tuple[SecretHandleGrant, ...]
    sandbox_bindings: tuple[SandboxBinding, ...]
    repository_snapshot_digests: tuple[Digest, ...]
    task_contract_digests: tuple[Digest, ...]
    task_binding_digests: tuple[Digest, ...]
    dataset_digests: tuple[Digest, ...]
    model_bindings: tuple[ModelIdentity, ...]
    verifier_grants: tuple[VerifierGrant, ...]
    policy_slot_grants: tuple[PolicySlotGrant, ...]
    evidence_policies: tuple[PolicyRef, ...]
    retention_policies: tuple[RetentionPolicyGrant, ...]
    mutable_pointer_rules: tuple[MutablePointerRule, ...]
    resource_maxima: ResourceLimits
    execution_maxima: ExecutionLimits
    allowed_egress_route_ids: tuple[Identifier, ...]
    mount_grants: tuple[MountGrant, ...]
    artifact_policy_maximum: ArtifactPolicyGrant

    @model_validator(mode="after")
    def _sets_are_canonical(self) -> OperatorCeiling:
        collections = (
            (self.runner_bindings, lambda item: (item.adapter_id, item.runtime_abi, item.implementation_digest), "runner_bindings"),
            (self.tool_grants, lambda item: item.tool_id, "tool_grants"),
            (self.setup_grants, lambda item: item.setup_id, "setup_grants"),
            (self.route_grants, lambda item: item.route_id, "route_grants"),
            (self.secret_handle_grants, lambda item: item.handle_id, "secret_handle_grants"),
            (self.sandbox_bindings, lambda item: (item.runtime_id, item.image_digest), "sandbox_bindings"),
            (self.repository_snapshot_digests, lambda item: item, "repository_snapshot_digests"),
            (self.task_contract_digests, lambda item: item, "task_contract_digests"),
            (self.task_binding_digests, lambda item: item, "task_binding_digests"),
            (self.dataset_digests, lambda item: item, "dataset_digests"),
            (self.model_bindings, lambda item: item.model_id, "model_bindings"),
            (self.verifier_grants, lambda item: item.verifier_id, "verifier_grants"),
            (self.policy_slot_grants, lambda item: item.slot_id, "policy_slot_grants"),
            (self.evidence_policies, lambda item: (item.policy_id, item.revision_digest), "evidence_policies"),
            (self.retention_policies, lambda item: (item.policy.policy_id, item.policy.revision_digest), "retention_policies"),
            (self.mutable_pointer_rules, lambda item: item.pointer, "mutable_pointer_rules"),
            (self.allowed_egress_route_ids, lambda item: item, "allowed_egress_route_ids"),
            (self.mount_grants, lambda item: (item.target_logical_path, item.source_artifact_digest, item.access.value), "mount_grants"),
        )
        for values, key, field_name in collections:
            _require_sorted_unique(values, key=key, field_name=field_name)
        return self


class CompilerConstraints(_ConfigRuntimeContract):
    allowed_compilers: tuple[CompilerIdentity, ...]

    @model_validator(mode="after")
    def _compilers_sorted(self) -> CompilerConstraints:
        _require_sorted_unique(
            self.allowed_compilers,
            key=lambda item: (
                item.compiler_id,
                item.semantic_version,
                item.code_digest,
                item.runtime_abi,
            ),
            field_name="allowed_compilers",
            nonempty=True,
        )
        return self


class RequiredSecurityPolicy(_ConfigRuntimeContract):
    minimum_isolation_class: RuntimeClass
    required_verifier_isolation_class: RuntimeClass
    required_evidence_roles: tuple[Identifier, ...]
    prohibited_runtime_classes: tuple[RuntimeClass, ...]
    minimum_retention_seconds: UInt53

    @model_validator(mode="after")
    def _sets_are_canonical(self) -> RequiredSecurityPolicy:
        _require_sorted_unique(
            self.required_evidence_roles,
            key=lambda value: value,
            field_name="required_evidence_roles",
        )
        _require_sorted_unique(
            self.prohibited_runtime_classes,
            key=lambda value: value.value,
            field_name="prohibited_runtime_classes",
        )
        if self.minimum_isolation_class in self.prohibited_runtime_classes:
            raise ValueError("minimum isolation class cannot be prohibited")
        return self


class RegistryDigestSet(_ConfigRuntimeContract):
    runner_registry_digest: Digest
    tool_registry_digest: Digest
    setup_registry_digest: Digest
    route_registry_digest: Digest
    secret_handle_registry_digest: Digest
    sandbox_runtime_registry_digest: Digest
    image_registry_digest: Digest
    repository_binding_registry_digest: Digest
    task_dataset_registry_digest: Digest
    model_registry_digest: Digest
    verifier_registry_digest: Digest
    evidence_policy_registry_digest: Digest
    retention_policy_registry_digest: Digest
    policy_capability_registry_digest: Digest
    snapshot_digest: Digest


class AdmissionPolicySnapshot(_ConfigRuntimeContract):
    schema_version: Literal["bb.rl.admission-policy.v1"] = ADMISSION_POLICY_SCHEMA_VERSION
    policy_id: Identifier
    revision: Identifier
    subject_scope_digest: Digest
    compiler_constraints: CompilerConstraints
    registry_digests: RegistryDigestSet
    ceiling: OperatorCeiling
    required_security: RequiredSecurityPolicy
    receipt_ttl_seconds: PositiveUInt53
    validity: ValidityWindow
    revocation: RevocationBinding

    @model_validator(mode="after")
    def _scope_and_ttl_are_bound(self) -> AdmissionPolicySnapshot:
        if self.subject_scope_digest != self.revocation.scope_digest:
            raise ValueError("policy and revocation scopes must match")
        from datetime import datetime

        start = datetime.strptime(self.validity.not_before, "%Y-%m-%dT%H:%M:%SZ")
        end = datetime.strptime(self.validity.expires_at, "%Y-%m-%dT%H:%M:%SZ")
        if self.receipt_ttl_seconds > int((end - start).total_seconds()):
            raise ValueError("receipt TTL cannot exceed the policy validity interval")
        return self


class RunnerRegistryRecord(_ConfigRuntimeContract):
    grant: RunnerGrant


class ToolRegistryRecord(_ConfigRuntimeContract):
    grant: ToolGrant
    argument_schema_digest: Digest
    result_schema_digest: Digest
    reserved: StrictBool


class SetupOutput(_ConfigRuntimeContract):
    role: Identifier
    artifact_id: Identifier


class RouteOwnerAuthority(_ConfigRuntimeContract):
    owner_id: Identifier
    authority_scope_digest: Digest


class SetupRegistryRecord(_ConfigRuntimeContract):
    grant: SetupGrant
    argv: tuple[str, ...]
    input_digests: tuple[Digest, ...]
    writable_output_subtrees: tuple[LogicalPath, ...]
    writable_output_slots: tuple[Identifier, ...]
    route_ids: tuple[Identifier, ...]
    secret_handle_ids: tuple[Identifier, ...]
    timeout_ms: PositiveUInt53
    expected_outputs: tuple[SetupOutput, ...]

    def plan_projection(self) -> dict[str, Any]:
        return {
            "schema_version": "bb.rl.setup-plan.v1",
            "setup_id": self.grant.setup_id,
            "implementation_digest": self.grant.implementation_digest,
            "argv": list(self.argv),
            "input_digests": list(self.input_digests),
            "writable_output_subtrees": list(self.writable_output_subtrees),
            "writable_output_slots": list(self.writable_output_slots),
            "route_ids": list(self.route_ids),
            "secret_handle_ids": list(self.secret_handle_ids),
            "timeout_ms": self.timeout_ms,
            "expected_outputs": [output.model_dump(mode="json") for output in self.expected_outputs],
        }

    def derived_plan_digest(self) -> str:
        return _canonical_digest(self.plan_projection())

    @model_validator(mode="after")
    def _plan_is_canonical_and_bound(self) -> SetupRegistryRecord:
        if not self.argv:
            raise ValueError("setup argv must not be empty")
        for argument in self.argv:
            _validate_text(argument, field_name="setup argv item", max_bytes=4096)
        for values, field_name in (
            (self.input_digests, "input_digests"),
            (self.writable_output_subtrees, "writable_output_subtrees"),
            (self.writable_output_slots, "writable_output_slots"),
            (self.route_ids, "route_ids"),
            (self.secret_handle_ids, "secret_handle_ids"),
        ):
            _require_sorted_unique(values, key=lambda value: value, field_name=field_name)
        _require_sorted_unique(
            self.expected_outputs,
            key=lambda output: (output.role, output.artifact_id),
            field_name="expected_outputs",
        )
        if self.grant.plan_digest != self.derived_plan_digest():
            raise ValueError("setup plan digest does not bind the typed setup authority")
        return self


class RouteRegistryRecord(_ConfigRuntimeContract):
    grant: RouteGrant
    scheme: RouteScheme
    authority: RouteAuthority
    paths: tuple[RoutePath, ...]
    methods: tuple[RouteMethod, ...]
    ip_policy_digest: Digest
    dns_policy_digest: Digest
    request_schema_digest: Digest
    response_schema_digest: Digest
    max_request_bytes: PositiveUInt53
    max_response_bytes: PositiveUInt53
    max_requests_per_minute: PositiveUInt53
    data_classification: DataClassification
    owner: RouteOwnerAuthority

    def authority_projection(self) -> dict[str, Any]:
        return {
            "schema_version": "bb.rl.route-authority.v1",
            "route_id": self.grant.route_id,
            "protocol_abi": self.grant.protocol_abi,
            "credential_handle_id": self.grant.credential_handle_id,
            "scheme": self.scheme.value,
            "authority": self.authority,
            "paths": list(self.paths),
            "methods": [method.value for method in self.methods],
            "ip_policy_digest": self.ip_policy_digest,
            "dns_policy_digest": self.dns_policy_digest,
            "request_schema_digest": self.request_schema_digest,
            "response_schema_digest": self.response_schema_digest,
            "max_request_bytes": self.max_request_bytes,
            "max_response_bytes": self.max_response_bytes,
            "max_requests_per_minute": self.max_requests_per_minute,
            "data_classification": self.data_classification.value,
            "owner": self.owner.model_dump(mode="json"),
        }

    def derived_route_revision_digest(self) -> str:
        return _canonical_digest(self.authority_projection())

    @model_validator(mode="after")
    def _authority_is_canonical_and_bound(self) -> RouteRegistryRecord:
        _require_sorted_unique(self.paths, key=lambda path: path, field_name="paths", nonempty=True)
        _require_sorted_unique(
            self.methods,
            key=lambda method: method.value,
            field_name="methods",
            nonempty=True,
        )
        if self.grant.route_revision_digest != self.derived_route_revision_digest():
            raise ValueError("route revision digest does not bind the typed route authority")
        return self


class SecretHandleRegistryRecord(_ConfigRuntimeContract):
    grant: SecretHandleGrant
    route_ids: tuple[Identifier, ...]

    @model_validator(mode="after")
    def _routes_sorted(self) -> SecretHandleRegistryRecord:
        _require_sorted_unique(
            self.route_ids, key=lambda value: value, field_name="route_ids"
        )
        return self


class SandboxRuntimeRegistryRecord(_ConfigRuntimeContract):
    binding: SandboxBinding


class ImageRegistryRecord(_ConfigRuntimeContract):
    image_digest: Digest
    runtime_id: Identifier
    repository_binding_digests: tuple[Digest, ...]

    @model_validator(mode="after")
    def _bindings_sorted(self) -> ImageRegistryRecord:
        _require_sorted_unique(
            self.repository_binding_digests,
            key=lambda value: value,
            field_name="repository_binding_digests",
        )
        return self


class RepositoryBindingRegistryRecord(_ConfigRuntimeContract):
    binding_digest: Digest
    repository_snapshot_digest: Digest
    image_digest: Digest


class TaskDatasetRegistryRecord(_ConfigRuntimeContract):
    task_contract_digest: Digest
    task_binding_digest: Digest
    repository_snapshot_digest: Digest | None
    dataset_digests: tuple[Digest, ...]
    input_artifact_digests: tuple[Digest, ...]

    @model_validator(mode="after")
    def _sets_are_canonical(self) -> TaskDatasetRegistryRecord:
        _require_sorted_unique(
            self.dataset_digests, key=lambda value: value, field_name="dataset_digests"
        )
        _require_sorted_unique(
            self.input_artifact_digests,
            key=lambda value: value,
            field_name="input_artifact_digests",
        )
        return self


class ModelRegistryRecord(_ConfigRuntimeContract):
    identity: ModelIdentity


class VerifierRegistryRecord(_ConfigRuntimeContract):
    grant: VerifierGrant
    runtime_id: Identifier
    runtime_class: RuntimeClass
    security_policy_digest: Digest


class EvidencePolicyRegistryRecord(_ConfigRuntimeContract):
    policy: PolicyRef
    required_roles: tuple[Identifier, ...]

    @model_validator(mode="after")
    def _roles_sorted(self) -> EvidencePolicyRegistryRecord:
        _require_sorted_unique(
            self.required_roles, key=lambda value: value, field_name="required_roles"
        )
        return self


class RetentionPolicyRegistryRecord(_ConfigRuntimeContract):
    grant: RetentionPolicyGrant


class PolicyCapabilityAttestationRecord(_ConfigRuntimeContract):
    route_id: Identifier
    route_revision_digest: Digest
    model_digest: Digest
    tokenizer_digest: Digest
    checkpoint_digest: Digest
    capability_digest: Digest
    validity: ValidityWindow
    revocation: RevocationBinding
    authorized_signer_key_ids: tuple[Identifier, ...]
    signature_verification_policy_digest: Digest
    attestation_digest: Digest

    def attestation_projection(self) -> dict[str, Any]:
        return {
            "schema_version": "bb.rl.policy-capability-attestation.v1",
            "route_id": self.route_id,
            "route_revision_digest": self.route_revision_digest,
            "model_digest": self.model_digest,
            "tokenizer_digest": self.tokenizer_digest,
            "checkpoint_digest": self.checkpoint_digest,
            "capability_digest": self.capability_digest,
            "validity": self.validity.model_dump(mode="json"),
            "revocation": self.revocation.model_dump(mode="json"),
            "authorized_signer_key_ids": list(self.authorized_signer_key_ids),
            "signature_verification_policy_digest": self.signature_verification_policy_digest,
        }

    def derived_attestation_digest(self) -> str:
        return _canonical_digest(self.attestation_projection())

    @model_validator(mode="after")
    def _attestation_is_bound(self) -> PolicyCapabilityAttestationRecord:
        _require_sorted_unique(
            self.authorized_signer_key_ids,
            key=lambda value: value,
            field_name="authorized_signer_key_ids",
            nonempty=True,
        )
        if self.attestation_digest != self.derived_attestation_digest():
            raise ValueError("attestation digest does not bind policy capability authority")
        return self


class RegistrySnapshotSet(_ConfigRuntimeContract):
    schema_version: Literal["bb.rl.registry-snapshot.v1"] = REGISTRY_SNAPSHOT_SCHEMA_VERSION
    digests: RegistryDigestSet
    runners: tuple[RunnerRegistryRecord, ...]
    tools: tuple[ToolRegistryRecord, ...]
    setups: tuple[SetupRegistryRecord, ...]
    routes: tuple[RouteRegistryRecord, ...]
    secret_handles: tuple[SecretHandleRegistryRecord, ...]
    sandbox_runtimes: tuple[SandboxRuntimeRegistryRecord, ...]
    images: tuple[ImageRegistryRecord, ...]
    repository_bindings: tuple[RepositoryBindingRegistryRecord, ...]
    task_datasets: tuple[TaskDatasetRegistryRecord, ...]
    models: tuple[ModelRegistryRecord, ...]
    verifiers: tuple[VerifierRegistryRecord, ...]
    evidence_policies: tuple[EvidencePolicyRegistryRecord, ...]
    retention_policies: tuple[RetentionPolicyRegistryRecord, ...]
    policy_capability_attestations: tuple[PolicyCapabilityAttestationRecord, ...]

    @staticmethod
    def component_projection(
        component: str, records: tuple[_ConfigRuntimeContract, ...]
    ) -> dict[str, Any]:
        allowed = {
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
        }
        if component not in allowed:
            raise ValueError("unknown registry component")
        if type(records) is not tuple:
            raise TypeError("registry records must be an immutable tuple")
        return {
            "schema_version": REGISTRY_SNAPSHOT_SCHEMA_VERSION,
            "component": component,
            "records": [record.model_dump(mode="json") for record in records],
        }

    @classmethod
    def derive_component_digest(
        cls, component: str, records: tuple[_ConfigRuntimeContract, ...]
    ) -> str:
        return _canonical_digest(cls.component_projection(component, records))

    @staticmethod
    def snapshot_projection(component_digests: dict[str, str]) -> dict[str, Any]:
        expected = {
            "runner_registry_digest",
            "tool_registry_digest",
            "setup_registry_digest",
            "route_registry_digest",
            "secret_handle_registry_digest",
            "sandbox_runtime_registry_digest",
            "image_registry_digest",
            "repository_binding_registry_digest",
            "task_dataset_registry_digest",
            "model_registry_digest",
            "verifier_registry_digest",
            "evidence_policy_registry_digest",
            "retention_policy_registry_digest",
            "policy_capability_registry_digest",
        }
        if type(component_digests) is not dict or set(component_digests) != expected:
            raise ValueError("snapshot root requires the exact registry component digest set")
        for digest in component_digests.values():
            _digest(digest)
        return {
            "schema_version": REGISTRY_SNAPSHOT_SCHEMA_VERSION,
            "component_digests": dict(component_digests),
        }

    @classmethod
    def derive_snapshot_digest(cls, component_digests: dict[str, str]) -> str:
        return _canonical_digest(cls.snapshot_projection(component_digests))

    @model_validator(mode="after")
    def _records_are_canonical_and_cross_bound(self) -> RegistrySnapshotSet:
        collections = (
            (self.runners, lambda item: (item.grant.adapter_id, item.grant.runtime_abi), "runners", "runner_registry_digest"),
            (self.tools, lambda item: item.grant.tool_id, "tools", "tool_registry_digest"),
            (self.setups, lambda item: item.grant.setup_id, "setups", "setup_registry_digest"),
            (self.routes, lambda item: item.grant.route_id, "routes", "route_registry_digest"),
            (self.secret_handles, lambda item: item.grant.handle_id, "secret_handles", "secret_handle_registry_digest"),
            (self.sandbox_runtimes, lambda item: item.binding.runtime_id, "sandbox_runtimes", "sandbox_runtime_registry_digest"),
            (self.images, lambda item: item.image_digest, "images", "image_registry_digest"),
            (self.repository_bindings, lambda item: item.binding_digest, "repository_bindings", "repository_binding_registry_digest"),
            (self.task_datasets, lambda item: (item.task_contract_digest, item.task_binding_digest), "task_datasets", "task_dataset_registry_digest"),
            (self.models, lambda item: item.identity.model_id, "models", "model_registry_digest"),
            (self.verifiers, lambda item: item.grant.verifier_id, "verifiers", "verifier_registry_digest"),
            (self.evidence_policies, lambda item: (item.policy.policy_id, item.policy.revision_digest), "evidence_policies", "evidence_policy_registry_digest"),
            (self.retention_policies, lambda item: (item.grant.policy.policy_id, item.grant.policy.revision_digest), "retention_policies", "retention_policy_registry_digest"),
            (self.policy_capability_attestations, lambda item: item.attestation_digest, "policy_capability_attestations", "policy_capability_registry_digest"),
        )
        component_digests: dict[str, str] = {}
        for values, key, component, digest_field in collections:
            _require_sorted_unique(values, key=key, field_name=component)
            derived = self.derive_component_digest(component, values)
            if getattr(self.digests, digest_field) != derived:
                raise ValueError(f"{digest_field} does not bind the exact registry records")
            component_digests[digest_field] = derived
        if self.digests.snapshot_digest != self.derive_snapshot_digest(component_digests):
            raise ValueError("snapshot_digest does not bind the component registry digests")

        routes = {record.grant.route_id: record for record in self.routes}
        secret_ids = {record.grant.handle_id for record in self.secret_handles}
        runtimes = {record.binding.runtime_id: record.binding for record in self.sandbox_runtimes}
        images = {record.image_digest: record for record in self.images}
        model_triples = {
            (record.identity.model_digest, record.identity.tokenizer_digest, record.identity.checkpoint_digest)
            for record in self.models
        }
        if any(record.grant.credential_handle_id not in secret_ids for record in self.routes):
            raise ValueError("registry route credential handles must resolve")
        if any(not set(record.route_ids) <= routes.keys() for record in self.secret_handles):
            raise ValueError("registry secret route bindings must resolve")
        if any(
            not set(record.route_ids) <= routes.keys()
            or not set(record.secret_handle_ids) <= secret_ids
            for record in self.setups
        ):
            raise ValueError("registry setup bindings must resolve")
        for record in self.policy_capability_attestations:
            route = routes.get(record.route_id)
            if route is None or route.grant.route_revision_digest != record.route_revision_digest:
                raise ValueError("policy capability attestation route authority must resolve")
            if (record.model_digest, record.tokenizer_digest, record.checkpoint_digest) not in model_triples:
                raise ValueError("policy capability attestation model authority must resolve")
        for verifier in self.verifiers:
            image = images.get(verifier.grant.image_digest)
            runtime = runtimes.get(verifier.runtime_id)
            if (
                image is None
                or image.runtime_id != verifier.runtime_id
                or runtime is None
                or runtime.image_digest != verifier.grant.image_digest
                or runtime.network_policy_digest != verifier.grant.network_policy_digest
            ):
                raise ValueError("verifier image, runtime, and network authority must resolve")
            if (
                runtime.runtime_class != verifier.runtime_class
                or runtime.security_policy_digest != verifier.security_policy_digest
            ):
                raise ValueError("verifier runtime class and security policy must match")
        return self


class PolicyBindingRef(_ConfigRuntimeContract):
    route_id: Identifier
    registry_revision_digest: Digest
    attestation_digest: Digest


class BehaviorSourceKind(str, Enum):
    COMPILED = "compiled"
    OVERLAY_DERIVED = "overlay_derived"


class CompiledBehaviorSource(_ConfigRuntimeContract):
    source_kind: Literal["compiled"] = BehaviorSourceKind.COMPILED.value
    manifest_digest: Digest
    semantic_digest: Digest


class OverlayDerivedBehaviorSource(_ConfigRuntimeContract):
    source_kind: Literal["overlay_derived"] = BehaviorSourceKind.OVERLAY_DERIVED.value
    base_manifest_digest: Digest
    parent_receipt_digest: Digest
    overlay_chain_digest: Digest
    derived_semantic_digest: Digest


BehaviorSource = Annotated[
    CompiledBehaviorSource | OverlayDerivedBehaviorSource,
    Field(discriminator="source_kind"),
]


class AdmissionRequest(_ConfigRuntimeContract):
    schema_version: Literal["bb.rl.admission-request.v1"] = ADMISSION_REQUEST_SCHEMA_VERSION
    subject: AuthenticatedSubject
    behavior_source: BehaviorSource
    compiled: CompiledArtifactIdentity
    requested_capabilities: CapabilityVector
    requested_capability_digest: Digest
    task_binding_digest: Digest
    policy_binding_ref: PolicyBindingRef
    admission_policy_digest: Digest
    registry_snapshot_digest: Digest
    validity: ValidityWindow
    parent_receipt_digest: Digest | None
    overlay_chain_digest: Digest | None

    @model_validator(mode="after")
    def _identity_is_complete(self) -> AdmissionRequest:
        if self.requested_capability_digest != self.requested_capabilities.canonical_digest():
            raise ValueError("requested_capability_digest does not bind the capability vector")
        if self.task_binding_digest != self.requested_capabilities.task.task_binding_digest:
            raise ValueError("task_binding_digest does not bind the task capability")
        route_ids = {route.route_id for route in self.requested_capabilities.routes}
        if self.policy_binding_ref.route_id not in route_ids:
            raise ValueError("policy binding route must be declared by the capability vector")
        source = self.behavior_source
        if isinstance(source, CompiledBehaviorSource):
            if self.parent_receipt_digest is not None or self.overlay_chain_digest is not None:
                raise ValueError("base behavior source forbids parent and overlay bindings")
            if source.manifest_digest != self.compiled.manifest_digest or source.semantic_digest != self.compiled.semantic_digest:
                raise ValueError("compiled behavior source does not match compiled identity")
        else:
            if self.parent_receipt_digest != source.parent_receipt_digest or self.overlay_chain_digest != source.overlay_chain_digest:
                raise ValueError("overlay behavior source bindings are inconsistent")
            if source.base_manifest_digest != self.compiled.manifest_digest or source.derived_semantic_digest != self.compiled.semantic_digest:
                raise ValueError("overlay behavior source does not match compiled identity")
        return self


class CapabilityDimension(str, Enum):
    RUNNER = "runner"
    TOOLS = "tools"
    SETUP_PLANS = "setup_plans"
    ROUTES = "routes"
    SECRET_HANDLES = "secret_handles"
    SANDBOX = "sandbox"
    RESOURCES = "resources"
    LIMITS = "limits"
    TASK = "task"
    POLICY_SLOTS = "policy_slots"
    VERIFIER = "verifier"
    MUTABLE_POINTERS = "mutable_pointers"
    ARTIFACTS = "artifacts"
    EVIDENCE = "evidence"
    RETENTION = "retention"


class CapabilityDelta(_ConfigRuntimeContract):
    dimension: CapabilityDimension
    ceiling_digest: Digest
    effective_digest: Digest
    reason_code: Identifier


class IssuanceAttestation(_ConfigRuntimeContract):
    key_id: Identifier
    algorithm: Identifier
    signed_payload_digest: Digest
    signature: Base64Url


class AdmissionReceipt(_ConfigRuntimeContract):
    schema_version: Literal["bb.rl.admission-receipt.v1"] = ADMISSION_RECEIPT_SCHEMA_VERSION
    subject: AuthenticatedSubject
    admission_request_digest: Digest
    behavior_source: BehaviorSource
    compiled: CompiledArtifactIdentity
    admission_policy_id: Identifier
    admission_policy_revision: Identifier
    admission_policy_digest: Digest
    operator_ceiling_digest: Digest
    registry_snapshot_digest: Digest
    requested_capability_digest: Digest
    effective_capabilities: CapabilityVector
    effective_capability_digest: Digest
    capability_deltas: tuple[CapabilityDelta, ...]
    pins: tuple[ArtifactIdentity, ...]
    mutable_pointer_policy_digest: Digest
    policy_binding_ref: PolicyBindingRef
    task_binding_digest: Digest
    decision: Literal["admitted"] = "admitted"
    reason_codes: tuple[Identifier, ...]
    validity: ValidityWindow
    revocation: RevocationBinding
    parent_receipt_digest: Digest | None
    overlay_chain_digest: Digest | None
    issuance_attestation: IssuanceAttestation

    @staticmethod
    def unsigned_canonical_obj_from_wire(value: dict[str, Any]) -> dict[str, Any]:
        if type(value) is not dict:
            raise TypeError("admission receipt signing value must be an object")
        attestation = value.get("issuance_attestation")
        if type(attestation) is not dict or set(attestation) != {
            "key_id",
            "algorithm",
            "signed_payload_digest",
            "signature",
        }:
            raise ValueError("issuance_attestation must contain the exact signing fields")
        projection = dict(value)
        projection["issuance_attestation"] = {
            "key_id": attestation["key_id"],
            "algorithm": attestation["algorithm"],
        }
        return _canonical_projection(projection)

    @classmethod
    def unsigned_canonical_bytes_from_wire(cls, value: dict[str, Any]) -> bytes:
        return _canonical_bytes(cls.unsigned_canonical_obj_from_wire(value))

    def unsigned_canonical_obj(self) -> dict[str, Any]:
        return self.unsigned_canonical_obj_from_wire(self.model_dump(mode="json"))

    def unsigned_canonical_bytes(self) -> bytes:
        return _canonical_bytes(self.unsigned_canonical_obj())

    def unsigned_payload_digest(self) -> str:
        return _canonical_digest(self.unsigned_canonical_obj())

    @model_validator(mode="after")
    def _receipt_is_canonical(self) -> AdmissionReceipt:
        effective_digest = self.effective_capabilities.canonical_digest()
        if self.effective_capability_digest != effective_digest:
            raise ValueError("effective_capability_digest does not bind the capability vector")
        if self.requested_capability_digest != effective_digest:
            raise ValueError("requested and effective capability identities must match")
        if self.task_binding_digest != self.effective_capabilities.task.task_binding_digest:
            raise ValueError("task binding does not match effective capabilities")
        if self.mutable_pointer_policy_digest != _canonical_digest(
            self.effective_capabilities.mutable_pointers
        ):
            raise ValueError("mutable pointer policy digest does not bind the exact effective rules")
        if self.revocation.scope_digest != self.subject.authority_scope_digest:
            raise ValueError("receipt revocation scope does not bind the admitted subject scope")
        route_ids = {route.route_id for route in self.effective_capabilities.routes}
        if self.policy_binding_ref.route_id not in route_ids:
            raise ValueError("receipt policy binding route is not an effective capability")
        _require_sorted_unique(
            self.capability_deltas,
            key=lambda item: item.dimension.value,
            field_name="capability_deltas",
        )
        _require_sorted_unique(
            self.pins,
            key=lambda item: (
                item.kind.value,
                item.logical_id,
                item.content_digest,
                item.qualifier_digest or "",
            ),
            field_name="pins",
        )
        _require_sorted_unique(
            self.reason_codes,
            key=lambda item: item,
            field_name="reason_codes",
        )
        source = self.behavior_source
        if isinstance(source, CompiledBehaviorSource):
            if self.parent_receipt_digest is not None or self.overlay_chain_digest is not None:
                raise ValueError("base receipt forbids parent and overlay bindings")
            if source.manifest_digest != self.compiled.manifest_digest or source.semantic_digest != self.compiled.semantic_digest:
                raise ValueError("compiled receipt source does not match compiled identity")
        else:
            if self.parent_receipt_digest != source.parent_receipt_digest or self.overlay_chain_digest != source.overlay_chain_digest:
                raise ValueError("derived receipt source bindings are inconsistent")
            if source.base_manifest_digest != self.compiled.manifest_digest or source.derived_semantic_digest != self.compiled.semantic_digest:
                raise ValueError("derived receipt source does not match compiled identity")
        pin_pairs = {(pin.kind, pin.content_digest) for pin in self.pins}
        required_pins = {
            (PinKind.BUNDLE, self.compiled.bundle_digest),
            (PinKind.CLOSURE, self.compiled.closure_digest),
            (PinKind.COMPILER_CODE, self.compiled.compiler.code_digest),
            (PinKind.COMPILED_MANIFEST, self.compiled.manifest_digest),
            (PinKind.COMPILED_SEMANTIC, self.compiled.semantic_digest),
            (PinKind.TASK, self.effective_capabilities.task.task_contract_digest),
            (PinKind.TASK, self.effective_capabilities.task.task_binding_digest),
            (PinKind.POLICY_CAPABILITY_ATTESTATION, self.policy_binding_ref.attestation_digest),
        }
        required_pins.update(
            (PinKind.INPUT_ARTIFACT, digest)
            for digest in self.effective_capabilities.task.input_artifact_digests
        )
        if not required_pins <= pin_pairs:
            raise ValueError("receipt pins do not bind source, task, policy, and input identities")
        if self.issuance_attestation.signed_payload_digest != self.unsigned_payload_digest():
            raise ValueError("issuance attestation does not bind the unsigned receipt payload")
        return self


class ArtifactRef(_ConfigRuntimeContract):
    schema_version: Literal["bb.rl.artifact-ref.v1"] = ARTIFACT_REF_SCHEMA_VERSION
    artifact_id: Digest
    sha256: Digest
    size_bytes: PositiveUInt53
    media_type: Annotated[str, StringConstraints(strict=True)]

    @field_validator("media_type")
    @classmethod
    def _media_type_is_closed(cls, value: str) -> str:
        if _MEDIA_TYPE_RE.fullmatch(value) is None:
            raise ValueError("media_type must be a BreadBoard version-1 canonical JSON type")
        return value

    @model_validator(mode="after")
    def _content_addressed(self) -> ArtifactRef:
        if self.artifact_id != self.sha256:
            raise ValueError("artifact_id and sha256 must identify the same canonical bytes")
        return self


class AdmissionReceiptRef(_ConfigRuntimeContract):
    digest: Digest
    ref: ArtifactRef

    @model_validator(mode="after")
    def _binds_ref(self) -> AdmissionReceiptRef:
        if self.digest != self.ref.sha256:
            raise ValueError("receipt digest and artifact reference must match")
        if self.ref.media_type != "application/vnd.breadboard.admission-receipt+json;version=1":
            raise ValueError("receipt reference has the wrong media type")
        return self


class PrivilegedCheckpoint(str, Enum):
    EPISODE_PREFLIGHT = "episode_preflight"
    BEFORE_ALLOCATION = "before_allocation"
    BEFORE_SETUP = "before_setup"
    BEFORE_POLICY_CREDENTIAL = "before_policy_credential"
    BEFORE_POLICY_INVOKE = "before_policy_invoke"
    BEFORE_PRIVILEGED_TOOL = "before_privileged_tool"
    BEFORE_VERIFIER = "before_verifier"
    BEFORE_REWARD_PUBLICATION = "before_reward_publication"
    BEFORE_EVIDENCE_PUBLICATION = "before_evidence_publication"


class CurrentnessToken(_ConfigRuntimeContract):
    schema_version: Literal["bb.rl.admission-currentness.v1"] = CURRENTNESS_TOKEN_SCHEMA_VERSION
    receipt_digest: Digest
    subject_digest: Digest
    admission_policy_digest: Digest
    registry_snapshot_digest: Digest
    revocation_scope_digest: Digest
    revocation_epoch: UInt64
    revocation_state_digest: Digest
    checkpoint: PrivilegedCheckpoint
    verified_at: UtcSecond
    expires_at: UtcSecond

    @model_validator(mode="after")
    def _current_at_verification(self) -> CurrentnessToken:
        if not self.verified_at < self.expires_at:
            raise ValueError("currentness token must be verified before expiry")
        return self


class VerifiedAdmission(_ConfigRuntimeContract):
    schema_version: Literal["bb.rl.verified-admission.v1"] = VERIFIED_ADMISSION_SCHEMA_VERSION
    receipt_ref: AdmissionReceiptRef
    receipt: AdmissionReceipt
    subject_digest: Digest
    checkpoint: PrivilegedCheckpoint
    currentness: CurrentnessToken

    @model_validator(mode="after")
    def _proof_is_consistent(self) -> VerifiedAdmission:
        receipt = self.receipt
        token = self.currentness
        if self.receipt_ref.digest != receipt.canonical_digest():
            raise ValueError("receipt ref does not bind the receipt payload")
        if self.subject_digest != receipt.subject.canonical_digest():
            raise ValueError("subject digest does not bind the receipt subject")
        if token.receipt_digest != self.receipt_ref.digest:
            raise ValueError("currentness token does not bind the receipt digest")
        if token.subject_digest != self.subject_digest:
            raise ValueError("currentness token does not bind the receipt subject")
        if token.admission_policy_digest != receipt.admission_policy_digest:
            raise ValueError("currentness token does not bind the receipt policy")
        if token.registry_snapshot_digest != receipt.registry_snapshot_digest:
            raise ValueError("currentness token does not bind the receipt registry snapshot")
        if (
            token.revocation_scope_digest != receipt.revocation.scope_digest
            or token.revocation_epoch != receipt.revocation.epoch
            or token.revocation_state_digest != receipt.revocation.state_digest
        ):
            raise ValueError("currentness token does not bind the receipt revocation state")
        if token.expires_at != receipt.validity.expires_at:
            raise ValueError("currentness token expiry does not bind the receipt expiry")
        if not receipt.validity.not_before <= token.verified_at < receipt.validity.expires_at:
            raise ValueError("currentness verification instant is outside receipt validity")
        if token.checkpoint != self.checkpoint:
            raise ValueError("currentness checkpoint does not match verification checkpoint")
        return self


class AttestationKind(str, Enum):
    STARTUP_PROBE = "startup_probe"
    OPERATOR_ATTESTATION = "operator_attestation"


class AttestationProvenance(_ConfigRuntimeContract):
    kind: AttestationKind
    issuer_id: Identifier
    signer_key_id: Identifier | None
    environment_digest: Digest
    evidence_digest: Digest
    validity: ValidityWindow


class PolicyCapabilityVector(_ConfigRuntimeContract):
    responses_protocol: Identifier
    modalities: tuple[Identifier, ...]
    tool_calling: StrictBool
    parallel_tool_calls: StrictBool
    token_ids: StrictBool
    token_logprobs: StrictBool
    routing_metadata: StrictBool
    cancellation: StrictBool
    max_context_tokens: UInt53
    max_output_tokens: UInt53
    policy_slot_count: UInt53
    request_features: tuple[Identifier, ...]

    @model_validator(mode="after")
    def _sets_are_canonical(self) -> PolicyCapabilityVector:
        _require_sorted_unique(
            self.modalities, key=lambda item: item, field_name="modalities"
        )
        _require_sorted_unique(
            self.request_features, key=lambda item: item, field_name="request_features"
        )
        return self


class PolicyCapabilityObservation(_ConfigRuntimeContract):
    schema_version: Literal["bb.rl.policy-capability-observation.v1"] = (
        POLICY_CAPABILITY_OBSERVATION_SCHEMA_VERSION
    )
    registry_revision_digest: Digest
    route_id: Identifier
    route_revision_digest: Digest
    provider_id: Identifier
    protocol_abi: Identifier
    bridge_instance_id: Identifier
    bridge_build_digest: Digest
    model_id: Identifier
    model_digest: Digest
    tokenizer_digest: Digest
    checkpoint_digest: Digest
    credential_handle_id: Identifier
    credential_handle_version_digest: Digest
    subject_scope_digest: Digest
    capabilities: PolicyCapabilityVector
    capability_digest: Digest
    provenance: AttestationProvenance
    revocation: RevocationBinding

    def selection_capability_obj(self) -> dict[str, Any]:
        return {
            "schema_version": "bb.rl.policy-selection-capabilities.v1",
            "protocol_abi": self.protocol_abi,
            "model_digest": self.model_digest,
            "tokenizer_digest": self.tokenizer_digest,
            "checkpoint_digest": self.checkpoint_digest,
            "capabilities": self.capabilities.to_canonical_obj(),
        }

    @model_validator(mode="after")
    def _observation_binds_selection_capabilities(self) -> PolicyCapabilityObservation:
        if self.capability_digest != _canonical_digest(self.selection_capability_obj()):
            raise ValueError("capability_digest does not bind selection capabilities")
        if self.revocation.scope_digest != self.subject_scope_digest:
            raise ValueError("observation revocation scope must bind the subject scope")
        return self


class AdmittedSetManifest(_ConfigRuntimeContract):
    schema_version: Literal["bb.rl.admitted-set.v1"] = ADMITTED_SET_SCHEMA_VERSION
    compiler_abi: Identifier
    admission_policy_digest: Digest
    operator_ceiling_digest: Digest
    registry_snapshot_digest: Digest
    revocation: RevocationBinding
    receipt_digests: tuple[Digest, ...]
    validity: ValidityWindow

    @model_validator(mode="after")
    def _receipts_are_a_set(self) -> AdmittedSetManifest:
        _require_sorted_unique(
            self.receipt_digests,
            key=lambda item: item,
            field_name="receipt_digests",
            nonempty=True,
        )
        return self


class AdmittedOverlayRef(_ConfigRuntimeContract):
    overlay_digest: Digest
    result_receipt_digest: Digest


class PolicyBoolField(str, Enum):
    TOOL_CALLING = "tool_calling"
    PARALLEL_TOOL_CALLS = "parallel_tool_calls"
    TOKEN_IDS = "token_ids"
    TOKEN_LOGPROBS = "token_logprobs"
    ROUTING_METADATA = "routing_metadata"
    CANCELLATION = "cancellation"


class PolicyIntField(str, Enum):
    MAX_CONTEXT_TOKENS = "max_context_tokens"
    MAX_OUTPUT_TOKENS = "max_output_tokens"
    POLICY_SLOT_COUNT = "policy_slot_count"


class PolicySetField(str, Enum):
    MODALITIES = "modalities"
    REQUEST_FEATURES = "request_features"


class TaskLabelEq(_ConfigRuntimeContract):
    kind: Literal["task_label_eq"] = "task_label_eq"
    key: Identifier
    value: Annotated[str, StringConstraints(strict=True)]

    @field_validator("value")
    @classmethod
    def _value_is_canonical(cls, value: str) -> str:
        return _validate_text(value, field_name="task label value", max_bytes=1024)


class TaskLabelIn(_ConfigRuntimeContract):
    kind: Literal["task_label_in"] = "task_label_in"
    key: Identifier
    values: tuple[Annotated[str, StringConstraints(strict=True)], ...]

    @field_validator("values")
    @classmethod
    def _values_are_text(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        for value in values:
            _validate_text(value, field_name="task label value", max_bytes=1024)
        return values

    @model_validator(mode="after")
    def _values_are_a_set(self) -> TaskLabelIn:
        _require_sorted_unique(
            self.values, key=lambda item: item.encode("utf-8"), field_name="values", nonempty=True
        )
        return self


class ArtifactRolePresent(_ConfigRuntimeContract):
    kind: Literal["artifact_role_present"] = "artifact_role_present"
    role: Identifier
    min_count: UInt53
    max_count: UInt53 | None
    media_types: tuple[Annotated[str, StringConstraints(strict=True)], ...]

    @field_validator("media_types")
    @classmethod
    def _media_types_are_canonical(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        for value in values:
            _validate_text(value, field_name="media type", max_bytes=256)
            if value != value.lower() or "/" not in value or any(ch.isspace() for ch in value):
                raise ValueError("media type must be lowercase canonical type/subtype")
        return values

    @model_validator(mode="after")
    def _bounds_and_set_are_canonical(self) -> ArtifactRolePresent:
        _require_sorted_unique(
            self.media_types,
            key=lambda item: item.encode("ascii", errors="strict"),
            field_name="media_types",
        )
        if self.max_count is not None and self.min_count > self.max_count:
            raise ValueError("min_count must not exceed max_count")
        return self


class PolicyBoolEq(_ConfigRuntimeContract):
    kind: Literal["policy_bool_eq"] = "policy_bool_eq"
    field: PolicyBoolField
    value: StrictBool


class PolicyIntGte(_ConfigRuntimeContract):
    kind: Literal["policy_int_gte"] = "policy_int_gte"
    field: PolicyIntField
    value: UInt53


class PolicySetContainsAll(_ConfigRuntimeContract):
    kind: Literal["policy_set_contains_all"] = "policy_set_contains_all"
    field: PolicySetField
    values: tuple[Identifier, ...]

    @model_validator(mode="after")
    def _values_are_a_set(self) -> PolicySetContainsAll:
        _require_sorted_unique(
            self.values, key=lambda item: item, field_name="values", nonempty=True
        )
        return self


class AllOf(_ConfigRuntimeContract):
    kind: Literal["all"] = "all"
    children: tuple[EligibilityPredicate, ...]

    @model_validator(mode="after")
    def _children_are_canonical(self) -> AllOf:
        _require_sorted_canonical(self.children, field_name="children", nonempty=True)
        return self


class AnyOf(_ConfigRuntimeContract):
    kind: Literal["any"] = "any"
    children: tuple[EligibilityPredicate, ...]

    @model_validator(mode="after")
    def _children_are_canonical(self) -> AnyOf:
        _require_sorted_canonical(self.children, field_name="children", nonempty=True)
        return self


EligibilityPredicate = Annotated[
    AllOf
    | AnyOf
    | TaskLabelEq
    | TaskLabelIn
    | ArtifactRolePresent
    | PolicyBoolEq
    | PolicyIntGte
    | PolicySetContainsAll,
    Field(discriminator="kind"),
]


def _predicate_size(predicate: EligibilityPredicate, *, depth: int = 1) -> tuple[int, int]:
    if isinstance(predicate, (AllOf, AnyOf)):
        child_sizes = tuple(_predicate_size(child, depth=depth + 1) for child in predicate.children)
        return max((item[0] for item in child_sizes), default=depth), 1 + sum(
            item[1] for item in child_sizes
        )
    return depth, 1


class Label(_ConfigRuntimeContract):
    key: Identifier
    value: Annotated[str, StringConstraints(strict=True)]

    @field_validator("value")
    @classmethod
    def _value_is_canonical(cls, value: str) -> str:
        return _validate_text(value, field_name="task label value", max_bytes=1024)


class TaskArtifact(_ConfigRuntimeContract):
    role: Identifier
    digest: Digest
    media_type: Annotated[str, StringConstraints(strict=True)]
    size_bytes: UInt53

    @field_validator("media_type")
    @classmethod
    def _media_type_is_canonical(cls, value: str) -> str:
        _validate_text(value, field_name="media type", max_bytes=256)
        if value != value.lower() or "/" not in value or any(ch.isspace() for ch in value):
            raise ValueError("media type must be lowercase canonical type/subtype")
        return value


class TaskEligibilityInput(_ConfigRuntimeContract):
    schema_version: Literal["bb.rl.task-eligibility.v1"] = TASK_ELIGIBILITY_SCHEMA_VERSION
    task_type: Identifier
    labels: tuple[Label, ...]
    artifacts: tuple[TaskArtifact, ...]
    parameters_digest: Digest

    @model_validator(mode="after")
    def _sets_are_canonical(self) -> TaskEligibilityInput:
        _require_sorted_unique(self.labels, key=lambda item: item.key, field_name="labels")
        _require_sorted_unique(
            self.artifacts,
            key=lambda item: (item.role, item.digest, item.media_type, item.size_bytes),
            field_name="artifacts",
        )
        return self


class ConfigCandidate(_ConfigRuntimeContract):
    candidate_id: CandidateId
    receipt_digest: Digest
    predicates: tuple[EligibilityPredicate, ...]
    overlays: tuple[AdmittedOverlayRef, ...]

    @model_validator(mode="after")
    def _candidate_is_canonical_and_bounded(self) -> ConfigCandidate:
        _require_sorted_canonical(self.predicates, field_name="predicates")
        node_count = 0
        max_depth = 0
        for predicate in self.predicates:
            depth, nodes = _predicate_size(predicate)
            max_depth = max(max_depth, depth)
            node_count += nodes
        if max_depth > 8 or node_count > 64:
            raise ValueError("candidate predicate AST exceeds depth 8 or 64 nodes")
        overlay_keys = tuple(
            (item.overlay_digest, item.result_receipt_digest) for item in self.overlays
        )
        if len(overlay_keys) != len(set(overlay_keys)):
            raise ValueError("candidate overlay refs must not repeat")
        return self


class DirectSelector(_ConfigRuntimeContract):
    schema_version: Literal["bb.rl.direct-selector.v1"] = DIRECT_SELECTOR_SCHEMA_VERSION
    admitted_set_root: Digest
    compiler_abi: Identifier
    runtime_abi: Identifier
    admission_policy_digest: Digest
    operator_ceiling_digest: Digest
    candidate: ConfigCandidate
    validity: ValidityWindow


class WeightedCandidate(_ConfigRuntimeContract):
    candidate: ConfigCandidate
    weight: PositiveUInt53


class ConfigSetManifest(_ConfigRuntimeContract):
    schema_version: Literal["bb.rl.config-set.v1"] = CONFIG_SET_SCHEMA_VERSION
    algorithm: Literal["weighted-v1"] = "weighted-v1"
    admitted_set_root: Digest
    compiler_abi: Identifier
    runtime_abi: Identifier
    admission_policy_digest: Digest
    operator_ceiling_digest: Digest
    candidates: tuple[WeightedCandidate, ...]
    validity: ValidityWindow

    @model_validator(mode="after")
    def _candidates_are_canonical(self) -> ConfigSetManifest:
        _require_sorted_unique(
            self.candidates,
            key=lambda item: item.candidate.candidate_id.encode("ascii"),
            field_name="candidates",
            nonempty=True,
        )
        identities: set[bytes] = set()
        total = 0
        for weighted in self.candidates:
            candidate = weighted.candidate
            identity = _canonical_bytes(
                {
                    "receipt_digest": candidate.receipt_digest,
                    "predicates": candidate.predicates,
                    "overlays": candidate.overlays,
                }
            )
            if identity in identities:
                raise ValueError("duplicate effective candidates are forbidden")
            identities.add(identity)
            total += weighted.weight
            if total > MAX_UINT53:
                raise ValueError("total candidate weight exceeds uint53")
        return self


class DirectSelectorRef(_ConfigRuntimeContract):
    selector_kind: Literal["direct"] = "direct"
    digest: Digest
    ref: ArtifactRef

    @model_validator(mode="after")
    def _ref_binds_payload(self) -> DirectSelectorRef:
        if self.digest != self.ref.sha256:
            raise ValueError("direct selector digest and ref must match")
        if self.ref.media_type != "application/vnd.breadboard.direct-selector+json;version=1":
            raise ValueError("direct selector ref has the wrong media type")
        return self


class WeightedSelectorRef(_ConfigRuntimeContract):
    selector_kind: Literal["weighted"] = "weighted"
    digest: Digest
    ref: ArtifactRef

    @model_validator(mode="after")
    def _ref_binds_payload(self) -> WeightedSelectorRef:
        if self.digest != self.ref.sha256:
            raise ValueError("weighted selector digest and ref must match")
        if self.ref.media_type != "application/vnd.breadboard.config-set+json;version=1":
            raise ValueError("weighted selector ref has the wrong media type")
        return self


SelectorRef = Annotated[
    DirectSelectorRef | WeightedSelectorRef,
    Field(discriminator="selector_kind"),
]


class CandidateEvaluation(_ConfigRuntimeContract):
    candidate_id: CandidateId
    receipt_digest: Digest
    eligible: StrictBool
    exclusion_codes: tuple[Identifier, ...]
    weight: PositiveUInt53 | None

    @model_validator(mode="after")
    def _reason_shape_is_exact(self) -> CandidateEvaluation:
        _require_sorted_unique(
            self.exclusion_codes, key=lambda item: item, field_name="exclusion_codes"
        )
        if self.eligible and self.exclusion_codes:
            raise ValueError("eligible evaluations must not carry exclusion codes")
        if not self.eligible and not self.exclusion_codes:
            raise ValueError("ineligible evaluations require an exclusion code")
        return self


class EligibleCandidate(_ConfigRuntimeContract):
    candidate_id: CandidateId
    receipt_digest: Digest
    weight: PositiveUInt53 | None


class OracleDraw(_ConfigRuntimeContract):
    framing: Literal["ascii-sha256-digests-v1"] = "ascii-sha256-digests-v1"
    preimage_hex: Annotated[str, StringConstraints(strict=True)]
    draw_digest: Digest
    unsigned_big_endian_hex: Annotated[str, StringConstraints(strict=True)]
    total_weight: PositiveUInt53
    modulo: UInt53
    selected_interval_start: UInt53
    selected_interval_end_exclusive: PositiveUInt53

    @model_validator(mode="after")
    def _draw_is_exact(self) -> OracleDraw:
        if _PREIMAGE_HEX_RE.fullmatch(self.preimage_hex) is None:
            raise ValueError("weighted-v1 preimage must be exactly 299 bytes of lowercase hex")
        preimage = bytes.fromhex(self.preimage_hex)
        try:
            decoded = preimage.decode("ascii")
        except UnicodeDecodeError as exc:
            raise ValueError("weighted-v1 preimage must be ASCII") from exc
        prefix = "bb-weighted-v1\x00"
        if not decoded.startswith(prefix):
            raise ValueError("weighted-v1 preimage has the wrong framing prefix")
        digest_parts = tuple(
            decoded[len(prefix) + (71 * index) : len(prefix) + (71 * (index + 1))]
            for index in range(4)
        )
        if any(_DIGEST_RE.fullmatch(part) is None for part in digest_parts):
            raise ValueError("weighted-v1 preimage must contain four canonical digests")
        raw_digest = hashlib.sha256(preimage).hexdigest()
        if self.draw_digest != f"sha256:{raw_digest}":
            raise ValueError("draw_digest does not bind the exact preimage")
        if _LOWER_HEX_64_RE.fullmatch(self.unsigned_big_endian_hex) is None:
            raise ValueError("unsigned_big_endian_hex must be 32-byte lowercase hex")
        if self.unsigned_big_endian_hex != raw_digest:
            raise ValueError("unsigned_big_endian_hex must equal the full draw digest bytes")
        expected_modulo = int(raw_digest, 16) % self.total_weight
        if self.modulo != expected_modulo:
            raise ValueError("modulo does not bind the weighted draw")
        if not (
            self.selected_interval_start
            <= self.modulo
            < self.selected_interval_end_exclusive
            <= self.total_weight
        ):
            raise ValueError("selected interval does not contain the draw")
        return self


class SelectionRecord(_ConfigRuntimeContract):
    schema_version: Literal["bb.rl.selection-record.v1"] = SELECTION_RECORD_SCHEMA_VERSION
    algorithm: Literal["direct-v1", "weighted-v1"]
    episode_id: Identifier
    subject_digest: Digest
    selector_digest: Digest
    config_set_digest: Digest | None
    admitted_set_root: Digest
    selection_nonce: Digest | None
    task_contract_digest: Digest
    policy_capability_observation_digest: Digest
    policy_capability_digest: Digest
    revocation_state_digest: Digest
    candidate_evaluations: tuple[CandidateEvaluation, ...]
    eligible_candidates: tuple[EligibleCandidate, ...]
    total_weight: PositiveUInt53 | None
    draw: OracleDraw | None
    selected_candidate_id: CandidateId
    selected_receipt_digest: Digest
    selected_overlays: tuple[AdmittedOverlayRef, ...]

    @model_validator(mode="after")
    def _record_binds_exact_oracle_inputs(self) -> SelectionRecord:
        _require_sorted_unique(
            self.candidate_evaluations,
            key=lambda item: item.candidate_id.encode("ascii"),
            field_name="candidate_evaluations",
            nonempty=True,
        )
        _require_sorted_unique(
            self.eligible_candidates,
            key=lambda item: item.candidate_id.encode("ascii"),
            field_name="eligible_candidates",
            nonempty=True,
        )
        evaluation_by_id = {item.candidate_id: item for item in self.candidate_evaluations}
        expected_eligible = tuple(item for item in self.candidate_evaluations if item.eligible)
        if tuple(item.candidate_id for item in self.eligible_candidates) != tuple(
            item.candidate_id for item in expected_eligible
        ):
            raise ValueError("eligible candidates must exactly match eligible evaluations")
        for candidate in self.eligible_candidates:
            evaluation = evaluation_by_id[candidate.candidate_id]
            if (
                candidate.receipt_digest != evaluation.receipt_digest
                or candidate.weight != evaluation.weight
            ):
                raise ValueError("eligible candidate does not bind its evaluation")
        selected = next(
            (item for item in self.eligible_candidates if item.candidate_id == self.selected_candidate_id),
            None,
        )
        if selected is None or selected.receipt_digest != self.selected_receipt_digest:
            raise ValueError("selected candidate must be one exact eligible candidate")
        if self.algorithm == "direct-v1":
            if any(
                value is not None
                for value in (self.config_set_digest, self.selection_nonce, self.total_weight, self.draw)
            ):
                raise ValueError("direct-v1 forbids set, nonce, weight total, and draw")
            if len(self.candidate_evaluations) != 1 or len(self.eligible_candidates) != 1:
                raise ValueError("direct-v1 binds exactly one eligible candidate")
            if any(item.weight is not None for item in self.candidate_evaluations) or selected.weight is not None:
                raise ValueError("direct-v1 candidate weights must be null")
            return self
        if self.config_set_digest is None or self.selection_nonce is None:
            raise ValueError("weighted-v1 requires config set and selection nonce")
        if self.selector_digest != self.config_set_digest:
            raise ValueError("weighted selector digest must equal config_set_digest")
        if self.total_weight is None or self.draw is None:
            raise ValueError("weighted-v1 requires total_weight and draw")
        evaluation_weights = tuple(item.weight for item in self.candidate_evaluations)
        if any(weight is None for weight in evaluation_weights):
            raise ValueError("weighted-v1 candidate evaluations all require weights")
        weights = tuple(item.weight for item in self.eligible_candidates)
        calculated_total = sum(weight for weight in weights if weight is not None)
        if calculated_total > MAX_UINT53 or calculated_total != self.total_weight:
            raise ValueError("total_weight does not bind eligible candidate weights")
        expected_preimage = (
            b"bb-weighted-v1\x00"
            + self.config_set_digest.encode("ascii")
            + self.selection_nonce.encode("ascii")
            + self.task_contract_digest.encode("ascii")
            + self.policy_capability_digest.encode("ascii")
        )
        if self.draw.preimage_hex != expected_preimage.hex() or self.draw.total_weight != self.total_weight:
            raise ValueError("draw does not bind S/N/T/P and eligible total")
        cursor = 0
        selected_start = 0
        selected_end = 0
        for candidate in self.eligible_candidates:
            assert candidate.weight is not None
            end = cursor + candidate.weight
            if candidate.candidate_id == self.selected_candidate_id:
                selected_start, selected_end = cursor, end
            cursor = end
        if (
            self.draw.selected_interval_start != selected_start
            or self.draw.selected_interval_end_exclusive != selected_end
        ):
            raise ValueError("draw interval does not bind the selected candidate")
        return self


class SelectionBinding(_ConfigRuntimeContract):
    schema_version: Literal["bb.rl.selection-binding.v1"] = SELECTION_BINDING_SCHEMA_VERSION
    owner_key: Digest
    request_digest: Digest
    selection_record_digest: Digest


class SelectionCommitToken(_ConfigRuntimeContract):
    schema_version: Literal["bb.rl.selection-commit.v1"] = SELECTION_COMMIT_SCHEMA_VERSION
    binding: SelectionBinding
    binding_ref: ArtifactRef
    verified_at: UtcSecond

    @model_validator(mode="after")
    def _commit_binds_readback(self) -> SelectionCommitToken:
        if self.binding_ref.sha256 != self.binding.canonical_digest():
            raise ValueError("selection binding ref does not bind canonical binding bytes")
        if self.binding_ref.media_type != "application/vnd.breadboard.selection-binding+json;version=1":
            raise ValueError("selection binding ref has the wrong media type")
        return self


class OverlayOperation(_ConfigRuntimeContract):
    op: Literal["add", "replace", "remove"]
    path: JsonPointer
    value: Any = None

    @field_validator("value", mode="before")
    @classmethod
    def _value_is_immutable_json(cls, value: Any) -> Any:
        return _freeze_json(value, field_name="overlay value")

    @model_validator(mode="after")
    def _operation_shape_is_exact(self) -> OverlayOperation:
        supplied = "value" in self.__pydantic_fields_set__
        if self.op in {"add", "replace"} and not supplied:
            raise ValueError("add and replace require value")
        if self.op == "remove" and supplied:
            raise ValueError("remove forbids value")
        return self

    @model_serializer(mode="plain")
    def _serialize_operation(self) -> dict[str, Any]:
        payload: dict[str, Any] = {"op": self.op, "path": self.path}
        if self.op != "remove":
            payload["value"] = _canonical_projection(self.value)
        return payload


class OverlayTransition(_ConfigRuntimeContract):
    operation_index: UInt53
    before_semantic_digest: Digest
    after_semantic_digest: Digest


class OverlaySourceKind(str, Enum):
    OPERATOR = "operator"
    EXPERIMENT = "experiment"
    OPTIMIZER = "optimizer"


class OverlayProvenance(_ConfigRuntimeContract):
    author_subject_digest: Digest
    source_kind: OverlaySourceKind
    source_artifact_digest: Digest
    rationale_code: Identifier


def _pointer_tokens(pointer: str) -> tuple[str, ...]:
    return tuple(pointer[1:].split("/"))


class MutationOverlayManifest(_ConfigRuntimeContract):
    schema_version: Literal["bb.rl.mutation-overlay.v1"] = MUTATION_OVERLAY_SCHEMA_VERSION
    base_compiled_manifest_digest: Digest
    parent_receipt_digest: Digest
    expected_before_semantic_digest: Digest
    operations: tuple[OverlayOperation, ...]
    expected_transitions: tuple[OverlayTransition, ...]
    expected_after_semantic_digest: Digest
    provenance: OverlayProvenance

    @model_validator(mode="after")
    def _operations_and_transitions_bind(self) -> MutationOverlayManifest:
        if not self.operations:
            raise ValueError("overlay operations must not be empty")
        token_paths = tuple(_pointer_tokens(operation.path) for operation in self.operations)
        for index, path in enumerate(token_paths):
            for other in token_paths[index + 1 :]:
                prefix_length = min(len(path), len(other))
                if path[:prefix_length] == other[:prefix_length]:
                    raise ValueError("overlay operation paths must not duplicate or overlap")
        if len(self.expected_transitions) != len(self.operations):
            raise ValueError("every overlay operation requires one transition")
        before = self.expected_before_semantic_digest
        for index, transition in enumerate(self.expected_transitions):
            if transition.operation_index != index:
                raise ValueError("overlay transitions must use contiguous source order indices")
            if transition.before_semantic_digest != before:
                raise ValueError("overlay transition chain has a before-digest gap")
            before = transition.after_semantic_digest
        if before != self.expected_after_semantic_digest:
            raise ValueError("overlay transition chain does not reach expected after digest")
        return self


class OverlayApplicationRecord(_ConfigRuntimeContract):
    overlay_digest: Digest
    parent_receipt_digest: Digest
    result_receipt_digest: Digest
    before_semantic_digest: Digest
    transitions: tuple[OverlayTransition, ...]
    after_semantic_digest: Digest
    provenance_digest: Digest

    @model_validator(mode="after")
    def _transition_chain_is_complete(self) -> OverlayApplicationRecord:
        if not self.transitions:
            raise ValueError("overlay application transitions must not be empty")
        before = self.before_semantic_digest
        for index, transition in enumerate(self.transitions):
            if transition.operation_index != index or transition.before_semantic_digest != before:
                raise ValueError("overlay application transition chain is not contiguous")
            before = transition.after_semantic_digest
        if before != self.after_semantic_digest:
            raise ValueError("overlay application transitions do not bind after_semantic_digest")
        return self


class EffectiveExecutionPlan(_ConfigRuntimeContract):
    schema_version: Literal["bb.rl.effective-execution-plan.v1"] = (
        EFFECTIVE_EXECUTION_PLAN_SCHEMA_VERSION
    )
    subject_digest: Digest
    base_compiled: CompiledArtifactIdentity
    base_receipt_digest: Digest
    selector_digest: Digest
    config_set_digest: Digest | None
    admitted_set_root: Digest
    selection_record_digest: Digest
    task_eligibility_digest: Digest
    policy_capability_observation_digest: Digest
    policy_capability_digest: Digest
    overlay_applications: tuple[OverlayApplicationRecord, ...]
    final_receipt_digest: Digest
    final_semantic_digest: Digest
    effective_semantics: Any
    effective_capabilities: CapabilityVector
    effective_capability_digest: Digest
    pins: tuple[ArtifactIdentity, ...]
    runner: RunnerGrant
    policy_slots: tuple[PolicySlotGrant, ...]
    sandbox: SandboxGrant
    verifier: VerifierGrant
    task: TaskGrant
    artifacts: ArtifactPolicyGrant
    evidence: PolicyRef
    retention: PolicyRef
    revocation: RevocationBinding

    @field_validator("effective_semantics", mode="before")
    @classmethod
    def _semantics_are_immutable_json(cls, value: Any) -> Any:
        if type(value) not in {dict, _FrozenDict}:
            raise ValueError("effective_semantics must be one closed JSON object")
        return _freeze_compiled_semantics(value)
    @field_serializer("effective_semantics")
    def _serialize_effective_semantics(self, value: Any) -> dict[str, Any]:
        return _canonical_projection(value)

    @model_validator(mode="after")
    def _plan_is_self_consistent(self) -> EffectiveExecutionPlan:
        if self.final_semantic_digest != _canonical_digest(
            {
                "schema": COMPILED_CONFIG_SEMANTIC_SCHEMA_ID,
                "config": self.effective_semantics,
            }
        ):
            raise ValueError("final_semantic_digest does not bind the WP2 semantic equation")
        if self.effective_capability_digest != self.effective_capabilities.canonical_digest():
            raise ValueError("effective_capability_digest does not bind effective capabilities")
        vector = self.effective_capabilities
        if (
            self.runner != vector.runner
            or self.policy_slots != vector.policy_slots
            or self.sandbox != vector.sandbox
            or self.verifier != vector.verifier
            or self.task != vector.task
            or self.artifacts != vector.artifacts
            or self.evidence != vector.evidence
            or self.retention != vector.retention
        ):
            raise ValueError("execution grants must exactly project effective capabilities")
        _require_sorted_unique(
            self.pins,
            key=lambda item: (
                item.kind.value,
                item.logical_id,
                item.content_digest,
                item.qualifier_digest or "",
            ),
            field_name="pins",
        )
        parent_receipt = self.base_receipt_digest
        before_semantic = self.base_compiled.semantic_digest
        for application in self.overlay_applications:
            if (
                application.parent_receipt_digest != parent_receipt
                or application.before_semantic_digest != before_semantic
            ):
                raise ValueError("overlay application chain does not bind its parent state")
            parent_receipt = application.result_receipt_digest
            before_semantic = application.after_semantic_digest
        if parent_receipt != self.final_receipt_digest or before_semantic != self.final_semantic_digest:
            raise ValueError("effective plan final identities do not match overlay chain")
        return self


class ResolveEpisodeRequest(_ConfigRuntimeContract):
    schema_version: Literal["bb.rl.config-resolution-request.v1"] = (
        RESOLVE_EPISODE_REQUEST_SCHEMA_VERSION
    )
    episode_id: Identifier
    subject: AuthenticatedSubject
    selector: SelectorRef
    selection_nonce: Digest | None
    task: TaskEligibilityInput
    policy_binding: PolicyBindingRef
    episode_overlays: tuple[AdmittedOverlayRef, ...]

    @model_validator(mode="after")
    def _nonce_matches_algorithm(self) -> ResolveEpisodeRequest:
        if isinstance(self.selector, DirectSelectorRef) and self.selection_nonce is not None:
            raise ValueError("direct selector forbids selection_nonce")
        if isinstance(self.selector, WeightedSelectorRef) and self.selection_nonce is None:
            raise ValueError("weighted selector requires selection_nonce")
        overlay_keys = tuple(
            (item.overlay_digest, item.result_receipt_digest) for item in self.episode_overlays
        )
        if len(overlay_keys) != len(set(overlay_keys)):
            raise ValueError("episode overlay refs must not repeat")
        return self


class ResolvedEpisodePlan(_ConfigRuntimeContract):
    schema_version: Literal["bb.rl.resolved-episode-plan.v1"] = RESOLVED_EPISODE_PLAN_SCHEMA_VERSION
    episode_id: Identifier
    subject_digest: Digest
    base_receipt_digest: Digest
    final_receipt_digest: Digest
    policy_capability_observation_digest: Digest
    selection_record_ref: ArtifactRef
    selection_commit: SelectionCommitToken
    effective_plan_ref: ArtifactRef
    effective_plan: EffectiveExecutionPlan
    currentness: CurrentnessToken

    @model_validator(mode="after")
    def _resolution_proofs_bind(self) -> ResolvedEpisodePlan:
        plan = self.effective_plan
        binding = self.selection_commit.binding
        expected_owner_key = _canonical_digest(
            {
                "schema_version": "bb.rl.selection-owner.v1",
                "subject_digest": self.subject_digest,
                "episode_id": self.episode_id,
            }
        )
        if binding.owner_key != expected_owner_key:
            raise ValueError("selection binding owner_key does not bind episode and subject")
        if self.selection_record_ref.sha256 != binding.selection_record_digest:
            raise ValueError("selection record ref does not bind committed selection")
        if self.selection_record_ref.media_type != "application/vnd.breadboard.selection-record+json;version=1":
            raise ValueError("selection record ref has the wrong media type")
        if self.effective_plan_ref.sha256 != plan.canonical_digest():
            raise ValueError("effective plan ref does not bind canonical plan bytes")
        if self.effective_plan_ref.media_type != "application/vnd.breadboard.effective-execution-plan+json;version=1":
            raise ValueError("effective plan ref has the wrong media type")
        if (
            self.subject_digest != plan.subject_digest
            or self.base_receipt_digest != plan.base_receipt_digest
            or self.final_receipt_digest != plan.final_receipt_digest
            or self.policy_capability_observation_digest
            != plan.policy_capability_observation_digest
            or binding.selection_record_digest != plan.selection_record_digest
        ):
            raise ValueError("resolved episode fields do not bind the effective plan")
        if (
            self.currentness.receipt_digest != self.final_receipt_digest
            or self.currentness.subject_digest != self.subject_digest
            or self.currentness.checkpoint is not PrivilegedCheckpoint.BEFORE_ALLOCATION
            or self.currentness.revocation_scope_digest != plan.revocation.scope_digest
            or self.currentness.revocation_epoch != plan.revocation.epoch
            or self.currentness.revocation_state_digest != plan.revocation.state_digest
        ):
            raise ValueError("resolved episode requires exact final-receipt pre-allocation currentness")
        return self



AllOf.model_rebuild(_types_namespace={"EligibilityPredicate": EligibilityPredicate})
AnyOf.model_rebuild(_types_namespace={"EligibilityPredicate": EligibilityPredicate})


class DenialStage(str, Enum):
    SUBJECT_AUTHENTICATION = "subject_authentication"
    BUNDLE_INTEGRITY = "bundle_integrity"
    COMPILE_BUDGET = "compile_budget"
    COMPILATION = "compilation"
    COMPILED_ARTIFACT_VERIFICATION = "compiled_artifact_verification"
    REGISTRY_RESOLUTION = "registry_resolution"
    CAPABILITY_INTERSECTION = "capability_intersection"
    IDENTITY_PINNING = "identity_pinning"
    RECEIPT_PUBLICATION = "receipt_publication"
    RECEIPT_RECHECK = "receipt_recheck"
    POLICY_OBSERVATION = "policy_observation"
    SELECTOR_VALIDATION = "selector_validation"
    ELIGIBILITY = "eligibility"
    SELECTION_ORACLE = "selection_oracle"
    SELECTION_PERSISTENCE = "selection_persistence"
    OVERLAY_VALIDATION = "overlay_validation"
    OVERLAY_APPLICATION = "overlay_application"
    READMISSION = "readmission"
    PLAN_PUBLICATION = "plan_publication"
    PRE_ALLOCATION_RECHECK = "pre_allocation_recheck"


class DenialCode(str, Enum):
    UNAUTHENTICATED_SUBJECT = "unauthenticated_subject"
    SUBJECT_SCOPE_MISMATCH = "subject_scope_mismatch"
    UNSUPPORTED_MANIFEST_SCHEMA = "unsupported_manifest_schema"
    UNSUPPORTED_CANONICALIZER = "unsupported_canonicalizer"
    COMPILED_DIGEST_MISMATCH = "compiled_digest_mismatch"
    INCOMPLETE_CAPABILITY_VECTOR = "incomplete_capability_vector"
    INCOMPLETE_TASK_CONTRACT = "incomplete_task_contract"
    INVALID_MUTABLE_POINTER_DECLARATION = "invalid_mutable_pointer_declaration"
    RUNNER_VISIBLE_LOSS = "runner_visible_loss"
    FORBIDDEN_RAW_AUTHORITY = "forbidden_raw_authority"
    REGISTRY_SNAPSHOT_MISMATCH = "registry_snapshot_mismatch"
    UNKNOWN_RUNNER = "unknown_runner"
    UNKNOWN_TOOL = "unknown_tool"
    UNKNOWN_SETUP = "unknown_setup"
    UNKNOWN_ROUTE = "unknown_route"
    UNKNOWN_SECRET_HANDLE = "unknown_secret_handle"
    UNKNOWN_RUNTIME = "unknown_runtime"
    UNKNOWN_IMAGE = "unknown_image"
    UNKNOWN_REPOSITORY_BINDING = "unknown_repository_binding"
    UNKNOWN_TASK = "unknown_task"
    UNKNOWN_DATASET = "unknown_dataset"
    UNKNOWN_MODEL = "unknown_model"
    UNKNOWN_VERIFIER = "unknown_verifier"
    UNKNOWN_EVIDENCE_POLICY = "unknown_evidence_policy"
    UNKNOWN_RETENTION_POLICY = "unknown_retention_policy"
    REGISTRY_BINDING_MISMATCH = "registry_binding_mismatch"
    RESERVED_TOOL_SHADOW = "reserved_tool_shadow"
    DUPLICATE_BINDING = "duplicate_binding"
    FALLBACK_CYCLE = "fallback_cycle"
    UNSUPPORTED_CAPABILITY = "unsupported_capability"
    CAPABILITY_INCREASE = "capability_increase"
    OPERATOR_CEILING_EXCEEDED = "operator_ceiling_exceeded"
    REQUIRED_SECURITY_MISSING = "required_security_missing"
    RETENTION_OUT_OF_BOUNDS = "retention_out_of_bounds"
    MUTABLE_IDENTITY = "mutable_identity"
    UNPINNED_IDENTITY = "unpinned_identity"
    REPOSITORY_IMAGE_MISMATCH = "repository_image_mismatch"
    MODEL_IDENTITY_MISMATCH = "model_identity_mismatch"
    VERIFIER_IDENTITY_MISMATCH = "verifier_identity_mismatch"
    RAW_URL_FORBIDDEN = "raw_url_forbidden"
    RAW_SECRET_FORBIDDEN = "raw_secret_forbidden"
    ENVIRONMENT_AUTHORITY_FORBIDDEN = "environment_authority_forbidden"
    ARBITRARY_SHELL_FORBIDDEN = "arbitrary_shell_forbidden"
    RECEIPT_STORE_UNAVAILABLE = "receipt_store_unavailable"
    RECEIPT_STORE_CONFLICT = "receipt_store_conflict"
    RECEIPT_READBACK_MISMATCH = "receipt_readback_mismatch"
    RECEIPT_FORGED = "receipt_forged"
    RECEIPT_NOT_YET_VALID = "receipt_not_yet_valid"
    RECEIPT_EXPIRED = "receipt_expired"
    RECEIPT_REVOKED = "receipt_revoked"
    RECEIPT_EPOCH_ROLLBACK = "receipt_epoch_rollback"
    RECEIPT_STALE_POLICY = "receipt_stale_policy"
    RECEIPT_CROSS_SUBJECT = "receipt_cross_subject"
    RECEIPT_COMPILED_MISMATCH = "receipt_compiled_mismatch"
    RECEIPT_TASK_MISMATCH = "receipt_task_mismatch"
    RECEIPT_POLICY_BINDING_MISMATCH = "receipt_policy_binding_mismatch"
    RECEIPT_ABI_MISMATCH = "receipt_abi_mismatch"
    UNKNOWN_POLICY_BINDING = "unknown_policy_binding"
    OBSERVATION_UNAVAILABLE = "observation_unavailable"
    ATTESTATION_INVALID = "attestation_invalid"
    ATTESTATION_NOT_YET_VALID = "attestation_not_yet_valid"
    ATTESTATION_EXPIRED = "attestation_expired"
    OBSERVATION_REVOKED = "observation_revoked"
    OBSERVATION_SCOPE_MISMATCH = "observation_scope_mismatch"
    PROTOCOL_MISMATCH = "protocol_mismatch"
    MODEL_MISMATCH = "model_mismatch"
    CHECKPOINT_MISMATCH = "checkpoint_mismatch"
    CAPABILITY_DIGEST_MISMATCH = "capability_digest_mismatch"
    REQUIRED_POLICY_CAPABILITY_MISSING = "required_policy_capability_missing"
    STALE_RUNTIME_POOL = "stale_runtime_pool"
    UNKNOWN_SELECTOR_ALGORITHM = "unknown_selector_algorithm"
    DIRECT_NONCE_FORBIDDEN = "direct_nonce_forbidden"
    WEIGHTED_NONCE_MISSING = "weighted_nonce_missing"
    INVALID_CONFIG_SET = "invalid_config_set"
    EMPTY_CONFIG_SET = "empty_config_set"
    DUPLICATE_CANDIDATE_ID = "duplicate_candidate_id"
    DUPLICATE_CANDIDATE = "duplicate_candidate"
    INVALID_CANDIDATE_ID = "invalid_candidate_id"
    WEIGHT_NOT_INTEGER = "weight_not_integer"
    WEIGHT_NONPOSITIVE = "weight_nonpositive"
    WEIGHT_OVERFLOW = "weight_overflow"
    TOTAL_WEIGHT_OVERFLOW = "total_weight_overflow"
    SET_ABI_MISMATCH = "set_abi_mismatch"
    SET_POLICY_MISMATCH = "set_policy_mismatch"
    SET_ROOT_STALE = "set_root_stale"
    INVALID_PREDICATE = "invalid_predicate"
    INVALID_OVERLAY_REF = "invalid_overlay_ref"
    STALE_CANDIDATE_RECEIPT = "stale_candidate_receipt"
    NO_ELIGIBLE_CANDIDATE = "no_eligible_candidate"
    ORACLE_INPUT_INVALID = "oracle_input_invalid"
    ORACLE_INVARIANT_FAILURE = "oracle_invariant_failure"
    SELECTION_STORE_UNAVAILABLE = "selection_store_unavailable"
    SELECTION_STORE_CONFLICT = "selection_store_conflict"
    SELECTION_IDEMPOTENCY_CONFLICT = "selection_idempotency_conflict"
    SELECTION_RECORD_CORRUPT = "selection_record_corrupt"
    SELECTION_READBACK_MISMATCH = "selection_readback_mismatch"
    OVERLAY_DIGEST_MISMATCH = "overlay_digest_mismatch"
    OVERLAY_BASE_MISMATCH = "overlay_base_mismatch"
    OVERLAY_RECEIPT_MISMATCH = "overlay_receipt_mismatch"
    POINTER_NOT_MUTABLE = "pointer_not_mutable"
    PROTECTED_POINTER = "protected_pointer"
    NONCANONICAL_POINTER = "noncanonical_pointer"
    OVERLAPPING_OPERATIONS = "overlapping_operations"
    OPERATION_NOT_ALLOWED = "operation_not_allowed"
    OVERLAY_VALUE_FORBIDDEN = "overlay_value_forbidden"
    OVERLAY_TRANSITION_MISMATCH = "overlay_transition_mismatch"
    ADD_TARGET_EXISTS = "add_target_exists"
    ADD_PARENT_MISSING = "add_parent_missing"
    ARRAY_ADD_FORBIDDEN = "array_add_forbidden"
    REPLACE_TARGET_MISSING = "replace_target_missing"
    REMOVE_TARGET_MISSING = "remove_target_missing"
    REMOVE_NOT_ALLOWED = "remove_not_allowed"
    OVERLAY_TYPE_MISMATCH = "overlay_type_mismatch"
    OVERLAY_BOUNDS_VIOLATION = "overlay_bounds_violation"
    POST_OVERLAY_SCHEMA_INVALID = "post_overlay_schema_invalid"
    POST_OVERLAY_INVARIANT_INVALID = "post_overlay_invariant_invalid"
    IMPLICIT_DEFAULT_FORBIDDEN = "implicit_default_forbidden"
    DERIVED_RECEIPT_MISMATCH = "derived_receipt_mismatch"
    PLAN_STORE_UNAVAILABLE = "plan_store_unavailable"
    PLAN_STORE_CONFLICT = "plan_store_conflict"
    PLAN_READBACK_MISMATCH = "plan_readback_mismatch"


class RetryDisposition(str, Enum):
    NEVER = "never"
    SAME_INPUT_ONCE = "same_input_once"
    AFTER_CONTROL_PLANE_CHANGE = "after_control_plane_change"


class SideEffectBoundary(str, Enum):
    PRE_ADMISSION = "pre_admission"
    PRE_ALLOCATION = "pre_allocation"
    POST_SELECTION = "post_selection"


class _ConfigRuntimeDenialPayload(_ConfigRuntimeContract):
    schema_version: Literal["bb.rl.config-runtime-denial.v1"] = CONFIG_RUNTIME_DENIAL_SCHEMA_VERSION
    stage: DenialStage
    code: DenialCode
    retry_disposition: RetryDisposition
    episode_id: Identifier | None = None
    subject_digest: Digest | None = None
    artifact_kind: Identifier | None = None
    artifact_digest: Digest | None = None
    policy_digest: Digest | None = None
    schema_digest: Digest | None = None
    candidate_id: Identifier | None = None
    pointer: JsonPointer | None = None
    operation_index: UInt53 | None = None
    selection_record_digest: Digest | None = None
    safe_detail: Annotated[str, StringConstraints(strict=True, max_length=1024)] = ""
    side_effect_boundary: SideEffectBoundary = SideEffectBoundary.PRE_ADMISSION

    @field_validator("safe_detail")
    @classmethod
    def _safe_detail_is_bounded(cls, value: str) -> str:
        if value:
            _validate_text(value, field_name="safe_detail", max_bytes=1024)
        return value


class ConfigRuntimeDenial(Exception):
    """Raised denial with an immutable validated payload and mutable traceback state."""

    __slots__ = ("_payload",)

    stage: DenialStage
    code: DenialCode
    retry_disposition: RetryDisposition
    episode_id: str | None
    subject_digest: str | None
    artifact_kind: str | None
    artifact_digest: str | None
    policy_digest: str | None
    schema_digest: str | None
    candidate_id: str | None
    pointer: str | None
    operation_index: int | None
    selection_record_digest: str | None
    safe_detail: str
    side_effect_boundary: SideEffectBoundary
    schema_version: str

    def __init__(
        self,
        stage: DenialStage,
        code: DenialCode,
        retry_disposition: RetryDisposition,
        episode_id: str | None = None,
        subject_digest: str | None = None,
        artifact_kind: str | None = None,
        artifact_digest: str | None = None,
        policy_digest: str | None = None,
        schema_digest: str | None = None,
        candidate_id: str | None = None,
        pointer: str | None = None,
        operation_index: int | None = None,
        selection_record_digest: str | None = None,
        safe_detail: str = "",
        side_effect_boundary: SideEffectBoundary = SideEffectBoundary.PRE_ADMISSION,
        schema_version: str = CONFIG_RUNTIME_DENIAL_SCHEMA_VERSION,
    ) -> None:
        payload = _ConfigRuntimeDenialPayload(
            schema_version=schema_version,
            stage=stage,
            code=code,
            retry_disposition=retry_disposition,
            episode_id=episode_id,
            subject_digest=subject_digest,
            artifact_kind=artifact_kind,
            artifact_digest=artifact_digest,
            policy_digest=policy_digest,
            schema_digest=schema_digest,
            candidate_id=candidate_id,
            pointer=pointer,
            operation_index=operation_index,
            selection_record_digest=selection_record_digest,
            safe_detail=safe_detail,
            side_effect_boundary=side_effect_boundary,
        )
        object.__setattr__(self, "_payload", payload)
        Exception.__init__(self, f"{payload.stage.value}:{payload.code.value}")

    def __getattr__(self, name: str) -> Any:
        payload = object.__getattribute__(self, "_payload")
        if name in type(payload).model_fields:
            return getattr(payload, name)
        raise AttributeError(name)

    def __setattr__(self, name: str, value: Any) -> None:
        if name == "_payload" and not hasattr(self, "_payload"):
            object.__setattr__(self, name, value)
            return
        if name in {
            "args",
            "__cause__",
            "__context__",
            "__notes__",
            "__suppress_context__",
            "__traceback__",
        }:
            BaseException.__setattr__(self, name, value)
            return
        raise TypeError("ConfigRuntimeDenial payload is frozen")

    @classmethod
    def from_dict(cls, value: Any) -> ConfigRuntimeDenial:
        payload = _ConfigRuntimeDenialPayload.from_dict(value)
        return cls(**payload.model_dump(mode="python"))

    def model_dump(self, *, mode: str = "python", **kwargs: Any) -> dict[str, Any]:
        return self._payload.model_dump(mode=mode, **kwargs)

    def to_canonical_obj(self) -> dict[str, Any]:
        return self._payload.to_canonical_obj()

    def canonical_bytes(self) -> bytes:
        return self._payload.canonical_bytes()

    def canonical_digest(self) -> str:
        return self._payload.canonical_digest()


__all__ += [
    "ADMISSION_POLICY_SCHEMA_VERSION",
    "ADMISSION_RECEIPT_SCHEMA_VERSION",
    "ADMISSION_REQUEST_SCHEMA_VERSION",
    "ARTIFACT_REF_SCHEMA_VERSION",
    "CONFIG_RUNTIME_DENIAL_SCHEMA_VERSION",
    "CURRENTNESS_TOKEN_SCHEMA_VERSION",
    "MAX_UINT53",
    "MAX_UINT64",
    "REGISTRY_SNAPSHOT_SCHEMA_VERSION",
    "OVERLAY_CHAIN_SCHEMA_VERSION",
    "VERIFIED_ADMISSION_SCHEMA_VERSION",
    "AdmissionPolicySnapshot",
    "AdmissionReceipt",
    "AdmissionReceiptRef",
    "AdmissionRequest",
    "ArtifactIdentity",
    "ArtifactKind",
    "ArtifactPolicyGrant",
    "ArtifactRef",
    "AuthenticatedSubject",
    "AuthorityEffect",
    "BehaviorSource",
    "BehaviorSourceKind",
    "CapabilityDelta",
    "CapabilityDimension",
    "CapabilityVector",
    "CompiledArtifactIdentity",
    "CompiledBehaviorSource",
    "CompilerConstraints",
    "CompilerIdentity",
    "ConfigRuntimeDenial",
    "CurrentnessToken",
    "DenialCode",
    "DataClassification",
    "DenialStage",
    "Digest",
    "ImmutableImageDigest",
    "EvidencePolicyRegistryRecord",
    "ExecutionLimits",
    "ImageRegistryRecord",
    "IssuanceAttestation",
    "ModelIdentity",
    "ModelRegistryRecord",
    "MountAccess",
    "MountGrant",
    "MutableOperation",
    "MutablePointerRule",
    "OperatorCeiling",
    "OverlayDerivedBehaviorSource",
    "PinKind",
    "PolicyCapabilityAttestationRecord",
    "PolicyBindingRef",
    "PolicyRef",
    "PolicySlotGrant",
    "PositiveUInt53",
    "PrivilegedCheckpoint",
    "RegistryDigestSet",
    "RegistrySnapshotSet",
    "RepositoryBindingRegistryRecord",
    "RequiredSecurityPolicy",
    "ResourceLimits",
    "RetentionPolicyGrant",
    "RetentionPolicyRegistryRecord",
    "RetryDisposition",
    "RevocationBinding",
    "RouteGrant",
    "RouteRegistryRecord",
    "RouteMethod",
    "RouteOwnerAuthority",
    "RouteScheme",
    "RunnerGrant",
    "RunnerRegistryRecord",
    "RuntimeClass",
    "SandboxBinding",
    "SandboxGrant",
    "SandboxRuntimeRegistryRecord",
    "SecretHandleGrant",
    "SecretHandleRegistryRecord",
    "SetupGrant",
    "SetupRegistryRecord",
    "SetupOutput",
    "SideEffectBoundary",
    "TaskDatasetRegistryRecord",
    "TaskGrant",
    "ToolGrant",
    "ToolRegistryRecord",
    "UInt53",
    "UInt64",
    "UtcSecond",
    "ValidityWindow",
    "VerifiedAdmission",
    "VerifierGrant",
    "VerifierRegistryRecord",
    "ADMITTED_SET_SCHEMA_VERSION",
    "CONFIG_SET_SCHEMA_VERSION",
    "DIRECT_SELECTOR_SCHEMA_VERSION",
    "EFFECTIVE_EXECUTION_PLAN_SCHEMA_VERSION",
    "MUTATION_OVERLAY_SCHEMA_VERSION",
    "POLICY_CAPABILITY_OBSERVATION_SCHEMA_VERSION",
    "RESOLVED_EPISODE_PLAN_SCHEMA_VERSION",
    "RESOLVE_EPISODE_REQUEST_SCHEMA_VERSION",
    "SELECTION_BINDING_SCHEMA_VERSION",
    "SELECTION_COMMIT_SCHEMA_VERSION",
    "SELECTION_RECORD_SCHEMA_VERSION",
    "TASK_ELIGIBILITY_SCHEMA_VERSION",
    "derive_overlay_chain_digest",
    "AdmittedOverlayRef",
    "AdmittedSetManifest",
    "AllOf",
    "AnyOf",
    "ArtifactRolePresent",
    "AttestationKind",
    "AttestationProvenance",
    "CandidateEvaluation",
    "CandidateId",
    "ConfigCandidate",
    "ConfigSetManifest",
    "DirectSelector",
    "DirectSelectorRef",
    "EffectiveExecutionPlan",
    "EligibilityPredicate",
    "EligibleCandidate",
    "Label",
    "MutationOverlayManifest",
    "OracleDraw",
    "OverlayApplicationRecord",
    "OverlayOperation",
    "OverlayProvenance",
    "OverlaySourceKind",
    "OverlayTransition",
    "PolicyBoolEq",
    "PolicyBoolField",
    "PolicyCapabilityObservation",
    "PolicyCapabilityVector",
    "PolicyIntField",
    "PolicyIntGte",
    "PolicySetContainsAll",
    "PolicySetField",
    "ResolveEpisodeRequest",
    "ResolvedEpisodePlan",
    "SelectionBinding",
    "SelectionCommitToken",
    "SelectionRecord",
    "SelectorRef",
    "TaskArtifact",
    "TaskEligibilityInput",
    "TaskLabelEq",
    "TaskLabelIn",
    "WeightedCandidate",
    "WeightedSelectorRef",
]
