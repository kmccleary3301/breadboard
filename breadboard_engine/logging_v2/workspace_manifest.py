"""Compatibility wrapper for `agentic_coder_prototype.run_logging.workspace_manifest`."""

import warnings

from ..run_logging.workspace_manifest import *  # noqa: F403

warnings.warn(
    "`agentic_coder_prototype.logging_v2.workspace_manifest` is deprecated; use "
    "`agentic_coder_prototype.run_logging.workspace_manifest` instead.",
    DeprecationWarning,
    stacklevel=2,
)
