from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable, Mapping, Protocol, Sequence, runtime_checkable


@dataclass(frozen=True)
class ExtensionContext:
    config: Mapping[str, Any]
    services: Mapping[str, Any] | None = None


class ToolProvider(Protocol):
    def register_tools(self, ctx: ExtensionContext) -> None: ...


class SandboxProvider(Protocol):
    def register_sandboxes(self, ctx: ExtensionContext) -> None: ...


class HookProvider(Protocol):
    def register_hooks(self, ctx: ExtensionContext) -> None: ...


@runtime_checkable
class EndpointProvider(Protocol):
    def register_routes(self, app: Any, get_service: Any) -> None: ...


@dataclass(frozen=True)
class ExtensionManifest:
    ext_id: str
    version: str
    provides: Sequence[str] = ()
    default_enabled: bool = False
    surfaces: Sequence[str] = ()
    description: str | None = None
    config_schema: Mapping[str, Any] | None = None


class Extension(Protocol):
    manifest: ExtensionManifest

    def providers(self) -> Iterable[object]: ...
