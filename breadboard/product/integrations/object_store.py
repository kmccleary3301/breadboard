"""Artifact/object-store adapter for the frozen integration catalog port."""
from __future__ import annotations

from typing import Any, Iterable, Mapping, Protocol, runtime_checkable

from .catalog import IntegrationDescriptor, ProbeReport, probe_for


@runtime_checkable
class ObjectStorePort(Protocol):
    def put(self, key: str, value: bytes) -> Any: ...
    def get(self, key: str) -> bytes: ...


class ObjectStoreIntegrationAdapter:
    def __init__(
        self,
        store_id: str,
        store: ObjectStorePort,
        *,
        implementation_id: str | None = None,
        capabilities: Iterable[str] = ("put", "get"),
        effects: Iterable[str] = ("artifact.write", "artifact.read"),
        permissions: Iterable[str] = ("artifact.write", "artifact.read"),
    ) -> None:
        if not store_id or not callable(getattr(store, "put", None)) or not callable(getattr(store, "get", None)):
            raise TypeError("object store requires put() and get()")
        self.store_id = store_id
        self.store = store
        self.descriptor = IntegrationDescriptor(
            "bb.integration_descriptor.v1",
            "store:" + store_id,
            "artifact_store",
            "object-store-port.v1",
            implementation_id or type(store).__name__,
            tuple(sorted(set(capabilities))),
            effects=tuple(sorted(set(effects))),
            permissions=tuple(sorted(set(permissions))),
        )

    def put(self, key: str, value: bytes) -> Any:
        return self.store.put(key, value)

    def get(self, key: str) -> bytes:
        return self.store.get(key)

    def probe(self) -> ProbeReport:
        return probe_for(self.descriptor)
