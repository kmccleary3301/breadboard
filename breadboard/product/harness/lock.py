"""Immutable effective Harness Lock records and canonical identity helpers."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Iterator, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from types import MappingProxyType
from typing import TYPE_CHECKING, Any

from breadboard.artifacts.cas import FilesystemCAS
from breadboard.artifacts.references import ArtifactRef
from breadboard.product.operations.model import portable_ref

if TYPE_CHECKING:
    from .packages import ModulePackage


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
    """Hash a configuration graph, never a complete Harness Lock."""
    if record.get("schema_version") != GRAPH_SCHEMA_VERSION:
        raise ValueError("graph_content_hash requires a configuration graph")
    preimage = _copy(record, freeze=False)
    preimage["graph_hash"] = None
    return sha256_json(preimage)


def lock_content_hash(record: Mapping[str, Any]) -> str:
    """Hash a complete v2 Lock with its identity field unset."""

    preimage = _copy(record, freeze=False)
    preimage["lock_id"] = None
    return sha256_json(preimage)


def _validate_v2_record(record: Mapping[str, Any]) -> None:
    from .validate import validate_effective_harness_lock

    findings = validate_effective_harness_lock(record)
    if findings:
        raise ValueError("; ".join(
            f"{item.pointer} [{item.code}]: {item.message}" for item in findings
        ))
    graph = record["configuration_graph"]
    if graph.get("graph_hash") != graph_content_hash(graph):
        raise ValueError("v2 Lock configuration graph identity does not match its contents")
    if lock_content_hash(record) != record["lock_id"]:
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
    packages: Mapping[str, ModulePackage]


def materialize_lock(
    lock: EffectiveHarnessLock,
    *,
    cas: FilesystemCAS,
) -> LockMaterialization:
    """Restore and verify captured inputs without reading any author path."""
    import yaml

    from .compile import _compile_module_bindings, _runtime_values
    from .packages import load_module_package
    from .validate import parse_harness_definition

    if lock["schema_version"] != LOCK_SCHEMA_VERSION:
        raise ValueError("only v2 Locks can be materialized")
    graph = lock.configuration_graph
    layers_by_ref = {}
    for layer in graph["source_layers"]:
        source_ref = layer["source_ref"]
        if not isinstance(source_ref, str):
            raise ValueError("Lock configuration source references must be strings")
        previous = layers_by_ref.setdefault(source_ref, layer)
        if (previous["scope"], previous["layer_hash"]) != (layer["scope"], layer["layer_hash"]):
            raise ValueError(f"Lock configuration source is inconsistent: {source_ref}")
    artifacts = lock["configuration_artifacts"]
    if set(artifacts) != set(layers_by_ref):
        raise ValueError("Lock configuration artifacts do not match graph sources")
    sources: dict[str, bytes] = {}
    resources: dict[str, bytes] = {}
    for source_ref, artifact in artifacts.items():
        layer = layers_by_ref[source_ref]
        reference = ArtifactRef.from_dict(artifact)
        if (
            reference.artifact_id != configuration_artifact_id(source_ref, reference.sha256)
            or reference.media_type != "application/octet-stream"
            or reference.metadata != {
                "source_ref": source_ref,
                "layer_hash": layer["layer_hash"],
                "content_sha256": reference.sha256,
            }
        ):
            raise ValueError(f"configuration artifact was rebound: {source_ref}")
        payload = cas.get_bytes(reference, max_bytes=reference.size_bytes)
        if layer["scope"] == "resource":
            if sha256_bytes(payload) != layer["layer_hash"]:
                raise ValueError(f"locked resource bytes failed verification: {source_ref}")
            resources[source_ref] = payload
        else:
            parsed = yaml.safe_load(payload.decode("utf-8"))
            if (
                not isinstance(parsed, Mapping)
                or sha256_json(_runtime_values(parsed)) != layer["layer_hash"]
            ):
                raise ValueError(f"locked configuration failed verification: {source_ref}")
            sources[source_ref] = payload
    packages: dict[str, bytes] = {}
    verified_packages: dict[str, ModulePackage] = {}
    modules = lock["modules"]
    if modules is not None:
        if set(modules) != {"root", "bindings"} or not isinstance(modules["bindings"], Mapping):
            raise ValueError("Lock module composition is invalid")
        declaration = {"root": modules["root"], "bindings": {}}
        for name, binding in modules["bindings"].items():
            if not isinstance(binding, Mapping) or set(binding) != {
                "package", "environment", "dependencies", "children", "config"
            }:
                raise ValueError(f"Lock module binding is invalid: {name}")
            package_record = binding["package"]
            reference = ArtifactRef.from_dict(package_record["artifact_ref"])
            payload = cas.get_bytes(reference, max_bytes=reference.size_bytes)
            package = load_module_package(payload, package_record["package_digest"], cas=cas)
            if package.lock_record() != _copy(package_record, freeze=False):
                raise ValueError(f"Lock package declaration does not match captured bytes: {name}")
            packages[name] = payload
            verified_packages[name] = package
            declaration["bindings"][name] = {
                "package": {"source": f"{name}.bbpkg", "digest": package.package_digest},
                "environment": binding["environment"],
                "dependencies": _copy(binding["dependencies"], freeze=False),
                "children": _copy(binding["children"], freeze=False),
                "config": _copy(binding["config"], freeze=False),
            }
        parse_harness_definition({
            "schema_version": "bb.harness_definition.v2",
            "version": 2,
            "modules": declaration,
        })
        if _compile_module_bindings(declaration, verified_packages) != _copy(modules, freeze=False):
            raise ValueError("Lock composition does not match its captured inputs")
    return LockMaterialization(
        lock=lock,
        configuration_graph=graph,
        source_bytes=MappingProxyType(sources),
        resource_bytes=MappingProxyType(resources),
        package_bytes=MappingProxyType(packages),
        packages=MappingProxyType(verified_packages),
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
