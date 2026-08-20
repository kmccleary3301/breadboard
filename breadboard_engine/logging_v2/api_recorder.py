"""Compatibility wrapper for `breadboard_engine.run_logging.api_recorder`."""

import warnings

from ..run_logging.api_recorder import *  # noqa: F403

warnings.warn(
    "`breadboard_engine.logging_v2.api_recorder` is deprecated; use "
    "`breadboard_engine.run_logging.api_recorder` instead.",
    DeprecationWarning,
    stacklevel=2,
)
