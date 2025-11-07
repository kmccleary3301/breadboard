"""
Enhanced Tool Executor - Integrates validation with existing LSP v2 system
Provides enhanced tool execution with workspace tracking, validation, and LSP diagnostics
Compatible with existing LSPEnhancedSandbox from sandbox_lsp_integration.py
"""
from __future__ import annotations

import asyncio
import logging
from typing import Dict, Any, Optional, List, Callable
import shlex
import re
from pathlib import Path

from ..utils.workspace_tracker import WorkspaceStateTracker
from kylecode.opencode_patch import PatchParseError, parse_opencode_patch, to_unified_diff
from .sequence_validator import SequenceValidator

logger = logging.getLogger(__name__)

SHELL_WRITE_PATTERNS_DEFAULT = [
    "cat >",
    "cat>>",
    "cat <<",
    "tee ",
    "printf ",
    "python - <<",
    "python3 - <<",
    "bash -lc 'cat",
    "bash -lc \"cat",
]

class EnhancedToolExecutor:
    """Tool executor with validation, tracking, and LSP integration"""
    
    def __init__(self, 
                 sandbox,
                 maybe_lsp_or_config: Any = None,
                 config: Dict[str, Any] = None):
        """
        Initialize enhanced tool executor
        
        Args:
            sandbox: Sandbox instance for executing tools (can be LSPEnhancedSandbox or regular DevSandboxV2)
            config: Configuration dictionary
        """
        # Backward-compat constructor: allow (sandbox, config) or (sandbox, lsp, config)
        self.lsp = None
        if config is None and isinstance(maybe_lsp_or_config, dict):
            config = maybe_lsp_or_config
        elif config is not None and maybe_lsp_or_config is not None and not isinstance(maybe_lsp_or_config, dict):
            # Support legacy signature (sandbox, lsp, config)
            self.lsp = maybe_lsp_or_config
        self.sandbox = sandbox
        self.config = config or {}
        # Optional: manipulations mapping from YAML-defined tools
        self.yaml_tool_manipulations: Dict[str, List[str]] = self.config.get("yaml_tool_manipulations", {})
        
        # Initialize workspace tracker
        # Accept v2 shape where `workspace` is a dict with `root`; fall back to '.'
        raw_ws = self.config.get("workspace", ".")
        if isinstance(raw_ws, dict):
            workspace_root = raw_ws.get("root", ".")
        else:
            workspace_root = raw_ws
        self.workspace_tracker = WorkspaceStateTracker(workspace_root)
        
        # Initialize sequence validator (without LSP rule - handled by LSPEnhancedSandbox)
        validation_config = self.config.get("enhanced_tools", {}).get("validation", {})
        if validation_config.get("enabled", False):
            enabled_rules = validation_config.get("rules", [])
            rule_config = validation_config.get("rule_config", {})
            
            # Remove LSP validation rule - handled by LSPEnhancedSandbox
            if "lsp_validation" in enabled_rules:
                enabled_rules.remove("lsp_validation")
            
            self.sequence_validator = SequenceValidator(
                enabled_rules, 
                rule_config, 
                workspace_root
            )
        else:
            self.sequence_validator = None
        
        # Check if sandbox has LSP capabilities (attribute or method that suggests LSP)
        # Only attribute-based detection; avoid brittle type-name heuristics
        self.has_lsp_sandbox = (
            hasattr(sandbox, 'lsp_manager') or 
            hasattr(sandbox, 'lsp_diagnostics') or
            (self.lsp is not None)
        )
        
        # LSP integration settings
        lsp_config = self.config.get("enhanced_tools", {}).get("lsp_integration", {})
        self.lsp_enabled = lsp_config.get("enabled", False) and self.has_lsp_sandbox
        self.format_lsp_feedback = lsp_config.get("format_feedback", True)
        
        # Tool execution hooks
        self.pre_execution_hooks: List[Callable] = []
        self.post_execution_hooks: List[Callable] = []
        self._latest_lsp_summary: Dict[str, Any] = {}
        self._latest_lsp_summary: Dict[str, Any] = {}

        loop_cfg = (self.config.get("loop", {}) or {})
        guardrails_cfg = (loop_cfg.get("guardrails", {}) or {})
        shell_write_cfg = guardrails_cfg.get("shell_write", {}) or {}
        self.shell_write_guard = {
            "enabled": bool(shell_write_cfg.get("enabled", False)),
            "message": shell_write_cfg.get("validation_message"),
            "patterns": list(shell_write_cfg.get("blocked_patterns") or []),
        }
        
        logger.info(f"Initialized EnhancedToolExecutor (validation={'enabled' if self.sequence_validator else 'disabled'}, "
                   f"lsp={'enabled' if self.lsp_enabled else 'disabled'})")

    # ---- path normalization helpers ----
    def _normalize_single_path(self, path_value: str) -> str:
        """Normalize a path to be workspace-relative, stripping duplicate workspace components.

        Ensures no leading workspace name or absolute workspace prefix leaks into arguments.
        """
        try:
            if not isinstance(path_value, str) or not path_value.strip():
                return path_value
            p = path_value.strip()
            # Short-circuit current dir markers
            if p == "." or p == "./":
                return "./"
            # Strip leading ./
            if p.startswith("./"):
                p = p[2:]
            ws_root_str = str(self.workspace_tracker.workspace_root)
            ws_root_norm = str(Path(ws_root_str).resolve())
            base = Path(ws_root_norm).name
            # If absolute and under workspace root -> make relative
            try:
                abs_p = Path(p)
                if abs_p.is_absolute():
                    try:
                        rel = abs_p.resolve().relative_to(ws_root_norm)
                        return str(rel)
                    except Exception:
                        # If absolute but not under workspace, leave as-is
                        return p
            except Exception:
                pass
            # If starts with workspace base name, strip it
            for sep in ("/", "\\"):
                marker = base + sep
                if p.startswith(marker):
                    return p[len(marker):]
            # If contains absolute workspace path, strip prefix
            if ws_root_norm in p:
                tail = p.split(ws_root_norm, 1)[1].lstrip("/\\")
                return tail
            # Already relative simple path
            return p
        except Exception:
            return path_value

    def _normalize_argument_paths(self, args: Dict[str, Any]) -> Dict[str, Any]:
        """Return a copy of args with known path fields normalized relative to workspace root."""
        if not isinstance(args, dict):
            return args
        out = dict(args)
        path_keys = ["path", "file_path", "file_name", "target_file"]
        for key in path_keys:
            if key in out and isinstance(out[key], str):
                out[key] = self._normalize_single_path(out[key])
        return out

    # ---- create_file_policy helpers ----
    def _normalize_udiff_add_headers(self, patch_text: str) -> str:
        """Normalize udiff add-file headers to /dev/null style when configured."""
        try:
            policy = (((self.config.get("tools") or {}).get("dialects") or {}).get("create_file_policy") or {}).get("unified_diff") or {}
            use_dev_null = bool(policy.get("use_dev_null", False))
        except Exception:
            use_dev_null = False
        if not use_dev_null:
            return patch_text
        try:
            lines = patch_text.splitlines()
            out: List[str] = []
            i = 0
            while i < len(lines):
                line = lines[i]
                # Rewrite headers if necessary
                if line.startswith("--- ") and i + 1 < len(lines) and lines[i+1].startswith("+++ "):
                    old_path = line[4:].strip()
                    new_line = lines[i+1]
                    new_path = new_line[4:].strip()
                    if old_path != "/dev/null" and (old_path == new_path or old_path == "a/" + new_path or old_path == new_path.replace("b/", "a/")):
                        out.append("--- /dev/null")
                        out.append(new_line)
                        i += 2
                        continue
                out.append(line)
                i += 1
            return "\n".join(out)
        except Exception:
            return patch_text

    def _apply_create_file_policy_for_search_replace(self, file_name: str, content: str) -> Optional[Dict[str, Any]]:
        """Prefer direct write for create-file flow when configured for Aider S/R."""
        try:
            policy = (((self.config.get("tools") or {}).get("dialects") or {}).get("create_file_policy") or {}).get("aider_search_replace") or {}
            prefer_write = bool(policy.get("prefer_write_file_tool", False))
        except Exception:
            prefer_write = False
        if not prefer_write:
            return None
        try:
            return self.sandbox.write_text(path=file_name, content=content)
        except Exception:
            return None

    async def execute_tool_call(self, tool_call: Dict[str, Any], original_executor: Callable = None) -> Dict[str, Any]:
        """
        Execute tool call with enhanced validation and tracking
        
        Args:
            tool_call: The tool call to execute
            original_executor: Original tool execution function to delegate to
            
        Returns:
            Enhanced result with validation info and diagnostics
        """
        function = tool_call.get("function", "unknown")
        
        logger.debug(f"Executing enhanced tool call: {function}")
        
        # 1. Pre-execution hooks
        for hook in self.pre_execution_hooks:
            try:
                await self._run_hook(hook, tool_call, "pre")
            except Exception as e:
                logger.warning(f"Pre-execution hook failed: {e}")
        
        # 2. Pre-execution validation
        context = self.workspace_tracker.get_context_for_prompt()
        # Permissions gating (pre-validation): shell and file writes
        perm_cfg = self.config.get("enhanced_tools", {}).get("permissions", {})
        perm_error = self._check_permissions(tool_call, perm_cfg)
        if perm_error:
            logger.info(f"Permission denied: {perm_error}")
            return {
                "error": f"Permission denied: {perm_error}",
                "validation_failure": True,
            }
        if self.sequence_validator:
            validation_error = self.sequence_validator.validate_call(tool_call, context)
            if validation_error:
                logger.info(f"Tool call validation failed: {validation_error}")
                return {
                    "error": f"Validation failed: {validation_error}",
                    "validation_failure": True,
                    "suggested_alternative": self._suggest_alternative(tool_call, validation_error)
                }
        
        # Preflight rule: guard premature `make` invocations until Makefile is ready
        try:
            if function == "run_shell":
                cmd_raw = str(tool_call.get("arguments", {}).get("command", "")).strip()
                preflight_error = self._preflight_check_make(cmd_raw)
                if preflight_error:
                    return {
                        "error": preflight_error,
                        "validation_failure": True,
                        "hint": "Ensure a Makefile exists defining targets: all, test, clean before running make"
                    }
                shell_guard_error = self._preflight_check_shell_write(cmd_raw)
                if shell_guard_error:
                    return {
                        "error": shell_guard_error,
                        "validation_failure": True,
                        "guardrail": "shell_write_blocked",
                    }
        except Exception:
            # Preflight should never crash execution path; ignore failures
            pass

        # 3. Execute the tool call (with create_file_policy adjustments)
        try:
            # Normalize path arguments before execution
            if isinstance(tool_call, dict):
                tool_call = dict(tool_call)
                tool_call["arguments"] = self._normalize_argument_paths(tool_call.get("arguments", {}))
            if function == "apply_unified_patch" and isinstance(tool_call.get("arguments", {}).get("patch"), str):
                tool_call = dict(tool_call)
                args = dict(tool_call.get("arguments", {}))
                args["patch"] = self._normalize_udiff_add_headers(args.get("patch", ""))
                tool_call["arguments"] = args
            elif function == "apply_search_replace":
                args = tool_call.get("arguments", {})
                file_name = str(args.get("file_name", ""))
                # Heuristic: if search block seems empty but replace has content, treat as create
                if args.get("search", "").strip() == "" and args.get("replace", ""):
                    direct = self._apply_create_file_policy_for_search_replace(file_name, str(args.get("replace", "")))
                    if isinstance(direct, dict):
                        result = direct
                    else:
                        result = {"status": "ok", "policy_applied": True}
                    # Skip original executor path
                    return result
            
            if original_executor:
                result = await self._call_with_timeout(original_executor, tool_call)
            else:
                result = await self._execute_raw_tool_call(tool_call)
            if not isinstance(result, dict):
                result = {"status": str(result)}
        except Exception as e:
            logger.error(f"Tool execution failed: {e}")
            result = {"error": str(e)}
        
        # 4. Track the operation (robust to non-dict mocks)
        try:
            self.workspace_tracker.track_operation(tool_call, result)
        except Exception:
            self.workspace_tracker.track_operation(tool_call, {})
        
        # 5. Post-execution processing (LSP feedback)
        if isinstance(result, dict) and ("error" not in result):
            enhanced_result = await self._post_execution_processing(tool_call, result)
            result.update(enhanced_result)
            if self.lsp_enabled and "lsp_diagnostics" not in result:
                try:
                    diagnostics = None
                    if hasattr(self.sandbox, 'get_diagnostics'):
                        diagnostics = self.sandbox.get_diagnostics()
                    elif self.lsp is not None and hasattr(self.lsp, 'get_diagnostics'):
                        diagnostics = self.lsp.get_diagnostics()
                    if diagnostics:
                        result["lsp_diagnostics"] = diagnostics
                        self._capture_lsp_metrics(diagnostics)
                except Exception:
                    pass
        
        # 6. Post-execution hooks
        for hook in self.post_execution_hooks:
            try:
                await self._run_hook(hook, tool_call, "post", result)
            except Exception as e:
                logger.warning(f"Post-execution hook failed: {e}")
        
        # 7. Add execution metadata (only if dict)
        if isinstance(result, dict):
            result["execution_metadata"] = {
            "enhanced_executor": True,
            "validation_enabled": self.sequence_validator is not None,
            "lsp_enabled": self.lsp_enabled,
            "workspace_root": str(self.workspace_tracker.workspace_root)
            }
        
        return result

    def _preflight_check_make(self, command: str) -> Optional[str]:
        """Deny `make*` commands until a Makefile with all/test/clean exists.

        Lightweight content sniffing; workspace root is implicit for sandbox calls.
        """
        if not command:
            return None
        try:
            tokens = shlex.split(command)
        except Exception:
            # If we cannot parse reliably, skip preflight
            return None

        if not tokens:
            return None
        exe = tokens[0]
        base = exe.rsplit("/", 1)[-1].lower()
        if base not in {"make", "gmake"}:
            return None

        # Check Makefile presence at workspace root
        try:
            has_mk = False
            for name in ("Makefile", "makefile", "GNUmakefile"):
                try:
                    if self.sandbox.exists(name):  # type: ignore[attr-defined]
                        has_mk = True
                        mk_name = name
                        break
                except Exception:
                    continue
            if not has_mk:
                return "Makefile not found in workspace; create it before running 'make'"

            # Read and check for required targets
            content = ""
            try:
                res = self.sandbox.read_text(mk_name)  # type: ignore[attr-defined]
                if isinstance(res, dict) and "content" in res:
                    content = str(res.get("content", ""))
                elif isinstance(res, str):
                    content = res
            except Exception:
                # If read fails, allow execution rather than hard-block
                return None

            missing = []
            for target in ("all", "test", "clean"):
                pat = re.compile(rf"^\s*{re.escape(target)}\s*:\s*", re.MULTILINE)
                if not pat.search(content):
                    missing.append(target)
            if missing:
                return f"Makefile missing required target(s): {', '.join(missing)}"
        except Exception:
            # Be permissive on unexpected errors
            return None

        return None

    def _preflight_check_shell_write(self, command: str) -> Optional[str]:
        """Block bash heredocs/redirections used for file writes."""
        guard_cfg = getattr(self, "shell_write_guard", {}) or {}
        if not guard_cfg.get("enabled") or not command:
            return None

        patterns = guard_cfg.get("patterns") or []
        lowered = command.lower()
        for pattern in list(patterns) + SHELL_WRITE_PATTERNS_DEFAULT:
            pat = str(pattern).strip().lower()
            if not pat:
                continue
            if pat in lowered:
                return guard_cfg.get("message") or (
                    "<VALIDATION_ERROR>\n"
                    "Do not use bash heredocs or redirection to write files. Use write/patch tools instead.\n"
                    "</VALIDATION_ERROR>"
                )
        return None
    
    async def _execute_raw_tool_call(self, tool_call: Dict[str, Any]) -> Dict[str, Any]:
        """Execute tool call using sandbox (fallback implementation)"""
        function = tool_call.get("function", "")
        arguments = tool_call.get("arguments", {})

        if function == "patch" and isinstance(arguments.get("patchText"), str):
            try:
                unified = self._convert_opencode_patch(arguments["patchText"])
            except PatchParseError as exc:
                return {"error": f"Invalid OpenCode patch: {exc}", "validation_failure": True}
            arguments = dict(arguments)
            arguments.pop("patchText", None)
            arguments["patch"] = unified
            tool_call = dict(tool_call)
            tool_call["function"] = function = "apply_unified_patch"
            tool_call["arguments"] = arguments

        # Map function names to sandbox methods (base)
        method_map = {
            # Legacy/internal
            "run_shell": "run",
            "create_file": "create_file",
            "read_file": "read_file",
            "list_dir": "list_dir",
            "apply_unified_patch": "apply_patch",
            "apply_search_replace": "apply_search_replace",
            "write_text": "write_text",
            "read_text": "read_text",
            "multiedit": "multiedit",
            "edit_replace": "edit_replace",
            # OpenCode-compatible names
            "bash": "run",
            "read": "read_text",
            "write": "write_text",
            "list": "ls",
            "grep": "grep",
            "glob": "glob",
            "edit": "edit_replace",
            "patch": "apply_patch",
        }

        # Prefer routing via manipulations map when available to reduce switch logic
        # Example mappings from manipulations to sandbox methods
        manipulation_to_method = {
            "shell.exec": "run",
            "file.read": "read_text",
            "file.write": "write_text",
            "diff.apply": "apply_patch",
            "file.list": "ls",
            "search.grep": "grep",
            "search.glob": "glob",
        }

        if self.yaml_tool_manipulations and function in self.yaml_tool_manipulations:
            manips = self.yaml_tool_manipulations.get(function) or []
            for m in manips:
                if m in manipulation_to_method:
                    method_map[function] = manipulation_to_method[m]
                    break
        
        sandbox_method = method_map.get(function)
        if not sandbox_method or not hasattr(self.sandbox, sandbox_method):
            return {"error": f"Unknown or unsupported function: {function}"}
        
        try:
            method = getattr(self.sandbox, sandbox_method)
            if asyncio.iscoroutinefunction(method):
                return await method(**arguments)
            else:
                return method(**arguments)
        except Exception as e:
            return {"error": str(e)}
    
    async def _post_execution_processing(self, tool_call: Dict[str, Any], result: Dict[str, Any]) -> Dict[str, Any]:
        """Handle post-execution processing with LSP feedback formatting"""
        enhanced_result = {}
        
        # Format LSP diagnostics if available in result
        if self.lsp_enabled and self.format_lsp_feedback and isinstance(result, dict) and "lsp_diagnostics" in result:
            lsp_diagnostics = result["lsp_diagnostics"]
            try:
                self._capture_lsp_metrics(lsp_diagnostics)
            except Exception:
                pass
            operation_type = tool_call.get("function", "operation")
            
            # Format diagnostics into user-friendly feedback
            feedback = self._format_lsp_diagnostics(lsp_diagnostics, operation_type)
            enhanced_result["lsp_feedback"] = feedback
            
            # Log the feedback
            if "0 linter errors" in feedback:
                logger.info(feedback)
            elif any(error_word in feedback for error_word in ["ERROR", "WARN", "error", "warning"]):
                logger.warning(feedback)
        
        return enhanced_result
    
    def _format_lsp_diagnostics(self, diagnostics: Dict[str, List], operation_type: str) -> str:
        """Format LSP diagnostics into feedback message (mimics OpenCode pattern)"""
        if not diagnostics:
            return f"{operation_type.capitalize()} completed with 0 linter errors"
        
        total_errors = 0
        total_warnings = 0
        error_details = []
        
        for file_path, file_diagnostics in diagnostics.items():
            if not file_diagnostics:
                continue
                
            file_errors = 0
            file_warnings = 0
            
            for diag in file_diagnostics[:3]:  # Limit to first 3 per file
                severity = diag.get("severity", "error").lower()
                line = diag.get("line", 0) + 1  # Convert to 1-based
                character = diag.get("character", 0) + 1
                message = diag.get("message", "Unknown error")
                source = diag.get("source", "LSP")
                
                if "error" in severity:
                    file_errors += 1
                    error_details.append(f"ERROR [{line}:{character}] {message}")
                elif "warn" in severity:
                    file_warnings += 1
                    error_details.append(f"WARN [{line}:{character}] {message}")
                else:
                    error_details.append(f"INFO [{line}:{character}] {message}")
            
            total_errors += file_errors
            total_warnings += file_warnings
        
        if total_errors == 0 and total_warnings == 0:
            return f"{operation_type.capitalize()} completed with 0 linter errors"
        
        # Create feedback message
        if total_errors > 0:
            feedback = f"{operation_type.capitalize()} completed with {total_errors} linter error(s)"
            if total_warnings > 0:
                feedback += f" and {total_warnings} warning(s)"
        else:
            feedback = f"{operation_type.capitalize()} completed with {total_warnings} linter warning(s)"
        
        if error_details:
            feedback += ":\n" + "\n".join(error_details[:5])  # Limit to 5 details
            if len(error_details) > 5:
                feedback += f"\n... and {len(error_details) - 5} more issues"
        
        return feedback

    def _capture_lsp_metrics(self, diagnostics: Dict[str, List]) -> None:
        """Summarize LSP diagnostics for reward metrics."""
        total_errors = 0
        files_with_errors: set[str] = set()
        diagnostics = diagnostics or {}
        for file_path, file_diagnostics in diagnostics.items():
            if not file_diagnostics:
                continue
            file_error_count = 0
            for diag in file_diagnostics:
                severity = str(diag.get("severity", "error")).lower()
                if "error" in severity:
                    file_error_count += 1
            if file_error_count > 0:
                total_errors += file_error_count
                files_with_errors.add(file_path or "<unknown>")
        self._latest_lsp_summary = {
            "led_errors": total_errors,
            "sbs_files_with_issues": len(files_with_errors),
        }

    def consume_lsp_metrics(self) -> Dict[str, Any]:
        """Return and clear the latest LSP metrics summary."""
        summary = dict(self._latest_lsp_summary)
        self._latest_lsp_summary.clear()
        return summary
    
    
    def _extract_affected_files(self, tool_call: Dict[str, Any]) -> List[str]:
        """Extract list of files affected by tool call"""
        function = tool_call.get("function", "")
        args = tool_call.get("arguments", {})
        files = []
        
        # Direct file arguments
        for key in ["path", "file_path", "file_name", "target_file"]:
            if key in args and args[key]:
                files.append(args[key])
        
        # Extract from patches
        if function == "patch" and "patchText" in args:
            patch_files = self._extract_files_from_patch(args["patchText"])
            files.extend(patch_files)
        
        return list(set(files))  # Remove duplicates
    
    def _extract_files_from_patch(self, patch_content: str) -> List[str]:
        """Extract file paths from OpenCode patch content"""
        try:
            operations = parse_opencode_patch(patch_content)
        except PatchParseError:
            return []
        return list({op.file_path for op in operations})

    def _fetch_file_content_for_patch(self, path: str) -> str:
        try:
            result = self.sandbox.read_text(path)
        except PatchParseError:
            raise
        except Exception as exc:
            raise PatchParseError(f"Failed to read {path}: {exc}") from exc

        if isinstance(result, dict) and "content" in result:
            return result["content"]
        if isinstance(result, str):
            return result
        raise PatchParseError(f"Unexpected sandbox read result for {path}")

    def _convert_opencode_patch(self, patch_text: str) -> str:
        return to_unified_diff(patch_text, self._fetch_file_content_for_patch)
    
    def _suggest_alternative(self, tool_call: Dict[str, Any], validation_error: str) -> Optional[Dict[str, Any]]:
        """Suggest alternative tool call based on validation error"""
        function = tool_call.get("function", "")
        args = tool_call.get("arguments", {})
        
        # Suggest edit tools instead of create for existing files
        if "already created" in validation_error or "already exists" in validation_error:
            if function == "create_file":
                return {
                    "suggested_function": "patch",
                    "reason": "File exists, use patch/edit instead of creation",
                    "example_usage": {
                        "function": "patch",
                        "arguments": {
                            "patchText": (
                                "*** Begin Patch\n"
                                f"*** Update File: {args.get('path', 'filename')}\n"
                                "@@ context\n"
                                "-old_line\n"
                                "+new_line\n"
                                "*** End Patch"
                            )
                        }
                    }
                }
        
        return None
    
    async def _call_with_timeout(self, func: Callable, *args, **kwargs) -> Any:
        """Call function with timeout"""
        timeout = self.config.get("tool_timeout", 30)
        try:
            return await asyncio.wait_for(func(*args, **kwargs), timeout=timeout)
        except asyncio.TimeoutError:
            return {"error": f"Tool execution timed out after {timeout}s"}
    
    async def _run_hook(self, hook: Callable, tool_call: Dict[str, Any], phase: str, result: Dict[str, Any] = None):
        """Run a hook function"""
        if asyncio.iscoroutinefunction(hook):
            await hook(tool_call, phase, result)
        else:
            hook(tool_call, phase, result)
    
    def add_pre_execution_hook(self, hook: Callable):
        """Add hook to run before tool execution"""
        self.pre_execution_hooks.append(hook)
    
    def add_post_execution_hook(self, hook: Callable):
        """Add hook to run after tool execution"""
        self.post_execution_hooks.append(hook)
    
    def get_workspace_context(self) -> Dict[str, Any]:
        """Get current workspace context for prompt generation"""
        return self.workspace_tracker.get_context_for_prompt()
    
    def get_validation_summary(self) -> Dict[str, Any]:
        """Get summary of validation rules and status"""
        if not self.sequence_validator:
            return {"validation_enabled": False}
        
        return {
            "validation_enabled": True,
            "active_rules": self.sequence_validator.get_active_rules(),
            "rule_descriptions": self.sequence_validator.get_rule_descriptions()
        }
    
    def clear_session_state(self):
        """Clear session state for new sessions"""
        self.workspace_tracker.clear_session_state()
        logger.info("Cleared enhanced executor session state")
    
    def export_session_data(self) -> Dict[str, Any]:
        """Export session data for analysis"""
        return {
            "workspace_state": self.workspace_tracker.export_state(),
            "validation_summary": self.get_validation_summary(),
            "config": self.config,
            "lsp_enabled": self.lsp_enabled
        }

    def _check_permissions(self, tool_call: Dict[str, Any], permissions_cfg: Dict[str, Any]) -> Optional[str]:
        """Check permissions for sensitive operations like shell and writes.

        Returns an error string if denied, otherwise None.
        """
        function = tool_call.get("function", "")
        args = tool_call.get("arguments", {})

        # Shell permissions
        if function == "run_shell":
            shell_cfg = permissions_cfg.get("shell", {})
            if not shell_cfg:
                return None
            default_policy = (shell_cfg.get("default") or "allow").lower()
            allowlist = shell_cfg.get("allowlist", [])
            denylist = shell_cfg.get("denylist", [])
            cmd = str(args.get("command", "")).strip()
            first_token = cmd.split()[0] if cmd else ""
            if first_token in denylist:
                return f"command '{first_token}' is explicitly denied"
            if default_policy == "deny" and first_token and first_token not in allowlist:
                return f"command '{first_token}' not in allowlist"
            return None

        # File write permissions (create/edit)
        if function in ["create_file", "patch", "apply_unified_patch", "apply_search_replace", "write_text", "multiedit", "edit_replace"]:
            write_cfg = permissions_cfg.get("file_write", {})
            if not write_cfg:
                return None
            default_policy = (write_cfg.get("default") or "allow").lower()
            if default_policy == "deny":
                return "file write operations are disabled by policy"
            return None

        return None
