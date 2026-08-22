from __future__ import annotations

import hashlib
import json
import math
import re
import unicodedata
from enum import Enum
from types import MappingProxyType
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Final

CONFIG_BUNDLE_SCHEMA: Final = "bb.config_bundle_manifest.v1"
DEPENDENCY_CLOSURE_SCHEMA: Final = "bb.dependency_closure_manifest.v1"
MAX_SAFE_INTEGER: Final = 9_007_199_254_740_991

_SHA256_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
_PERCENT_ESCAPE_RE = re.compile(r"%[0-9A-Fa-f]{2}")
_DRIVE_RE = re.compile(r"^[A-Za-z]:")
_ENTRYPOINT_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")


class BundleError(ValueError):
    """Base class for bundle contract failures."""


class CanonicalJSONError(BundleError):
    """A value cannot be represented by the canonical JSON contract."""


class LogicalPathError(BundleError):
    """A logical member path is unsafe or non-canonical."""


class BundleValidationError(BundleError):
    """A manifest or dependency closure violates its closed schema."""


class BundleLimitError(BundleValidationError):
    """A bounded bundle resource limit was exceeded."""


class BundleSecurityError(BundleValidationError):
    """Unsafe filesystem or archive input was rejected."""


class BundleIntegrityError(BundleValidationError):
    """Declared content and immutable CAS content do not agree."""


class UndeclaredMemberError(BundleIntegrityError, KeyError):
    """A reader request names a member outside the admitted closure."""


def _utf16_sort_key(value: str) -> bytes:
    try:
        return value.encode("utf-16-be", errors="strict")
    except UnicodeEncodeError as exc:
        raise CanonicalJSONError("JSON strings must not contain lone surrogates") from exc


def _encode_string(value: str) -> str:
    try:
        value.encode("utf-8", errors="strict")
        return json.dumps(value, ensure_ascii=False, allow_nan=False)
    except (UnicodeEncodeError, ValueError) as exc:
        raise CanonicalJSONError("JSON strings must be valid Unicode") from exc


def _encode_float(value: float) -> str:
    if not math.isfinite(value):
        raise CanonicalJSONError("NaN and infinity are forbidden")
    if value == 0:
        return "0"

    negative = value < 0
    raw = repr(abs(value)).lower()
    if "e" not in raw:
        if raw.endswith(".0"):
            raw = raw[:-2]
        return ("-" if negative else "") + raw

    mantissa, exponent_text = raw.split("e", 1)
    exponent = int(exponent_text)
    digits = mantissa.replace(".", "")
    decimal_position = 1 + exponent
    if -6 <= exponent < 21:
        if decimal_position <= 0:
            rendered = "0." + ("0" * -decimal_position) + digits
        elif decimal_position >= len(digits):
            rendered = digits + ("0" * (decimal_position - len(digits)))
        else:
            rendered = digits[:decimal_position] + "." + digits[decimal_position:]
    else:
        rendered = digits[0]
        if len(digits) > 1:
            rendered += "." + digits[1:]
        rendered += "e" + ("+" if exponent >= 0 else "-") + str(abs(exponent))
    return ("-" if negative else "") + rendered


def _encode_canonical(value: Any) -> str:
    if value is None:
        return "null"
    if value is True:
        return "true"
    if value is False:
        return "false"
    if type(value) is str:
        return _encode_string(value)
    if type(value) is int:
        try:
            as_float = float(value)
        except OverflowError as error:
            raise CanonicalJSONError("integer is outside the finite binary64 domain") from error
        if as_float != value or as_float in (float("inf"), float("-inf")):
            raise CanonicalJSONError("integer is outside the finite binary64 domain")
        return _encode_float(as_float)
    if type(value) is float:
        return _encode_float(value)
    if isinstance(value, Mapping):
        items: list[tuple[str, Any]] = []
        seen: set[str] = set()
        for key, item in value.items():
            if type(key) is not str:
                raise CanonicalJSONError("JSON object keys must be strings")
            if key in seen:
                raise CanonicalJSONError(f"duplicate JSON object key: {key!r}")
            seen.add(key)
            items.append((key, item))
        items.sort(key=lambda item: _utf16_sort_key(item[0]))
        return "{" + ",".join(
            _encode_string(key) + ":" + _encode_canonical(item)
            for key, item in items
        ) + "}"
    if type(value) in {list, tuple}:
        return "[" + ",".join(_encode_canonical(item) for item in value) + "]"
    raise CanonicalJSONError(f"unsupported JSON value: {type(value).__name__}")
def canonical_json_bytes(value: Any) -> bytes:
    """Encode a JSON value using the RFC 8785/JCS representation."""

    try:
        return _encode_canonical(value).encode("utf-8", errors="strict")
    except UnicodeEncodeError as exc:
        raise CanonicalJSONError("JSON strings must be valid Unicode") from exc


def canonical_json_loads(data: str | bytes | bytearray) -> Any:
    """Decode JSON while rejecting duplicate keys and non-canonical value types."""

    def object_from_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise CanonicalJSONError(f"duplicate JSON object key: {key!r}")
            result[key] = value
        return result

    def invalid_constant(value: str) -> None:
        raise CanonicalJSONError(f"invalid JSON number: {value}")

    try:
        value = json.loads(
            data,
            object_pairs_hook=object_from_pairs,
            parse_constant=invalid_constant,
        )
    except CanonicalJSONError:
        raise
    except (json.JSONDecodeError, TypeError, UnicodeDecodeError) as exc:
        raise CanonicalJSONError("invalid JSON payload") from exc
    canonical_json_bytes(value)
    return value


def canonical_sha256(value: Any) -> str:
    return "sha256:" + hashlib.sha256(canonical_json_bytes(value)).hexdigest()


def bytes_sha256(value: bytes) -> str:
    return "sha256:" + hashlib.sha256(value).hexdigest()


def require_sha256(value: str, field_name: str = "digest") -> str:
    if not isinstance(value, str) or _SHA256_RE.fullmatch(value) is None:
        raise BundleValidationError(
            f"{field_name} must be a full lowercase sha256:<64 hex> digest"
        )
    return value


def normalize_logical_path(path: str) -> str:
    """Return the NFC POSIX-relative representation of a logical member path."""

    if not isinstance(path, str) or not path:
        raise LogicalPathError("logical path must be a non-empty string")
    if "\x00" in path:
        raise LogicalPathError("logical path must not contain NUL")
    if "\\" in path:
        raise LogicalPathError("logical path must use POSIX separators")
    normalized = unicodedata.normalize("NFC", path)
    if normalized.startswith("/") or normalized.startswith("//"):
        raise LogicalPathError("logical path must be relative")
    if _DRIVE_RE.match(normalized):
        raise LogicalPathError("logical path must not contain a drive prefix")
    if _PERCENT_ESCAPE_RE.search(normalized):
        raise LogicalPathError("percent-encoded path components are ambiguous")
    parts = normalized.split("/")
    if any(part in {"", ".", ".."} for part in parts):
        raise LogicalPathError("logical path contains an empty, dot, or parent component")
    if any(any(ord(character) < 0x20 or ord(character) == 0x7F for character in part) for part in parts):
        raise LogicalPathError("logical path contains a control character")
    try:
        normalized.encode("utf-8", errors="strict")
    except UnicodeEncodeError as exc:
        raise LogicalPathError("logical path must be valid Unicode") from exc
    return normalized


def _strict_mapping(value: Any, fields: set[str], type_name: str) -> Mapping[str, Any]:
    if type(value) is not dict:
        raise BundleValidationError(f"{type_name} must be an object")
    if any(type(key) is not str for key in value):
        raise BundleValidationError(f"{type_name} keys must be strings")
    unknown = set(value) - fields
    if unknown:
        raise BundleValidationError(
            f"{type_name} contains unknown fields: {', '.join(sorted(unknown))}"
        )
    return value

def _require_sequence(value: Any, type_name: str) -> Sequence[Any]:
    if not isinstance(value, (list, tuple)):
        raise BundleValidationError(f"{type_name} must be an array")
    return value


def _require_manifest_integer(value: Any, field_name: str) -> int:
    if type(value) is not int or value < 0:
        raise BundleValidationError(f"{field_name} must be a non-negative integer")
    if value > MAX_SAFE_INTEGER:
        raise BundleValidationError(f"{field_name} exceeds the safe integer range")
    return value


def _validate_logical_tree(paths: Iterable[str], type_name: str) -> None:
    folded_paths: set[str] = set()
    for path in paths:
        folded = path.casefold()
        if folded in folded_paths:
            raise BundleValidationError(f"case-fold-colliding {type_name} paths")
        folded_paths.add(folded)
    for folded in folded_paths:
        components = folded.split("/")
        for index in range(1, len(components)):
            if "/".join(components[:index]) in folded_paths:
                raise BundleValidationError(f"a {type_name} member shadows a directory")


@dataclass(frozen=True)
class BundleLimits:
    max_member_bytes: int = 8 * 1024 * 1024
    max_total_bytes: int = 64 * 1024 * 1024
    max_members: int = 4096
    max_path_bytes: int = 1024
    max_path_depth: int = 64
    max_archive_bytes: int = 32 * 1024 * 1024
    max_compression_ratio: int = 100
    max_dependency_edges: int = 16_384
    max_dependency_depth: int = 4096

    def __post_init__(self) -> None:
        for name in (
            "max_member_bytes",
            "max_total_bytes",
            "max_members",
            "max_path_bytes",
            "max_path_depth",
            "max_archive_bytes",
            "max_compression_ratio",
            "max_dependency_edges",
            "max_dependency_depth",
        ):
            value = getattr(self, name)
            if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
                raise BundleValidationError(f"{name} must be a positive integer")
            if value > MAX_SAFE_INTEGER:
                raise BundleValidationError(f"{name} exceeds the safe integer range")
        if self.max_member_bytes > self.max_total_bytes:
            raise BundleValidationError("max_member_bytes must not exceed max_total_bytes")

    def validate_path(self, path: str) -> None:
        if len(path.encode("utf-8")) > self.max_path_bytes:
            raise BundleLimitError("logical path byte limit exceeded")
        if len(path.split("/")) > self.max_path_depth:
            raise BundleLimitError("logical path depth limit exceeded")

    def to_dict(self) -> dict[str, int]:
        return {
            "max_archive_bytes": self.max_archive_bytes,
            "max_compression_ratio": self.max_compression_ratio,
            "max_dependency_depth": self.max_dependency_depth,
            "max_dependency_edges": self.max_dependency_edges,
            "max_member_bytes": self.max_member_bytes,
            "max_members": self.max_members,
            "max_path_bytes": self.max_path_bytes,
            "max_path_depth": self.max_path_depth,
            "max_total_bytes": self.max_total_bytes,
        }

    @classmethod
    def from_dict(cls, value: Any) -> BundleLimits:
        fields = {
            "max_archive_bytes",
            "max_compression_ratio",
            "max_dependency_depth",
            "max_dependency_edges",
            "max_member_bytes",
            "max_members",
            "max_path_bytes",
            "max_path_depth",
            "max_total_bytes",
        }
        raw = _strict_mapping(value, fields, "BundleLimits")
        if set(raw) != fields:
            raise BundleValidationError("BundleLimits requires every limit field")
        return cls(**{name: raw[name] for name in fields})


@dataclass(frozen=True)
class BundleProvenance:
    source_kind: str
    raw_source_digest: str
    source_label: str = ""

    def __post_init__(self) -> None:
        if not isinstance(self.source_kind, str) or not self.source_kind.strip():
            raise BundleValidationError("source_kind must be non-empty")
        require_sha256(self.raw_source_digest, "raw_source_digest")
        if not isinstance(self.source_label, str):
            raise BundleValidationError("source_label must be a string")

    def to_dict(self) -> dict[str, Any]:
        return {
            "raw_source_digest": self.raw_source_digest,
            "source_kind": self.source_kind,
            "source_label": self.source_label,
        }

    @classmethod
    def from_dict(cls, value: Any) -> BundleProvenance:
        raw = _strict_mapping(
            value,
            {"raw_source_digest", "source_kind", "source_label"},
            "BundleProvenance",
        )
        if {"raw_source_digest", "source_kind"} - set(raw):
            raise BundleValidationError("BundleProvenance is missing required fields")
        return cls(
            source_kind=raw["source_kind"],
            raw_source_digest=raw["raw_source_digest"],
            source_label=raw.get("source_label", ""),
        )


