from __future__ import annotations

import inspect
import json
import os
import time
from typing import Any, Callable, Dict, Optional

from ..permission_broker import PermissionBroker
from .sequence_validator import SequenceValidator


class EnhancedToolExecutor:
    """Enhanced tool executor with validation and permission gating."""

    def __init__(self, sandbox: Any, config: Dict[str, Any]) -> None:
        self.sandbox = sandbox
        self.config = config or {}
        self._pending_lsp_metrics: Dict[str, Any] = {}

        self._sequence_context: Dict[str, Any] = {
            "recent_operations": [],
            "files_read_this_session": [],
            "files_created_this_session": [],
            "files_modified_this_session": [],
            "files_last_read_at": {},
            "workspace_root": self._resolve_workspace_root(self.config),
        }
        self._last_validation_failure: Optional[Dict[str, Any]] = None

        self._sequence_validator: Optional[SequenceValidator] = None
        enhanced_cfg = self.config.get("enhanced_tools", {}) if isinstance(self.config, dict) else {}
        validation_cfg = enhanced_cfg.get("validation", {}) if isinstance(enhanced_cfg, dict) else {}
        if isinstance(validation_cfg, dict) and validation_cfg.get("enabled"):
            rules = validation_cfg.get("rules") or []
            rule_config = validation_cfg.get("rule_config") or {}
            if not isinstance(rules, list):
                rules = []
            if not isinstance(rule_config, dict):
                rule_config = {}
            self._sequence_validator = SequenceValidator(
                enabled_rules=[str(r) for r in rules if r],
                config=rule_config,
                workspace_root=self._sequence_context["workspace_root"],
            )

        self._permission_broker: Optional[PermissionBroker] = None
        permissions_cfg = enhanced_cfg.get("permissions") if isinstance(enhanced_cfg, dict) else None
        if isinstance(permissions_cfg, dict) and permissions_cfg:
            self._permission_broker = PermissionBroker(permissions_cfg)

        self._shell_write_guard = self._resolve_shell_write_guard(self.config)

    @staticmethod
    def _resolve_workspace_root(config: Dict[str, Any]) -> str:
        workspace = config.get("workspace") if isinstance(config, dict) else None
        if isinstance(workspace, dict):
            root = workspace.get("root")
            if isinstance(root, str) and root.strip():
                return root.strip()
            return "."
        if isinstance(workspace, str) and workspace.strip():
            return workspace.strip()
        return "."

    @staticmethod
    def _resolve_shell_write_guard(config: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        loop_cfg = config.get("loop", {}) if isinstance(config, dict) else {}
        guardrails = loop_cfg.get("guardrails", {}) if isinstance(loop_cfg, dict) else {}
        shell_cfg = guardrails.get("shell_write") if isinstance(guardrails, dict) else None
        if not isinstance(shell_cfg, dict):
            return None
        if not shell_cfg.get("enabled"):
            return None
        patterns = shell_cfg.get("blocked_patterns") or []
        if not isinstance(patterns, list):
            patterns = []
        blocked = [str(p) for p in patterns if isinstance(p, str) and p]
        return {
            "validation_message": str(shell_cfg.get("validation_message") or ""),
            "blocked_patterns": blocked,
        }

    @staticmethod
    def _parse_arguments(raw: Any) -> Dict[str, Any]:
        if isinstance(raw, dict):
            return dict(raw)
        if isinstance(raw, str):
            text = raw.strip()
            if not text:
                return {}
            try:
                parsed = json.loads(text)
            except Exception:
                return {"__raw": text}
            return parsed if isinstance(parsed, dict) else {"value": parsed}
        return {}

    def _normalize_udiff_add_headers(self, patch: str) -> str:
        cfg = self.config if isinstance(self.config, dict) else {}
        tools_cfg = cfg.get("tools", {}) if isinstance(cfg.get("tools"), dict) else {}
        dialects_cfg = tools_cfg.get("dialects", {}) if isinstance(tools_cfg.get("dialects"), dict) else {}
        policy_cfg = (
            dialects_cfg.get("create_file_policy", {})
            if isinstance(dialects_cfg.get("create_file_policy"), dict)
            else {}
        )
        unified_cfg = (
            policy_cfg.get("unified_diff", {})
            if isinstance(policy_cfg.get("unified_diff"), dict)
            else {}
        )
        if not bool(unified_cfg.get("use_dev_null")):
            return patch

        text = str(patch or "")
        if "/dev/null" in text:
            return text

        lines = text.splitlines(True)
        if len(lines) < 2:
            return text
        if not lines[0].startswith("--- ") or not lines[1].startswith("+++ "):
            return text
        if "@@ -0,0" not in text:
            return text
        if lines[0].startswith("--- a/") and lines[1].startswith("+++ b/"):
            newline = "\n" if lines[0].endswith("\n") else ""
            lines[0] = f"--- /dev/null{newline}"
            return "".join(lines)
        return text

    def get_workspace_context(self) -> Dict[str, Any]:
        return dict(self._sequence_context)

    def get_validation_summary(self) -> Dict[str, Any]:
        summary: Dict[str, Any] = {
            "validation_enabled": bool(self._sequence_validator),
            "active_rules": self._sequence_validator.get_active_rules() if self._sequence_validator else [],
        }
        if self._last_validation_failure:
            summary["last_failure"] = dict(self._last_validation_failure)
        return summary

    async def execute_tool_call(
        self,
        tool_call: Dict[str, Any],
        exec_func: Optional[Callable[[Dict[str, Any]], Any]] = None,
    ) -> Dict[str, Any]:
        """Validate then execute a tool call.

        If `exec_func` is provided, it is used for execution; otherwise we call the sandbox directly.
        """
        failure = self._validate_tool_call(tool_call)
        if failure:
            self._last_validation_failure = dict(failure)
            return {
                "validation_failure": True,
                "error": failure.get("error", ""),
                "function": tool_call.get("function"),
            }

        try:
            if exec_func is None:
                result: Any = await self._execute_raw_tool_call(tool_call)
            else:
                maybe = exec_func(tool_call)
                if inspect.isawaitable(maybe):
                    result = await maybe
                else:
                    result = maybe
        except Exception as exc:
            result = {"error": str(exc), "function": tool_call.get("function")}

        if not isinstance(result, dict):
            result = {"result": result}

        self._record_operation(tool_call)
        return result

    def _validate_tool_call(self, tool_call: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        if not isinstance(tool_call, dict):
            return {"error": "invalid tool call", "reason": "bad_tool_call"}

        fn = str(tool_call.get("function") or "")
        args = self._parse_arguments(tool_call.get("arguments"))

        shell_block = self._validate_shell_write_block(fn, args)
        if shell_block:
            return {
                "error": shell_block,
                "reason": "shell_write_block",
            }

        if self._permission_broker:
            try:
                decision = self._permission_broker.decide({"function": fn, "arguments": args})
            except Exception:
                decision = None
            if decision == "deny":
                return {"error": "Permission denied for tool call.", "reason": "permission_denied"}

        if self._sequence_validator:
            err = self._sequence_validator.validate_call({"function": fn, "arguments": args}, self._sequence_context)
            if err:
                return {"error": err, "reason": "sequence_validation"}

        return None

    def _validate_shell_write_block(self, fn: str, args: Dict[str, Any]) -> Optional[str]:
        guard = self._shell_write_guard
        if not guard:
            return None
        if fn not in {"bash", "run_shell", "shell_command"}:
            return None
        command = str(args.get("command") or "")
        if not command:
            return None
        for pattern in guard.get("blocked_patterns") or []:
            if pattern and pattern in command:
                message = guard.get("validation_message") or ""
                return str(message) if message else "Shell write redirection is blocked"
        return None

    def _record_operation(self, tool_call: Dict[str, Any]) -> None:
        fn = str(tool_call.get("function") or "")
        args = self._parse_arguments(tool_call.get("arguments"))
        op = {"function": fn, "timestamp": time.time()}
        recent = self._sequence_context.get("recent_operations")
        if not isinstance(recent, list):
            recent = []
            self._sequence_context["recent_operations"] = recent
        recent.append(op)
        if len(recent) > 100:
            del recent[:-100]

        if fn in {"read_file", "read_text"}:
            path = args.get("path")
            if isinstance(path, str) and path:
                files = self._sequence_context.get("files_read_this_session")
                if not isinstance(files, list):
                    files = []
                    self._sequence_context["files_read_this_session"] = files
                if path not in files:
                    files.append(path)
                last_read = self._sequence_context.get("files_last_read_at")
                if not isinstance(last_read, dict):
                    last_read = {}
                    self._sequence_context["files_last_read_at"] = last_read
                last_read[path] = op["timestamp"]

    async def _execute_raw_tool_call(self, tool_call: Dict[str, Any]) -> Dict[str, Any]:
        fn = str(tool_call.get("function") or "")
        args = self._parse_arguments(tool_call.get("arguments"))

        if fn in {"run_shell", "bash", "shell_command"}:
            command = str(args.get("command") or "")
            timeout = int(args.get("timeout") or 30)
            stream = bool(args.get("stream", False))
            return self._call_sandbox("run", command, timeout=timeout, stream=stream)

        if fn == "read_file":
            path = str(args.get("path") or "")
            if hasattr(self.sandbox, "read_file"):
                return self._call_sandbox("read_file", path)
            return self._call_sandbox("read_text", path)

        if fn == "list_dir":
            path = str(args.get("path") or "")
            try:
                max_depth = int(os.environ.get("BREADBOARD_LIST_DIR_MAX_DEPTH", "1"))
            except Exception:
                max_depth = 1
            if max_depth < 1:
                max_depth = 1
            try:
                depth = int(args.get("depth") or 1)
            except Exception:
                depth = 1
            depth = min(max(depth, 1), max_depth)
            if hasattr(self.sandbox, "list_dir"):
                return self._call_sandbox("list_dir", path, depth=depth)
            return self._call_sandbox("ls", path, depth=depth)

        if fn in {"write_file", "write_text", "create_file"}:
            path = str(args.get("path") or args.get("file_name") or "")
            content = args.get("content")
            if content is None:
                content = ""
            if hasattr(self.sandbox, "write_text"):
                return self._call_sandbox("write_text", path, str(content))
            if hasattr(self.sandbox, "put"):
                return self._call_sandbox("put", path, str(content).encode("utf-8"))
            raise AttributeError("Sandbox does not support write operations")

        raise AttributeError(f"Unknown tool function: {fn}")

    def _call_sandbox(self, method: str, *args: Any, **kwargs: Any) -> Dict[str, Any]:
        fn = getattr(self.sandbox, method, None)
        if fn is None:
            raise AttributeError(f"Sandbox missing method {method}")
        remote = getattr(fn, "remote", None)
        if callable(remote):
            import ray  # type: ignore

            return ray.get(remote(*args, **kwargs))
        return fn(*args, **kwargs)

    def consume_lsp_metrics(self) -> Dict[str, Any]:
        metrics = dict(self._pending_lsp_metrics)
        self._pending_lsp_metrics = {}
        return metrics
