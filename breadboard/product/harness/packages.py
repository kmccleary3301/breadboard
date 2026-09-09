"""Immutable, byte-pinned author module packages.

Packaging is a data-only boundary: it stages a source directory through the
existing hardened bundle ingester, validates the author manifest against the
captured bytes, and writes a deterministic ZIP. It never imports or executes
anything from a package.
"""

from __future__ import annotations

import io
import os
import tempfile
import zipfile
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Final, TypeAlias

from breadboard.artifacts.cas import ArtifactStoreError, FilesystemCAS
from breadboard.artifacts.references import ArtifactRef
from breadboard_engine.compilation.bundle import ingest_bundle
from breadboard_engine.compilation.contracts import (
    BundleEntry,
    BundleError,
    BundleLimits,
    BundleValidationError,
    bytes_sha256,
    canonical_json_bytes,
    canonical_json_loads,
    canonical_sha256,
    normalize_logical_path,
    require_sha256,
)

MODULE_MANIFEST_SCHEMA: Final = "bb.module_manifest.v1"
_PACKAGE_MEDIA_TYPE: Final = "application/zip"
_PACKAGE_ENTRYPOINT: Final = "manifest"
_PACKAGE_MANIFEST_PATH: Final = "module.json"
_MAX_SAFE_INTEGER: Final = 9_007_199_254_740_991
_MANIFEST_FIELDS: Final = frozenset(
    {
        "accepted_checkpoint_schema_ids",
        "checkpoint_schema_id",
        "child_targets",
        "dependency_contract_ids",
        "entrypoint",
        "execution_tier",
        "import_members",
        "input_schema_ids",
        "logical_package",
        "output_schema_ids",
        "package_version",
        "requested_authority",
        "resource_budget",
        "runtime",
        "schema_version",
        "source_members",
        "worker_protocol",
    }
)

JsonScalar: TypeAlias = None | bool | int | float | str
JsonValue: TypeAlias = JsonScalar | dict[str, "JsonValue"] | list["JsonValue"]
FrozenJsonValue: TypeAlias = (
    JsonScalar | Mapping[str, "FrozenJsonValue"] | tuple["FrozenJsonValue", ...]
)


class ModulePackageError(ValueError):
    """Base class for package contract failures."""


class ModulePackageValidationError(ModulePackageError):
    """A package manifest or source declaration is malformed."""


class ModulePackageIntegrityError(ModulePackageError):
    """Captured package bytes do not match immutable declarations."""


class ModulePackageSecurityError(ModulePackageError):
    """A source or archive crossed the package security boundary."""


