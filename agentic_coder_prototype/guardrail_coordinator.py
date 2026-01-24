from __future__ import annotations

from typing import Any, Dict, List, Optional


class GuardrailCoordinator:
    """Encapsulates guard preflight checks for workspace/todo policies."""

    def __init__(self, config: Dict[str, Any]) -> None:
        self.config = config or {}

    # Public helpers -----------------------------------------------------

    def todo_config(self) -> Dict[str, Any]:
        features = (self.config.get("features") or {}).get("todos") or {}
        enabled = bool(features.get("enabled"))
        return {
            "enabled": enabled,
            "strict": bool(features.get("strict", True)) if enabled else False,
            # When true, enforce "at least one todo must exist in plan mode"
            # before allowing side-effect tools. Some harnesses (e.g. Claude Code)
            # provide TodoWrite but do not hard-require it; allow configs to
            # disable this guard without disabling todos entirely.
            "plan_mode_requires_todo": bool(features.get("plan_mode_requires_todo", True)) if enabled else False,
            "reset_streak_on_todo": bool(features.get("reset_streak_on_todo", False)) if enabled else False,
            "allow_low_priority_open": bool(features.get("allow_low_priority_open", False)) if enabled else False,
        }

    def workspace_guard_config(
        self,
        workspace_guard_handler: Any = None,
        todo_guard_handler: Any = None,
    ) -> Dict[str, Any]:
        if workspace_guard_handler or todo_guard_handler:
            return {
                "require_exploration": bool(getattr(workspace_guard_handler, "require_exploration", False)),
                "limit_todo_without_progress": bool(getattr(todo_guard_handler, "enabled", False)),
                "require_read": bool(getattr(workspace_guard_handler, "require_read", False)),
                "todo_progress_requires_progress": bool(getattr(workspace_guard_handler, "require_progress", False)),
                "todo_updates_before_progress": int(getattr(todo_guard_handler, "limit", 0) or 0),
            }
        loop_cfg = self.config.get("loop") or {}
        guardrails_cfg = (loop_cfg.get("guardrails") or {})
        workspace_cfg = guardrails_cfg.get("workspace_context") or {}
        return {
            "require_exploration": bool(workspace_cfg.get("require_exploration_before_edit")),
            "limit_todo_without_progress": bool(workspace_cfg.get("limit_todo_without_progress")),
            "require_read": bool(workspace_cfg.get("require_read_before_edit")),
            "todo_progress_requires_progress": bool(workspace_cfg.get("todo_updates_require_progress")),
            "todo_updates_before_progress": int(workspace_cfg.get("todo_updates_before_progress") or 0),
        }

    def todo_guard_preflight(
        self,
        session_state,
        parsed_call,
        current_mode: Optional[str],
    ) -> Optional[str]:
        cfg = self.todo_config()
        if not (cfg["enabled"] and cfg["strict"]):
            return None
        if current_mode == "plan" and not cfg.get("plan_mode_requires_todo", True):
            return None
        manager = session_state.get_todo_manager()
        if not manager:
            return None
        snapshot = manager.snapshot()
        todos = snapshot.get("todos", []) if isinstance(snapshot, dict) else []
        has_todos = bool(todos)
        fn = getattr(parsed_call, "function", "") or ""
        if current_mode == "plan":
            side_effect_tools = {
                "apply_unified_patch",
                "apply_search_replace",
                "bash",
                "patch",
                "write",
                "create_file",
                "create_file_from_block",
                "run_shell",
                "write_text",
            }
            if not has_todos and fn in side_effect_tools:
                return "Plan mode requires creating at least one todo via `todo.create` before using edit or bash tools."
        return None

    def workspace_context_guard_preflight(
        self,
        session_state,
        parsed_call,
        workspace_guard_handler: Any,
        todo_guard_handler: Any,
    ) -> Optional[str]:
        if workspace_guard_handler:
            return workspace_guard_handler.preflight(session_state, parsed_call)
        guard_cfg = self.workspace_guard_config(workspace_guard_handler, todo_guard_handler)
        require_exploration = guard_cfg["require_exploration"]
        require_read = guard_cfg["require_read"]
        require_progress = guard_cfg["todo_progress_requires_progress"]
        if not (require_exploration or require_read or require_progress):
            return None

        normalized_fn = (getattr(parsed_call, "function", "") or "").lower()
        if not normalized_fn:
            return None

        required = bool(session_state.get_provider_metadata("workspace_context_required"))
        initialized = bool(session_state.get_provider_metadata("workspace_context_initialized"))
        pending_read = bool(session_state.get_provider_metadata("workspace_pending_read"))

        list_tools = {"list", "list_dir"}
        read_tools = {"read", "read_file"}
        bash_tools = {"bash", "run_shell", "shell_command"}
        edit_tools = {
            "patch",
            "write",
            "create_file",
            "create_file_from_block",
            "apply_unified_patch",
            "apply_search_replace",
        }
        todo_tools = {
            "todowrite",
            "todo.write_board",
            "todo.create",
            "todo.update",
            "todo.list",
            "todo.note",
            "todo.complete",
            "todo.cancel",
            "todo.reorder",
            "todo.attach",
        }

        def _mark_initialized() -> None:
            session_state.set_provider_metadata("workspace_context_required", False)
            session_state.set_provider_metadata("workspace_context_initialized", True)

        if normalized_fn in read_tools or normalized_fn in bash_tools:
            _mark_initialized()
            session_state.set_provider_metadata("workspace_pending_read", False)
            session_state.set_provider_metadata("workspace_pending_todo_update_allowed", True)
            session_state.set_provider_metadata("todo_updates_since_progress", 0)
            return None

        if normalized_fn in list_tools:
            if require_exploration or not initialized or required:
                _mark_initialized()
            if not require_progress:
                session_state.set_provider_metadata("workspace_pending_todo_update_allowed", True)
            session_state.set_provider_metadata("todo_updates_since_progress", 0)
            return None

        if normalized_fn in edit_tools:
            if require_read and pending_read:
                return (
                    "<VALIDATION_ERROR>\n"
                    "Read the relevant files or run Bash commands to gather real workspace context before editing.\n"
                    "</VALIDATION_ERROR>"
                )
            _mark_initialized()
            if require_read:
                session_state.set_provider_metadata("workspace_pending_read", False)
            session_state.set_provider_metadata("workspace_pending_todo_update_allowed", True)
            session_state.set_provider_metadata("todo_updates_since_progress", 0)
            return None

        if normalized_fn in todo_tools and not initialized:
            session_state.set_provider_metadata("workspace_context_required", True)
            if session_state.get_provider_metadata("plan_mode_disabled"):
                return (
                    "<VALIDATION_ERROR>\n"
                    "Explore the workspace (List/Read) before updating todos so your plan reflects real files.\n"
                    "</VALIDATION_ERROR>"
                )

        if guard_cfg["require_exploration"] and (required or not initialized):
            if normalized_fn in todo_tools:
                return None
            return (
                "<VALIDATION_ERROR>\n"
                "Explore the workspace (List/Read) before updating todos or editing files so you operate on real context.\n"
                "</VALIDATION_ERROR>"
            )
        return None

    def todo_progress_guard_preflight(
        self,
        session_state,
        parsed_call,
        todo_rate_guard_handler: Any,
    ) -> Optional[str]:
        if todo_rate_guard_handler:
            return todo_rate_guard_handler.preflight(session_state, parsed_call)
        guard_cfg = self.workspace_guard_config(None, todo_rate_guard_handler)
        if not guard_cfg["limit_todo_without_progress"]:
            return None
        fn = (getattr(parsed_call, "function", "") or "").lower()
        todo_tools = {
            "todowrite",
            "todo.write_board",
            "todo.create",
            "todo.update",
            "todo.list",
            "todo.note",
            "todo.complete",
            "todo.cancel",
            "todo.reorder",
            "todo.attach",
        }
        if fn not in todo_tools:
            return None
        if not session_state.get_provider_metadata("plan_mode_disabled"):
            return None
        if not session_state.get_provider_metadata("workspace_context_initialized"):
            return None
        allowed = session_state.get_provider_metadata("workspace_pending_todo_update_allowed")
        limit = int(guard_cfg.get("todo_updates_before_progress") or 0)
        if allowed:
            session_state.set_provider_metadata("workspace_pending_todo_update_allowed", False)
            session_state.set_provider_metadata("todo_updates_since_progress", 0)
            return None
        count = int(session_state.get_provider_metadata("todo_updates_since_progress") or 0)
        if count < limit:
            session_state.set_provider_metadata("todo_updates_since_progress", count + 1)
            return None
        return (
            "<VALIDATION_ERROR>\n"
            "Inspect the codebase (Read) or run Bash tests/commands before updating todos again so the checklist reflects real progress.\n"
            "</VALIDATION_ERROR>"
        )
