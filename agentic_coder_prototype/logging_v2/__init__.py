"""Compatibility package for the canonical run-logging subsystem."""

import warnings

from ..run_logging import LoggerV2Manager

warnings.warn(
    "`agentic_coder_prototype.logging_v2` is deprecated; use "
    "`agentic_coder_prototype.run_logging` instead.",
    DeprecationWarning,
    stacklevel=2,
)

__all__ = ["LoggerV2Manager"]
