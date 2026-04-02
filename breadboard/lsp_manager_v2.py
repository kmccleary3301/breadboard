"""
Compatibility wrapper for the canonical LSP manager module.

The richer multi-language implementation now lives at `breadboard.lsp_manager`.
This module remains as a temporary compatibility import path.
"""

import warnings

from .lsp_manager import (
    CLILinterRunner,
    LSPJSONRPCClient,
    LSPManager,
    LSPManagerV2,
    LSPOrchestrator,
    LSPServer,
    LSP_SERVER_CONFIGS,
    UnifiedDiagnostics,
)

warnings.warn(
    "`breadboard.lsp_manager_v2` is deprecated; use `breadboard.lsp_manager` instead.",
    DeprecationWarning,
    stacklevel=2,
)

__all__ = [
    "CLILinterRunner",
    "LSPJSONRPCClient",
    "LSPManager",
    "LSPManagerV2",
    "LSPOrchestrator",
    "LSPServer",
    "LSP_SERVER_CONFIGS",
    "UnifiedDiagnostics",
]
