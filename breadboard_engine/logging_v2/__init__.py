"""Compatibility package for the canonical run-logging subsystem."""

import warnings

from ..run_logging import LoggerV2Manager

warnings.warn(
    "`breadboard_engine.logging_v2` is deprecated; use "
    "`breadboard_engine.run_logging` instead.",
    DeprecationWarning,
    stacklevel=2,
)

__all__ = ["LoggerV2Manager"]