@dataclass(frozen=True)
class BundleEntry:
    logical_path: str
    blob_digest: str
    size_bytes: int
    media_type: str = "application/octet-stream"
    mode: int = 0o444
    artifact_id: str = ""

    def __post_init__(self) -> None:
        normalized = normalize_logical_path(self.logical_path)
        if normalized != self.logical_path:
            raise BundleValidationError("bundle entry logical_path must already be NFC-normalized")
        require_sha256(self.blob_digest, "blob_digest")
        if not isinstance(self.size_bytes, int) or isinstance(self.size_bytes, bool) or self.size_bytes < 0:
            raise BundleValidationError("size_bytes must be a non-negative integer")
        if self.size_bytes > MAX_SAFE_INTEGER:
            raise BundleValidationError("size_bytes exceeds the safe integer range")
        if not isinstance(self.media_type, str) or not self.media_type:
            raise BundleValidationError("media_type must be non-empty")
        if self.mode not in {0o444, 0o555}:
            raise BundleValidationError("mode must be an approved read-only mode")
        if not isinstance(self.artifact_id, str):
            raise BundleValidationError("artifact_id must be a string")
        if not self.artifact_id:
            object.__setattr__(self, "artifact_id", self.blob_digest)

    def to_dict(self) -> dict[str, Any]:
        return {
            "artifact_id": self.artifact_id,
            "blob_digest": self.blob_digest,
            "logical_path": self.logical_path,
            "media_type": self.media_type,
            "mode": self.mode,
            "size_bytes": self.size_bytes,
        }

    @classmethod
    def from_dict(cls, value: Any) -> BundleEntry:
        fields = {"artifact_id", "blob_digest", "logical_path", "media_type", "mode", "size_bytes"}
        raw = _strict_mapping(value, fields, "BundleEntry")
        required = {"blob_digest", "logical_path", "media_type", "mode", "size_bytes"}
        if required - set(raw):
            raise BundleValidationError("BundleEntry is missing required fields")
        return cls(
            logical_path=raw["logical_path"],
            blob_digest=raw["blob_digest"],
            size_bytes=raw["size_bytes"],
            media_type=raw["media_type"],
            mode=raw["mode"],
            artifact_id=raw.get("artifact_id", ""),
        )


@dataclass(frozen=True)
class BundleEntrypoint:
    name: str
    logical_path: str

    def __post_init__(self) -> None:
        if not isinstance(self.name, str) or _ENTRYPOINT_RE.fullmatch(self.name) is None:
            raise BundleValidationError("entrypoint name is invalid")
        normalized = normalize_logical_path(self.logical_path)
        if normalized != self.logical_path:
            raise BundleValidationError("entrypoint path must already be NFC-normalized")

    def to_dict(self) -> dict[str, str]:
        return {"logical_path": self.logical_path, "name": self.name}

    @classmethod
    def from_dict(cls, value: Any) -> BundleEntrypoint:
        raw = _strict_mapping(value, {"logical_path", "name"}, "BundleEntrypoint")
        if set(raw) != {"logical_path", "name"}:
            raise BundleValidationError("BundleEntrypoint requires name and logical_path")
        return cls(name=raw["name"], logical_path=raw["logical_path"])


@dataclass(frozen=True)
class ConfigBundleManifest:
    entries: tuple[BundleEntry, ...]
    entrypoints: tuple[BundleEntrypoint, ...]
    provenance: BundleProvenance
    limits: BundleLimits = field(default_factory=BundleLimits)
    schema_version: str = CONFIG_BUNDLE_SCHEMA
    bundle_digest: str = ""

    def __post_init__(self) -> None:
        if self.schema_version != CONFIG_BUNDLE_SCHEMA:
            raise BundleValidationError("unsupported config bundle schema")
        if not isinstance(self.limits, BundleLimits):
            raise BundleValidationError("limits must be BundleLimits")
        if not isinstance(self.provenance, BundleProvenance):
            raise BundleValidationError("provenance must be BundleProvenance")
        entry_values = _require_sequence(self.entries, "entries")
        entrypoint_values = _require_sequence(self.entrypoints, "entrypoints")
        if not entry_values:
            raise BundleValidationError("bundle must contain at least one member")
        if len(entry_values) > self.limits.max_members:
            raise BundleLimitError("bundle member count limit exceeded")
        if len(entrypoint_values) > self.limits.max_members:
            raise BundleLimitError("bundle entrypoint count limit exceeded")
        raw_entries = tuple(entry_values)
        raw_entrypoints = tuple(entrypoint_values)
        if any(not isinstance(entry, BundleEntry) for entry in raw_entries):
            raise BundleValidationError("entries must contain BundleEntry values")
        if any(
            not isinstance(entrypoint, BundleEntrypoint)
            for entrypoint in raw_entrypoints
        ):
            raise BundleValidationError(
                "entrypoints must contain BundleEntrypoint values"
            )
        paths: set[str] = set()
        total_bytes = 0
        for entry in raw_entries:
            self.limits.validate_path(entry.logical_path)
            paths.add(entry.logical_path)
            if entry.size_bytes > self.limits.max_member_bytes:
                raise BundleLimitError("bundle member byte limit exceeded")
            total_bytes += entry.size_bytes
            if total_bytes > self.limits.max_total_bytes:
                raise BundleLimitError("bundle total byte limit exceeded")
        _validate_logical_tree(paths, "bundle")
        if len(paths) != len(raw_entries):
            raise BundleValidationError("duplicate bundle member path")
        if not raw_entrypoints:
            raise BundleValidationError("bundle must declare at least one entrypoint")
        names: set[str] = set()
        for entrypoint in raw_entrypoints:
            if entrypoint.name in names:
                raise BundleValidationError("duplicate bundle entrypoint name")
            if entrypoint.logical_path not in paths:
                raise BundleValidationError("bundle entrypoint is not a declared member")
            names.add(entrypoint.name)
        entries = tuple(sorted(raw_entries, key=lambda entry: entry.logical_path))
        entrypoints = tuple(sorted(raw_entrypoints, key=lambda entry: entry.name))
        object.__setattr__(self, "entries", entries)
        object.__setattr__(self, "entrypoints", entrypoints)
        expected = canonical_sha256(self._identity_dict())
        if self.bundle_digest != "":
            require_sha256(self.bundle_digest, "bundle_digest")
            if self.bundle_digest != expected:
                raise BundleIntegrityError("bundle manifest digest mismatch")
        object.__setattr__(self, "bundle_digest", expected)

    @property
    def total_bytes(self) -> int:
        return sum(entry.size_bytes for entry in self.entries)

    def _identity_dict(self) -> dict[str, Any]:
        return {
            "entries": [entry.to_dict() for entry in self.entries],
            "entrypoints": [entrypoint.to_dict() for entrypoint in self.entrypoints],
            "limits": self.limits.to_dict(),
            "schema_version": self.schema_version,
            "total_bytes": self.total_bytes,
            "total_members": len(self.entries),
        }

    def to_dict(self, *, include_digest: bool = True) -> dict[str, Any]:
        payload = self._identity_dict()
        payload["provenance"] = self.provenance.to_dict()
        if include_digest:
            payload["bundle_digest"] = self.bundle_digest
        return payload

    def canonical_bytes(self) -> bytes:
        return canonical_json_bytes(self.to_dict())

    @classmethod
    def from_dict(cls, value: Any) -> ConfigBundleManifest:
        fields = {
            "bundle_digest",
            "entries",
            "entrypoints",
            "limits",
            "provenance",
            "schema_version",
            "total_bytes",
            "total_members",
        }
        raw = _strict_mapping(value, fields, "ConfigBundleManifest")
        required = fields
        if required - set(raw):
            raise BundleValidationError(
                "ConfigBundleManifest is missing required fields"
            )
        entries_raw = _require_sequence(raw["entries"], "entries")
        entrypoints_raw = _require_sequence(raw["entrypoints"], "entrypoints")
        total_bytes = _require_manifest_integer(raw["total_bytes"], "total_bytes")
        total_members = _require_manifest_integer(
            raw["total_members"], "total_members"
        )
        require_sha256(raw["bundle_digest"], "bundle_digest")
        limits = BundleLimits.from_dict(raw["limits"])
        if len(entries_raw) > limits.max_members:
            raise BundleLimitError("bundle member count limit exceeded")
        if len(entrypoints_raw) > limits.max_members:
            raise BundleLimitError("bundle entrypoint count limit exceeded")
        manifest = cls(
            entries=tuple(BundleEntry.from_dict(item) for item in entries_raw),
            entrypoints=tuple(
                BundleEntrypoint.from_dict(item) for item in entrypoints_raw
            ),
            provenance=BundleProvenance.from_dict(raw["provenance"]),
            limits=limits,
            schema_version=raw["schema_version"],
            bundle_digest=raw["bundle_digest"],
        )
        if total_bytes != manifest.total_bytes or total_members != len(manifest.entries):
            raise BundleIntegrityError("bundle manifest totals mismatch")
        return manifest

    @classmethod
    def from_json(cls, data: str | bytes | bytearray) -> ConfigBundleManifest:
        return cls.from_dict(canonical_json_loads(data))


@dataclass(frozen=True)
class ClosureMember:
    logical_path: str
    artifact_id: str
    blob_digest: str
    size_bytes: int
    media_type: str
    source: str = "bundle"

    def __post_init__(self) -> None:
        normalized = normalize_logical_path(self.logical_path)
        if normalized != self.logical_path:
            raise BundleValidationError("closure member path must already be NFC-normalized")
        if not isinstance(self.artifact_id, str) or not self.artifact_id:
            raise BundleValidationError("closure member artifact_id must be non-empty")
        require_sha256(self.blob_digest, "blob_digest")
        if not isinstance(self.size_bytes, int) or isinstance(self.size_bytes, bool) or self.size_bytes < 0:
            raise BundleValidationError("closure member size_bytes is invalid")
        if self.size_bytes > MAX_SAFE_INTEGER:
            raise BundleValidationError(
                "closure member size_bytes exceeds the safe integer range"
            )
        if not isinstance(self.media_type, str) or not self.media_type:
            raise BundleValidationError("closure member media_type must be non-empty")
        if self.source not in {"bundle", "external"}:
            raise BundleValidationError("closure member source must be bundle or external")

    @classmethod
    def from_bundle_entry(cls, entry: BundleEntry) -> ClosureMember:
        return cls(
            logical_path=entry.logical_path,
            artifact_id=entry.artifact_id,
            blob_digest=entry.blob_digest,
            size_bytes=entry.size_bytes,
            media_type=entry.media_type,
            source="bundle",
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "artifact_id": self.artifact_id,
            "blob_digest": self.blob_digest,
            "logical_path": self.logical_path,
            "media_type": self.media_type,
            "size_bytes": self.size_bytes,
            "source": self.source,
        }

    @classmethod
    def from_dict(cls, value: Any) -> ClosureMember:
        fields = {"artifact_id", "blob_digest", "logical_path", "media_type", "size_bytes", "source"}
        raw = _strict_mapping(value, fields, "ClosureMember")
        if set(raw) != fields:
            raise BundleValidationError("ClosureMember requires every field")
        return cls(**{name: raw[name] for name in fields})


@dataclass(frozen=True)
class DependencyEdge:
    from_path: str
    kind: str
    raw_ref: str
    logical_path: str
    ordinal: int = 0

    def __post_init__(self) -> None:
        for field_name in ("from_path", "logical_path"):
            value = getattr(self, field_name)
            normalized = normalize_logical_path(value)
            if normalized != value:
                raise BundleValidationError(f"dependency edge {field_name} must be normalized")
        if not isinstance(self.kind, str) or not self.kind:
            raise BundleValidationError("dependency edge kind must be non-empty")
        if not isinstance(self.raw_ref, str) or not self.raw_ref:
            raise BundleValidationError("dependency edge raw_ref must be non-empty")
        if not isinstance(self.ordinal, int) or isinstance(self.ordinal, bool) or self.ordinal < 0:
            raise BundleValidationError("dependency edge ordinal must be non-negative")
        if self.ordinal > MAX_SAFE_INTEGER:
            raise BundleValidationError(
                "dependency edge ordinal exceeds the safe integer range"
            )

    def to_dict(self) -> dict[str, Any]:
        return {
            "from_path": self.from_path,
            "kind": self.kind,
            "logical_path": self.logical_path,
            "ordinal": self.ordinal,
            "raw_ref": self.raw_ref,
        }

    @classmethod
    def from_dict(cls, value: Any) -> DependencyEdge:
        fields = {"from_path", "kind", "logical_path", "ordinal", "raw_ref"}
        raw = _strict_mapping(value, fields, "DependencyEdge")
        if set(raw) != fields:
            raise BundleValidationError("DependencyEdge requires every field")
        return cls(**{name: raw[name] for name in fields})