def _object(value: object, type_name: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise ModulePackageValidationError(f"{type_name} must be an object")
    if any(type(key) is not str for key in value):
        raise ModulePackageValidationError(f"{type_name} keys must be strings")
    return value


def _array(value: object, type_name: str) -> Sequence[object]:
    if not isinstance(value, (list, tuple)):
        raise ModulePackageValidationError(f"{type_name} must be an array")
    return value


def _text(value: object, field_name: str) -> str:
    if not isinstance(value, str) or not value or value.strip() != value:
        raise ModulePackageValidationError(f"{field_name} must be a non-empty string")
    return value


def _text_array(value: object, field_name: str) -> tuple[str, ...]:
    values = tuple(_text(item, f"{field_name}[]") for item in _array(value, field_name))
    if len(set(values)) != len(values):
        raise ModulePackageValidationError(f"{field_name} must not contain duplicates")
    return tuple(sorted(values))


def _freeze_json(value: object) -> FrozenJsonValue:
    if value is None:
        return None
    if type(value) is bool:
        return value
    if type(value) is str:
        return value
    if type(value) is int:
        if abs(value) > _MAX_SAFE_INTEGER:
            raise ModulePackageValidationError(
                "JSON integer exceeds the safe integer range"
            )
        return value
    if type(value) is float:
        if value != value or value in (float("inf"), float("-inf")):
            raise ModulePackageValidationError("JSON numbers must be finite")
        return value
    if isinstance(value, Mapping):
        frozen: dict[str, FrozenJsonValue] = {}
        for key, item in value.items():
            if type(key) is not str:
                raise ModulePackageValidationError("JSON object keys must be strings")
            frozen[key] = _freeze_json(item)
        return MappingProxyType(frozen)
    if isinstance(value, (list, tuple)):
        return tuple(_freeze_json(item) for item in value)
    raise ModulePackageValidationError(
        f"JSON value has unsupported type {type(value).__name__}"
    )


def _thaw_json(value: FrozenJsonValue) -> JsonValue:
    if isinstance(value, Mapping):
        return {key: _thaw_json(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_thaw_json(item) for item in value]
    return value


def _frozen_object(
    value: Mapping[str, object], field_name: str
) -> Mapping[str, FrozenJsonValue]:
    frozen = _freeze_json(value)
    if not isinstance(frozen, Mapping):
        raise ModulePackageValidationError(f"{field_name} must be an object")
    return frozen


def _size(value: object, field_name: str) -> int:
    if type(value) is not int or value < 0:
        raise ModulePackageValidationError(
            f"{field_name} must be a non-negative integer"
        )
    if value > _MAX_SAFE_INTEGER:
        raise ModulePackageValidationError(f"{field_name} exceeds the safe integer range")
    return value


@dataclass(frozen=True, slots=True)
class ChildTarget:
    """A named child target declared by a package."""

    label: str
    target: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "label", _text(self.label, "child target label"))
        object.__setattr__(self, "target", _text(self.target, "child target"))

    @classmethod
    def from_dict(cls, value: object) -> ChildTarget:
        raw = _object(value, "ChildTarget")
        expected = {"label", "target"}
        unknown = set(raw) - expected
        missing = expected - set(raw)
        if unknown:
            raise ModulePackageValidationError(
                "ChildTarget contains unknown fields: " + ", ".join(sorted(unknown))
            )
        if missing:
            raise ModulePackageValidationError(
                "ChildTarget is missing fields: " + ", ".join(sorted(missing))
            )
        return cls(label=raw["label"], target=raw["target"])

    def as_dict(self) -> dict[str, str]:
        return {"label": self.label, "target": self.target}


@dataclass(frozen=True, slots=True)
class SourceMember:
    """One exact implementation or schema file captured in a package."""

    path: str
    sha256: str
    size_bytes: int

    def __post_init__(self) -> None:
        try:
            normalized = normalize_logical_path(self.path)
        except BundleError as exc:
            raise ModulePackageValidationError(
                "source member path is not safe"
            ) from exc
        if normalized != self.path or self.path == _PACKAGE_MANIFEST_PATH:
            raise ModulePackageValidationError(
                "source member path must be normalized and not module.json"
            )
        try:
            require_sha256(self.sha256, "source member sha256")
        except BundleValidationError as exc:
            raise ModulePackageValidationError(str(exc)) from exc
        object.__setattr__(
            self, "size_bytes", _size(self.size_bytes, "source member size_bytes")
        )

    @classmethod
    def from_dict(cls, value: object) -> SourceMember:
        raw = _object(value, "SourceMember")
        expected = {"path", "sha256", "size_bytes"}
        if set(raw) != expected:
            raise ModulePackageValidationError(
                "SourceMember requires exactly path, sha256, and size_bytes"
            )
        return cls(
            path=raw["path"], sha256=raw["sha256"], size_bytes=raw["size_bytes"]
        )

    def as_dict(self) -> dict[str, object]:
        return {
            "path": self.path,
            "sha256": self.sha256,
            "size_bytes": self.size_bytes,
        }


@dataclass(frozen=True, slots=True)
class ImportMember:
    """One exact import-name to captured-file binding."""

    module: str
    path: str
    sha256: str
    size_bytes: int

    def __post_init__(self) -> None:
        object.__setattr__(self, "module", _text(self.module, "import member module"))
        try:
            normalized = normalize_logical_path(self.path)
        except BundleError as exc:
            raise ModulePackageValidationError(
                "import member path is not safe"
            ) from exc
        if normalized != self.path or self.path == _PACKAGE_MANIFEST_PATH:
            raise ModulePackageValidationError(
                "import member path must be normalized and not module.json"
            )
        try:
            require_sha256(self.sha256, "import member sha256")
        except BundleValidationError as exc:
            raise ModulePackageValidationError(str(exc)) from exc
        object.__setattr__(
            self, "size_bytes", _size(self.size_bytes, "import member size_bytes")
        )

    @classmethod
    def from_dict(cls, value: object) -> ImportMember:
        raw = _object(value, "ImportMember")
        expected = {"module", "path", "sha256", "size_bytes"}
        if set(raw) != expected:
            raise ModulePackageValidationError(
                "ImportMember requires exactly module, path, sha256, and size_bytes"
            )
        return cls(
            module=raw["module"],
            path=raw["path"],
            sha256=raw["sha256"],
            size_bytes=raw["size_bytes"],
        )

    def as_dict(self) -> dict[str, object]:
        return {
            "module": self.module,
            "path": self.path,
            "sha256": self.sha256,
            "size_bytes": self.size_bytes,
        }


@dataclass(frozen=True, slots=True)
class RuntimeDescriptor:
    """A content-pinned runtime identity, never a mutable version alias."""

    kind: str
    ref: str
    platform: str
    entrypoint: tuple[str, ...]

    def __post_init__(self) -> None:
        kind = _text(self.kind, "runtime kind")
        if kind not in {"oci", "native"}:
            raise ModulePackageValidationError("runtime kind must be oci or native")
        object.__setattr__(self, "kind", kind)
        object.__setattr__(self, "ref", _text(self.ref, "runtime ref"))
        object.__setattr__(self, "platform", _text(self.platform, "runtime platform"))
        commands = tuple(_text(item, "runtime entrypoint[]") for item in self.entrypoint)
        if not commands:
            raise ModulePackageValidationError("runtime entrypoint must not be empty")
        object.__setattr__(self, "entrypoint", commands)
        if self.kind == "oci":
            marker = "@sha256:"
            if marker not in self.ref:
                raise ModulePackageValidationError(
                    "OCI runtime ref must contain a pinned @sha256 digest"
                )
            try:
                require_sha256(
                    "sha256:" + self.ref.rsplit(marker, 1)[1], "runtime ref"
                )
            except BundleValidationError as exc:
                raise ModulePackageValidationError(str(exc)) from exc
        else:
            try:
                require_sha256(self.ref, "runtime ref")
            except BundleValidationError as exc:
                raise ModulePackageValidationError(
                    "native runtime ref must be an exact sha256 artifact digest"
                ) from exc

    @classmethod
    def from_dict(cls, value: object) -> RuntimeDescriptor:
        raw = _object(value, "RuntimeDescriptor")
        expected = {"kind", "ref", "platform", "entrypoint"}
        if set(raw) != expected:
            raise ModulePackageValidationError(
                "RuntimeDescriptor requires exactly kind, ref, platform, and entrypoint"
            )
        return cls(
            kind=raw["kind"],
            ref=raw["ref"],
            platform=raw["platform"],
            entrypoint=tuple(
                _text(item, "runtime entrypoint[]")
                for item in _array(raw["entrypoint"], "runtime entrypoint")
            ),
        )

    def as_dict(self) -> dict[str, object]:
        return {
            "entrypoint": list(self.entrypoint),
            "kind": self.kind,
            "platform": self.platform,
            "ref": self.ref,
        }


@dataclass(frozen=True, slots=True)
class ModuleManifest:
    """A recursively immutable, validated ``bb.module_manifest.v1`` record."""

    logical_package: str
    package_version: str
    entrypoint: str
    execution_tier: str
    worker_protocol: str
    input_schema_ids: tuple[str, ...]
    output_schema_ids: tuple[str, ...]
    checkpoint_schema_id: str | None
    accepted_checkpoint_schema_ids: tuple[str, ...]
    dependency_contract_ids: tuple[str, ...]
    child_targets: tuple[ChildTarget, ...]
    requested_authority: Mapping[str, FrozenJsonValue]
    resource_budget: Mapping[str, FrozenJsonValue]
    source_members: tuple[SourceMember, ...]
    import_members: tuple[ImportMember, ...]
    runtime: RuntimeDescriptor
    schema_version: str = MODULE_MANIFEST_SCHEMA

    def __post_init__(self) -> None:
        if self.schema_version != MODULE_MANIFEST_SCHEMA:
            raise ModulePackageValidationError(
                f"unsupported module manifest schema: {self.schema_version!r}"
            )
        for field_name in (
            "logical_package",
            "package_version",
            "entrypoint",
            "worker_protocol",
        ):
            object.__setattr__(self, field_name, _text(getattr(self, field_name), field_name))
        execution_tier = _text(self.execution_tier, "execution_tier")
        if execution_tier not in {"trusted_native", "enforced_isolated"}:
            raise ModulePackageValidationError(
                "execution_tier must be trusted_native or enforced_isolated"
            )
        object.__setattr__(self, "execution_tier", execution_tier)
        if self.checkpoint_schema_id is not None:
            object.__setattr__(
                self,
                "checkpoint_schema_id",
                _text(self.checkpoint_schema_id, "checkpoint_schema_id"),
            )
        for field_name in (
            "input_schema_ids",
            "output_schema_ids",
            "accepted_checkpoint_schema_ids",
            "dependency_contract_ids",
        ):
            object.__setattr__(
                self,
                field_name,
                _text_array(getattr(self, field_name), field_name),
            )
        children = tuple(self.child_targets)
        if any(not isinstance(item, ChildTarget) for item in children):
            raise ModulePackageValidationError("child_targets must contain ChildTarget values")
        if len({item.label for item in children}) != len(children):
            raise ModulePackageValidationError("child target labels must be unique")
        object.__setattr__(
            self, "child_targets", tuple(sorted(children, key=lambda item: item.label))
        )
        authority = _freeze_json(self.requested_authority)
        budget = _freeze_json(self.resource_budget)
        if not isinstance(authority, Mapping) or not isinstance(budget, Mapping):
            raise ModulePackageValidationError(
                "requested_authority and resource_budget must be objects"
            )
        object.__setattr__(self, "requested_authority", authority)
        object.__setattr__(self, "resource_budget", budget)
        sources = tuple(self.source_members)
        imports = tuple(self.import_members)
        if not sources:
            raise ModulePackageValidationError("source_members must not be empty")
        if any(not isinstance(item, SourceMember) for item in sources):
            raise ModulePackageValidationError("source_members must contain SourceMember values")
        if any(not isinstance(item, ImportMember) for item in imports):
            raise ModulePackageValidationError("import_members must contain ImportMember values")
        if len({item.path for item in sources}) != len(sources):
            raise ModulePackageValidationError("source member paths must be unique")
        if len({(item.module, item.path) for item in imports}) != len(imports):
            raise ModulePackageValidationError("import member bindings must be unique")
        object.__setattr__(
            self, "source_members", tuple(sorted(sources, key=lambda item: item.path))
        )
        object.__setattr__(
            self,
            "import_members",
            tuple(sorted(imports, key=lambda item: (item.module, item.path))),
        )
        if not isinstance(self.runtime, RuntimeDescriptor):
            raise ModulePackageValidationError("runtime must be a RuntimeDescriptor")

    @classmethod
    def from_dict(cls, value: object) -> ModuleManifest:
        raw = _object(value, "ModuleManifest")
        unknown = set(raw) - _MANIFEST_FIELDS
        missing = _MANIFEST_FIELDS - set(raw)
        if unknown:
            raise ModulePackageValidationError(
                "ModuleManifest contains unknown fields: " + ", ".join(sorted(unknown))
            )
        if missing:
            raise ModulePackageValidationError(
                "ModuleManifest is missing fields: " + ", ".join(sorted(missing))
            )
        checkpoint = raw["checkpoint_schema_id"]
        if checkpoint is not None:
            checkpoint = _text(checkpoint, "checkpoint_schema_id")
        return cls(
            schema_version=raw["schema_version"],
            logical_package=raw["logical_package"],
            package_version=raw["package_version"],
            entrypoint=raw["entrypoint"],
            execution_tier=raw["execution_tier"],
            worker_protocol=raw["worker_protocol"],
            input_schema_ids=_text_array(raw["input_schema_ids"], "input_schema_ids"),
            output_schema_ids=_text_array(raw["output_schema_ids"], "output_schema_ids"),
            checkpoint_schema_id=checkpoint,
            accepted_checkpoint_schema_ids=_text_array(
                raw["accepted_checkpoint_schema_ids"],
                "accepted_checkpoint_schema_ids",
            ),
            dependency_contract_ids=_text_array(
                raw["dependency_contract_ids"], "dependency_contract_ids"
            ),
            child_targets=tuple(
                ChildTarget.from_dict(item)
                for item in _array(raw["child_targets"], "child_targets")
            ),
            requested_authority=_frozen_object(
                _object(raw["requested_authority"], "requested_authority"),
                "requested_authority",
            ),
            resource_budget=_frozen_object(
                _object(raw["resource_budget"], "resource_budget"),
                "resource_budget",
            ),
            source_members=tuple(
                SourceMember.from_dict(item)
                for item in _array(raw["source_members"], "source_members")
            ),
            import_members=tuple(
                ImportMember.from_dict(item)
                for item in _array(raw["import_members"], "import_members")
            ),
            runtime=RuntimeDescriptor.from_dict(raw["runtime"]),
        )

    @classmethod
    def from_json(cls, value: str | bytes | bytearray) -> ModuleManifest:
        try:
            decoded = canonical_json_loads(value)
        except BundleError as exc:
            raise ModulePackageValidationError(str(exc)) from exc
        return cls.from_dict(decoded)

    def as_dict(self) -> dict[str, object]:
        return {
            "accepted_checkpoint_schema_ids": list(self.accepted_checkpoint_schema_ids),
            "checkpoint_schema_id": self.checkpoint_schema_id,
            "child_targets": [item.as_dict() for item in self.child_targets],
            "dependency_contract_ids": list(self.dependency_contract_ids),
            "entrypoint": self.entrypoint,
            "execution_tier": self.execution_tier,
            "import_members": [item.as_dict() for item in self.import_members],
            "input_schema_ids": list(self.input_schema_ids),
            "logical_package": self.logical_package,
            "output_schema_ids": list(self.output_schema_ids),
            "package_version": self.package_version,
            "requested_authority": _thaw_json(self.requested_authority),
            "resource_budget": _thaw_json(self.resource_budget),
            "runtime": self.runtime.as_dict(),
            "schema_version": self.schema_version,
            "source_members": [item.as_dict() for item in self.source_members],
            "worker_protocol": self.worker_protocol,
        }

    def canonical_json(self) -> str:
        try:
            return canonical_json_bytes(self.as_dict()).decode("utf-8")
        except BundleError as exc:
            raise ModulePackageValidationError("manifest cannot be encoded canonically") from exc


@dataclass(frozen=True, slots=True)
class ModulePackage:
    """An immutable package identity backed by a CAS-pinned ZIP artifact."""

    package_digest: str
    artifact_ref: ArtifactRef
    manifest: ModuleManifest
    runtime_key: str
    import_members: tuple[tuple[str, str], ...]

    def __post_init__(self) -> None:
        try:
            require_sha256(self.package_digest, "package_digest")
            require_sha256(self.runtime_key, "runtime_key")
        except BundleValidationError as exc:
            raise ModulePackageValidationError(str(exc)) from exc
        if not isinstance(self.artifact_ref, ArtifactRef):
            raise ModulePackageValidationError("artifact_ref must be an ArtifactRef")
        if (
            self.artifact_ref.artifact_id != self.package_digest
            or self.artifact_ref.sha256 != self.package_digest
            or self.artifact_ref.media_type != _PACKAGE_MEDIA_TYPE
            or self.artifact_ref.metadata
        ):
            raise ModulePackageIntegrityError(
                "package artifact reference is not bound to the package digest"
            )
        if not isinstance(self.manifest, ModuleManifest):
            raise ModulePackageValidationError("manifest must be a ModuleManifest")
        expected_runtime_key = canonical_sha256(self.manifest.runtime.as_dict())
        if self.runtime_key != expected_runtime_key:
            raise ModulePackageIntegrityError("runtime key does not match manifest runtime")
        members = tuple(self.import_members)
        expected_members = tuple(
            (item.module, item.path) for item in self.manifest.import_members
        )
        if members != expected_members:
            raise ModulePackageIntegrityError(
                "import member accessor does not match manifest declarations"
            )
        object.__setattr__(self, "import_members", members)

    def lock_record(self) -> dict[str, object]:
        """Return a detached canonical package record for Lock compilation."""
        record: dict[str, object] = {
            "artifact_ref": self.artifact_ref.to_dict(),
            "import_members": [
                {"module": module, "path": path} for module, path in self.import_members
            ],
            "manifest": self.manifest.as_dict(),
            "package_digest": self.package_digest,
            "runtime_key": self.runtime_key,
        }
        try:
            canonical_json_bytes(record)
        except BundleError as exc:
            raise ModulePackageIntegrityError("package lock record is not canonical JSON") from exc
        return record


def _bundle_member_payload(
    bundle_entries: Mapping[str, BundleEntry], path: str, cas: FilesystemCAS
) -> bytes:
    entry = bundle_entries.get(path)
    if entry is None:
        raise ModulePackageIntegrityError(f"captured package member is missing: {path}")
    try:
        reference = cas.get_ref(entry.artifact_id)
        payload = cas.get_bytes(reference, max_bytes=entry.size_bytes)
    except (ArtifactStoreError, KeyError, FileNotFoundError) as exc:
        raise ModulePackageIntegrityError(
            f"captured package member cannot be read: {path}"
        ) from exc
    if len(payload) != entry.size_bytes or bytes_sha256(payload) != entry.blob_digest:
        raise ModulePackageIntegrityError(f"captured package member digest mismatch: {path}")
    return payload


def _manifest_from_bundle(
    bundle_entries: Mapping[str, BundleEntry],
    cas: FilesystemCAS,
    *,
    require_canonical: bool = True,
) -> tuple[ModuleManifest, bytes]:
    if _PACKAGE_MANIFEST_PATH not in bundle_entries:
        raise ModulePackageValidationError("package must contain module.json")
    payload = _bundle_member_payload(bundle_entries, _PACKAGE_MANIFEST_PATH, cas)
    try:
        decoded = canonical_json_loads(payload)
        canonical = canonical_json_bytes(decoded)
    except BundleError as exc:
        raise ModulePackageValidationError("module.json is not valid canonical JSON") from exc
    if not isinstance(decoded, Mapping):
        raise ModulePackageValidationError("module.json must contain an object")
    if require_canonical and canonical != payload:
        raise ModulePackageValidationError("module.json must use canonical JSON encoding")
    return ModuleManifest.from_dict(decoded), canonical


def _validate_declared_members(
    bundle_entries: Mapping[str, BundleEntry], manifest: ModuleManifest, cas: FilesystemCAS
) -> dict[str, bytes]:
    expected_paths = {_PACKAGE_MANIFEST_PATH}
    declarations: dict[str, tuple[str, int]] = {}
    for member in (*manifest.source_members, *manifest.import_members):
        expected_paths.add(member.path)
        current = (member.sha256, member.size_bytes)
        prior = declarations.get(member.path)
        if prior is not None and prior != current:
            raise ModulePackageIntegrityError(
                f"conflicting source/import declarations for captured member: {member.path}"
            )
        declarations[member.path] = current
    actual_paths = set(bundle_entries)
    missing = expected_paths - actual_paths
    extra = actual_paths - expected_paths
    if missing:
        raise ModulePackageIntegrityError(
            "package is missing declared members: " + ", ".join(sorted(missing))
        )
    if extra:
        raise ModulePackageValidationError(
            "package contains undeclared members: " + ", ".join(sorted(extra))
        )
    payloads: dict[str, bytes] = {}
    for path in sorted(actual_paths):
        payload = _bundle_member_payload(bundle_entries, path, cas)
        payloads[path] = payload
        if path == _PACKAGE_MANIFEST_PATH:
            continue
        digest, size_bytes = declarations[path]
        if bytes_sha256(payload) != digest or len(payload) != size_bytes:
            raise ModulePackageIntegrityError(
                f"captured bytes do not match manifest declaration: {path}"
            )
    return payloads


def _zip_bytes(payloads: Mapping[str, bytes]) -> bytes:
    output = io.BytesIO()
    try:
        with zipfile.ZipFile(output, mode="w", compression=zipfile.ZIP_STORED) as archive:
            for path in sorted(payloads):
                info = zipfile.ZipInfo(path, date_time=(1980, 1, 1, 0, 0, 0))
                info.create_system = 3
                info.create_version = 20
                info.extract_version = 20
                info.flag_bits = 0x800
                info.compress_type = zipfile.ZIP_STORED
                info.external_attr = 0o100444 << 16
                archive.writestr(info, payloads[path])
    except (OSError, ValueError, zipfile.BadZipFile) as exc:
        raise ModulePackageSecurityError("module package ZIP cannot be created") from exc
    archive_bytes = output.getvalue()
    if len(archive_bytes) > BundleLimits().max_archive_bytes:
        raise ModulePackageValidationError("module package archive exceeds the byte limit")
    return archive_bytes


def _write_output(path: Path, payload: bytes) -> None:
    if path.exists() and path.is_dir():
        raise ModulePackageSecurityError("package output must be a regular file")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
        )
        try:
            os.fchmod(descriptor, 0o600)
            with os.fdopen(descriptor, "wb") as stream:
                descriptor = -1
                stream.write(payload)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary_name, path)
        finally:
            if descriptor >= 0:
                os.close(descriptor)
            try:
                os.unlink(temporary_name)
            except FileNotFoundError:
                pass
    except OSError as exc:
        raise ModulePackageSecurityError("module package output cannot be published") from exc


