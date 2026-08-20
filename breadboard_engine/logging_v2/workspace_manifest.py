"""Compatibility wrapper for `breadboard_engine.run_logging.workspace_manifest`."""

import warnings

from ..run_logging.workspace_manifest import *  # noqa: F403

warnings.warn(
    "`breadboard_engine.logging_v2.workspace_manifest` is deprecated; use "
    "`breadboard_engine.run_logging.workspace_manifest` instead.",
    DeprecationWarning,
    stacklevel=2,
)
