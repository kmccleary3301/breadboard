from __future__ import annotations

from typing import Any, Dict, List, Optional

from .manager import GuardrailDefinition, GuardrailPromptRenderer, GuardrailHandlerRegistry


class BaseGuardrailHandler:
    """Common helper for guard handlers."""

    guard_type = "base"

    def __init__(self, definition: GuardrailDefinition, renderer: GuardrailPromptRenderer, config: Dict[str, Any]):
        self.definition = definition
        self.renderer = renderer
        self.config = config
        self.parameters = dict(definition.parameters or {})
        self.templates = dict(definition.templates or {})

    def render(self, template_key: str, context: Optional[Dict[str, Any]] = None, fallback: Optional[str] = None) -> Optional[str]:
        rendered = self.renderer.render(self.templates.get(template_key), context or {})
        if rendered:
            return rendered
        return fallback

    def as_config_snapshot(self) -> Dict[str, Any]:
        return dict(self.parameters)


@GuardrailHandlerRegistry.register("workspace_context")
class WorkspaceContextGuardHandler(BaseGuardrailHandler):
    guard_type = "workspace_context"

    LIST_TOOLS = {"list", "list_dir"}
    READ_TOOLS = {"read", "read_file"}
    BASH_TOOLS = {"bash", "run_shell"}
    EDIT_TOOLS = {
        "patch",
        "write",
        "create_file",
        "create_file_from_block",
        "apply_unified_patch",
        "apply_search_replace",
    }
    TODO_TOOLS = {
        "todowrite",
        "todo.write_board",
        "todo.create",
        "todo.update",
        "todo.list",
    }

    def __init__(self, definition: GuardrailDefinition, renderer: GuardrailPromptRenderer, config: Dict[str, Any]):
        super().__init__(definition, renderer, config)
        self.require_exploration = bool(self.parameters.get("require_exploration_before_edit"))
        self.require_read = bool(self.parameters.get("require_read_before_edit"))
        self.require_progress = bool(self.parameters.get("require_progress_before_todo_updates"))

    def _mark_initialized(self, session_state: Any) -> None:
        session_state.set_provider_metadata("workspace_context_required", False)
        session_state.set_provider_metadata("workspace_context_initialized", True)

    def preflight(self, session_state: Any, parsed_call: Any) -> Optional[str]:
        if not (self.require_exploration or self.require_read or self.require_progress):
            return None

        normalized_fn = (getattr(parsed_call, "function", "") or "").lower()
        if not normalized_fn:
            return None

        required = bool(session_state.get_provider_metadata("workspace_context_required"))
        initialized = bool(session_state.get_provider_metadata("workspace_context_initialized"))
        pending_read = bool(session_state.get_provider_metadata("workspace_pending_read"))

        if normalized_fn in self.READ_TOOLS or normalized_fn in self.BASH_TOOLS:
            self._mark_initialized(session_state)
            session_state.set_provider_metadata("workspace_pending_read", False)
            session_state.set_provider_metadata("workspace_pending_todo_update_allowed", True)
            session_state.set_provider_metadata("todo_updates_since_progress", 0)
            return None

        if normalized_fn in self.LIST_TOOLS:
            if self.require_exploration or not initialized or required:
                self._mark_initialized(session_state)
            if not self.require_progress:
                session_state.set_provider_metadata("workspace_pending_todo_update_allowed", True)
            session_state.set_provider_metadata("todo_updates_since_progress", 0)
            return None

        if normalized_fn in self.EDIT_TOOLS:
            if self.require_read and pending_read:
                return self.render(
                    "require_read_before_edit",
                    fallback="<VALIDATION_ERROR>\nRead the relevant files or run Bash commands to gather real workspace context before editing.\n</VALIDATION_ERROR>",
                )
            self._mark_initialized(session_state)
            if self.require_read:
                session_state.set_provider_metadata("workspace_pending_read", True)
            else:
                session_state.set_provider_metadata("workspace_pending_read", False)
            if self.require_progress:
                session_state.set_provider_metadata("workspace_pending_todo_update_allowed", False)
            session_state.set_provider_metadata("todo_updates_since_progress", 0)
            return None

        if normalized_fn in self.TODO_TOOLS and not initialized:
            session_state.set_provider_metadata("workspace_context_required", True)
            if session_state.get_provider_metadata("plan_mode_disabled"):
                return self.render(
                    "plan_only_todo",
                    fallback="<VALIDATION_ERROR>\nExplore the workspace (List/Read) before updating todos so your plan reflects real files.\n</VALIDATION_ERROR>",
                )

        if self.require_exploration and (required or not initialized):
            if normalized_fn in self.TODO_TOOLS:
                return None
            return self.render(
                "require_exploration",
                fallback="<VALIDATION_ERROR>\nExplore the workspace (List/Read) before updating todos or editing files so you operate on real context.\n</VALIDATION_ERROR>",
            )
        return None

    def as_config_snapshot(self) -> Dict[str, Any]:
        return {
            "require_exploration": self.require_exploration,
            "require_read": self.require_read,
            "todo_progress_requires_progress": self.require_progress,
        }


