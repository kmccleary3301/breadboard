"""Registry for provider runtime implementations."""

from __future__ import annotations

from typing import Dict, Optional, Type

from .contracts import ProviderRuntime, ProviderRuntimeContext, ProviderRuntimeError
from .routing import ProviderDescriptor


class ProviderRuntimeRegistry:
    """Registry that maps runtime identifiers to implementation classes."""

    def __init__(self) -> None:
        self._runtime_classes: Dict[str, Type[ProviderRuntime]] = {}

    def register_runtime(self, runtime_id: str, runtime_cls: Type[ProviderRuntime]) -> None:
        if not issubclass(runtime_cls, ProviderRuntime):  # defensive guard
            raise TypeError(f"Runtime {runtime_cls!r} must inherit ProviderRuntime")
        self._runtime_classes[runtime_id] = runtime_cls

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
