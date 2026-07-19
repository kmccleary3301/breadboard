"""Kernel extension interfaces and registry scaffolding."""

from .interfaces import (
    ExtensionContext,
    ExtensionManifest,
    EndpointProvider,
    HookProvider,
    SandboxProvider,
    ToolProvider,
)
from .registry import ExtensionError, ExtensionRegistry

__all__ = [
    "ExtensionContext",
    "ExtensionError",
    "ExtensionManifest",
    "EndpointProvider",
    "HookProvider",
    "SandboxProvider",
    "ToolProvider",
    "ExtensionRegistry",
]
