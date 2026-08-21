"""Registry for provider runtime implementations."""

from __future__ import annotations

from typing import Dict, Optional, Type

from .contracts import ProviderRuntime, ProviderRuntimeContext, ProviderRuntimeError
from .routing import ProviderDescriptor


_BUILTIN_RUNTIME_ORDER = (
    "openai_chat",
    "openrouter_chat",
    "openai_responses",
    "anthropic_messages",
    "mock_chat",
    "smoke_chat",
    "cli_mock_chat",
    "replay",
    "codex_app_server",
)


class ProviderRuntimeRegistry:
    """Registry that maps runtime identifiers to implementation classes."""

    def __init__(self) -> None:
        self._runtime_classes: Dict[str, Type[ProviderRuntime]] = {}

    def register_runtime(self, runtime_id: str, runtime_cls: Type[ProviderRuntime]) -> None:
        if not issubclass(runtime_cls, ProviderRuntime):  # defensive guard
            raise TypeError(f"Runtime {runtime_cls!r} must inherit ProviderRuntime")
        self._runtime_classes[runtime_id] = runtime_cls
        if runtime_id in _BUILTIN_RUNTIME_ORDER:
            # Concrete runtime modules register at import time. Rebuild only
            # known built-ins so prior import order cannot move them, while
            # retaining every third-party registration after the canonical set.
            builtin_classes = {
                name: self._runtime_classes[name]
                for name in _BUILTIN_RUNTIME_ORDER
                if name in self._runtime_classes
            }
            third_party_classes = {
                name: runtime
                for name, runtime in self._runtime_classes.items()
                if name not in builtin_classes
            }
            self._runtime_classes.clear()
            self._runtime_classes.update(builtin_classes)
            self._runtime_classes.update(third_party_classes)

    def get_runtime_class(self, runtime_id: str) -> Optional[Type[ProviderRuntime]]:
        return self._runtime_classes.get(runtime_id)

    def create_runtime(self, descriptor: ProviderDescriptor) -> ProviderRuntime:
        runtime_cls = self.get_runtime_class(descriptor.runtime_id)
        if runtime_cls is None:
            raise ProviderRuntimeError(
                f"Unknown provider runtime '{descriptor.runtime_id}' for provider '{descriptor.provider_id}'"
            )
        return runtime_cls(descriptor)


provider_registry = ProviderRuntimeRegistry()
