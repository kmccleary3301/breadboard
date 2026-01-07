from __future__ import annotations

import asyncio
import json
import threading
from contextlib import AsyncExitStack
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

try:
    from mcp.client.session import ClientSession
    from mcp.client.stdio import StdioServerParameters, stdio_client
except Exception as exc:  # pragma: no cover - optional dependency at runtime
    ClientSession = object  # type: ignore[misc,assignment]
    StdioServerParameters = object  # type: ignore[misc,assignment]
    stdio_client = None  # type: ignore[assignment]
    _MCP_IMPORT_ERROR: Exception | None = exc
else:  # pragma: no cover
    _MCP_IMPORT_ERROR = None


@dataclass(frozen=True)
class MCPToolInfo:
    name: str
    description: str | None
    schema: Dict[str, Any]
    server: str


class MCPManager:
    """Live MCP stdio client manager (sync wrapper).

    This manager:
      - Starts MCP servers over stdio based on config
      - Lists tools and exposes them with `mcp.<server>.<tool>` names
      - Routes tool calls to the correct server session
    """

    def __init__(self, servers: List[Dict[str, Any]] | None = None, *, startup_timeout_s: float = 20.0) -> None:
        if _MCP_IMPORT_ERROR is not None or stdio_client is None:
            raise RuntimeError(
                "MCP support requires the optional 'mcp' Python package. "
                "Install it (e.g. `pip install mcp`) or disable config.mcp.enabled."
            )
        self.servers = [dict(s) for s in (servers or []) if isinstance(s, dict)]
        self._tools: Dict[str, str] = {}
        self._tool_targets: Dict[str, Tuple[str, str]] = {}
        self._tool_infos: List[MCPToolInfo] = []
        self._sessions: Dict[str, ClientSession] = {}
        self._stack: AsyncExitStack | None = None

        self._loop = asyncio.new_event_loop()
        self._loop_ready = threading.Event()
        self._thread = threading.Thread(target=self._run_loop, name="mcp-manager-loop", daemon=True)
        self._thread.start()
        self._loop_ready.wait(timeout=5.0)

        fut = asyncio.run_coroutine_threadsafe(self._async_start(), self._loop)
        fut.result(timeout=startup_timeout_s)

    def _run_loop(self) -> None:
        asyncio.set_event_loop(self._loop)
        self._loop_ready.set()
        self._loop.run_forever()

    async def _async_start(self) -> None:
        self._stack = AsyncExitStack()
        for server_cfg in self.servers:
            if server_cfg.get("enabled") is False:
                continue
            server_name = (
                server_cfg.get("name")
                or server_cfg.get("id")
                or server_cfg.get("server")
                or server_cfg.get("label")
            )
            if not isinstance(server_name, str) or not server_name.strip():
                continue
            server_name = server_name.strip()
            command = server_cfg.get("command") or server_cfg.get("cmd")
            if not isinstance(command, str) or not command.strip():
                continue
            args_raw = server_cfg.get("args") or server_cfg.get("arguments") or []
            args = [str(a) for a in (args_raw or []) if isinstance(a, (str, int, float))]
            env = server_cfg.get("env") if isinstance(server_cfg.get("env"), dict) else None
            cwd = server_cfg.get("cwd") if server_cfg.get("cwd") is not None else None

            params = StdioServerParameters(
                command=command.strip(),
                args=args,
                env={str(k): str(v) for k, v in (env or {}).items()} if env else None,
                cwd=cwd,
            )

            read_stream, write_stream = await self._stack.enter_async_context(stdio_client(params))
            session = await self._stack.enter_async_context(ClientSession(read_stream, write_stream))
            await session.initialize()
            tools_result = await session.list_tools()
            self._sessions[server_name] = session

            for tool in getattr(tools_result, "tools", []) or []:
                tool_name = getattr(tool, "name", None)
                if not isinstance(tool_name, str) or not tool_name.strip():
                    continue
                internal = f"mcp.{server_name}.{tool_name.strip()}"
                description = getattr(tool, "description", None)
                schema = getattr(tool, "inputSchema", None)
                if not isinstance(schema, dict):
                    schema = {}
                info = MCPToolInfo(
                    name=internal,
                    description=str(description) if isinstance(description, str) else None,
                    schema=dict(schema),
                    server=server_name,
                )
                self._tool_infos.append(info)
                self._tools[internal] = server_name
                self._tool_targets[internal] = (server_name, tool_name.strip())

    def list_tools(self) -> List[MCPToolInfo]:
        return list(self._tool_infos)

    def has_tool(self, name: str) -> bool:
        return name in self._tools

    def resolve_tool_server(self, name: str) -> Optional[str]:
        return self._tools.get(name)

    async def _async_call_tool(self, name: str, arguments: Dict[str, Any]) -> Any:
        target = self._tool_targets.get(name)
        if not target:
            raise RuntimeError(f"MCP tool '{name}' not available.")
        server_name, tool_name = target
        session = self._sessions.get(server_name)
        if session is None:
            raise RuntimeError(f"MCP server '{server_name}' not initialized.")

        result = await session.call_tool(tool_name, arguments=arguments if isinstance(arguments, dict) else {})
        structured = getattr(result, "structuredContent", None)
        is_error = bool(getattr(result, "isError", False))
        if isinstance(structured, dict) and structured:
            payload = dict(structured)
            if is_error:
                return {"error": payload}
            return payload

        content = getattr(result, "content", None) or []
        text_parts: List[str] = []
        for block in content:
            text = getattr(block, "text", None)
            if isinstance(text, str):
                text_parts.append(text)
                continue
            try:
                dump = block.model_dump()  # pydantic models
                text_parts.append(str(dump))
            except Exception:
                text_parts.append(str(block))
        text_out = "\n".join(text_parts).strip()
        if is_error:
            return {"error": text_out or "mcp tool error"}
        if text_out:
            try:
                parsed = json.loads(text_out)
                if isinstance(parsed, dict):
                    return parsed
            except Exception:
                pass
            return {"result": text_out}
        return {"ok": True}

    def call_tool(self, name: str, arguments: Dict[str, Any]) -> Any:
        fut = asyncio.run_coroutine_threadsafe(self._async_call_tool(name, dict(arguments or {})), self._loop)
        return fut.result(timeout=120.0)

    async def _async_close(self) -> None:
        if self._stack is not None:
            try:
                await self._stack.aclose()
            finally:
                self._stack = None

    def close(self) -> None:
        try:
            fut = asyncio.run_coroutine_threadsafe(self._async_close(), self._loop)
            fut.result(timeout=10.0)
        except Exception:
            pass
        try:
            self._loop.call_soon_threadsafe(self._loop.stop)
        except Exception:
            pass
        try:
            self._thread.join(timeout=2.0)
        except Exception:
            pass

    def __del__(self) -> None:  # pragma: no cover - best-effort
        try:
            self.close()
        except Exception:
            pass
