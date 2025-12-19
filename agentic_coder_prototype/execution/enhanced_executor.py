from __future__ import annotations

from typing import Any, Dict
import inspect


class EnhancedToolExecutor:
    """Minimal enhanced executor stub.

    Provides async-compatible execution hooks and optional LSP metrics capture.
    """

    def __init__(self, sandbox: Any, config: Dict[str, Any]) -> None:
        self.sandbox = sandbox
        self.config = config or {}
        self._pending_lsp_metrics: Dict[str, Any] = {}

    async def execute_tool_call(self, tool_call: Dict[str, Any], exec_func) -> Dict[str, Any]:
        """Execute tool call via provided async-compatible exec function."""
        result = exec_func(tool_call)
        if inspect.isawaitable(result):
            result = await result
        return result

    def get_workspace_context(self) -> Dict[str, Any]:
        return {}

    def consume_lsp_metrics(self) -> Dict[str, Any]:
        metrics = dict(self._pending_lsp_metrics)
        self._pending_lsp_metrics = {}
        return metrics