def _publish_package(payload: bytes, digest: str, cas: FilesystemCAS) -> ArtifactRef:
    try:
        reference = cas.put_bytes(
            payload,
            artifact_id=digest,
            media_type=_PACKAGE_MEDIA_TYPE,
        )
    except ArtifactStoreError as exc:
        raise ModulePackageIntegrityError(
            "module package cannot be published to CAS"
        ) from exc
    if (
        reference.artifact_id != digest
        or reference.sha256 != digest
        or reference.size_bytes != len(payload)
        or reference.media_type != _PACKAGE_MEDIA_TYPE
        or reference.metadata
    ):
        raise ModulePackageIntegrityError("CAS returned a rebound module package reference")
    return reference


def _package_from_archive(
    archive_bytes: bytes,
    bundle_entries: Mapping[str, BundleEntry],
    cas: FilesystemCAS,
    *,
    expected_digest: str,
) -> ModulePackage:
    actual_digest = bytes_sha256(archive_bytes)
    if actual_digest != expected_digest:
        raise ModulePackageIntegrityError(
            f"module package digest mismatch: expected {expected_digest}, got {actual_digest}"
        )
    manifest, canonical_manifest = _manifest_from_bundle(bundle_entries, cas)
    payloads = _validate_declared_members(bundle_entries, manifest, cas)
    if payloads[_PACKAGE_MANIFEST_PATH] != canonical_manifest:
        raise ModulePackageIntegrityError("module.json changed during package validation")
    reference = _publish_package(archive_bytes, actual_digest, cas)
    return ModulePackage(
        package_digest=actual_digest,
        artifact_ref=reference,
        manifest=manifest,
        runtime_key=canonical_sha256(manifest.runtime.as_dict()),
        import_members=tuple((item.module, item.path) for item in manifest.import_members),
    )