@dataclass(frozen=True)
class DependencyClosureManifest:
    bundle_digest: str
    root_entrypoint: str
    members: tuple[ClosureMember, ...]
    edges: tuple[DependencyEdge, ...] = ()
    limits: BundleLimits = field(default_factory=BundleLimits)
    provenance: tuple[str, ...] = ()
    schema_version: str = DEPENDENCY_CLOSURE_SCHEMA
    closure_digest: str = ""

    def __post_init__(self) -> None:
        if self.schema_version != DEPENDENCY_CLOSURE_SCHEMA:
            raise BundleValidationError("unsupported dependency closure schema")
        require_sha256(self.bundle_digest, "bundle_digest")
        root = normalize_logical_path(self.root_entrypoint)
        if root != self.root_entrypoint:
            raise BundleValidationError(
                "root_entrypoint must already be NFC-normalized"
            )
        if not isinstance(self.limits, BundleLimits):
            raise BundleValidationError("limits must be BundleLimits")
        member_values = _require_sequence(self.members, "members")
        edge_values = _require_sequence(self.edges, "edges")
        provenance_values = _require_sequence(self.provenance, "provenance")
        if len(member_values) > self.limits.max_members:
            raise BundleLimitError("closure member count limit exceeded")
        if len(edge_values) > self.limits.max_dependency_edges:
            raise BundleLimitError("dependency edge count limit exceeded")
        if len(provenance_values) > self.limits.max_dependency_edges:
            raise BundleLimitError("closure provenance count limit exceeded")
        raw_members = tuple(member_values)
        raw_edges = tuple(edge_values)
        raw_provenance = tuple(provenance_values)
        if any(not isinstance(member, ClosureMember) for member in raw_members):
            raise BundleValidationError("members must contain ClosureMember values")
        if any(not isinstance(edge, DependencyEdge) for edge in raw_edges):
            raise BundleValidationError("edges must contain DependencyEdge values")
        if any(not isinstance(item, str) or not item for item in raw_provenance):
            raise BundleValidationError(
                "closure provenance values must be non-empty strings"
            )
        paths: set[str] = set()
        total_bytes = 0
        for member in raw_members:
            self.limits.validate_path(member.logical_path)
            paths.add(member.logical_path)
            if member.size_bytes > self.limits.max_member_bytes:
                raise BundleLimitError("closure member byte limit exceeded")
            total_bytes += member.size_bytes
            if total_bytes > self.limits.max_total_bytes:
                raise BundleLimitError("closure total byte limit exceeded")
        if not raw_members or root not in paths:
            raise BundleValidationError("root entrypoint must be a closure member")
        _validate_logical_tree(
            (member.logical_path for member in raw_members),
            "dependency closure",
        )
        if len(paths) != len(raw_members):
            raise BundleValidationError("duplicate dependency closure member")
        folded_paths: dict[str, str] = {}
        for member in raw_members:
            folded = member.logical_path.casefold()
            if folded in folded_paths:
                raise BundleValidationError(
                    "dependency closure members collide under case folding"
                )
            folded_paths[folded] = member.logical_path
        for folded in folded_paths:
            prefix = folded + "/"
            if any(other.startswith(prefix) for other in folded_paths):
                raise BundleValidationError(
                    "a closure member shadows a folded archive directory"
                )
        edge_keys: set[tuple[str, str, int]] = set()
        ordinal_groups: dict[tuple[str, str], tuple[int, int]] = {}
        adjacency: dict[str, set[str]] = {path: set() for path in paths}
        for edge in raw_edges:
            if edge.from_path not in paths or edge.logical_path not in paths:
                raise BundleValidationError(
                    "dependency edge names an undeclared closure member"
                )
            if edge.from_path == edge.logical_path:
                raise BundleValidationError("self-referential dependency edge")
            key = (edge.from_path, edge.kind, edge.ordinal)
            if key in edge_keys:
                raise BundleValidationError("duplicate dependency edge ordinal")
            edge_keys.add(key)
            group_key = (edge.from_path, edge.kind)
            count, maximum = ordinal_groups.get(group_key, (0, -1))
            ordinal_groups[group_key] = (count + 1, max(maximum, edge.ordinal))
            adjacency[edge.from_path].add(edge.logical_path)
        if any(maximum != count - 1 for count, maximum in ordinal_groups.values()):
            raise BundleValidationError(
                "dependency edge ordinals must be contiguous from zero"
            )

        reachable = {root}
        pending = [root]
        while pending:
            source = pending.pop()
            for target in adjacency[source]:
                if target not in reachable:
                    reachable.add(target)
                    pending.append(target)
        if reachable != paths:
            raise BundleValidationError(
                "dependency closure contains unreachable members"
            )

        indegree = {path: 0 for path in paths}
        for targets in adjacency.values():
            for target in targets:
                indegree[target] += 1
        queue = [path for path, degree in indegree.items() if degree == 0]
        topological: list[str] = []
        cursor = 0
        while cursor < len(queue):
            source = queue[cursor]
            cursor += 1
            topological.append(source)
            for target in adjacency[source]:
                indegree[target] -= 1
                if indegree[target] == 0:
                    queue.append(target)
        if len(topological) != len(paths):
            raise BundleValidationError("dependency closure contains a cycle")
        depth = {root: 0}
        for source in topological:
            source_depth = depth.get(source, 0)
            for target in adjacency[source]:
                target_depth = max(depth.get(target, 0), source_depth + 1)
                if target_depth > self.limits.max_dependency_depth:
                    raise BundleLimitError("dependency depth limit exceeded")
                depth[target] = target_depth

        members = tuple(
            sorted(raw_members, key=lambda member: member.logical_path)
        )
        edges = tuple(
            sorted(
                raw_edges,
                key=lambda edge: (
                    edge.from_path,
                    edge.kind,
                    edge.ordinal,
                    edge.raw_ref,
                    edge.logical_path,
                ),
            )
        )
        provenance = tuple(sorted(raw_provenance))
        object.__setattr__(self, "members", members)
        object.__setattr__(self, "edges", edges)
        object.__setattr__(self, "provenance", provenance)
        expected = canonical_sha256(self.to_dict(include_digest=False))
        if self.closure_digest != "":
            require_sha256(self.closure_digest, "closure_digest")
            if self.closure_digest != expected:
                raise BundleIntegrityError("dependency closure digest mismatch")
        object.__setattr__(self, "closure_digest", expected)

    @property
    def total_bytes(self) -> int:
        return sum(member.size_bytes for member in self.members)

    def to_dict(self, *, include_digest: bool = True) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "bundle_digest": self.bundle_digest,
            "edges": [edge.to_dict() for edge in self.edges],
            "limits": self.limits.to_dict(),
            "members": [member.to_dict() for member in self.members],
            "provenance": list(self.provenance),
            "root_entrypoint": self.root_entrypoint,
            "schema_version": self.schema_version,
            "total_bytes": self.total_bytes,
            "total_members": len(self.members),
        }
        if include_digest:
            payload["closure_digest"] = self.closure_digest
        return payload

    def canonical_bytes(self) -> bytes:
        return canonical_json_bytes(self.to_dict())

    @classmethod
    def from_dict(cls, value: Any) -> DependencyClosureManifest:
        fields = {
            "bundle_digest",
            "closure_digest",
            "edges",
            "limits",
            "members",
            "provenance",
            "root_entrypoint",
            "schema_version",
            "total_bytes",
            "total_members",
        }
        raw = _strict_mapping(value, fields, "DependencyClosureManifest")
        if fields - set(raw):
            raise BundleValidationError(
                "DependencyClosureManifest is missing required fields"
            )
        members_raw = _require_sequence(raw["members"], "members")
        edges_raw = _require_sequence(raw["edges"], "edges")
        provenance_raw = _require_sequence(raw["provenance"], "provenance")
        total_bytes = _require_manifest_integer(raw["total_bytes"], "total_bytes")
        total_members = _require_manifest_integer(
            raw["total_members"], "total_members"
        )
        require_sha256(raw["bundle_digest"], "bundle_digest")
        require_sha256(raw["closure_digest"], "closure_digest")
        limits = BundleLimits.from_dict(raw["limits"])
        if len(members_raw) > limits.max_members:
            raise BundleLimitError("closure member count limit exceeded")
        if len(edges_raw) > limits.max_dependency_edges:
            raise BundleLimitError("dependency edge count limit exceeded")
        if len(provenance_raw) > limits.max_dependency_edges:
            raise BundleLimitError("closure provenance count limit exceeded")
        closure = cls(
            bundle_digest=raw["bundle_digest"],
            root_entrypoint=raw["root_entrypoint"],
            members=tuple(ClosureMember.from_dict(item) for item in members_raw),
            edges=tuple(DependencyEdge.from_dict(item) for item in edges_raw),
            limits=limits,
            provenance=tuple(provenance_raw),
            schema_version=raw["schema_version"],
            closure_digest=raw["closure_digest"],
        )
        if total_bytes != closure.total_bytes or total_members != len(closure.members):
            raise BundleIntegrityError("dependency closure totals mismatch")
        return closure

    @classmethod
    def from_json(cls, data: str | bytes | bytearray) -> DependencyClosureManifest:
        return cls.from_dict(canonical_json_loads(data))



# Compiler-owned public wire identities. These values are part of the digest ABI.
COMPILE_OPTIONS_SCHEMA_ID: Final = "bb.compile-options.v1"
COMPILER_INPUT_SCHEMA_ID: Final = "bb.compiler-input.v1"
COMPILED_CONFIG_SEMANTIC_SCHEMA_ID: Final = "bb.compiled-config-semantic.v1"
COMPILED_CONFIG_MANIFEST_SCHEMA_ID: Final = "bb.compiled-config-manifest.v1"
CONFIG_COMPILE_ERROR_SCHEMA_ID: Final = "bb.config-compile-error.v1"
CONFIG_NODE_ID_SCHEMA_ID: Final = "bb.config-node-id.v1"
PROMPT_VARIANT_ID_SCHEMA_ID: Final = "bb.prompt-variant-id.v1"
SERVER_CONFIG_COMPILER_ID: Final = "breadboard.server-config-compiler"
CANONICALIZER_ID: Final = "rfc8785-jcs-v1"
AGENT_CONFIG_SCHEMA_ID: Final = "breadboard.agent-config.v2"
AGENT_CONFIG_SCHEMA_VERSION: Final = 2
COMPILED_CONFIG_MANIFEST_SCHEMA_VERSION: Final = 1
V1_SHADOW_TRANSLATOR_ID: Final = "breadboard.v1-shadow-translator.v1"
JCS_SAFE_INTEGER_MIN: Final = -MAX_SAFE_INTEGER
JCS_SAFE_INTEGER_MAX: Final = MAX_SAFE_INTEGER
SHA256_DIGEST_RE: Final = _SHA256_RE

Digest = str
JsonValue = Any
JsonObject = Mapping[str, JsonValue]


class _ImmutableJsonObject(Mapping[str, JsonValue]):
    """A recursively immutable JSON object used inside frozen contracts."""

    __slots__ = ("_data", "_hash")

    def __init__(self, items: Mapping[str, JsonValue]) -> None:
        data: dict[str, JsonValue] = {}
        for key, value in items.items():
            if type(key) is not str:
                raise BundleValidationError("JSON object keys must be strings")
            if key in data:
                raise BundleValidationError(f"duplicate JSON object key: {key!r}")
            data[key] = _freeze_json(value)
        self._data = MappingProxyType(data)
        self._hash: int | None = None

    def __getitem__(self, key: str) -> JsonValue:
        return self._data[key]

    def __iter__(self):
        return iter(self._data)

    def __len__(self) -> int:
        return len(self._data)

    def __hash__(self) -> int:
        cached = self._hash
        if cached is None:
            cached = hash(canonical_json_bytes(self))
            self._hash = cached
        return cached


