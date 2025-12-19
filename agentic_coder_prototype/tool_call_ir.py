from __future__ import annotations

from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any, Dict, Optional, Protocol


class ToolCallLike(Protocol):
    """
    Structural protocol for parsed tool calls used throughout the engine.

    Any object that has at least:
    - .function: str
    - .arguments: mapping or JSON-serializable payload

    is considered a valid tool call for guardrails/execution.
    """

    function: str
    arguments: Any


@dataclass
class ToolCallIR:
    """
    Canonical tool call intermediate representation.

    This is the shape that guardrails, TODO manager, and execution logic
    can rely on when they need a stable view of a tool call:
    - function: canonical internal tool name
    - arguments: normalized argument payload (dict-like)
    - provider_name: original provider-facing name, if different
    - call_id: provider tool_call id, if present
    - dialect: optional dialect identifier (e.g., "json_block")
    - raw: original parsed object for debugging if needed
    """

    function: str
    arguments: Dict[str, Any]
    provider_name: Optional[str] = None
    call_id: Optional[str] = None
    dialect: Optional[str] = None
    raw: Optional[Any] = None


def to_tool_call_ir(obj: ToolCallLike) -> ToolCallIR:
    """
    Normalize an arbitrary parsed tool call object into ToolCallIR.

    This helper is intentionally tolerant: it uses getattr to extract
    known attributes and falls back to defaults when missing.
    """

    fn = getattr(obj, "function", "") or ""
    args = getattr(obj, "arguments", {}) or {}
    if not isinstance(args, dict):
        # Best-effort: wrap non-dict arguments under a generic key
        args = {"value": args}

    provider_name = getattr(obj, "provider_name", None)
    call_id = getattr(obj, "call_id", None)
    dialect = getattr(obj, "dialect", None)

    return ToolCallIR(
        function=str(fn),
        arguments=dict(args),
        provider_name=str(provider_name) if provider_name else None,
        call_id=str(call_id) if call_id else None,
        dialect=str(dialect) if dialect else None,
        raw=obj,
    )


def as_simplenamespace(ir: ToolCallIR) -> SimpleNamespace:
    """
    Convert ToolCallIR back into a SimpleNamespace-compatible object.

    This is useful when interacting with legacy code paths that expect
    parsed calls to be SimpleNamespace instances with .function and .arguments.
    """

    payload = {
        "function": ir.function,
        "arguments": dict(ir.arguments),
    }
    if ir.provider_name is not None:
        payload["provider_name"] = ir.provider_name
    if ir.call_id is not None:
        payload["call_id"] = ir.call_id
    if ir.dialect is not None:
        payload["dialect"] = ir.dialect
    return SimpleNamespace(**payload)

