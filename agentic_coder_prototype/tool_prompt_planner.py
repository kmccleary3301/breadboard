from __future__ import annotations

from typing import Callable, List, Optional

from .compilation.system_prompt_compiler import get_compiler


class ToolPromptPlanner:
    """Builds per-turn tool prompt directives based on the active mode."""

    def plan(
        self,
        *,
        tool_prompt_mode: str,
        send_messages: List[dict],
        session_state,
        tool_defs,
        active_dialect_names: List[str],
        local_tools_prompt: str,
        markdown_logger,
        append_text_block: Callable[[dict, str], None],
        current_native_tools: Optional[List],
    ) -> Optional[str]:
        per_turn_written_text: Optional[str] = None
        if not send_messages:
            return None

        last_message = send_messages[-1]
        if tool_prompt_mode in ("per_turn_append", "system_and_per_turn"):
            tool_names = [t.name for t in tool_defs if getattr(t, "name", None)]
            tool_directive_text = (
                "\n\nSYSTEM MESSAGE - AVAILABLE TOOLS\n"
                f"<FUNCTIONS>\n{local_tools_prompt}\n</FUNCTIONS>\n"
                "MANDATORY: Respond ONLY with one or more tool calls using <TOOL_CALL> ..., with <BASH>...</BASH> for shell, or with diff blocks (SEARCH/REPLACE for edits; unified diff or OpenCode Add File for new files).\n"
                "You may call multiple tools in one reply; non-blocking tools may run concurrently.\n"
                "Some tools are blocking and must run alone in sequence.\n"
                "Do NOT include any extra prose beyond tool calls or diff blocks.\n"
                "When you deem the task fully complete, call mark_task_complete(). If you cannot call tools, end your reply with a single line `TASK COMPLETE`.\n"
                "NEVER use bash to write large file contents (heredocs, echo >>). For files: call create_file() then apply a diff block for contents.\n"
                "Do NOT include extra prose.\nEND SYSTEM MESSAGE\n"
            )
            append_text_block(last_message, tool_directive_text)
            if markdown_logger:
                try:
                    markdown_logger.log_tool_availability(tool_names)
                except Exception:
                    pass
            per_turn_written_text = tool_directive_text
        elif tool_prompt_mode == "system_compiled_and_persistent_per_turn":
            compiler = get_compiler()
            enabled_tools = [t.name for t in tool_defs if getattr(t, "name", None)]
            per_turn_availability = compiler.format_per_turn_availability(enabled_tools, active_dialect_names)
            native_tools = current_native_tools or []
            if native_tools:
                native_names: List[str] = []
                for tool in native_tools:
                    name = getattr(tool, "name", None)
                    if name:
                        native_names.append(str(name))
                if native_names:
                    native_block = "\n\nNATIVE TOOLS AVAILABLE:\n" + "\n".join(f"- {name}" for name in native_names)
                    per_turn_availability = (per_turn_availability or "") + native_block

            if per_turn_availability:
                append_text_block(last_message, "\n\n" + per_turn_availability)
                provider_messages = session_state.provider_messages
                if provider_messages:
                    append_text_block(provider_messages[-1], "\n\n" + per_turn_availability)
                per_turn_written_text = per_turn_availability

        return per_turn_written_text
