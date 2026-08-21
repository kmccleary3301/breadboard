"""Canonical package for provider support modules."""

from __future__ import annotations

from importlib import import_module
from typing import Any

__all__ = [
    "CAPABILITY_MATRIX",
    "OpenAIAdapter",
    "ProviderDescriptor",
    "ProviderCapabilities",
    "ProviderCapabilityProbeRunner",
    "ReplayRuntime",
    "ProviderInvoker",
    "ProviderMetricsCollector",
    "RouteHealthManager",
    "provider_adapter_manager",
    "provider_router",
    "normalize_provider_result",
    "sanitize_openai_tool_name",
]

_SYMBOL_TO_MODULE = {
    "CAPABILITY_MATRIX": ".capabilities",
    "OpenAIAdapter": ".adapters",
    "ProviderDescriptor": ".routing",
    "ProviderCapabilities": ".capabilities",
    "ProviderCapabilityProbeRunner": ".capability_probe",
    "ReplayRuntime": ".runtime_replay",
    "ProviderInvoker": ".invoker",
    "ProviderMetricsCollector": ".metrics",
    "RouteHealthManager": ".health",
    "provider_adapter_manager": ".adapters",
    "provider_router": ".routing",
    "normalize_provider_result": ".normalizer",
    "sanitize_openai_tool_name": ".adapters",
}


def __getattr__(name: str) -> Any:
    module_name = _SYMBOL_TO_MODULE.get(name)
    if module_name is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    module = import_module(module_name, __name__)
    value = getattr(module, name)
    globals()[name] = value
    return value
