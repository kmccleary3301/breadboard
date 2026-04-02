"""Compatibility wrapper for the canonical sandbox factory module."""

from .sandbox_factory import DeploymentMode, SandboxFactory
import warnings

warnings.warn(
    "`breadboard.sandbox_virtualized` is deprecated; use `breadboard.sandbox_factory` instead.",
    DeprecationWarning,
    stacklevel=2,
)

__all__ = ["DeploymentMode", "SandboxFactory"]
