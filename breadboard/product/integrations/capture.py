"""Capture adapters, trusted local declarations, and installed entry points."""
from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
from importlib import metadata
import json
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Protocol, Sequence, runtime_checkable

from .catalog import (
    IntegrationDescriptor,
    IncompatibleAdapterError,
    ProjectDeclarationError,
    ProbeReport,
    probe_for,
)

_CAPTURE_GROUP = "breadboard.capture_adapters"


@runtime_checkable
class CapturePort(Protocol):
    def capture(self, value: Any) -> Any: ...


@dataclass(frozen=True)
class ProjectCaptureDeclaration:
    adapter_id: str
    source_path: str
    source_sha256: str
    grants: tuple[str, ...]

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "ProjectCaptureDeclaration":
        adapter_id = value.get("adapter_id", value.get("id", value.get("adapter")))
        source_path = value.get("source_path", value.get("source"))
        digest = value.get("source_sha256", value.get("source_hash", value.get("sha256")))
        grants = value.get("grants")
        if not isinstance(adapter_id, str) or not isinstance(source_path, str) or not isinstance(digest, str):
            raise ProjectDeclarationError("local capture declaration requires adapter, source, and source_sha256")
        if not isinstance(grants, (list, tuple, set)) or not grants or not all(isinstance(item, str) and item for item in grants):
            raise ProjectDeclarationError("local capture declaration requires explicit grants")
        if not digest.startswith("sha256:") or len(digest) != 71:
            raise ProjectDeclarationError("local capture declaration requires a sha256 source hash")
        return cls(adapter_id, source_path, digest, tuple(sorted(set(grants))))

    def verify(self, project_root: str | Path) -> Path:
        if not self.grants or "capture" not in self.grants and f"capture:{self.adapter_id}" not in self.grants:
            raise ProjectDeclarationError("local capture declaration is missing the capture grant")
        root = Path(project_root).resolve()
        path = (root / self.source_path).resolve()
        try:
            path.relative_to(root)
        except ValueError as exc:
            raise ProjectDeclarationError("local capture source must remain inside project root") from exc
        if not path.is_file():
            raise ProjectDeclarationError(f"local capture source is missing: {self.source_path}")
        actual = "sha256:" + sha256(path.read_bytes()).hexdigest()
        if actual != self.source_sha256:
            raise ProjectDeclarationError("local capture source hash does not match declaration")
        return path


class CaptureIntegrationAdapter:
    """Conforming wrapper around a built-in or installed capture implementation."""

    def __init__(
        self,
        adapter_id: str,
        implementation: CapturePort | Callable[[Any], Any],
        *,
        source_sha256: str,
        implementation_id: str | None = None,
        capabilities: Iterable[str] = ("capture",),
        effects: Iterable[str] = ("record",),
        permissions: Iterable[str] = ("capture.write",),
    ) -> None:
        if not adapter_id or not callable(implementation) and not callable(getattr(implementation, "capture", None)):
            raise IncompatibleAdapterError("capture adapter must expose capture()")
        if not _valid_hash(source_sha256):
            raise IncompatibleAdapterError("capture adapter source hash is required")
        self.adapter_id = adapter_id
        self.implementation = implementation
        self.source_sha256 = source_sha256
        self.descriptor = IntegrationDescriptor(
            "bb.integration_descriptor.v1",
            "capture:" + adapter_id,
            "capture_adapter",
            "capture-port.v1",
            implementation_id or type(implementation).__name__,
            tuple(sorted(set(capabilities))),
            secret_reference_names=(),
            effects=tuple(sorted(set(effects))),
            permissions=tuple(sorted(set(permissions))),
        )

    def capture(self, value: Any) -> Any:
        method = getattr(self.implementation, "capture", None)
        return method(value) if callable(method) else self.implementation(value)  # type: ignore[operator]

    def probe(self) -> ProbeReport:
        return probe_for(self.descriptor)


class MemoryCaptureAdapter:
    """Deterministic internal capture adapter used by offline and conformance lanes."""

    source_sha256 = "sha256:" + "0" * 64

    def capture(self, value: Any) -> Any:
        return value


class JsonCaptureAdapter:
    """Deterministic internal capture adapter that emits canonical JSON bytes."""

    source_sha256 = "sha256:" + "1" * 64

    def capture(self, value: Any) -> bytes:
        return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")


def internal_capture_adapters() -> tuple[CaptureIntegrationAdapter, CaptureIntegrationAdapter]:
    return (
        CaptureIntegrationAdapter("memory", MemoryCaptureAdapter(), source_sha256=MemoryCaptureAdapter.source_sha256, implementation_id="breadboard.memory"),
        CaptureIntegrationAdapter("json", JsonCaptureAdapter(), source_sha256=JsonCaptureAdapter.source_sha256, implementation_id="breadboard.json"),
    )


def load_capture_entry_points(*, group: str = _CAPTURE_GROUP) -> list[CaptureIntegrationAdapter]:
    """Load only installed entry points; never import a project module by sys.path."""
    if group != _CAPTURE_GROUP:
        raise IncompatibleAdapterError(f"unsupported capture entry-point group: {group}")
    points = metadata.entry_points()
    selected = list(points.select(group=group)) if hasattr(points, "select") else list(points.get(group, ()))
    loaded: list[CaptureIntegrationAdapter] = []
    for point in sorted(selected, key=lambda item: item.name):
        try:
            implementation = point.load()
            if isinstance(implementation, type):
                implementation = implementation()
            source_hash = getattr(implementation, "source_sha256", getattr(implementation, "source_hash", None))
            adapter_id = str(getattr(implementation, "adapter_id", point.name))
            loaded.append(CaptureIntegrationAdapter(adapter_id, implementation, source_sha256=source_hash, implementation_id=f"entrypoint:{point.name}"))
        except Exception as exc:
            raise IncompatibleAdapterError(f"capture entry point {point.name!r} is incompatible") from exc
    return loaded


def resolve_local_capture_declaration(
    declaration: Mapping[str, Any] | ProjectCaptureDeclaration,
    *,
    project_root: str,
    adapters: Mapping[str, CaptureIntegrationAdapter] | None = None,
    adapter: CaptureIntegrationAdapter | None = None,
) -> CaptureIntegrationAdapter:
    """Validate a hash-bound declaration and use only an explicitly supplied adapter."""
    parsed = declaration if isinstance(declaration, ProjectCaptureDeclaration) else ProjectCaptureDeclaration.from_mapping(declaration)
    parsed.verify(project_root)
    selected = adapter or (adapters or {}).get(parsed.adapter_id)
    if selected is None or selected.adapter_id != parsed.adapter_id:
        raise ProjectDeclarationError("local declaration names an unknown adapter")
    if selected.source_sha256 != parsed.source_sha256:
        raise ProjectDeclarationError("local adapter source hash does not match declaration")
    return selected


def _valid_hash(value: Any) -> bool:
    return isinstance(value, str) and len(value) == 71 and value.startswith("sha256:") and all(char in "0123456789abcdef" for char in value[7:])