def build_module_package(
    source: Path, output: Path, *, cas: FilesystemCAS
) -> ModulePackage:
    """Capture and package a source directory without importing its code."""

    source_path, output_path = Path(source).expanduser(), Path(output).expanduser()
    if not source_path.is_dir():
        raise ModulePackageSecurityError("module package source must be a directory")
    try:
        bundle = ingest_bundle(
            source_path,
            cas,
            entrypoints={_PACKAGE_ENTRYPOINT: _PACKAGE_MANIFEST_PATH},
            source_label=str(source_path),
        )
    except (BundleError, OSError) as exc:
        raise ModulePackageSecurityError(f"module package source rejected: {exc}") from exc
    entries = {entry.logical_path: entry for entry in bundle.entries}
    manifest, canonical_manifest = _manifest_from_bundle(
        entries, cas, require_canonical=False
    )
    payloads = _validate_declared_members(entries, manifest, cas)
    payloads[_PACKAGE_MANIFEST_PATH] = canonical_manifest
    archive_bytes = _zip_bytes(payloads)
    digest = bytes_sha256(archive_bytes)
    reference = _publish_package(archive_bytes, digest, cas)
    _write_output(output_path, archive_bytes)
    return ModulePackage(
        package_digest=digest,
        artifact_ref=reference,
        manifest=manifest,
        runtime_key=canonical_sha256(manifest.runtime.as_dict()),
        import_members=tuple((item.module, item.path) for item in manifest.import_members),
    )


