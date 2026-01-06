from __future__ import annotations

from typing import Any, Dict, List, Optional


class MCPManager:
    """Minimal placeholder MCP manager.

    The real implementation will manage MCP server processes and tool routing.
    """

    def __init__(self, servers: List[Dict[str, Any]] | None = None) -> None:
        self.servers = list(servers or [])
        self._tools: Dict[str, str] = {}

    def has_tool(self, name: str) -> bool:
        return name in self._tools

    def resolve_tool_server(self, name: str) -> Optional[str]:
        return self._tools.get(name)

    def call_tool(self, name: str, arguments: Dict[str, Any]) -> Any:
        raise RuntimeError(f"MCP tool '{name}' not available (MCP not configured).")

