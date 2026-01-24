from __future__ import annotations

from typing import Any, List

from ..core.core import BaseToolDialect, ToolDefinition, ToolCallParsed


class CompositeToolCaller:
    def __init__(self, dialects: List[BaseToolDialect]):
        self._dialects = list(dialects)

    def build_prompt(self, tool_defs: List[ToolDefinition]) -> str:
        sections: list[str] = []
        for d in self._dialects:
            subset = [t for t in tool_defs if t.type_id == d.type_id]
            if subset:
                sections.append(d.prompt_for_tools(subset))
        return "\n\n".join(sections)

    def parse_all(self, text: str, tool_defs: List[ToolDefinition]) -> List[ToolCallParsed]:
        # Aggregate parsed calls from all dialects and de-duplicate by (function, arguments)
        all_calls: list[ToolCallParsed] = []
        seen: set[str] = set()
        for d in self._dialects:
            subset = [t for t in tool_defs if t.type_id == d.type_id]
            for call in d.parse_calls(text, subset):
                # Build a stable key from function name and sorted arguments representation
                try:
                    import json as _json
                    key = f"{call.function}|" + _json.dumps(call.arguments, sort_keys=True, separators=(",", ":"))
                except Exception:
                    key = f"{call.function}|{str(call.arguments)}"
                if key in seen:
                    continue
                seen.add(key)
                if getattr(call, "dialect", None) is None:
                    call.dialect = self._dialect_identifier(d)
                all_calls.append(call)
        return all_calls

    def track_tool_usage(self, parsed_calls: List[Any], *, session_state: Any) -> None:
        """
        Best-effort telemetry hook for text-based tool calling.

        Native tool calling paths emit `tool_call` events via `SessionState.add_message()`.
        For text-based tool calls, we parse the calls out of assistant text and emit
        lightweight `tool_call` events so downstream consumers (CLI bridge, etc.)
        can observe tool intent.

        This method is intentionally side-effect tolerant and must never raise.
        """
        if not parsed_calls or session_state is None:
            return

        try:
            turn_index = session_state.get_provider_metadata("current_turn_index")
        except Exception:
            turn_index = None
        turn_hint = turn_index if isinstance(turn_index, int) else None

        try:
            existing = session_state.get_provider_metadata("text_tool_calls", [])
        except Exception:
            existing = []
        calls_meta: list[dict[str, Any]] = list(existing) if isinstance(existing, list) else []

        todo_seed_names = {"todo.create", "todo.write_board", "todowrite"}
        saw_todo_seed = False

        for call in parsed_calls:
            try:
                name = getattr(call, "function", None)
                if not isinstance(name, str) or not name.strip():
                    continue
                name = name.strip()
            except Exception:
                continue

            try:
                arguments = getattr(call, "arguments", None)
            except Exception:
                arguments = None
            if not isinstance(arguments, dict):
                arguments = {}

            call_id = None
            try:
                call_id = getattr(call, "call_id", None)
            except Exception:
                call_id = None
            if call_id is not None:
                call_id = str(call_id)

            dialect = None
            try:
                dialect = getattr(call, "dialect", None)
            except Exception:
                dialect = None
            if dialect is not None:
                dialect = str(dialect)

            calls_meta.append(
                {
                    "name": name,
                    "id": call_id,
                    "arguments": arguments,
                    "dialect": dialect,
                }
            )

            if name.lower() in todo_seed_names:
                saw_todo_seed = True

            try:
                emit = getattr(session_state, "_emit_event", None)
                if callable(emit):
                    tool_call_payload = {
                        "id": call_id,
                        "type": "function",
                        "function": {
                            "name": name,
                            "arguments": arguments,
                        },
                    }
                    emit("tool_call", {"call": tool_call_payload}, turn=turn_hint)
            except Exception:
                pass

        try:
            session_state.set_provider_metadata("text_tool_calls", calls_meta)
        except Exception:
            pass

        if saw_todo_seed:
            try:
                session_state.set_provider_metadata("todo_seed_completed", True)
            except Exception:
                pass

    @staticmethod
    def _dialect_identifier(dialect: BaseToolDialect) -> str:
        identifier = getattr(dialect, "dialect_id", None)
        if identifier:
            return str(identifier)
        if hasattr(dialect, "type_id") and isinstance(dialect.type_id, str):
            base = dialect.type_id
        else:
            base = dialect.__class__.__name__
        # Convert CamelCaseDialect -> camel_case
        import re

        name = base.replace("Dialect", "")
        snake = re.sub(r"(?<!^)(?=[A-Z])", "_", name).lower()
        snake = snake.strip("_") or base.lower()
        return snake