@GuardrailHandlerRegistry.register("todo_rate_limit")
class TodoRateLimitGuardHandler(BaseGuardrailHandler):
    guard_type = "todo_rate_limit"

    TODO_TOOLS = {
        "todowrite",
        "todo.write_board",
        "todo.create",
        "todo.update",
        "todo.list",
    }

    def __init__(self, definition: GuardrailDefinition, renderer: GuardrailPromptRenderer, config: Dict[str, Any]):
        super().__init__(definition, renderer, config)
        self.enabled = bool(self.parameters.get("enabled", True))
        self.limit = int(self.parameters.get("todo_updates_before_progress") or 0)

    def preflight(self, session_state: Any, parsed_call: Any) -> Optional[str]:
        if not self.enabled:
            return None
        fn = (getattr(parsed_call, "function", "") or "").lower()
        if fn not in self.TODO_TOOLS:
            return None
        if not session_state.get_provider_metadata("plan_mode_disabled"):
            return None
        if not session_state.get_provider_metadata("workspace_context_initialized"):
            return None
        allowed = session_state.get_provider_metadata("workspace_pending_todo_update_allowed")
        if allowed:
            session_state.set_provider_metadata("workspace_pending_todo_update_allowed", False)
            session_state.set_provider_metadata("todo_updates_since_progress", 0)
            return None
        count = int(session_state.get_provider_metadata("todo_updates_since_progress") or 0)
        if count < self.limit:
            session_state.set_provider_metadata("todo_updates_since_progress", count + 1)
            return None
        return self.render(
            "warning",
            fallback="<VALIDATION_ERROR>\nInspect the codebase (Read) or run Bash tests/commands before updating todos again so the checklist reflects real progress.\n</VALIDATION_ERROR>",
        )

    def as_config_snapshot(self) -> Dict[str, Any]:
        return {
            "limit_todo_without_progress": self.enabled,
            "todo_updates_before_progress": self.limit,
        }


@GuardrailHandlerRegistry.register("zero_tool_watchdog")
class ZeroToolGuardHandler(BaseGuardrailHandler):
    guard_type = "zero_tool_watchdog"

    DEFAULT_WARN = (
        "<VALIDATION_ERROR>\n"
        "No tool usage detected. Use read/list/diff/bash tools to make progress before responding again.\n"
        "</VALIDATION_ERROR>"
    )
    DEFAULT_ABORT = (
        "<VALIDATION_ERROR>\n"
        "No tool usage detected across multiple turns. The run is being stopped to avoid idle looping.\n"
        "</VALIDATION_ERROR>"
    )

    def __init__(self, definition: GuardrailDefinition, renderer: GuardrailPromptRenderer, config: Dict[str, Any]):
        super().__init__(definition, renderer, config)
        self.warn_after = int(self.parameters.get("warn_after_turns") or 0)
        self.abort_after = int(self.parameters.get("abort_after_turns") or 0)
        self.emit_event = bool(self.parameters.get("emit_event", True))

    def warning_message(self) -> str:
        return self.render("warn", fallback=self.DEFAULT_WARN) or self.DEFAULT_WARN

    def abort_message(self) -> str:
        return self.render("abort", fallback=self.DEFAULT_ABORT) or self.DEFAULT_ABORT


@GuardrailHandlerRegistry.register("shell_write_block")
class ShellWriteGuardHandler(BaseGuardrailHandler):
    guard_type = "shell_write_block"

    DEFAULT_MESSAGE = (
        "<VALIDATION_ERROR>\n"
        "Do not use bash heredocs or redirection to write files. Use the Write tool instead.\n"
        "</VALIDATION_ERROR>"
    )

    def __init__(self, definition: GuardrailDefinition, renderer: GuardrailPromptRenderer, config: Dict[str, Any]):
        super().__init__(definition, renderer, config)
        self.enabled = bool(self.parameters.get("enabled", True))
        patterns = self.parameters.get("blocked_patterns") or []
        self.blocked_patterns = [p for p in patterns if isinstance(p, str) and p]

    def executor_config(self) -> Dict[str, Any]:
        return {
            "enabled": self.enabled,
            "validation_message": self.render("validation", fallback=self.DEFAULT_MESSAGE) or self.DEFAULT_MESSAGE,
            "blocked_patterns": list(self.blocked_patterns),
        }


@GuardrailHandlerRegistry.register("completion_guard")
class CompletionGuardHandler(BaseGuardrailHandler):
    guard_type = "completion_guard"

    def __init__(self, definition: GuardrailDefinition, renderer: GuardrailPromptRenderer, config: Dict[str, Any]):
        super().__init__(definition, renderer, config)
        self.threshold = int(self.parameters.get("warnings_before_abort") or 0) or 0

    def render_feedback(self, reason: str, remaining: int) -> str:
        if remaining > 0:
            return self.render(
                "warning",
                {"reason": reason, "remaining": remaining},
                fallback=self._default_warning(reason, remaining),
            )
        return self.render(
            "abort",
            {"reason": reason, "remaining": remaining},
            fallback=self._default_abort(reason),
        )

    @staticmethod
    def _default_warning(reason: str, remaining: int) -> str:
        return (
            "<VALIDATION_ERROR>\n"
            f"{reason}\n"
            f"Completion guard engaged. Provide concrete file edits and successful tests. Warnings remaining before abort: {remaining}.\n"
            "</VALIDATION_ERROR>"
        )

    @staticmethod
    def _default_abort(reason: str) -> str:
        return (
            "<VALIDATION_ERROR>\n"
            f"{reason}\n"
            "Completion guard engaged repeatedly. The run will now terminate to avoid wasting budget.\n"
            "</VALIDATION_ERROR>"
        )