def _freeze_json(value: Any) -> JsonValue:
    if value is None or type(value) is bool:
        canonical_json_bytes(value)
        return value
    if type(value) is int:
        if not JCS_SAFE_INTEGER_MIN <= value <= JCS_SAFE_INTEGER_MAX:
            raise BundleValidationError("integer is outside the safe integer range")
        canonical_json_bytes(value)
        return value
    if type(value) is float:
        if not math.isfinite(value):
            raise BundleValidationError("non-finite floats are forbidden")
        if value == 0.0 and math.copysign(1.0, value) < 0:
            raise BundleValidationError("negative zero is forbidden")
        canonical_json_bytes(value)
        return value
    if type(value) is str:
        canonical_json_bytes(value)
        return value
    if isinstance(value, Mapping):
        if any(type(key) is not str for key in value):
            raise BundleValidationError("JSON object keys must be strings")
        frozen = _ImmutableJsonObject(value)
        canonical_json_bytes(frozen)
        return frozen
    if isinstance(value, (list, tuple)):
        frozen_array = tuple(_freeze_json(item) for item in value)
        canonical_json_bytes(frozen_array)
        return frozen_array
    raise BundleValidationError(
        f"value is not canonical JSON: {type(value).__name__}"
    )


def _freeze_object(value: Any, field_name: str) -> _ImmutableJsonObject:
    if not isinstance(value, Mapping):
        raise BundleValidationError(f"{field_name} must be an object")
    frozen = _freeze_json(value)
    assert isinstance(frozen, _ImmutableJsonObject)
    return frozen


