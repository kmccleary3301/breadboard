"""Kernel extension interfaces and registry scaffolding."""

from .interfaces import (
    ExtensionContext,
    ExtensionManifest,
    EndpointProvider,
    HookProvider,
    SandboxProvider,
    ToolProvider,
)
from .registry import ExtensionRegistry

__all__ = [
    "ExtensionContext",
    "ExtensionManifest",
    "EndpointProvider",
    "HookProvider",
    "SandboxProvider",
    "ToolProvider",
    "ExtensionRegistry",
]
