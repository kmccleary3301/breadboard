"""
Compatibility wrapper for the canonical LSP manager module.

The richer multi-language implementation now lives at `breadboard.lsp_manager`.
This module remains as a temporary compatibility import path.
"""

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