def _thaw_json(value: JsonValue) -> JsonValue:
    if isinstance(value, Mapping):
        return {key: _thaw_json(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_thaw_json(item) for item in value]
    return value


def _compiler_mapping(
    value: Any,
    fields: set[str],
    type_name: str,
    *,
    optional: set[str] | None = None,
) -> Mapping[str, Any]:
    raw = _strict_mapping(value, fields, type_name)
    missing = fields - (optional or set()) - set(raw)
    if missing:
        raise BundleValidationError(
            f"{type_name} is missing required fields: {', '.join(sorted(missing))}"
        )
    return raw


def _require_string(
    value: Any,
    field_name: str,
    *,
    nonempty: bool = False,
    identifier: bool = False,
) -> str:
    if type(value) is not str:
        raise BundleValidationError(f"{field_name} must be a string")
    try:
        value.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise BundleValidationError(f"{field_name} must be valid Unicode") from exc
    if nonempty and not value:
        raise BundleValidationError(f"{field_name} must not be empty")
    if identifier and (
        not value
        or value != value.strip()
        or unicodedata.normalize("NFC", value) != value
    ):
        raise BundleValidationError(
            f"{field_name} must be a nonempty, untrimmed NFC identifier"
        )
    return value


def _require_optional_string(value: Any, field_name: str) -> str | None:
    if value is None:
        return None
    return _require_string(value, field_name)


def _require_bool(value: Any, field_name: str) -> bool:
    if type(value) is not bool:
        raise BundleValidationError(f"{field_name} must be a boolean")
    return value


def _require_integer(
    value: Any,
    field_name: str,
    *,
    minimum: int = JCS_SAFE_INTEGER_MIN,
) -> int:
    if type(value) is not int or not minimum <= value <= JCS_SAFE_INTEGER_MAX:
        raise BundleValidationError(
            f"{field_name} must be an integer in the supported range"
        )
    return value


def _require_optional_integer(
    value: Any,
    field_name: str,
    *,
    minimum: int = 0,
) -> int | None:
    if value is None:
        return None
    return _require_integer(value, field_name, minimum=minimum)


def _require_tuple(value: Any, field_name: str) -> tuple[Any, ...]:
    if type(value) is not tuple:
        raise BundleValidationError(f"{field_name} must be a tuple")
    return value


def _wire_tuple(value: Any, field_name: str) -> tuple[Any, ...]:
    if not isinstance(value, (list, tuple)):
        raise BundleValidationError(f"{field_name} must be an array")
    return tuple(value)


def _require_string_tuple(
    value: Any,
    field_name: str,
    *,
    identifiers: bool = False,
    unique: bool = False,
) -> tuple[str, ...]:
    items = _require_tuple(value, field_name)
    checked = tuple(
        _require_string(item, field_name, nonempty=identifiers, identifier=identifiers)
        for item in items
    )
    if unique and len(set(checked)) != len(checked):
        raise BundleValidationError(f"{field_name} must not contain duplicates")
    return checked


def _require_pointer(value: Any, field_name: str) -> str:
    pointer = _require_string(value, field_name)
    if pointer and not pointer.startswith("/"):
        raise BundleValidationError(f"{field_name} must be a JSON pointer")
    return pointer



def _compiler_dataclass(cls: type[Any]) -> type[Any]:
    """Freeze compiler models and add slots when the runtime supports them."""
    try:
        return dataclass(frozen=True, slots=True)(cls)
    except TypeError as exc:
        if "unexpected keyword argument 'slots'" not in str(exc):
            raise
        return dataclass(frozen=True)(cls)

class _CanonicalContract:
    def to_canonical_obj(self) -> dict[str, JsonValue]:
        raise NotImplementedError

    def to_dict(self) -> dict[str, JsonValue]:
        return self.to_canonical_obj()

    def canonical_bytes(self) -> bytes:
        return canonical_json_bytes(self.to_canonical_obj())


class CompileStage(str, Enum):
    READER_INTEGRITY = "reader_integrity"
    PARSE = "parse"
    DEPENDENCY_RESOLUTION = "dependency_resolution"
    MERGE = "merge"
    TRANSLATION = "translation"
    SCHEMA = "schema"
    REFERENCE_RESOLUTION = "reference_resolution"
    SEMANTIC_VALIDATION = "semantic_validation"
    RENDER = "render"
    CANONICALIZATION = "canonicalization"
    IDENTITY = "identity"


class CompileErrorCode(str, Enum):
    READER_INTEGRITY = "READER_INTEGRITY"
    CLOSURE_MISMATCH = "CLOSURE_MISMATCH"
    SOURCE_UNDECLARED = "SOURCE_UNDECLARED"
    SOURCE_MISSING = "SOURCE_MISSING"
    SOURCE_INTEGRITY = "SOURCE_INTEGRITY"
    SOURCE_LIMIT_EXCEEDED = "SOURCE_LIMIT_EXCEEDED"
    UTF8_INVALID = "UTF8_INVALID"
    DOCUMENT_NOT_MAPPING = "DOCUMENT_NOT_MAPPING"
    DUPLICATE_MAPPING_KEY = "DUPLICATE_MAPPING_KEY"
    UNSUPPORTED_YAML_TAG = "UNSUPPORTED_YAML_TAG"
    UNSUPPORTED_YAML_SCALAR = "UNSUPPORTED_YAML_SCALAR"
    INVALID_JSON = "INVALID_JSON"
    NUMBER_OUT_OF_RANGE = "NUMBER_OUT_OF_RANGE"
    REFERENCE_INVALID = "REFERENCE_INVALID"
    REFERENCE_UNDECLARED = "REFERENCE_UNDECLARED"
    REFERENCE_MISSING = "REFERENCE_MISSING"
    REFERENCE_AMBIGUOUS = "REFERENCE_AMBIGUOUS"
    REFERENCE_CYCLE = "REFERENCE_CYCLE"
    DEPENDENCY_DEPTH_EXCEEDED = "DEPENDENCY_DEPTH_EXCEEDED"
    RESOURCE_LIMIT_EXCEEDED = "RESOURCE_LIMIT_EXCEEDED"
    MERGE_TYPE_INVALID = "MERGE_TYPE_INVALID"
    V1_TRANSLATION_UNSUPPORTED = "V1_TRANSLATION_UNSUPPORTED"
    V1_TRANSLATION_LOSS_FORBIDDEN = "V1_TRANSLATION_LOSS_FORBIDDEN"
    SHADOW_PROJECTION_MISMATCH = "SHADOW_PROJECTION_MISMATCH"
    SCHEMA_VERSION_UNSUPPORTED = "SCHEMA_VERSION_UNSUPPORTED"
    SCHEMA_UNKNOWN_FIELD = "SCHEMA_UNKNOWN_FIELD"
    SCHEMA_TYPE_MISMATCH = "SCHEMA_TYPE_MISMATCH"
    SCHEMA_INVALID_VALUE = "SCHEMA_INVALID_VALUE"
    FORBIDDEN_AUTHORITY = "FORBIDDEN_AUTHORITY"
    TASK_CONTRACT_MISMATCH = "TASK_CONTRACT_MISMATCH"
    PROMPT_PACK_UNKNOWN = "PROMPT_PACK_UNKNOWN"
    PROMPT_KEY_UNKNOWN = "PROMPT_KEY_UNKNOWN"
    PROMPT_MODE_UNKNOWN = "PROMPT_MODE_UNKNOWN"
    PROMPT_DIALECT_UNKNOWN = "PROMPT_DIALECT_UNKNOWN"
    PROMPT_TEMPLATE_INVALID = "PROMPT_TEMPLATE_INVALID"
    PROMPT_RENDER_FAILED = "PROMPT_RENDER_FAILED"
    TOOL_INVALID = "TOOL_INVALID"
    TOOL_DUPLICATE_ID = "TOOL_DUPLICATE_ID"
    TOOL_DUPLICATE_NAME = "TOOL_DUPLICATE_NAME"
    TOOL_OVERLAY_INVALID = "TOOL_OVERLAY_INVALID"
    TOOL_OVERLAY_TARGET_UNKNOWN = "TOOL_OVERLAY_TARGET_UNKNOWN"
    TOOL_ALIAS_TARGET_UNKNOWN = "TOOL_ALIAS_TARGET_UNKNOWN"
    TOOL_SELECTION_UNKNOWN = "TOOL_SELECTION_UNKNOWN"
    TOOL_BINDING_INVALID = "TOOL_BINDING_INVALID"
    TOOL_BINDING_CYCLE = "TOOL_BINDING_CYCLE"
    PLUGIN_INVALID = "PLUGIN_INVALID"
    PLUGIN_DUPLICATE_ID = "PLUGIN_DUPLICATE_ID"
    PLUGIN_TRUST_UNDECLARED = "PLUGIN_TRUST_UNDECLARED"
    PLUGIN_SKILL_INVALID = "PLUGIN_SKILL_INVALID"
    PLUGIN_RUNTIME_FORBIDDEN = "PLUGIN_RUNTIME_FORBIDDEN"
    GUARDRAIL_INVALID = "GUARDRAIL_INVALID"
    GUARDRAIL_DUPLICATE_ID = "GUARDRAIL_DUPLICATE_ID"
    GUARDRAIL_OVERRIDE_TARGET_UNKNOWN = "GUARDRAIL_OVERRIDE_TARGET_UNKNOWN"
    GUARDRAIL_TEMPLATE_INVALID = "GUARDRAIL_TEMPLATE_INVALID"
    TEAM_INVALID = "TEAM_INVALID"
    TEAM_CONFIG_CYCLE = "TEAM_CONFIG_CYCLE"
    TASK_SOURCE_INVALID = "TASK_SOURCE_INVALID"
    PROVIDER_INVALID = "PROVIDER_INVALID"
    PROVIDER_FALLBACK_CYCLE = "PROVIDER_FALLBACK_CYCLE"
    RUNTIME_SLOT_INVALID = "RUNTIME_SLOT_INVALID"
    CANONICALIZATION_INVALID = "CANONICALIZATION_INVALID"
    COMPILER_INPUT_MISMATCH = "COMPILER_INPUT_MISMATCH"
    MANIFEST_IDENTITY_MISMATCH = "MANIFEST_IDENTITY_MISMATCH"


@dataclass(frozen=True, slots=True)
class ConfigCompileError(Exception, _CanonicalContract):
    stage: CompileStage
    code: CompileErrorCode
    logical_path: str | None = None
    instance_pointer: str | None = None
    dependency_kind: str | None = None
    raw_reference: str | None = None
    related_logical_paths: tuple[str, ...] = ()
    details: JsonObject = field(default_factory=dict)
    schema_id: str = CONFIG_COMPILE_ERROR_SCHEMA_ID


    def __post_init__(self) -> None:
        if self.schema_id != CONFIG_COMPILE_ERROR_SCHEMA_ID:
            raise BundleValidationError("unsupported config compile error schema")
        if not isinstance(self.stage, CompileStage):
            raise BundleValidationError("stage must be CompileStage")
        if not isinstance(self.code, CompileErrorCode):
            raise BundleValidationError("code must be CompileErrorCode")
        if self.logical_path is not None:
            logical_path = normalize_logical_path(self.logical_path)
            if logical_path != self.logical_path:
                raise BundleValidationError("logical_path must already be normalized")
        if self.instance_pointer is not None:
            _require_pointer(self.instance_pointer, "instance_pointer")
        if self.dependency_kind is not None:
            _require_string(self.dependency_kind, "dependency_kind", identifier=True)
        _require_optional_string(self.raw_reference, "raw_reference")
        paths = _require_string_tuple(
            self.related_logical_paths,
            "related_logical_paths",
            identifiers=True,
            unique=True,
        )
        normalized_paths = tuple(normalize_logical_path(path) for path in paths)
        if normalized_paths != paths:
            raise BundleValidationError("related_logical_paths must already be normalized")
        if paths != tuple(sorted(paths)):
            raise BundleValidationError("related_logical_paths must be sorted")
        object.__setattr__(self, "details", _freeze_object(self.details, "details"))
        Exception.__init__(self, f"{self.stage.value}:{self.code.value}")

    def to_canonical_obj(self) -> dict[str, JsonValue]:
        return {
            "schema_id": self.schema_id,
            "stage": self.stage.value,
            "code": self.code.value,
            "logical_path": self.logical_path,
            "instance_pointer": self.instance_pointer,
            "dependency_kind": self.dependency_kind,
            "raw_reference": self.raw_reference,
            "related_logical_paths": list(self.related_logical_paths),
            "details": _thaw_json(self.details),
        }

    @classmethod
    def from_dict(cls, value: Any) -> ConfigCompileError:
        fields = {
            "schema_id", "stage", "code", "logical_path", "instance_pointer",
            "dependency_kind", "raw_reference", "related_logical_paths", "details",
        }
        raw = _compiler_mapping(value, fields, "ConfigCompileError")
        try:
            stage = CompileStage(raw["stage"])
            code = CompileErrorCode(raw["code"])
        except (TypeError, ValueError) as exc:
            raise BundleValidationError("unknown compile error stage or code") from exc
        return cls(
            schema_id=raw["schema_id"],
            stage=stage,
            code=code,
            logical_path=raw["logical_path"],
            instance_pointer=raw["instance_pointer"],
            dependency_kind=raw["dependency_kind"],
            raw_reference=raw["raw_reference"],
            related_logical_paths=_wire_tuple(
                raw["related_logical_paths"], "related_logical_paths"
            ),
            details=raw["details"],
        )


@_compiler_dataclass
class CompileTarget(_CanonicalContract):
    runner_adapter_id: str
    runtime_abi: str

    def __post_init__(self) -> None:
        _require_string(self.runner_adapter_id, "runner_adapter_id", identifier=True)
        _require_string(self.runtime_abi, "runtime_abi", identifier=True)

    def to_canonical_obj(self) -> dict[str, JsonValue]:
        return {
            "runner_adapter_id": self.runner_adapter_id,
            "runtime_abi": self.runtime_abi,
        }

    @classmethod
    def from_dict(cls, value: Any) -> CompileTarget:
        fields = {"runner_adapter_id", "runtime_abi"}
        raw = _compiler_mapping(value, fields, "CompileTarget")
        return cls(**{name: raw[name] for name in fields})


@_compiler_dataclass
class TaskArtifactContract(_CanonicalContract):
    artifact_id: str
    direction: str
    media_types: tuple[str, ...]
    required: bool
    cardinality: str
    max_bytes: int | None

    def __post_init__(self) -> None:
        _require_string(self.artifact_id, "artifact_id", identifier=True)
        if self.direction not in {"input", "output"}:
            raise BundleValidationError("direction must be input or output")
        media_types = _require_string_tuple(
            self.media_types, "media_types", identifiers=True, unique=True
        )
        if not media_types:
            raise BundleValidationError("media_types must not be empty")
        _require_bool(self.required, "required")
        if self.cardinality not in {"one", "zero_or_one", "many"}:
            raise BundleValidationError("unsupported artifact cardinality")
        _require_optional_integer(self.max_bytes, "max_bytes", minimum=1)

    def to_canonical_obj(self) -> dict[str, JsonValue]:
        return {
            "artifact_id": self.artifact_id,
            "direction": self.direction,
            "media_types": list(self.media_types),
            "required": self.required,
            "cardinality": self.cardinality,
            "max_bytes": self.max_bytes,
        }

    @classmethod
    def from_dict(cls, value: Any) -> TaskArtifactContract:
        fields = {
            "artifact_id", "direction", "media_types", "required",
            "cardinality", "max_bytes",
        }
        raw = _compiler_mapping(value, fields, "TaskArtifactContract")
        return cls(
            artifact_id=raw["artifact_id"],
            direction=raw["direction"],
            media_types=_wire_tuple(raw["media_types"], "media_types"),
            required=raw["required"],
            cardinality=raw["cardinality"],
            max_bytes=raw["max_bytes"],
        )


@_compiler_dataclass
class TaskVerifierContract(_CanonicalContract):
    binding_id: str | None
    input_artifact_ids: tuple[str, ...]
    result_schema: JsonObject | None
    timeout_ms: int | None

    def __post_init__(self) -> None:
        if self.binding_id is not None:
            _require_string(self.binding_id, "binding_id", identifier=True)
        _require_string_tuple(
            self.input_artifact_ids,
            "input_artifact_ids",
            identifiers=True,
            unique=True,
        )
        if self.result_schema is not None:
            object.__setattr__(
                self, "result_schema", _freeze_object(self.result_schema, "result_schema")
            )
        _require_optional_integer(self.timeout_ms, "timeout_ms", minimum=1)

    def to_canonical_obj(self) -> dict[str, JsonValue]:
        return {
            "binding_id": self.binding_id,
            "input_artifact_ids": list(self.input_artifact_ids),
            "result_schema": (
                None if self.result_schema is None else _thaw_json(self.result_schema)
            ),
            "timeout_ms": self.timeout_ms,
        }

    @classmethod
    def from_dict(cls, value: Any) -> TaskVerifierContract:
        fields = {"binding_id", "input_artifact_ids", "result_schema", "timeout_ms"}
        raw = _compiler_mapping(value, fields, "TaskVerifierContract")
        return cls(
            binding_id=raw["binding_id"],
            input_artifact_ids=_wire_tuple(
                raw["input_artifact_ids"], "input_artifact_ids"
            ),
            result_schema=raw["result_schema"],
            timeout_ms=raw["timeout_ms"],
        )


@_compiler_dataclass
class TaskEvidenceContract(_CanonicalContract):
    required_event_types: tuple[str, ...]
    required_artifact_ids: tuple[str, ...]

    def __post_init__(self) -> None:
        _require_string_tuple(
            self.required_event_types,
            "required_event_types",
            identifiers=True,
            unique=True,
        )
        _require_string_tuple(
            self.required_artifact_ids,
            "required_artifact_ids",
            identifiers=True,
            unique=True,
        )

    def to_canonical_obj(self) -> dict[str, JsonValue]:
        return {
            "required_event_types": list(self.required_event_types),
            "required_artifact_ids": list(self.required_artifact_ids),
        }

    @classmethod
    def from_dict(cls, value: Any) -> TaskEvidenceContract:
        fields = {"required_event_types", "required_artifact_ids"}
        raw = _compiler_mapping(value, fields, "TaskEvidenceContract")
        return cls(
            required_event_types=_wire_tuple(
                raw["required_event_types"], "required_event_types"
            ),
            required_artifact_ids=_wire_tuple(
                raw["required_artifact_ids"], "required_artifact_ids"
            ),
        )


@_compiler_dataclass
class TaskRetentionContract(_CanonicalContract):
    retention_class_id: str
    minimum_retention_seconds: int | None

    def __post_init__(self) -> None:
        _require_string(
            self.retention_class_id, "retention_class_id", identifier=True
        )
        _require_optional_integer(
            self.minimum_retention_seconds,
            "minimum_retention_seconds",
            minimum=0,
        )

    def to_canonical_obj(self) -> dict[str, JsonValue]:
        return {
            "retention_class_id": self.retention_class_id,
            "minimum_retention_seconds": self.minimum_retention_seconds,
        }

    @classmethod
    def from_dict(cls, value: Any) -> TaskRetentionContract:
        fields = {"retention_class_id", "minimum_retention_seconds"}
        raw = _compiler_mapping(value, fields, "TaskRetentionContract")
        return cls(**{name: raw[name] for name in fields})


@_compiler_dataclass
class TaskContract(_CanonicalContract):
    contract_id: str
    parameter_schema: JsonObject
    artifacts: tuple[TaskArtifactContract, ...]
    verifier: TaskVerifierContract
    evidence: TaskEvidenceContract
    retention: TaskRetentionContract

    def __post_init__(self) -> None:
        _require_string(self.contract_id, "contract_id", identifier=True)
        object.__setattr__(
            self,
            "parameter_schema",
            _freeze_object(self.parameter_schema, "parameter_schema"),
        )
        artifacts = _require_tuple(self.artifacts, "artifacts")
        if any(not isinstance(item, TaskArtifactContract) for item in artifacts):
            raise BundleValidationError(
                "artifacts must contain TaskArtifactContract values"
            )
        artifact_ids = tuple(item.artifact_id for item in artifacts)
        if len(set(artifact_ids)) != len(artifact_ids):
            raise BundleValidationError("artifact IDs must be unique")
        if not isinstance(self.verifier, TaskVerifierContract):
            raise BundleValidationError("verifier must be TaskVerifierContract")
        if not set(self.verifier.input_artifact_ids).issubset(artifact_ids):
            raise BundleValidationError("verifier names an unknown input artifact")
        if not isinstance(self.evidence, TaskEvidenceContract):
            raise BundleValidationError("evidence must be TaskEvidenceContract")
        if not set(self.evidence.required_artifact_ids).issubset(artifact_ids):
            raise BundleValidationError("evidence names an unknown artifact")
        if not isinstance(self.retention, TaskRetentionContract):
            raise BundleValidationError("retention must be TaskRetentionContract")

    def to_canonical_obj(self) -> dict[str, JsonValue]:
        return {
            "contract_id": self.contract_id,
            "parameter_schema": _thaw_json(self.parameter_schema),
            "artifacts": [item.to_canonical_obj() for item in self.artifacts],
            "verifier": self.verifier.to_canonical_obj(),
            "evidence": self.evidence.to_canonical_obj(),
            "retention": self.retention.to_canonical_obj(),
        }

    @classmethod
    def from_dict(cls, value: Any) -> TaskContract:
        fields = {
            "contract_id", "parameter_schema", "artifacts", "verifier",
            "evidence", "retention",
        }
        raw = _compiler_mapping(value, fields, "TaskContract")
        artifacts = _wire_tuple(raw["artifacts"], "artifacts")
        return cls(
            contract_id=raw["contract_id"],
            parameter_schema=raw["parameter_schema"],
            artifacts=tuple(TaskArtifactContract.from_dict(item) for item in artifacts),
            verifier=TaskVerifierContract.from_dict(raw["verifier"]),
            evidence=TaskEvidenceContract.from_dict(raw["evidence"]),
            retention=TaskRetentionContract.from_dict(raw["retention"]),
        )


@_compiler_dataclass
class CompileOptions(_CanonicalContract):
    target: CompileTarget
    task_contract: TaskContract
    source_contract: str = "v2"
    v1_loss_policy: str = "reject_all"
    schema_id: str = COMPILE_OPTIONS_SCHEMA_ID

    def __post_init__(self) -> None:
        if self.schema_id != COMPILE_OPTIONS_SCHEMA_ID:
            raise BundleValidationError("unsupported compile options schema")
        if self.source_contract not in {"v2", "v1_shadow"}:
            raise BundleValidationError("unsupported source_contract")
        if self.v1_loss_policy not in {
            "reject_all", "allow_enumerated_nonsemantic"
        }:
            raise BundleValidationError("unsupported v1_loss_policy")
        if self.source_contract == "v2" and self.v1_loss_policy != "reject_all":
            raise BundleValidationError("v2 compilation requires reject_all loss policy")
        if not isinstance(self.target, CompileTarget):
            raise BundleValidationError("target must be CompileTarget")
        if not isinstance(self.task_contract, TaskContract):
            raise BundleValidationError("task_contract must be TaskContract")

    def to_canonical_obj(self) -> dict[str, JsonValue]:
        return {
            "schema_id": self.schema_id,
            "source_contract": self.source_contract,
            "target": self.target.to_canonical_obj(),
            "task_contract": self.task_contract.to_canonical_obj(),
            "v1_loss_policy": self.v1_loss_policy,
        }

    @classmethod
    def from_dict(cls, value: Any) -> CompileOptions:
        fields = {
            "schema_id", "source_contract", "target", "task_contract",
            "v1_loss_policy",
        }
        raw = _compiler_mapping(value, fields, "CompileOptions")
        return cls(
            schema_id=raw["schema_id"],
            source_contract=raw["source_contract"],
            target=CompileTarget.from_dict(raw["target"]),
            task_contract=TaskContract.from_dict(raw["task_contract"]),
            v1_loss_policy=raw["v1_loss_policy"],
        )


@_compiler_dataclass
class CompilerIdentity(_CanonicalContract):
    compiler_version: str
    compiler_code_digest: Digest
    config_schema_digest: Digest
    manifest_schema_digest: Digest
    runtime_abi: str
    compiler_id: str = SERVER_CONFIG_COMPILER_ID
    config_schema_id: str = AGENT_CONFIG_SCHEMA_ID
    config_schema_version: int = AGENT_CONFIG_SCHEMA_VERSION
    manifest_schema_id: str = COMPILED_CONFIG_MANIFEST_SCHEMA_ID
    manifest_schema_version: int = COMPILED_CONFIG_MANIFEST_SCHEMA_VERSION
    canonicalizer_id: str = CANONICALIZER_ID

    def __post_init__(self) -> None:
        if self.compiler_id != SERVER_CONFIG_COMPILER_ID:
            raise BundleValidationError("unsupported compiler_id")
        if self.config_schema_id != AGENT_CONFIG_SCHEMA_ID:
            raise BundleValidationError("unsupported config_schema_id")
        if type(self.config_schema_version) is not int or self.config_schema_version != AGENT_CONFIG_SCHEMA_VERSION:
            raise BundleValidationError("unsupported config_schema_version")
        if self.manifest_schema_id != COMPILED_CONFIG_MANIFEST_SCHEMA_ID:
            raise BundleValidationError("unsupported manifest_schema_id")
        if type(self.manifest_schema_version) is not int or self.manifest_schema_version != COMPILED_CONFIG_MANIFEST_SCHEMA_VERSION:
            raise BundleValidationError("unsupported manifest_schema_version")
        if self.canonicalizer_id != CANONICALIZER_ID:
            raise BundleValidationError("unsupported canonicalizer_id")
        _require_string(self.compiler_version, "compiler_version", identifier=True)
        require_sha256(self.compiler_code_digest, "compiler_code_digest")
        require_sha256(self.config_schema_digest, "config_schema_digest")
        require_sha256(self.manifest_schema_digest, "manifest_schema_digest")
        _require_string(self.runtime_abi, "runtime_abi", identifier=True)

    def to_canonical_obj(self) -> dict[str, JsonValue]:
        return {
            "compiler_id": self.compiler_id,
            "compiler_version": self.compiler_version,
            "compiler_code_digest": self.compiler_code_digest,
            "config_schema_id": self.config_schema_id,
            "config_schema_version": self.config_schema_version,
            "config_schema_digest": self.config_schema_digest,
            "manifest_schema_id": self.manifest_schema_id,
            "manifest_schema_version": self.manifest_schema_version,
            "manifest_schema_digest": self.manifest_schema_digest,
            "canonicalizer_id": self.canonicalizer_id,
            "runtime_abi": self.runtime_abi,
        }

    @classmethod
    def from_dict(cls, value: Any) -> CompilerIdentity:
        fields = {
            "compiler_id", "compiler_version", "compiler_code_digest",
            "config_schema_id", "config_schema_version", "config_schema_digest",
            "manifest_schema_id", "manifest_schema_version",
            "manifest_schema_digest", "canonicalizer_id", "runtime_abi",
        }
        raw = _compiler_mapping(value, fields, "CompilerIdentity")
        return cls(**{name: raw[name] for name in fields})


@_compiler_dataclass
class CompileInputIdentity(_CanonicalContract):
    bundle_digest: Digest
    closure_digest: Digest
    entrypoint: str
    options: CompileOptions
    compiler_input_digest: Digest

    def __post_init__(self) -> None:
        require_sha256(self.bundle_digest, "bundle_digest")
        require_sha256(self.closure_digest, "closure_digest")
        entrypoint = normalize_logical_path(self.entrypoint)
        if entrypoint != self.entrypoint:
            raise BundleValidationError("entrypoint must already be normalized")
        if not isinstance(self.options, CompileOptions):
            raise BundleValidationError("options must be CompileOptions")
        require_sha256(self.compiler_input_digest, "compiler_input_digest")

    def to_canonical_obj(self) -> dict[str, JsonValue]:
        return {
            "bundle_digest": self.bundle_digest,
            "closure_digest": self.closure_digest,
            "entrypoint": self.entrypoint,
            "options": self.options.to_canonical_obj(),
            "compiler_input_digest": self.compiler_input_digest,
        }

    @classmethod
    def from_dict(cls, value: Any) -> CompileInputIdentity:
        fields = {
            "bundle_digest", "closure_digest", "entrypoint", "options",
            "compiler_input_digest",
        }
        raw = _compiler_mapping(value, fields, "CompileInputIdentity")
        return cls(
            bundle_digest=raw["bundle_digest"],
            closure_digest=raw["closure_digest"],
            entrypoint=raw["entrypoint"],
            options=CompileOptions.from_dict(raw["options"]),
            compiler_input_digest=raw["compiler_input_digest"],
        )


@_compiler_dataclass
class SourceDependency(_CanonicalContract):
    dependency_kind: str
    from_logical_path: str | None
    raw_reference: str | None
    logical_path: str
    blob_digest: Digest
    size_bytes: int
    media_type: str

    def __post_init__(self) -> None:
        _require_string(self.dependency_kind, "dependency_kind", identifier=True)
        if self.from_logical_path is not None:
            if normalize_logical_path(self.from_logical_path) != self.from_logical_path:
                raise BundleValidationError("from_logical_path must be normalized")
        _require_optional_string(self.raw_reference, "raw_reference")
        if normalize_logical_path(self.logical_path) != self.logical_path:
            raise BundleValidationError("logical_path must be normalized")
        require_sha256(self.blob_digest, "blob_digest")
        _require_integer(self.size_bytes, "size_bytes", minimum=0)
        _require_string(self.media_type, "media_type", identifier=True)

    @property
    def sort_key(self) -> tuple[str, str, str, str]:
        return (
            self.logical_path,
            self.dependency_kind,
            self.from_logical_path or "",
            self.raw_reference or "",
        )

    def to_canonical_obj(self) -> dict[str, JsonValue]:
        return {
            "dependency_kind": self.dependency_kind,
            "from_logical_path": self.from_logical_path,
            "raw_reference": self.raw_reference,
            "logical_path": self.logical_path,
            "blob_digest": self.blob_digest,
            "size_bytes": self.size_bytes,
            "media_type": self.media_type,
        }

    @classmethod
    def from_dict(cls, value: Any) -> SourceDependency:
        fields = {
            "dependency_kind", "from_logical_path", "raw_reference",
            "logical_path", "blob_digest", "size_bytes", "media_type",
        }
        raw = _compiler_mapping(value, fields, "SourceDependency")
        return cls(**{name: raw[name] for name in fields})


@_compiler_dataclass
class ProvenanceContribution(_CanonicalContract):
    origin_kind: str
    logical_path: str | None
    blob_digest: Digest | None
    source_pointer: str | None
    dependency_kind: str | None
    precedence_index: int
    action: str
    shadowed: bool

    def __post_init__(self) -> None:
        if self.origin_kind not in {
            "source", "compiler_default", "v1_translation", "renderer"
        }:
            raise BundleValidationError("unsupported provenance origin_kind")
        if self.logical_path is not None:
            if normalize_logical_path(self.logical_path) != self.logical_path:
                raise BundleValidationError("logical_path must be normalized")
        if self.blob_digest is not None:
            require_sha256(self.blob_digest, "blob_digest")
        if self.source_pointer is not None:
            _require_pointer(self.source_pointer, "source_pointer")
        _require_optional_string(self.dependency_kind, "dependency_kind")
        _require_integer(self.precedence_index, "precedence_index", minimum=0)
        if self.action not in {
            "set", "merge", "merge_noop", "replace", "default", "translate",
            "resolve", "overlay", "filter", "render", "dedupe",
        }:
            raise BundleValidationError("unsupported provenance action")
        _require_bool(self.shadowed, "shadowed")

    def to_canonical_obj(self) -> dict[str, JsonValue]:
        return {
            "origin_kind": self.origin_kind,
            "logical_path": self.logical_path,
            "blob_digest": self.blob_digest,
            "source_pointer": self.source_pointer,
            "dependency_kind": self.dependency_kind,
            "precedence_index": self.precedence_index,
            "action": self.action,
            "shadowed": self.shadowed,
        }

    @classmethod
    def from_dict(cls, value: Any) -> ProvenanceContribution:
        fields = {
            "origin_kind", "logical_path", "blob_digest", "source_pointer",
            "dependency_kind", "precedence_index", "action", "shadowed",
        }
        raw = _compiler_mapping(value, fields, "ProvenanceContribution")
        return cls(**{name: raw[name] for name in fields})


@_compiler_dataclass
class FieldProvenance(_CanonicalContract):
    target_pointer: str
    winner_index: int
    contributions: tuple[ProvenanceContribution, ...]

    def __post_init__(self) -> None:
        _require_pointer(self.target_pointer, "target_pointer")
        contributions = _require_tuple(self.contributions, "contributions")
        if not contributions or any(
            not isinstance(item, ProvenanceContribution) for item in contributions
        ):
            raise BundleValidationError(
                "contributions must contain ProvenanceContribution values"
            )
        winner = _require_integer(self.winner_index, "winner_index", minimum=0)
        if winner >= len(contributions):
            raise BundleValidationError("winner_index is out of range")
        if contributions[winner].shadowed:
            raise BundleValidationError("winning contribution cannot be shadowed")

    def to_canonical_obj(self) -> dict[str, JsonValue]:
        return {
            "target_pointer": self.target_pointer,
            "winner_index": self.winner_index,
            "contributions": [item.to_canonical_obj() for item in self.contributions],
        }

    @classmethod
    def from_dict(cls, value: Any) -> FieldProvenance:
        fields = {"target_pointer", "winner_index", "contributions"}
        raw = _compiler_mapping(value, fields, "FieldProvenance")
        values = _wire_tuple(raw["contributions"], "contributions")
        return cls(
            target_pointer=raw["target_pointer"],
            winner_index=raw["winner_index"],
            contributions=tuple(ProvenanceContribution.from_dict(item) for item in values),
        )


@_compiler_dataclass
class DefaultRecord(_CanonicalContract):
    target_pointer: str
    default_code: str
    value: JsonValue

    def __post_init__(self) -> None:
        _require_pointer(self.target_pointer, "target_pointer")
        _require_string(self.default_code, "default_code", identifier=True)
        object.__setattr__(self, "value", _freeze_json(self.value))

    def to_canonical_obj(self) -> dict[str, JsonValue]:
        return {
            "target_pointer": self.target_pointer,
            "default_code": self.default_code,
            "value": _thaw_json(self.value),
        }

    @classmethod
    def from_dict(cls, value: Any) -> DefaultRecord:
        fields = {"target_pointer", "default_code", "value"}
        raw = _compiler_mapping(value, fields, "DefaultRecord")
        return cls(**{name: raw[name] for name in fields})


@_compiler_dataclass
class LossRecord(_CanonicalContract):
    source_logical_path: str
    source_pointer: str
    loss_code: str
    observed_type: str
    reason: str
    runner_visible: bool = False

    def __post_init__(self) -> None:
        if normalize_logical_path(self.source_logical_path) != self.source_logical_path:
            raise BundleValidationError("source_logical_path must be normalized")
        _require_pointer(self.source_pointer, "source_pointer")
        _require_string(self.loss_code, "loss_code", identifier=True)
        _require_string(self.observed_type, "observed_type", identifier=True)
        _require_string(self.reason, "reason", nonempty=True)
        if self.runner_visible is not False:
            raise BundleValidationError("loss records must be non-runner-visible")

    def to_canonical_obj(self) -> dict[str, JsonValue]:
        return {
            "source_logical_path": self.source_logical_path,
            "source_pointer": self.source_pointer,
            "loss_code": self.loss_code,
            "observed_type": self.observed_type,
            "reason": self.reason,
            "runner_visible": self.runner_visible,
        }

    @classmethod
    def from_dict(cls, value: Any) -> LossRecord:
        fields = {
            "source_logical_path", "source_pointer", "loss_code",
            "observed_type", "reason", "runner_visible",
        }
        raw = _compiler_mapping(value, fields, "LossRecord")
        return cls(**{name: raw[name] for name in fields})


@_compiler_dataclass
class NoticeRecord(_CanonicalContract):
    code: str
    target_pointer: str | None
    details: JsonObject

    def __post_init__(self) -> None:
        _require_string(self.code, "code", identifier=True)
        if self.target_pointer is not None:
            _require_pointer(self.target_pointer, "target_pointer")
        object.__setattr__(self, "details", _freeze_object(self.details, "details"))

    def to_canonical_obj(self) -> dict[str, JsonValue]:
        return {
            "code": self.code,
            "target_pointer": self.target_pointer,
            "details": _thaw_json(self.details),
        }

    @classmethod
    def from_dict(cls, value: Any) -> NoticeRecord:
        fields = {"code", "target_pointer", "details"}
        raw = _compiler_mapping(value, fields, "NoticeRecord")
        return cls(**{name: raw[name] for name in fields})


@_compiler_dataclass
class CompileDiagnostics(_CanonicalContract):
    defaults: tuple[DefaultRecord, ...] = ()
    losses: tuple[LossRecord, ...] = ()
    notices: tuple[NoticeRecord, ...] = ()

    def __post_init__(self) -> None:
        for name, model in (
            ("defaults", DefaultRecord),
            ("losses", LossRecord),
            ("notices", NoticeRecord),
        ):
            values = _require_tuple(getattr(self, name), name)
            if any(not isinstance(item, model) for item in values):
                raise BundleValidationError(f"{name} contains the wrong record type")

    def to_canonical_obj(self) -> dict[str, JsonValue]:
        return {
            "defaults": [item.to_canonical_obj() for item in self.defaults],
            "losses": [item.to_canonical_obj() for item in self.losses],
            "notices": [item.to_canonical_obj() for item in self.notices],
        }

    @classmethod
    def from_dict(cls, value: Any) -> CompileDiagnostics:
        fields = {"defaults", "losses", "notices"}
        raw = _compiler_mapping(value, fields, "CompileDiagnostics")
        return cls(
            defaults=tuple(
                DefaultRecord.from_dict(item)
                for item in _wire_tuple(raw["defaults"], "defaults")
            ),
            losses=tuple(
                LossRecord.from_dict(item)
                for item in _wire_tuple(raw["losses"], "losses")
            ),
            notices=tuple(
                NoticeRecord.from_dict(item)
                for item in _wire_tuple(raw["notices"], "notices")
            ),
        )


_COMPILED_CONFIG_OBJECT_FIELDS: Final = (
    "metadata", "providers", "prompts", "tools", "plugins", "guardrails",
    "task", "runtime", "loop", "turn_strategy", "features", "completion",
    "concurrency", "permissions", "enhanced_tools", "replay", "long_running",
    "terminal_sessions", "observability", "sampling",
)


@_compiler_dataclass
class CompiledConfig(_CanonicalContract):
    """Closed, recursively immutable semantic root."""

    root_config_node_id: str
    config_nodes: tuple[JsonObject, ...]
    metadata: JsonObject
    providers: JsonObject
    prompts: JsonObject
    tools: JsonObject
    plugins: JsonObject
    guardrails: JsonObject
    team: JsonObject | None
    task: JsonObject
    runtime: JsonObject
    modes: tuple[JsonObject, ...]
    loop: JsonObject
    turn_strategy: JsonObject
    features: JsonObject
    completion: JsonObject
    concurrency: JsonObject
    permissions: JsonObject
    enhanced_tools: JsonObject
    replay: JsonObject
    long_running: JsonObject
    terminal_sessions: JsonObject
    observability: JsonObject
    sampling: JsonObject
    optimizer_mutable_pointers: tuple[str, ...]

    def __post_init__(self) -> None:
        require_sha256(self.root_config_node_id, "root_config_node_id")
        nodes = _require_tuple(self.config_nodes, "config_nodes")
        checked_nodes: list[_ImmutableJsonObject] = []
        node_ids: set[str] = set()
        for index, node in enumerate(nodes):
            raw_node = _compiler_mapping(node, {"node_id", "semantic_config"}, f"config_nodes[{index}]")
            node_id = raw_node["node_id"]
            require_sha256(node_id, f"config_nodes[{index}].node_id")
            if node_id in node_ids:
                raise BundleValidationError("config_nodes must have unique node IDs")
            node_ids.add(node_id)
            _freeze_object(raw_node["semantic_config"], f"config_nodes[{index}].semantic_config")
            checked_nodes.append(_freeze_object(raw_node, f"config_nodes[{index}]"))
        if not checked_nodes:
            raise BundleValidationError("config_nodes must not be empty")
        if self.root_config_node_id not in node_ids:
            raise BundleValidationError("root_config_node_id must name a config node")
        object.__setattr__(self, "config_nodes", tuple(checked_nodes))
        for name in _COMPILED_CONFIG_OBJECT_FIELDS:
            object.__setattr__(self, name, _freeze_object(getattr(self, name), name))
        if self.team is not None:
            team = _freeze_object(self.team, "team")
            agents = team.get("agents", ())
            if not isinstance(agents, tuple) or not agents:
                raise BundleValidationError("team.agents must be a nonempty array")
            entrypoint_count = 0
            for index, agent in enumerate(agents):
                if not isinstance(agent, Mapping) or agent.get("config_node_id") not in node_ids:
                    raise BundleValidationError(f"team.agents[{index}] references an unknown config node")
                if type(agent.get("entrypoint")) is not bool:
                    raise BundleValidationError(f"team.agents[{index}].entrypoint must be boolean")
                entrypoint_count += int(agent["entrypoint"])
            if entrypoint_count != 1:
                raise BundleValidationError("team must have exactly one entrypoint agent")
            object.__setattr__(self, "team", team)
        modes = _require_tuple(self.modes, "modes")
        frozen_modes = tuple(_freeze_object(mode, f"modes[{index}]") for index, mode in enumerate(modes))
        object.__setattr__(self, "modes", frozen_modes)
        pointers = _require_string_tuple(self.optimizer_mutable_pointers, "optimizer_mutable_pointers", unique=True)
        for pointer in pointers:
            _require_pointer(pointer, "optimizer_mutable_pointers")
        if pointers != tuple(sorted(pointers)):
            raise BundleValidationError("optimizer_mutable_pointers must be sorted")

        task_tool = self.task.get("task_tool")
        if isinstance(task_tool, Mapping):
            subagents = task_tool.get("subagents", ())
            if not isinstance(subagents, tuple):
                raise BundleValidationError("task_tool.subagents must be an array")
            for index, subagent in enumerate(subagents):
                if not isinstance(subagent, Mapping):
                    raise BundleValidationError(f"task_tool.subagents[{index}] must be an object")
                config_node_id = subagent.get("config_node_id")
                if config_node_id is not None and config_node_id not in node_ids:
                    raise BundleValidationError(f"task_tool.subagents[{index}] references an unknown config node")

        def validate_prompts(prompts: Any) -> None:
            if not isinstance(prompts, Mapping):
                raise BundleValidationError("config prompts must be an object")
            variants = prompts.get("variants", ())
            if not isinstance(variants, tuple):
                raise BundleValidationError("prompt variants must be an array")
            for variant_index, variant in enumerate(variants):
                if not isinstance(variant, Mapping):
                    raise BundleValidationError("prompt variant must be an object")
                tool_ids = variant.get("effective_tool_ids")
                catalog = variant.get("tool_catalog")
                if not isinstance(tool_ids, tuple) or not isinstance(catalog, Mapping):
                    raise BundleValidationError("prompt variant tool-set fields are invalid")
                expected_tool_set = canonical_sha256({"schema": "bb.tool-set.v1", "tool_ids": list(tool_ids)})
                if variant.get("tool_set_digest") != expected_tool_set:
                    raise BundleValidationError(f"prompt variants[{variant_index}] tool_set_digest mismatch")
                if catalog.get("effective_tool_ids") != tool_ids:
                    raise BundleValidationError(f"prompt variants[{variant_index}] tool_catalog.effective_tool_ids mismatch")

        validate_prompts(self.prompts)
        for node in checked_nodes:
            validate_prompts(node["semantic_config"].get("prompts"))

        root_node = next(node for node in checked_nodes if node["node_id"] == self.root_config_node_id)
        root_semantic = root_node["semantic_config"]
        for name in _COMPILED_CONFIG_OBJECT_FIELDS:
            expected_value = None if name == "team" and self.team is None else getattr(self, name)
            if root_semantic.get(name) != expected_value:
                raise BundleValidationError(f"root config node {name} differs from top-level semantic field")
        if root_semantic.get("modes") != frozen_modes:
            raise BundleValidationError("root config node modes differ from top-level semantic field")
        if root_semantic.get("optimizer_mutable_pointers") != pointers:
            raise BundleValidationError("root config node optimizer pointers differ from top-level semantic field")

    def to_canonical_obj(self) -> dict[str, JsonValue]:
        payload: dict[str, JsonValue] = {
            "root_config_node_id": self.root_config_node_id,
            "config_nodes": [_thaw_json(node) for node in self.config_nodes],
            "team": None if self.team is None else _thaw_json(self.team),
            "modes": [_thaw_json(mode) for mode in self.modes],
            "optimizer_mutable_pointers": list(self.optimizer_mutable_pointers),
        }
        for name in _COMPILED_CONFIG_OBJECT_FIELDS:
            payload[name] = _thaw_json(getattr(self, name))
        return payload

    @classmethod
    def from_dict(cls, value: Any) -> CompiledConfig:
        fields = {"root_config_node_id", "config_nodes", "team", "modes", "optimizer_mutable_pointers", *_COMPILED_CONFIG_OBJECT_FIELDS}
        raw = _compiler_mapping(value, fields, "CompiledConfig")
        kwargs = {name: raw[name] for name in _COMPILED_CONFIG_OBJECT_FIELDS}
        return cls(
            root_config_node_id=raw["root_config_node_id"],
            config_nodes=_wire_tuple(raw["config_nodes"], "config_nodes"),
            team=raw["team"],
            modes=_wire_tuple(raw["modes"], "modes"),
            optimizer_mutable_pointers=_wire_tuple(raw["optimizer_mutable_pointers"], "optimizer_mutable_pointers"),
            **kwargs,
        )


def _json_leaf_pointers(value: Any, pointer: str = "") -> set[str]:
    if isinstance(value, Mapping) and value:
        result: set[str] = set()
        for key, item in value.items():
            token = key.replace("~", "~0").replace("/", "~1")
            child = f"{pointer}/{token}" if pointer else f"/{token}"
            result.update(_json_leaf_pointers(item, child))
        return result
    if isinstance(value, (list, tuple)) and value:
        result = set()
        for index, item in enumerate(value):
            child = f"{pointer}/{index}" if pointer else f"/{index}"
            result.update(_json_leaf_pointers(item, child))
        return result
    return {pointer}


def _json_pointer_exists(value: Any, pointer: str) -> bool:
    if pointer == "":
        return True
    current = value
    for raw_token in pointer.lstrip("/").split("/"):
        token = raw_token.replace("~1", "/").replace("~0", "~")
        if isinstance(current, Mapping) and token in current:
            current = current[token]
        elif isinstance(current, (list, tuple)) and token.isdigit() and int(token) < len(current):
            current = current[int(token)]
        else:
            return False
    return True


@_compiler_dataclass
class CompiledConfigManifest(_CanonicalContract):
    compiler: CompilerIdentity
    inputs: CompileInputIdentity
    source_dependencies: tuple[SourceDependency, ...]
    semantic: CompiledConfig
    provenance: tuple[FieldProvenance, ...]
    diagnostics: CompileDiagnostics
    semantic_digest: Digest
    compiled_manifest_digest: Digest = ""
    schema_id: str = COMPILED_CONFIG_MANIFEST_SCHEMA_ID
    schema_version: int = COMPILED_CONFIG_MANIFEST_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.schema_id != COMPILED_CONFIG_MANIFEST_SCHEMA_ID:
            raise BundleValidationError("unsupported compiled manifest schema")
        if type(self.schema_version) is not int or self.schema_version != COMPILED_CONFIG_MANIFEST_SCHEMA_VERSION:
            raise BundleValidationError("unsupported compiled manifest schema version")
        if not isinstance(self.compiler, CompilerIdentity):
            raise BundleValidationError("compiler must be CompilerIdentity")
        if not isinstance(self.inputs, CompileInputIdentity):
            raise BundleValidationError("inputs must be CompileInputIdentity")
        if self.compiler.runtime_abi != self.inputs.options.target.runtime_abi:
            raise BundleValidationError("compiler and compile target runtime ABI differ")
        dependencies = _require_tuple(self.source_dependencies, "source_dependencies")
        if any(not isinstance(item, SourceDependency) for item in dependencies):
            raise BundleValidationError("source_dependencies must contain SourceDependency values")
        dependency_keys = tuple(item.sort_key for item in dependencies)
        if dependency_keys != tuple(sorted(dependency_keys)):
            raise BundleValidationError("source_dependencies must be sorted")
        if len(set(dependency_keys)) != len(dependency_keys):
            raise BundleValidationError("source_dependencies must be unique")
        if not isinstance(self.semantic, CompiledConfig):
            raise BundleValidationError("semantic must be CompiledConfig")
        provenance = _require_tuple(self.provenance, "provenance")
        if any(not isinstance(item, FieldProvenance) for item in provenance):
            raise BundleValidationError("provenance must contain FieldProvenance values")
        pointers = tuple(item.target_pointer for item in provenance)
        if pointers != tuple(sorted(pointers)) or len(set(pointers)) != len(pointers):
            raise BundleValidationError("provenance pointers must be sorted and unique")
        if not isinstance(self.diagnostics, CompileDiagnostics):
            raise BundleValidationError("diagnostics must be CompileDiagnostics")
        semantic_object = self.semantic.to_canonical_obj()
        semantic_leaves = _json_leaf_pointers(semantic_object)
        if not semantic_leaves.issubset(pointers):
            missing = sorted(semantic_leaves - set(pointers))
            raise BundleValidationError(f"semantic provenance is incomplete at {missing[0]}")
        dependency_bindings = {
            (item.logical_path, item.blob_digest, item.dependency_kind)
            for item in dependencies
        }
        provenance_by_pointer = {item.target_pointer: item for item in provenance}
        for record in provenance:
            winner = record.contributions[record.winner_index]
            if winner.origin_kind in {"source", "v1_translation", "renderer"}:
                if None in (winner.logical_path, winner.blob_digest, winner.source_pointer, winner.dependency_kind):
                    raise BundleValidationError(f"source provenance is incomplete at {record.target_pointer}")
                if (winner.logical_path, winner.blob_digest, winner.dependency_kind) not in dependency_bindings:
                    raise BundleValidationError(f"source provenance is not dependency-bound at {record.target_pointer}")
        default_pointers: set[str] = set()
        for default in self.diagnostics.defaults:
            if default.target_pointer in default_pointers:
                raise BundleValidationError("default pointers must be unique")
            default_pointers.add(default.target_pointer)
            if not _json_pointer_exists(semantic_object, default.target_pointer):
                raise BundleValidationError(f"default pointer is absent from semantics: {default.target_pointer}")
            provenance_record = provenance_by_pointer.get(default.target_pointer)
            if provenance_record is None or provenance_record.contributions[provenance_record.winner_index].origin_kind != "compiler_default":
                raise BundleValidationError(f"default provenance is incomplete at {default.target_pointer}")
        expected_input = canonical_sha256(self.compiler_input_preimage())
        if self.inputs.compiler_input_digest != expected_input:
            raise ConfigCompileError(stage=CompileStage.IDENTITY, code=CompileErrorCode.COMPILER_INPUT_MISMATCH, details={"actual": self.inputs.compiler_input_digest, "expected": expected_input})
        expected_semantic = canonical_sha256({"schema": COMPILED_CONFIG_SEMANTIC_SCHEMA_ID, "config": self.semantic.to_canonical_obj()})
        require_sha256(self.semantic_digest, "semantic_digest")
        if self.semantic_digest != expected_semantic:
            raise ConfigCompileError(stage=CompileStage.IDENTITY, code=CompileErrorCode.MANIFEST_IDENTITY_MISMATCH, details={"actual": self.semantic_digest, "expected": expected_semantic, "field": "semantic_digest"})
        expected_manifest = canonical_sha256(self.to_canonical_obj(include_digest=False))
        if self.compiled_manifest_digest != "":
            require_sha256(self.compiled_manifest_digest, "compiled_manifest_digest")
            if self.compiled_manifest_digest != expected_manifest:
                raise ConfigCompileError(stage=CompileStage.IDENTITY, code=CompileErrorCode.MANIFEST_IDENTITY_MISMATCH, details={"actual": self.compiled_manifest_digest, "expected": expected_manifest, "field": "compiled_manifest_digest"})
        object.__setattr__(self, "compiled_manifest_digest", expected_manifest)

    def compiler_input_preimage(self) -> dict[str, JsonValue]:
        return {
            "schema": COMPILER_INPUT_SCHEMA_ID,
            "bundle_digest": self.inputs.bundle_digest,
            "closure_digest": self.inputs.closure_digest,
            "entrypoint": self.inputs.entrypoint,
            "compiler_id": self.compiler.compiler_id,
            "compiler_version": self.compiler.compiler_version,
            "compiler_code_digest": self.compiler.compiler_code_digest,
            "config_schema_id": self.compiler.config_schema_id,
            "config_schema_version": self.compiler.config_schema_version,
            "config_schema_digest": self.compiler.config_schema_digest,
            "manifest_schema_id": self.compiler.manifest_schema_id,
            "manifest_schema_version": self.compiler.manifest_schema_version,
            "manifest_schema_digest": self.compiler.manifest_schema_digest,
            "canonicalizer_id": self.compiler.canonicalizer_id,
            "runtime_abi": self.inputs.options.target.runtime_abi,
            "compile_options": self.inputs.options.to_canonical_obj(),
        }

    def to_canonical_obj(self, *, include_digest: bool = True) -> dict[str, JsonValue]:
        payload: dict[str, JsonValue] = {
            "schema_id": self.schema_id,
            "schema_version": self.schema_version,
            "compiler": self.compiler.to_canonical_obj(),
            "inputs": self.inputs.to_canonical_obj(),
            "source_dependencies": [item.to_canonical_obj() for item in self.source_dependencies],
            "semantic": self.semantic.to_canonical_obj(),
            "provenance": [item.to_canonical_obj() for item in self.provenance],
            "diagnostics": self.diagnostics.to_canonical_obj(),
            "semantic_digest": self.semantic_digest,
        }
        if include_digest:
            payload["compiled_manifest_digest"] = self.compiled_manifest_digest
        return payload

    def to_dict(self, *, include_digest: bool = True) -> dict[str, JsonValue]:
        return self.to_canonical_obj(include_digest=include_digest)

    @classmethod
    def from_dict(cls, value: Any) -> CompiledConfigManifest:
        fields = {"schema_id", "schema_version", "compiler", "inputs", "source_dependencies", "semantic", "provenance", "diagnostics", "semantic_digest", "compiled_manifest_digest"}
        raw = _compiler_mapping(value, fields, "CompiledConfigManifest")
        require_sha256(raw["compiled_manifest_digest"], "compiled_manifest_digest")
        return cls(
            schema_id=raw["schema_id"],
            schema_version=raw["schema_version"],
            compiler=CompilerIdentity.from_dict(raw["compiler"]),
            inputs=CompileInputIdentity.from_dict(raw["inputs"]),
            source_dependencies=tuple(SourceDependency.from_dict(item) for item in _wire_tuple(raw["source_dependencies"], "source_dependencies")),
            semantic=CompiledConfig.from_dict(raw["semantic"]),
            provenance=tuple(FieldProvenance.from_dict(item) for item in _wire_tuple(raw["provenance"], "provenance")),
            diagnostics=CompileDiagnostics.from_dict(raw["diagnostics"]),
            semantic_digest=raw["semantic_digest"],
            compiled_manifest_digest=raw["compiled_manifest_digest"],
        )

    @classmethod
    def from_json(cls, data: str | bytes | bytearray) -> CompiledConfigManifest:
        return cls.from_dict(canonical_json_loads(data))

__all__ = [
    "CONFIG_BUNDLE_SCHEMA",
    "DEPENDENCY_CLOSURE_SCHEMA",
    "MAX_SAFE_INTEGER",
    "BundleEntry",
    "BundleEntrypoint",
    "BundleError",
    "BundleIntegrityError",
    "BundleLimitError",
    "BundleLimits",
    "BundleProvenance",
    "BundleSecurityError",
    "BundleValidationError",
    "CanonicalJSONError",
    "ClosureMember",
    "ConfigBundleManifest",
    "DependencyClosureManifest",
    "DependencyEdge",
    "LogicalPathError",
    "UndeclaredMemberError",
    "bytes_sha256",
    "canonical_json_bytes",
    "canonical_json_loads",
    "canonical_sha256",
    "normalize_logical_path",
    "require_sha256",
    "AGENT_CONFIG_SCHEMA_ID",
    "AGENT_CONFIG_SCHEMA_VERSION",
    "CANONICALIZER_ID",
    "COMPILED_CONFIG_MANIFEST_SCHEMA_ID",
    "COMPILED_CONFIG_MANIFEST_SCHEMA_VERSION",
    "COMPILED_CONFIG_SEMANTIC_SCHEMA_ID",
    "COMPILER_INPUT_SCHEMA_ID",
    "COMPILE_OPTIONS_SCHEMA_ID",
    "CONFIG_COMPILE_ERROR_SCHEMA_ID",
    "CONFIG_NODE_ID_SCHEMA_ID",
    "JCS_SAFE_INTEGER_MAX",
    "JCS_SAFE_INTEGER_MIN",
    "PROMPT_VARIANT_ID_SCHEMA_ID",
    "SERVER_CONFIG_COMPILER_ID",
    "SHA256_DIGEST_RE",
    "V1_SHADOW_TRANSLATOR_ID",
    "CompileDiagnostics",
    "CompileErrorCode",
    "CompileInputIdentity",
    "CompileOptions",
    "CompileStage",
    "CompileTarget",
    "CompiledConfig",
    "CompiledConfigManifest",
    "CompilerIdentity",
    "ConfigCompileError",
    "DefaultRecord",
    "Digest",
    "FieldProvenance",
    "JsonObject",
    "JsonValue",
    "LossRecord",
    "NoticeRecord",
    "ProvenanceContribution",
    "SourceDependency",
    "TaskArtifactContract",
    "TaskContract",
    "TaskEvidenceContract",
    "TaskRetentionContract",
    "TaskVerifierContract",
]
