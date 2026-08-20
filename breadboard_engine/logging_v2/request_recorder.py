"""Compatibility wrapper for `breadboard_engine.run_logging.request_recorder`."""

import warnings

from ..run_logging.request_recorder import *  # noqa: F403

warnings.warn(
    "`breadboard_engine.logging_v2.request_recorder` is deprecated; use "
    "`breadboard_engine.run_logging.request_recorder` instead.",
    DeprecationWarning,
    stacklevel=2,
)
