"""Immutable effective Harness Lock records and canonical identity helpers."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Iterator, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from types import MappingProxyType
from typing import Any

from breadboard.product.operations.model import portable_ref


_SHA256_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
LOCK_SCHEMA_VERSION = "bb.effective_harness_lock.v2"
GRAPH_SCHEMA_VERSION = "bb.effective_config_graph.v1"


def _copy(value: Any, *, freeze: bool) -> Any:
    """Detach a JSON value, optionally replacing containers with immutable ones."""

    if isinstance(value, Mapping):
        if any(type(key) is not str for key in value):
            raise TypeError("JSON object keys must be strings")
        copied = {key: _copy(item, freeze=freeze) for key, item in value.items()}
        return MappingProxyType(copied) if freeze else copied
    if isinstance(value, (list, tuple)):
        copied = [_copy(item, freeze=freeze) for item in value]
        return tuple(copied) if freeze else copied
    json.dumps(value, allow_nan=False)
    return value


def canonical_json_bytes(value: Any) -> bytes:
    """Return the existing BreadBoard canonical JSON encoding."""

    plain = _copy(value, freeze=False)
    text = (
        json.dumps(
            plain,
            allow_nan=False,
            ensure_ascii=False,
            indent=2,
            separators=(",", ": "),
            sort_keys=True,
        )
        + "\n"
    )
    return text.encode("utf-8")


def sha256_bytes(value: bytes) -> str:
    return "sha256:" + hashlib.sha256(value).hexdigest()


def sha256_json(value: Any) -> str:
    return sha256_bytes(canonical_json_bytes(value))


def graph_content_hash(record: Mapping[str, Any]) -> str:
    """Hash only a configuration graph using its legacy ``graph_hash`` preimage.

    A v2 Lock is accepted as a convenience for retained callers, but the
    resulting value is always the nested configuration graph identity and
    never the complete Lock identity.
    """

    value = record
    if record.get("schema_version") == LOCK_SCHEMA_VERSION:
        nested = record.get("configuration_graph")
        if not isinstance(nested, Mapping):
            raise ValueError("v2 Lock has no configuration_graph")
        value = nested
    preimage = _copy(value, freeze=False)
    preimage["graph_hash"] = None
    return sha256_json(preimage)


def lock_content_hash(record: Mapping[str, Any]) -> str:
    """Hash a complete v2 Lock with its identity field unset."""

    preimage = _copy(record, freeze=False)
    preimage["lock_id"] = None
    return sha256_json(preimage)


def _validate_v2_record(record: Mapping[str, Any]) -> None:
    required = {
        "schema_version",
        "lock_id",
        "configuration_graph",
        "configuration_artifacts",
        "modules",
    }
    unknown = sorted(set(record) - required)
    missing = sorted(required - set(record))
    if unknown:
        raise ValueError("v2 Lock contains unknown fields: " + ", ".join(unknown))
    if missing:
        raise ValueError("v2 Lock is missing required fields: " + ", ".join(missing))
    if record.get("schema_version") != LOCK_SCHEMA_VERSION:
        raise ValueError("unsupported Harness Lock schema version")
    lock_id = record.get("lock_id")
    if not isinstance(lock_id, str) or _SHA256_RE.fullmatch(lock_id) is None:
        raise ValueError("v2 Lock lock_id must be a full sha256 digest")
    graph = record.get("configuration_graph")
    if not isinstance(graph, Mapping):
        raise ValueError("v2 Lock configuration_graph must be a mapping")
    artifacts = record.get("configuration_artifacts")
    if not isinstance(artifacts, Mapping):
        raise ValueError("v2 Lock configuration_artifacts must be an object")
    artifact_fields = {"artifact_id", "sha256", "size_bytes", "media_type", "metadata"}
    for source_ref, artifact in artifacts.items():
        if not isinstance(source_ref, str) or not isinstance(artifact, Mapping):
            raise ValueError("v2 Lock configuration artifact is invalid")
        if set(artifact) != artifact_fields:
            raise ValueError(f"v2 Lock configuration artifact is invalid: {source_ref}")
    if graph.get("schema_version") != GRAPH_SCHEMA_VERSION:
        raise ValueError("v2 Lock configuration_graph must be bb.effective_config_graph.v1")
    modules = record.get("modules")
    if modules is not None and not isinstance(modules, Mapping):
        raise ValueError("v2 Lock modules must be an object or null")
    if lock_content_hash(record) != lock_id:
        raise ValueError("v2 Lock lock_id does not match its canonical contents")


@dataclass(frozen=True, slots=True, init=False)
class _FrozenRecord(Mapping[str, Any]):
    _record: Mapping[str, Any] = field(repr=False)

    @classmethod
    def _from_record(cls, record: Mapping[str, Any]):
        instance = object.__new__(cls)
        object.__setattr__(instance, "_record", _copy(record, freeze=True))
        return instance

    def __getitem__(self, key: str) -> Any:
        return self._record[key]

    def __iter__(self) -> Iterator[str]:
        return iter(self._record)

    def __len__(self) -> int:
        return len(self._record)

    def as_dict(self) -> dict[str, Any]:
        return _copy(self._record, freeze=False)

    def canonical_json(self) -> str:
        return canonical_json_bytes(self._record).decode("utf-8")


class EffectiveHarnessLock(_FrozenRecord):
    """An immutable v2 complete Lock or a retained v1 graph history record."""

    __slots__ = ()

    @classmethod
    def _from_record(cls, record: Mapping[str, Any]) -> EffectiveHarnessLock:
        if not isinstance(record, Mapping):
            raise TypeError("Harness Lock must be a mapping")
        snapshot = _copy(record, freeze=False)
        if snapshot.get("schema_version") == LOCK_SCHEMA_VERSION:
            _validate_v2_record(snapshot)
        elif snapshot.get("schema_version") not in (None, GRAPH_SCHEMA_VERSION):
            raise ValueError("unsupported Harness Lock schema version")
        return super()._from_record(snapshot)

    @property
    def generation_id(self) -> str:
        """The complete immutable Lock identity used for work attribution."""

        if self._record.get("schema_version") == LOCK_SCHEMA_VERSION:
            return str(self._record["lock_id"])
        legacy = self._record.get("generation_id")
        if legacy is None:
            legacy = self._record.get("graph_hash")
        if not isinstance(legacy, str) or _SHA256_RE.fullmatch(legacy) is None:
            raise ValueError("retained v1 Lock has no generation identity")
        return legacy

    @property
    def configuration_graph(self) -> Mapping[str, Any]:
        """The configuration-only graph constituent, never a Lock identity."""

        if self._record.get("schema_version") == LOCK_SCHEMA_VERSION:
            graph = self._record["configuration_graph"]
            if not isinstance(graph, Mapping):
                raise ValueError("v2 Lock configuration_graph is invalid")
            return graph
        return self._record

    @property
    def configuration_graph_hash(self) -> str:
        graph = self.configuration_graph
        value = graph.get("graph_hash")
        if not isinstance(value, str) or _SHA256_RE.fullmatch(value) is None:
            raise ValueError("Lock configuration graph has no graph identity")
        return value

def configuration_artifact_id(source_ref: str, content_hash: str) -> str:
    """Return a deterministic CAS identity for exact source/resource bytes."""

    return "harness-config:" + sha256_json(
        {"content_hash": content_hash, "source_ref": source_ref}
    ).removeprefix("sha256:")


@dataclass(frozen=True, slots=True)
class LockMaterialization:
    lock: EffectiveHarnessLock
    configuration_graph: Mapping[str, Any]
    source_bytes: Mapping[str, bytes]
    resource_bytes: Mapping[str, bytes]
    package_bytes: Mapping[str, bytes]


def materialize_lock(
    lock: EffectiveHarnessLock,
    *,
    cas: Any,
) -> LockMaterialization:
    """Read the exact compiler-owned bytes bound to a complete Lock."""

    if not isinstance(lock, EffectiveHarnessLock):
        raise TypeError("materialize_lock requires an EffectiveHarnessLock")
    if lock["schema_version"] != LOCK_SCHEMA_VERSION:
        raise ValueError("only v2 Locks can be materialized")
    graph = lock.configuration_graph
    sources: dict[str, bytes] = {}
    resources: dict[str, bytes] = {}
    from breadboard.artifacts.references import ArtifactRef

    layers_by_ref = {
        layer["source_ref"]: layer
        for layer in graph.get("source_layers", ())
        if isinstance(layer, Mapping) and isinstance(layer.get("source_ref"), str)
    }
    artifacts = lock["configuration_artifacts"]
    if not isinstance(artifacts, Mapping):
        raise ValueError("v2 Lock configuration_artifacts must be an object")
    if set(artifacts) != set(layers_by_ref):
        raise ValueError("v2 Lock configuration artifacts do not match graph sources")
    for source_ref, artifact in artifacts.items():
        if not isinstance(source_ref, str) or not isinstance(artifact, Mapping):
            raise ValueError("v2 Lock configuration artifact is invalid")
        layer = layers_by_ref[source_ref]
        layer_hash = layer["layer_hash"]
        try:
            reference = ArtifactRef(
                artifact_id=str(artifact["artifact_id"]),
                sha256=str(artifact["sha256"]),
                size_bytes=int(artifact["size_bytes"]),
                media_type=str(artifact["media_type"]),
                metadata=dict(artifact.get("metadata", {})),
            )
            payload = cas.get_bytes(reference, max_bytes=reference.size_bytes)
        except (KeyError, TypeError, ValueError) as error:
            raise ValueError(
                f"v2 Lock configuration artifact is invalid: {source_ref}"
            ) from error
        if layer.get("scope") == "resource":
            if sha256_bytes(payload) != layer_hash:
                raise ValueError(f"locked resource bytes failed verification: {source_ref}")
            resources[source_ref] = payload
        else:
            try:
                import yaml

                parsed = yaml.safe_load(payload.decode("utf-8"))
                from .compile import _runtime_values

                if not isinstance(parsed, Mapping) or sha256_json(
                    _runtime_values(parsed)
                ) != layer_hash:
                    raise ValueError("source layer digest mismatch")
            except (UnicodeDecodeError, ValueError, TypeError, yaml.YAMLError) as error:
                raise ValueError(
                    f"locked configuration source failed verification: {source_ref}"
                ) from error
            sources[source_ref] = payload
    packages: dict[str, bytes] = {}
    modules = lock.get("modules")
    if isinstance(modules, Mapping):
        bindings = modules.get("bindings")
        if isinstance(bindings, Mapping):
            from breadboard.artifacts.references import ArtifactRef

            for name, binding in bindings.items():
                if not isinstance(name, str) or not isinstance(binding, Mapping):
                    raise ValueError("v2 Lock module binding is invalid")
                package = binding.get("package")
                if not isinstance(package, Mapping):
                    raise ValueError(f"v2 Lock package record is invalid: {name}")
                artifact = package.get("artifact_ref")
                if not isinstance(artifact, Mapping):
                    raise ValueError(f"v2 Lock package artifact is invalid: {name}")
                try:
                    reference = ArtifactRef(
                        artifact_id=str(artifact["artifact_id"]),
                        sha256=str(artifact["sha256"]),
                        size_bytes=int(artifact["size_bytes"]),
                        media_type=str(artifact["media_type"]),
                        metadata=dict(artifact.get("metadata", {})),
                    )
                    payload = cas.get_bytes(reference, max_bytes=reference.size_bytes)
                    expected_digest = package.get("package_digest")
                    if not isinstance(expected_digest, str) or sha256_bytes(payload) != expected_digest:
                        raise ValueError(f"v2 Lock package digest mismatch: {name}")
                    packages[name] = payload
                except (KeyError, TypeError, ValueError) as error:
                    raise ValueError(f"v2 Lock package artifact is invalid: {name}") from error
    return LockMaterialization(
        lock=lock,
        configuration_graph=_copy(graph, freeze=True),
        source_bytes=MappingProxyType(sources),
        resource_bytes=MappingProxyType(resources),
        package_bytes=MappingProxyType(packages),
    )
def make_effective_harness_lock(
    configuration_graph: Mapping[str, Any],
    modules: Mapping[str, Any] | None,
    configuration_artifacts: Mapping[str, Any] | None = None,
) -> EffectiveHarnessLock:
    """Build and self-hash one complete immutable v2 Lock."""

    if not isinstance(configuration_graph, Mapping):
        raise TypeError("configuration_graph must be a mapping")
    if configuration_graph.get("schema_version") != GRAPH_SCHEMA_VERSION:
        raise ValueError("configuration_graph must be bb.effective_config_graph.v1")
    record: dict[str, Any] = {
        "configuration_artifacts": (
            {}
            if configuration_artifacts is None
            else _copy(configuration_artifacts, freeze=False)
        ),
        "configuration_graph": _copy(configuration_graph, freeze=False),
        "lock_id": None,
        "modules": None if modules is None else _copy(modules, freeze=False),
        "schema_version": LOCK_SCHEMA_VERSION,
    }
    record["lock_id"] = lock_content_hash(record)
    return EffectiveHarnessLock._from_record(record)



def lock_path(path: str | Path, out: str | Path | None = None) -> Path:
    """Return the effective lock path for a harness source."""

    source = path if isinstance(path, Path) else Path(path)
    if out:
        target = Path(out).expanduser()
        return (
            target
            if target.is_file() or target.suffix == ".json"
            else target / f"{source.stem}.lock.json"
        )
    return source.with_name(source.stem + ".lock.json")


def lock_metadata_path(path: str | Path) -> Path:
    """Return the sidecar metadata path for an effective lock."""

    source = path if isinstance(path, Path) else Path(path)
    return source.with_name("." + source.name + ".meta.json")


def load_lock(
    path: str | Path,
    workspace: str | Path,
    *,
    explicit: bool = False,
) -> tuple[EffectiveHarnessLock, Path]:
    """Load an immutable Lock and require its metadata sidecar."""

    source = path if isinstance(path, Path) else Path(path)
    root = workspace if isinstance(workspace, Path) else Path(workspace)
    target = source if explicit or source.name.endswith(".lock.json") else lock_path(source)
    if not target.exists():
        raise FileNotFoundError(f"lock is missing: {portable_ref(target, root)}")
    metadata = lock_metadata_path(target)
    if not metadata.exists():
        raise ValueError("lock metadata is missing; lock must be regenerated")
    return EffectiveHarnessLock._from_record(json.loads(target.read_text())), metadata
