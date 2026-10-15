"""Agent-owned lifecycle for profile-selected native stock tool sessions."""
from __future__ import annotations

from typing import Any, Mapping, Protocol


class StockToolSession(Protocol):
    def close(self) -> None: ...


def native_concurrent_prompts(config: Mapping[str, Any]) -> bool:
    """Read the explicit native prompt-concurrency capability."""
    tools = config.get("tools")
    return isinstance(tools, Mapping) and tools.get("native_concurrent_prompts") is True


def admit_stock_execution_mode(config: Mapping[str, Any], *, local: bool) -> None:
    """Reject remote admission before a local-only stock sidecar can start."""
    tools = config.get("tools")
    if not local and isinstance(tools, Mapping) and tools.get("native_local_only") is True:
        raise ValueError(
            "Stock-sidecar native profiles require local execution; remote Ray execution is unsupported. "
            "Use force_local_mode=True."
        )


def register_stock_session(owner: Any, session: StockToolSession) -> None:
    sessions = getattr(owner, "_stock_tool_sessions", None)
    if sessions is None:
        sessions = owner._stock_tool_sessions = []
    sessions.append(session)


def close_stock_sessions(owner: Any) -> None:
    sessions = getattr(owner, "_stock_tool_sessions", None)
    if not sessions:
        return
    errors = []
    failed = []
    for session in sessions:
        try:
            session.close()
        except Exception as error:
            errors.append(error)
            failed.append(session)
    sessions[:] = failed
    if errors:
        raise RuntimeError(f"Stock tool shutdown failed for {len(errors)} session(s)") from errors[0]