def load_module_package(
    source: Path, expected_digest: str, *, cas: FilesystemCAS
) -> ModulePackage:
    """Load a byte-pinned package through the hardened ZIP/bundle boundary."""

    try:
        expected = require_sha256(expected_digest, "expected_digest")
    except BundleValidationError as exc:
        raise ModulePackageValidationError(str(exc)) from exc
    source_path = Path(source).expanduser()
    try:
        bundle = ingest_bundle(
            source_path,
            cas,
            entrypoints={_PACKAGE_ENTRYPOINT: _PACKAGE_MANIFEST_PATH},
            archive_format="zip",
            source_label=str(source_path),
        )
    except (BundleError, OSError) as exc:
        raise ModulePackageSecurityError(f"module package archive rejected: {exc}") from exc
    actual = bundle.provenance.raw_source_digest
    if actual != expected:
        raise ModulePackageIntegrityError(
            f"module package digest mismatch: expected {expected}, got {actual}"
        )
    try:
        archive_bytes = source_path.read_bytes()
    except OSError as exc:
        raise ModulePackageSecurityError("module package archive cannot be read") from exc
    if len(archive_bytes) > BundleLimits().max_archive_bytes:
        raise ModulePackageValidationError("module package archive exceeds the byte limit")
    if bytes_sha256(archive_bytes) != actual:
        raise ModulePackageIntegrityError("module package changed while being loaded")
    entries = {entry.logical_path: entry for entry in bundle.entries}
    return _package_from_archive(
        archive_bytes,
        entries,
        cas,
        expected_digest=expected,
    )


__all__ = [
    "ChildTarget",
    "ImportMember",
    "ModuleManifest",
    "ModulePackage",
    "ModulePackageError",
    "ModulePackageIntegrityError",
    "ModulePackageSecurityError",
    "ModulePackageValidationError",
    "RuntimeDescriptor",
    "SourceMember",
    "build_module_package",
    "load_module_package",
]
