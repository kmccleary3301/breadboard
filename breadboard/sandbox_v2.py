"""Compatibility wrapper for the old sandbox module path.

The canonical module path is now `breadboard.sandbox`. Keep this wrapper during
the transition so existing imports do not break.
"""

from .sandbox import DevSandbox, DevSandboxV2, new_dev_sandbox, new_dev_sandbox_v2
import warnings

warnings.warn(
    "`breadboard.sandbox_v2` is deprecated; use `breadboard.sandbox` instead.",
    DeprecationWarning,
    stacklevel=2,
)

__all__ = [
    "DevSandbox",
    "DevSandboxV2",
    "new_dev_sandbox",
    "new_dev_sandbox_v2",
]
