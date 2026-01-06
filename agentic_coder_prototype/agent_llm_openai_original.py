from __future__ import annotations

import json
import os
import shutil
import uuid
from typing import Any, Dict, List, Optional
from pathlib import Path

import ray

from kylecode.sandbox_v2 import DevSandboxV2
from .core.core import ToolDefinition, ToolParameter
from .dialects.pythonic02 import Pythonic02Dialect
from .dialects.pythonic_inline import PythonicInlineDialect
from .dialects.aider_diff import AiderDiffDialect
from .dialects.unified_diff import UnifiedDiffDialect
from .dialects.opencode_patch import OpenCodePatchDialect
from .execution.composite import CompositeToolCaller
from .dialects.bash_block import BashBlockDialect
from .execution.dialect_manager import DialectManager
from .execution.enhanced_executor import EnhancedToolExecutor
from .compilation.tool_yaml_loader import load_yaml_tools
from .compilation.provider_schema import build_openai_tools_schema_from_yaml, filter_tools_for_provider_native
from .compilation.system_prompt_compiler import get_compiler
from .compilation.enhanced_config_validator import EnhancedConfigValidator
from .execution.sequential_executor import SequentialToolExecutor
from .provider_routing import provider_router
from .provider_adapters import provider_adapter_manager

try:
    from openai import OpenAI  # type: ignore
except Exception:  # pragma: no cover - optional dependency
    OpenAI = None  # type: ignore


def _tools_schema() -> List[Dict[str, Any]]:
    return [
        {
            "type": "function",
            "function": {
                "name": "write_file",
                "description": "Write a text file to the workspace (overwrites).",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "path": {"type": "string"},
                        "content": {"type": "string"},
                    },
                    "required": ["path", "content"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "read_file",
                "description": "Read a text file from the workspace.",
                "parameters": {
                    "type": "object",
                    "properties": {"path": {"type": "string"}},
                    "required": ["path"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "list_dir",
                "description": "List files in a directory in the workspace. Optional depth parameter for tree structure (1-5, default 1).",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "path": {"type": "string"},
                        "depth": {"type": "integer", "description": "Tree depth (1-5, default 1)", "default": 1}
                    },
                    "required": ["path"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "run_shell",
                "description": "Run a shell command in the workspace and return stdout/stderr/exit.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "command": {"type": "string"},
                        "timeout": {"type": "integer"},
                    },
                    "required": ["command"],
                },
            },
        },
    ]


def _dump_tool_defs(tool_defs: List[ToolDefinition]) -> List[Dict[str, Any]]:
    result: List[Dict[str, Any]] = []
    for t in tool_defs:
        result.append(
            {
                "type_id": t.type_id,
                "name": t.name,
                "description": t.description,
                "parameters": [
                    {
                        "name": p.name,
                        "type": p.type,
                        "description": p.description,
                        "default": p.default,
                    }
                    for p in t.parameters
                ],
            }
        )
    return result


@ray.remote
class OpenAIConductor:
    def __init__(self, workspace: str, image: str = "python-dev:latest", config: Optional[Dict[str, Any]] = None) -> None:
        # Local-host fallback unless explicitly using docker by env
        self.sandbox = DevSandboxV2.options(name=f"oa-sb-{uuid.uuid4()}").remote(image=image, workspace=workspace)
        self.workspace = workspace
        self.image = image
        self.config = config or {}
        
        # Initialize enhanced tools and configuration validator
        self.dialect_manager = DialectManager(self.config)
        
        # Load YAML-defined tools and manipulations map for routing/validation
        try:
            # Allow overriding the YAML tool defs directory via config
            defs_dir = (
                (self.config.get("tools", {}) or {}).get("defs_dir")
                or "implementations/tools/defs"
            )
            loaded = load_yaml_tools(defs_dir)
            self.yaml_tools = loaded.tools
            self.yaml_tool_manipulations = loaded.manipulations_by_id
        except Exception:
            self.yaml_tools = []
            self.yaml_tool_manipulations = {}
        # Expose manipulations map to downstream components via config
        try:
            self.config.setdefault("yaml_tool_manipulations", self.yaml_tool_manipulations)
        except Exception:
            pass

        # Initialize enhanced configuration validator
        enhanced_tools_config = self.config.get("tools", {}).get("registry")
        if enhanced_tools_config and os.path.exists("implementations/tools/enhanced_tools.yaml"):
            try:
                self.config_validator = EnhancedConfigValidator("implementations/tools/enhanced_tools.yaml")
            except Exception:
                self.config_validator = None
        else:
            self.config_validator = None
        
        # Initialize sequential executor for design decision implementation
        self.sequential_executor = None
        
        # Initialize enhanced executor if requested
        enhanced_config = self.config.get("enhanced_tools", {})
        # If LSP integration is requested, wrap sandbox even if enhanced executor not enabled
        if enhanced_config.get("lsp_integration", {}).get("enabled", False):
            try:
                from kylecode.sandbox_lsp_integration import LSPEnhancedSandbox
                self.sandbox = LSPEnhancedSandbox.remote(self.sandbox, workspace)
            except ImportError:
                pass
        if enhanced_config.get("enabled", False) or enhanced_config.get("lsp_integration", {}).get("enabled", False):
            # Wrap sandbox with LSP capabilities if requested
            sandbox_for_executor = self.sandbox
            if enhanced_config.get("lsp_integration", {}).get("enabled", False):
                try:
                    from kylecode.sandbox_lsp_integration import LSPEnhancedSandbox
                    sandbox_for_executor = LSPEnhancedSandbox.remote(self.sandbox, workspace)
                except ImportError:
                    pass  # LSP integration not available, use regular sandbox
            
            self.enhanced_executor = EnhancedToolExecutor(
                sandbox=sandbox_for_executor,
                config=self.config
            )
        else:
            self.enhanced_executor = None

    def _tool_defs_from_yaml(self) -> Optional[List[ToolDefinition]]:
        """Convert YAML EnhancedToolDefinitions to legacy ToolDefinition list for prompts."""
        try:
            if not getattr(self, "yaml_tools", None):
                return None
            
            # Get enabled tools from config
            enabled_tools = self.config.get("tools", {}).get("enabled", {})
            if not enabled_tools:
                return None
            
            converted: List[ToolDefinition] = []
            for t in self.yaml_tools:
                # Only include tools that are enabled in config
                if not enabled_tools.get(t.name, False):
                    continue
                    
                params = []
                for p in getattr(t, "parameters", []) or []:
                    params.append(ToolParameter(name=p.name, type=p.type, description=p.description, default=p.default))
                converted.append(
                    ToolDefinition(
                        type_id=getattr(t, "type_id", "python"),
                        name=t.name,
                        description=t.description,
                        parameters=params,
                        blocking=getattr(t, "blocking", False),
                    )
                )
            return converted if converted else None
        except Exception:
            return None

    def create_file(self, path: str) -> Dict[str, Any]:
        return ray.get(self.sandbox.write_text.remote(path, ""))

    def read_file(self, path: str) -> Dict[str, Any]:
        return ray.get(self.sandbox.read_text.remote(path))

    def list_dir(self, path: str, depth: int = 1) -> Dict[str, Any]:
        return ray.get(self.sandbox.ls.remote(path, depth))

    def run_shell(self, command: str, timeout: Optional[int] = None) -> Dict[str, Any]:
        stream = ray.get(self.sandbox.run.remote(command, timeout=timeout or 30, stream=True))
        # Decode adaptive-encoded list materialized; last element is {"exit": code}
        exit_obj = stream[-1] if stream else {"exit": None}
        # Collect only string lines, drop adaptive markers (>>>>> ...)
        lines: list[str] = []
        for x in stream[:-1]:
            if not isinstance(x, str):
                continue
            if x.startswith(">>>>>"):
                continue
            lines.append(x)
        stdout = "\n".join(lines)
        return {"stdout": stdout, "exit": exit_obj.get("exit")}

    def vcs(self, request: Dict[str, Any]) -> Dict[str, Any]:
        return ray.get(self.sandbox.vcs.remote(request))

    def _normalize_workspace_path(self, path_in: str) -> str:
        ws = os.path.normpath(self.workspace)
        if not path_in:
            return ws
        if os.path.isabs(path_in):
            return os.path.normpath(path_in)
        base = os.path.basename(ws)
        for sep in (os.sep, "/", "\\"):
            marker = base + sep
            if marker in path_in:
                tail = path_in.split(marker, 1)[1].lstrip("/\\")
                return os.path.join(ws, tail)
        if ws in path_in:
            tail = path_in.split(ws, 1)[1].lstrip("/\\")
            return os.path.join(ws, tail)
        return os.path.join(ws, path_in)
    
    def _is_tool_failure(self, tool_name: str, result: Dict[str, Any]) -> bool:
        """Check if a tool call failed based on its result"""
        # Check for explicit error
        if result.get("error"):
            return True
        
        # Check for explicit failure flag
        if result.get("ok") is False:
            return True
        
        # Check for bash command failure (non-zero exit code)
        if tool_name == "run_shell" and result.get("exit", 0) != 0:
            return True
        
        return False
    
    def _detect_completion_sota(self, msg_content: str, choice_finish_reason: str, tool_results: List[Dict[str, Any]] = None) -> Dict[str, Any]:
        """
        State-of-the-art completion detection using multiple methods.
        Returns: {"completed": bool, "method": str, "confidence": float, "reason": str}
        """
        completion_result = {"completed": False, "method": "none", "confidence": 0.0, "reason": ""}
        
        # Check if mark_task_complete tool is available
        mark_task_complete_available = True
        if hasattr(self, 'config') and self.config:
            tools_config = self.config.get("tools", {})
            mark_task_complete_available = tools_config.get("mark_task_complete", True)
        
        # 1. TOOL-BASED COMPLETION (Primary - Highest Confidence)
        if tool_results:
            for result in tool_results:
                if isinstance(result, dict) and result.get("action") == "complete":
                    return {
                        "completed": True, 
                        "method": "tool_based", 
                        "confidence": 1.0, 
                        "reason": "mark_task_complete() called"
                    }
        
        # 2. TEXT SENTINEL DETECTION (High Confidence - Enhanced when no tool completion)
        if self.completion_config.get("enable_text_sentinels", True) and msg_content:
            for sentinel in self.completion_config.get("text_sentinels", []):
                if sentinel and sentinel in msg_content:
                    # Boost confidence when tool completion is not available
                    confidence = 0.95 if not mark_task_complete_available else 0.9
                    return {
                        "completed": True,
                        "method": "text_sentinel", 
                        "confidence": confidence,
                        "reason": f"Sentinel detected: '{sentinel}'" + (
                            " (boosted confidence - no tool completion)" if not mark_task_complete_available else ""
                        )
                    }
        
        # 3. PROVIDER FINISH REASON ANALYSIS (Medium Confidence)
        if self.completion_config.get("enable_provider_signals", True) and choice_finish_reason:
            if choice_finish_reason in ["stop", "end_turn", "length"]:
                # Analyze content for completion indicators
                success_score = 0
                failure_score = 0
                
                if msg_content:
                    content_lower = msg_content.lower()
                    for keyword in self.completion_config.get("success_keywords", []):
                        if keyword in content_lower:
                            success_score += 1
                    for keyword in self.completion_config.get("failure_keywords", []):
                        if keyword in content_lower:
                            failure_score += 1
                
                # Natural completion if strong success indicators (enhanced scoring when no tool completion)
                required_score = 1 if not mark_task_complete_available else 2
                if success_score >= required_score and failure_score == 0:
                    confidence = 0.8 if not mark_task_complete_available else 0.7
                    return {
                        "completed": True,
                        "method": "provider_natural",
                        "confidence": confidence,
                        "reason": f"Provider finish + success indicators (score: {success_score})" + (
                            " (enhanced for no-tool mode)" if not mark_task_complete_available else ""
                        )
                    }
        
        # 4. NATURAL FINISH DETECTION (Lower Confidence)
        if self.completion_config.get("enable_natural_finish", True) and msg_content:
            # Look for natural completion patterns
            content_lower = msg_content.lower()
            natural_patterns = [
                "task is complete", "implementation is done", "everything is working",
                "all requirements met", "successfully implemented", "ready for use"
            ]
            
            for pattern in natural_patterns:
                if pattern in content_lower:
                    # Enhanced confidence when no tool completion available
                    confidence = 0.75 if not mark_task_complete_available else 0.6
                    return {
                        "completed": True,
                        "method": "natural_language",
                        "confidence": confidence,
                        "reason": f"Natural completion pattern: '{pattern}'" + (
                            " (enhanced for no-tool mode)" if not mark_task_complete_available else ""
                        )
                    }
        
        return completion_result
    
    def _format_tree_structure(self, tree_data: list[dict], prefix: str = "") -> str:
        """Format tree structure for display"""
        lines = []
        for i, item in enumerate(tree_data):
            is_last = i == len(tree_data) - 1
            current_prefix = "└── " if is_last else "├── "
            name = item["name"]
            if item["is_dir"]:
                name += "/"
            lines.append(prefix + current_prefix + name)
            
            # Add children if present
            if item.get("children"):
                child_prefix = prefix + ("    " if is_last else "│   ")
                child_lines = self._format_tree_structure(item["children"], child_prefix)
                lines.append(child_lines)
        
        return "\n".join(lines)
    
    def _format_tool_output(self, tool_name: str, result: dict, args: dict) -> str:
        """Format tool output in the new TOOL_OUTPUT format"""
        if tool_name == "list_dir":
            path = args.get("path", ".")
            depth = args.get("depth", 1)
            
            if result.get("error"):
                return f"<TOOL_OUTPUT tool=\"list_dir\" path=\"{path}\">\nError: {result['error']}\n</TOOL_OUTPUT>"
            
            if result.get("tree_format"):
                # Tree structure format
                tree_display = self._format_tree_structure(result.get("tree_structure", []))
                return f"<TOOL_OUTPUT tool=\"list_dir\" path=\"{path}\" depth={depth}>\n{tree_display}\n</TOOL_OUTPUT>"
            else:
                # Simple list format
                items = result.get("items", [])
                items_display = "\n".join(items)
                return f"<TOOL_OUTPUT tool=\"list_dir\" path=\"{path}\">\n{items_display}\n</TOOL_OUTPUT>"
        
        elif tool_name == "read_file":
            path = args.get("path", "")
            content = result.get("content", "")
            # Virtualize path (remove workspace prefix)
            rel_path = path.replace(self.workspace.rstrip("/"), "").lstrip("/")
            if not rel_path:
                rel_path = path
            return f"<TOOL_OUTPUT tool=\"read_file\" file=\"{rel_path}\">\n{content}\n</TOOL_OUTPUT>"
        
        elif tool_name in ["create_file", "create_file_from_block", "apply_unified_patch", "apply_search_replace"]:
            # File operation results with LSP integration
            path = result.get("path", args.get("path", args.get("file_name", "unknown")))
            rel_path = path.replace(self.workspace.rstrip("/"), "").lstrip("/") if isinstance(path, str) else "unknown"
            
            # Check for LSP diagnostics in result
            lsp_feedback = result.get("lsp_feedback", "")
            if lsp_feedback:
                if "0 linter errors" in lsp_feedback:
                    return f"<PATCH_RESULT file=\"{rel_path}\">\nFile successfully {'created' if 'create' in tool_name else 'patched'} with 0 linter errors.\n</PATCH_RESULT>"
                else:
                    return f"<PATCH_RESULT file=\"{rel_path}\">\nFile successfully {'created' if 'create' in tool_name else 'patched'}, but with linter errors:\n\n{lsp_feedback}\n</PATCH_RESULT>"
            else:
                # Fallback without LSP
                return f"<PATCH_RESULT file=\"{rel_path}\">\nFile successfully {'created' if 'create' in tool_name else 'patched'} with 0 linter errors.\n</PATCH_RESULT>"
        
        elif tool_name == "run_shell":
            stdout = result.get("stdout", "")
            stderr = result.get("stderr", "")
            exit_code = result.get("exit", 0)
            
            output_lines = []
            if stdout:
                output_lines.append(stdout.rstrip())
            if stderr:
                output_lines.append(f"stderr: {stderr.rstrip()}")
            if exit_code != 0:
                output_lines.append(f"exit code: {exit_code}")
            
            output_content = "\n".join(output_lines) if output_lines else "(no output)"
            return f"<BASH_RESULT>\n{output_content}\n</BASH_RESULT>"
        
        elif tool_name == "mark_task_complete":
            return f"<TOOL_OUTPUT tool=\"mark_task_complete\">\nTask marked as complete. Ending session.\n</TOOL_OUTPUT>"
        
        else:
            # Fallback to JSON for unknown tools
            try:
                return json.dumps(result)
            except Exception:
                return f"TOOL {tool_name} OUTPUT"
    
    def _exec_raw(self, tool_call: Dict[str, Any]) -> Dict[str, Any]:
        """Raw tool execution without enhanced features (for compatibility)"""
        name = tool_call["function"]
        args = tool_call["arguments"]
        
        if name == "create_file":
            target = self._normalize_workspace_path(str(args.get("path", "")))
            return self.create_file(target)
        if name == "read_file":
            target = self._normalize_workspace_path(str(args.get("path", "")))
            return self.read_file(target)
        if name == "list_dir":
            target = self._normalize_workspace_path(str(args.get("path", "")))
            depth = int(args.get("depth", 1))
            return self.list_dir(target, depth)
        if name == "run_shell":
            return self.run_shell(args["command"], args.get("timeout"))
        if name == "apply_search_replace":
            target = self._normalize_workspace_path(str(args.get("file_name", "")))
            search_text = str(args.get("search", ""))
            replace_text = str(args.get("replace", ""))
            try:
                exists = ray.get(self.sandbox.exists.remote(target))
            except Exception:
                exists = False
            if not exists or search_text.strip() == "":
                return ray.get(self.sandbox.write_text.remote(target, replace_text))
            return ray.get(self.sandbox.edit_replace.remote(target, search_text, replace_text, 1))
        if name == "apply_unified_patch":
            patch_text = str(args.get("patch", ""))
            return self.vcs({
                "action": "apply_patch",
                "params": {
                    "patch": patch_text,
                    "three_way": True,
                    "index": True,
                    "whitespace": "fix",
                    "keep_rejects": True,
                },
            })
        if name == "create_file_from_block":
            path = self._normalize_workspace_path(str(args.get("file_name", "")))
            content = str(args.get("content", ""))
            return ray.get(self.sandbox.write_text.remote(path, content))
        if name == "mark_task_complete":
            return {"action": "complete"}
        return {"error": f"unknown tool {name}"}

    def _write_snapshot(self, output_path: Optional[str], model: str, messages: List[Dict[str, Any]], transcript: List[Dict[str, Any]]) -> None:
        if not output_path:
            return
        try:
            diff = self.vcs({"action": "diff", "params": {"staged": False, "unified": 3}})
        except Exception:
            diff = {"ok": False, "data": {"diff": ""}}
        
        # Enhanced debugging information about tool usage and provider configuration
        provider_cfg = self.config.get("provider_tools", {}) if hasattr(self, 'config') else {}
        debug_info = {
            "provider_tools_config": provider_cfg,
            "tool_prompt_mode": getattr(self, 'last_tool_prompt_mode', 'unknown'),
            "native_tools_enabled": bool(provider_cfg.get("use_native", False)),
            "tools_suppressed": bool(provider_cfg.get("suppress_prompts", False)),
            "yaml_tools_count": len(getattr(self, 'yaml_tools', [])),
            "enhanced_executor_enabled": bool(getattr(self, 'enhanced_executor', None)),
        }
        
        # Analyze messages for tool calling patterns
        tool_analysis = {
            "total_messages": len(messages),
            "assistant_messages": len([m for m in messages if m.get("role") == "assistant"]),
            "messages_with_native_tool_calls": len([m for m in messages if m.get("role") == "assistant" and m.get("tool_calls")]),
            "messages_with_text_tool_calls": len([m for m in messages if m.get("role") == "assistant" and m.get("content") and "<TOOL_CALL>" in str(m.get("content", ""))]),
            "tool_role_messages": len([m for m in messages if m.get("role") == "tool"]),
        }
        
        snapshot = {
            "workspace": self.workspace,
            "image": self.image,
            "model": model,
            "messages": messages,
            "transcript": transcript,
            "diff": diff,
            "debug_info": debug_info,
            "tool_analysis": tool_analysis,
        }
        try:
            outp = Path(output_path)
            outp.parent.mkdir(parents=True, exist_ok=True)
            outp.write_text(json.dumps(snapshot, indent=2))
        except Exception:
            pass

    def _append_markdown(self, output_path: Optional[str], sections: List[Dict[str, str]]) -> None:
        """
        Append sections to a markdown log file. Each section is {"role": label, "content": text}.
        Renders as bold role headings with blank lines around.
        """
        if not output_path:
            return
        try:
            outp = Path(output_path)
            outp.parent.mkdir(parents=True, exist_ok=True)
            with outp.open("a", encoding="utf-8") as f:
                for sec in sections:
                    role = sec.get("role", "")
                    content = sec.get("content", "")
                    if not isinstance(content, str):
                        content = str(content)
                    f.write("\n\n**" + role + "**\n\n")
                    f.write(content.rstrip("\n") + "\n")
        except Exception:
            # Best-effort only; ignore logging errors
            pass

    def run_agentic_loop(
        self,
        system_prompt: str,
        user_prompt: str,
        model: str,
        max_steps: int = 12,
        output_json_path: Optional[str] = None,
        stream_responses: bool = False,
        output_md_path: Optional[str] = None,
        tool_prompt_mode: str = "system_once",  # system_once | per_turn_append | system_and_per_turn | none
        completion_sentinel: Optional[str] = ">>>>>> END RESPONSE",
        completion_config: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        if OpenAI is None:
            raise RuntimeError("openai package not installed")
        
        # Use provider router for client configuration
        provider_config, resolved_model, supports_native_tools_for_model = provider_router.get_provider_config(model)
        client_config = provider_router.create_client_config(model)
        
        if not client_config["api_key"]:
            raise RuntimeError(f"{provider_config.api_key_env} missing in environment")
        
        # Create client with provider-specific configuration
        client_kwargs = {"api_key": client_config["api_key"]}
        if client_config.get("base_url"):
            client_kwargs["base_url"] = client_config["base_url"]
        if client_config.get("default_headers"):
            client_kwargs["default_headers"] = client_config["default_headers"]
        
        client = OpenAI(**client_kwargs)
        
        # Update model to resolved version for API calls
        model = resolved_model
        # Public chat history for JSON output (true messages only)
        # Enhanced with descriptive debugging fields
        enhanced_system_msg = {"role": "system", "content": system_prompt}
        enhanced_user_msg = {"role": "user", "content": user_prompt}
        
        messages: List[Dict[str, Any]] = [enhanced_system_msg, enhanced_user_msg]
        # Provider chat history (what we send to the model). May include synthetic user messages with tool outputs.
        provider_messages: List[Dict[str, Any]] = [m.copy() for m in messages]
        # Build local tool-calling prompt for fallback/directives
        tool_defs_yaml = self._tool_defs_from_yaml()
        tool_defs = tool_defs_yaml or [
            ToolDefinition(
                type_id="python",
                name="run_shell",
                description="Run a shell command in the workspace and return stdout/exit.",
                parameters=[
                    ToolParameter(name="command", type="string", description="The shell command to execute"),
                    ToolParameter(name="timeout", type="integer", description="Timeout seconds", default=30),
                ],
                blocking=True,
            ),
            ToolDefinition(
                type_id="python",
                name="create_file",
                description=(
                    "Create an empty file (akin to 'touch'). For contents, use diff blocks: "
                    "SEARCH/REPLACE for edits to existing files; unified diff (```patch/```diff) or OpenCode Add File for new files."
                ),
                parameters=[
                    ToolParameter(name="path", type="string"),
                ],
            ),
            ToolDefinition(
                type_id="python",
                name="mark_task_complete",
                description=(
                    "Signal that the task is fully complete. When called, the agent will stop the run."
                ),
                parameters=[],
                blocking=True,
            ),
            ToolDefinition(
                type_id="python",
                name="read_file",
                description="Read a text file from the workspace.",
                parameters=[ToolParameter(name="path", type="string")],
            ),
            ToolDefinition(
                type_id="python",
                name="list_dir",
                description="List files in a directory in the workspace. Optional depth parameter for tree structure (1-5, default 1).",
                parameters=[
                    ToolParameter(name="path", type="string"),
                    ToolParameter(name="depth", type="integer", description="Tree depth (1-5, default 1)", default=1)
                ],
            ),
            ToolDefinition(
                type_id="diff",
                name="apply_search_replace",
                description="Edit code via SEARCH/REPLACE block (Aider-style)",
                parameters=[
                    ToolParameter(name="file_name", type="string"),
                    ToolParameter(name="search", type="string"),
                    ToolParameter(name="replace", type="string"),
                ],
            ),
            ToolDefinition(
                type_id="diff",
                name="apply_unified_patch",
                description="Apply a unified-diff patch (may include new files, edits, deletes)",
                parameters=[
                    ToolParameter(name="patch", type="string", description="Unified diff text; label blocks as ```patch or ```diff"),
                ],
                blocking=True,
            ),
            ToolDefinition(
                type_id="diff",
                name="create_file_from_block",
                description=(
                    "Create a new file from an OpenCode-style Add File block's parsed content. "
                    "Use when not emitting a unified diff."
                ),
                parameters=[
                    ToolParameter(name="file_name", type="string"),
                    ToolParameter(name="content", type="string"),
                ],
            ),
        ]
        # Create mapping from config names to dialect instances
        dialect_mapping = {
            "pythonic02": Pythonic02Dialect(),
            "pythonic_inline": PythonicInlineDialect(), 
            "bash_block": BashBlockDialect(),
            "aider_diff": AiderDiffDialect(),
            "unified_diff": UnifiedDiffDialect(),
            "opencode_patch": OpenCodePatchDialect(),
        }
        all_dialects = list(dialect_mapping.values())
        
        # Filter dialects based on model configuration using config names
        config_dialect_names = list(dialect_mapping.keys())
        active_dialect_names = self.dialect_manager.get_dialects_for_model(
            model, 
            config_dialect_names
        )
        
        # Initialize caller for all modes (needed for parsing)
        filtered_dialects = [dialect_mapping[name] for name in active_dialect_names if name in dialect_mapping]
        caller = CompositeToolCaller(filtered_dialects)
        
        # CRITICAL: Detect native tools early and adjust tool_prompt_mode before adding prompts
        # Use provider router to determine native tool capability
        use_native_tools = provider_router.should_use_native_tools(model, self.config)
        will_use_native_tools = False
        
        if use_native_tools and getattr(self, "yaml_tools", None):
            try:
                # Use provider adapter to filter tools properly
                provider_id = provider_router.parse_model_id(model)[0]
                native_tools, text_based_tools = provider_adapter_manager.filter_tools_for_provider(self.yaml_tools, provider_id)
                will_use_native_tools = bool(native_tools)
                self.current_native_tools = native_tools
                self.current_text_based_tools = text_based_tools
            except Exception:
                will_use_native_tools = False
        
        # Override tool_prompt_mode if native tools will be used
        if will_use_native_tools:
            suppress_prompts = bool(self.config.get("provider_tools", {}).get("suppress_prompts", False))
            if suppress_prompts:
                tool_prompt_mode = "none"  # Suppress prompts entirely for native tools
            else:
                tool_prompt_mode = "per_turn_append"  # Minimal prompts for native tools
        
        # Store tool_prompt_mode for debugging
        self.last_tool_prompt_mode = tool_prompt_mode
        
        # Initialize SoTA completion detection system
        default_completion_config = {
            "primary_method": "hybrid",  # tool_based | text_based | provider_based | hybrid
            "enable_text_sentinels": True,
            "enable_provider_signals": True,
            "enable_natural_finish": True,
            "confidence_threshold": 0.6,
            "text_sentinels": [
                completion_sentinel,
                "TASK COMPLETE",
                "ALL TESTS PASSED", 
                "IMPLEMENTATION COMPLETE",
                "DONE"
            ] if completion_sentinel else [
                "TASK COMPLETE",
                "ALL TESTS PASSED",
                "IMPLEMENTATION COMPLETE", 
                "DONE"
            ],
            "success_keywords": [
                "success", "complete", "finished", "done", "passed", "working", "ready"
            ],
            "failure_keywords": [
                "failed", "error", "broken", "timeout", "cancelled"
            ]
        }
        
        # Merge with config from YAML file 
        yaml_completion_config = self.config.get("completion", {}) if hasattr(self, 'config') and self.config else {}
        if yaml_completion_config.get("text_sentinels"):
            # Merge text sentinels from YAML with defaults
            yaml_sentinels = yaml_completion_config["text_sentinels"]
            default_sentinels = default_completion_config["text_sentinels"]
            yaml_completion_config["text_sentinels"] = list(set(yaml_sentinels + default_sentinels))
        
        self.completion_config = {**default_completion_config, **yaml_completion_config, **(completion_config or {})}
        
        # Use only text-based tools for prompt compilation (exclude provider-native tools)
        prompt_tool_defs = getattr(self, 'current_text_based_tools', None) or tool_defs
        
        # Handle different tool prompt modes
        if tool_prompt_mode == "system_compiled_and_persistent_per_turn":
            # NEW MODE: Use comprehensive cached system prompt + small per-turn availability
            compiler = get_compiler()
            
            # Get comprehensive system prompt (cached) - includes primary prompt first
            primary_prompt = messages[0].get("content", "")
            comprehensive_prompt, tools_hash = compiler.get_or_create_system_prompt(prompt_tool_defs, active_dialect_names, primary_prompt)
            
            # Replace system prompt with comprehensive version (primary + tools)
            messages[0]["content"] = comprehensive_prompt
            
            # Create enhanced per-turn availability formatter with format preferences
            def get_per_turn_availability():
                enabled_tools = [t.name for t in prompt_tool_defs]
                preferred_formats = compiler.get_preferred_formats(active_dialect_names)
                return compiler.format_per_turn_availability_enhanced(enabled_tools, preferred_formats)
            
            tool_directive_text = get_per_turn_availability()
            local_tools_prompt = "(using cached comprehensive system prompt with research-based preferences)"
        else:
            # LEGACY MODES: Use old composite caller approach
            local_tools_prompt = caller.build_prompt(prompt_tool_defs)
            
            # Add workspace context if enhanced tools are enabled
            if self.enhanced_executor:
                context = self.enhanced_executor.get_workspace_context()
                if context.get("files_created_this_session"):
                    local_tools_prompt += f"\n\nWORKSPACE CONTEXT:\nFiles created this session: {context['files_created_this_session']}"
                    local_tools_prompt += "\nIMPORTANT: Use edit tools for existing files, not create tools.\n"
            
            # Build a reusable directive text
            tool_directive_text = (
                "\n\nSYSTEM MESSAGE - AVAILABLE TOOLS\n"
                f"<FUNCTIONS>\n{local_tools_prompt}\n</FUNCTIONS>\n"
                "MANDATORY: Respond ONLY with one or more tool calls using <TOOL_CALL> ..., with <BASH>...</BASH> for shell, or with diff blocks (SEARCH/REPLACE for edits; unified diff or OpenCode Add File for new files).\n"
                "You may call multiple tools in one reply; non-blocking tools may run concurrently.\n"
                "Some tools are blocking and must run alone in sequence.\n"
                "Do NOT include any extra prose beyond tool calls or diff blocks.\n"
                "When you deem the task fully complete, call mark_task_complete().\n"
                "NEVER use bash to write large file contents (heredocs, echo >>). For files: call create_file() then apply a diff block for contents.\n"
                "Do NOT include extra prose.\nEND SYSTEM MESSAGE\n"
            )
        for del_path in [output_md_path, output_json_path]:
            if os.path.exists(del_path):
                os.remove(del_path)
        
        # Helper: concise tools list for markdown-only logging
        def _tools_available_md(tools: List[ToolDefinition]) -> str:
            names = "\n".join(t.name for t in tools)
            return f"<TOOLS_AVAILABLE>\n{names}\n</TOOLS_AVAILABLE>"

        # Optionally embed the tools directive once into the initial system prompt
        if tool_prompt_mode in ("system_once", "system_and_per_turn"):
            messages[0]["content"] = (messages[0].get("content") or "") + tool_directive_text
            # Mirror into markdown log
            self._append_markdown(output_md_path, [{"role": "System", "content": _tools_available_md(tool_defs)}])
        elif tool_prompt_mode == "system_compiled_and_persistent_per_turn":
            # The comprehensive prompt was already added to system message above
            # Don't log the abridged availability as a system message - it will be added to user messages
            pass
        elif tool_prompt_mode == "none":
            # Native tools mode: suppress all tool prompts - tools are provided via OpenAI API
            pass

        # Add enhanced descriptive fields to initial messages after tool setup
        # System message enhancement
        if tool_prompt_mode in ("system_once", "system_and_per_turn", "system_compiled_and_persistent_per_turn"):
            messages[0]["compiled_tools_available"] = [
                {
                    "name": t.name,
                    "type_id": t.type_id,
                    "description": t.description,
                    "parameters": [{"name": p.name, "type": p.type, "description": p.description, "default": p.default} for p in t.parameters] if t.parameters else []
                }
                for t in tool_defs
            ]
        
        # User message enhancement
        tools_prompt_content = None
        native_tools_spec = None
        
        if tool_prompt_mode == "system_compiled_and_persistent_per_turn":
            enabled_tools = [t.name for t in tool_defs]
            tools_prompt_content = get_compiler().format_per_turn_availability(enabled_tools, active_dialect_names)
        elif tool_prompt_mode in ("per_turn_append", "system_and_per_turn"):
            tools_prompt_content = tool_directive_text
        
        # Check if native tools will be used
        if will_use_native_tools:
            try:
                native_tools = getattr(self, 'current_native_tools', [])
                if native_tools:
                    provider_id = provider_router.parse_model_id(model)[0]
                    native_tools_spec = provider_adapter_manager.translate_tools_to_native_schema(native_tools, provider_id)
            except Exception:
                pass
        
        messages[1]["tools_available_prompt"] = tools_prompt_content
        if native_tools_spec:
            messages[1]["tools"] = native_tools_spec
        
        transcript: List[Dict[str, Any]] = []
        
        # For the new mode, append tools to initial user message too
        initial_user_content = user_prompt
        if tool_prompt_mode == "system_compiled_and_persistent_per_turn":
            enabled_tools = [t.name for t in tool_defs]
            per_turn_availability = get_compiler().format_per_turn_availability(enabled_tools, active_dialect_names)
            initial_user_content = user_prompt + "\n\n" + per_turn_availability
            # Also update the actual message arrays
            messages[1]["content"] = initial_user_content
            provider_messages[1]["content"] = initial_user_content
        
        # Initialize markdown log with initial messages
        self._append_markdown(output_md_path, [
            {"role": "System", "content": system_prompt},
            {"role": "User", "content": initial_user_content},
        ])
        # Initial snapshot
        self._write_snapshot(output_json_path, model, messages, transcript)
        for _ in range(max_steps):
            try:
                # Build a per-turn message list that appends tool prompts into the last user message
                send_messages: List[Dict[str, Any]] = [dict(m) for m in provider_messages]
                # Ensure last message is user; if not, append a user wrapper for tools prompt
                if not send_messages or send_messages[-1].get("role") != "user":
                    send_messages.append({"role": "user", "content": ""})
                if tool_prompt_mode in ("per_turn_append", "system_and_per_turn"):
                    send_messages[-1]["content"] = (send_messages[-1].get("content") or "") + tool_directive_text
                    # Log the tool prompt to markdown for debugging
                    self._append_markdown(output_md_path, [{"role": "System", "content": _tools_available_md(tool_defs)}])
                elif tool_prompt_mode == "system_compiled_and_persistent_per_turn":
                    # NEW MODE: Permanently append small availability list to user messages
                    # Get fresh per-turn availability (in case tools changed)
                    enabled_tools = [t.name for t in tool_defs]
                    per_turn_availability = get_compiler().format_per_turn_availability(enabled_tools, active_dialect_names)
                    
                    # Permanently append to the actual message history
                    send_messages[-1]["content"] = (send_messages[-1].get("content") or "") + "\n\n" + per_turn_availability
                    provider_messages[-1]["content"] = (provider_messages[-1].get("content") or "") + "\n\n" + per_turn_availability
                elif tool_prompt_mode == "none":
                    # Native tools mode: no tool prompts added - tools provided via OpenAI API
                    pass
                # Per-turn tooling context logging (available tools + compiled tools prompt)
                transcript.append(
                    {
                        "tools_context": {
                            "available_tools": _dump_tool_defs(tool_defs),
                            "compiled_tools_prompt": local_tools_prompt,
                        }
                    }
                )
                # Snapshot after adding tooling context to enable near real-time monitoring
                self._write_snapshot(output_json_path, model, messages, transcript)
                # Always use raw text prompting; do not pass native tools to avoid provider call_id coupling
                # Optionally pass provider-native tools based on provider capabilities
                use_native_tools = provider_router.should_use_native_tools(model, self.config)
                tools_schema = None
                if use_native_tools and getattr(self, "current_native_tools", None):
                    try:
                        # Use provider adapter to translate tools to native schema
                        provider_id = provider_router.parse_model_id(model)[0]
                        native_tools = getattr(self, 'current_native_tools', [])
                        if native_tools:
                            tools_schema = provider_adapter_manager.translate_tools_to_native_schema(native_tools, provider_id)
                    except Exception:
                        tools_schema = None

                create_kwargs = {
                    "model": model,
                    "messages": send_messages,
                }
                if tools_schema:
                    create_kwargs["tools"] = tools_schema
                    # Note: tool_prompt_mode was already adjusted earlier based on provider_tools config

                resp = client.chat.completions.create(**create_kwargs)
            except Exception as e:
                # Surface OpenAI errors without crashing the actor
                result = {
                    "error": str(e),
                    "error_type": e.__class__.__name__,
                    "messages": messages,
                    "transcript": transcript,
                    "hint": "Verify OPENAI_API_KEY and model name; try a known model via --model",
                }
                # Write error snapshot
                try:
                    if output_json_path:
                        Path(output_json_path).write_text(json.dumps(result, indent=2))
                except Exception:
                    pass
                return result
            choice = resp.choices[0]
            msg = choice.message
            # Lightweight debug snapshot of the model's first choice
            try:
                debug_tc = None
                if getattr(msg, "tool_calls", None):
                    debug_tc = [
                        {"id": getattr(tc, "id", None), "name": getattr(getattr(tc, "function", None), "name", None)}
                        for tc in msg.tool_calls
                    ]
                transcript.append(
                    {
                        "choice_debug": {
                            "finish_reason": getattr(choice, "finish_reason", None),
                            "has_content": bool(getattr(msg, "content", None)),
                            "tool_calls_len": len(msg.tool_calls) if getattr(msg, "tool_calls", None) else 0,
                        }
                    }
                )
            except Exception:
                pass
            # Mirror assistant response into markdown log
            if getattr(msg, "content", None):
                self._append_markdown(output_md_path, [{"role": "Assistant", "content": str(msg.content)}])
            # Optional stdout echo (non-blocking)
            if stream_responses and getattr(msg, "content", None):
                try:
                    print(str(msg.content))
                except Exception:
                    pass

            # Capture degenerate responses to aid debugging
            if not getattr(msg, "tool_calls", None) and not getattr(msg, "content", None):
                transcript.append(
                    {
                        "empty_response": {
                            "finish_reason": getattr(choice, "finish_reason", None),
                            "index": getattr(choice, "index", None),
                        }
                    }
                )
                # Break to avoid looping endlessly on empty responses
                if stream_responses:
                    print("[stop] reason=empty-response")
                break
            # Fallback parse for local tool-calling if no function-tools present but text includes our dialects
            if not getattr(msg, "tool_calls", None) and (msg.content or ""):
                parsed = caller.parse_all(msg.content, tool_defs)
                if parsed:
                    # CRITICAL: Add assistant message with text-based tool calls to messages array
                    # Create synthetic tool_calls array with enhanced metadata for text-based parsing
                    synthetic_tool_calls = []
                    for i, p in enumerate(parsed):
                        synthetic_tool_calls.append({
                            "id": f"text_call_{i}",
                            "type": "function", 
                            "function": {
                                "name": p.function,
                                "arguments": json.dumps(p.arguments) if isinstance(p.arguments, dict) else str(p.arguments)
                            },
                            "syntax_type": "custom-pythonic"  # Our <TOOL_CALL> format
                        })
                    
                    assistant_entry = {
                        "role": "assistant", 
                        "content": msg.content,
                        "tool_calls": synthetic_tool_calls
                    }
                    messages.append(assistant_entry)
                    transcript.append({
                        "assistant_with_text_tool_calls": {
                            "content": msg.content,
                            "parsed_tools_count": len(parsed),
                            "parsed_tools": [p.function for p in parsed]
                        }
                    })
                    
                    # NEW: Validate tool calls against design decision constraints
                    tool_calls_for_validation = [{"name": p.function, "args": p.arguments} for p in parsed]
                    
                    # Use enhanced validation if available
                    validation_result = None
                    if self.config_validator:
                        validation_result = self.config_validator.validate_tool_calls(tool_calls_for_validation)
                        
                        # Check for validation errors (critical constraints)
                        if not validation_result["valid"]:
                            error_message = "Tool execution validation failed:\n" + "\n".join(validation_result["errors"])
                            formatted_error = f"<VALIDATION_ERROR>\n{error_message}\n</VALIDATION_ERROR>"
                            
                            provider_messages.append({"role": "user", "content": formatted_error})
                            self._append_markdown(output_md_path, [{"role": "User", "content": formatted_error}])
                            
                            if stream_responses:
                                print(f"[stop] reason=validation-failed")
                            continue
                    
                    # Execute tool calls in order with policy controls
                    # IMPORTANT: Prioritize file operations before bash commands to avoid bash failures cancelling file creation
                    file_ops = [p for p in parsed if p.function in ['create_file', 'create_file_from_block', 'apply_unified_patch', 'apply_search_replace']]
                    other_ops = [p for p in parsed if p.function not in ['create_file', 'create_file_from_block', 'apply_unified_patch', 'apply_search_replace']]
                    reordered_parsed = file_ops + other_ops
                    
                    # CRITICAL: Check for mark_task_complete isolation constraint
                    completion_calls = [p for p in parsed if p.function == "mark_task_complete"]
                    if completion_calls and len(parsed) > 1:
                        # Mark_task_complete must be isolated - reject other operations
                        error_msg = f"mark_task_complete() cannot be combined with other operations. Found {len(parsed)} total calls including: {[p.function for p in parsed]}"
                        formatted_error = f"<CONSTRAINT_ERROR>\n{error_msg}\nPlease call mark_task_complete() alone in a separate response after completing all file operations.\n</CONSTRAINT_ERROR>"
                        
                        provider_messages.append({"role": "user", "content": formatted_error})
                        self._append_markdown(output_md_path, [{"role": "User", "content": formatted_error}])
                        
                        if stream_responses:
                            print(f"[constraint] mark_task_complete isolation violated")
                        continue
                    
                    # Helper to run a single call
                    def _exec_call(name: str, args: Dict[str, Any]) -> Dict[str, Any]:
                        tool_call = {"function": name, "arguments": args}
                        
                        # Use enhanced executor if available
                        if self.enhanced_executor:
                            try:
                                import asyncio
                                
                                # Create async wrapper for _exec_raw
                                async def async_exec_raw(tool_call):
                                    return self._exec_raw(tool_call)
                                
                                # Try to get existing event loop, create new one if needed
                                try:
                                    loop = asyncio.get_event_loop()
                                    if loop.is_closed():
                                        raise RuntimeError("Loop is closed")
                                except RuntimeError:
                                    loop = asyncio.new_event_loop()
                                    asyncio.set_event_loop(loop)
                                
                                result = loop.run_until_complete(
                                    self.enhanced_executor.execute_tool_call(tool_call, async_exec_raw)
                                )
                                return result
                            except Exception:
                                # Fallback to raw execution on enhanced executor failure
                                pass
                        
                        # Raw execution (original implementation)
                        return self._exec_raw(tool_call)
                    
                    # Concurrency policy: allow multiple nonblocking tools per turn (diff/read/search), but do not mix with others in same turn
                    turn_cfg = self.config.get("turn_strategy", {}) if isinstance(self.config, dict) else {}
                    allow_multi = bool(turn_cfg.get("allow_multiple_per_turn", False))
                    conc_cfg = self.config.get("concurrency", {}) if isinstance(self.config, dict) else {}
                    nonblocking_names = set(conc_cfg.get("nonblocking_tools", [])) or set([
                        'apply_unified_patch', 'apply_search_replace', 'create_file_from_block',
                        'read', 'read_file', 'glob', 'grep', 'list', 'list_dir', 'patch'
                    ])
                    nb_calls = [p for p in reordered_parsed if p.function in nonblocking_names]
                    other_calls = [p for p in reordered_parsed if p.function not in nonblocking_names]
                    if nb_calls:
                        calls_to_execute = nb_calls
                    else:
                        calls_to_execute = reordered_parsed if allow_multi else reordered_parsed[:1]

                    # Apply design decision constraints during execution
                    executed_results = []
                    failed_at_index = -1
                    bash_executed = False  # Track bash constraint (critical research finding)
                    
                    # If executing multiple nonblocking calls, run them concurrently for performance
                    def _run_single_call(p):
                        try:
                            out = _exec_call(p.function, p.arguments)
                        except Exception as e:
                            out = {"error": str(e), "function": p.function}
                        return (p, out)

                    if len(calls_to_execute) > 1 and all(c.function in nonblocking_names for c in calls_to_execute):
                        try:
                            import concurrent.futures
                            with concurrent.futures.ThreadPoolExecutor(max_workers=min(8, len(calls_to_execute))) as pool:
                                futures = [pool.submit(_run_single_call, p) for p in calls_to_execute]
                                for fut in futures:
                                    p, out = fut.result()
                                    transcript.append({"tool": p.function, "result": out})
                                    self._write_snapshot(output_json_path, model, messages, transcript)
                                    executed_results.append((p, out))
                                    if self._is_tool_failure(p.function, out) and failed_at_index < 0:
                                        failed_at_index = 0
                        except Exception as _:
                            # Fallback to sequential if thread pool execution fails
                            for i, p in enumerate(calls_to_execute):
                                if p.function == "run_shell":
                                    if bash_executed:
                                        out = {"error": "Only one bash command allowed per turn (policy)", "skipped": True}
                                        executed_results.append((p, out))
                                        continue
                                    bash_executed = True
                                try:
                                    out = _exec_call(p.function, p.arguments)
                                except Exception as e:
                                    out = {"error": str(e), "function": p.function}
                                transcript.append({"tool": p.function, "result": out})
                                self._write_snapshot(output_json_path, model, messages, transcript)
                                executed_results.append((p, out))
                                if self._is_tool_failure(p.function, out):
                                    failed_at_index = i
                                    break
                    else:
                        for i, p in enumerate(calls_to_execute):
                            if p.function == "run_shell":
                                if bash_executed:
                                    out = {"error": "Only one bash command allowed per turn (policy)", "skipped": True}
                                    executed_results.append((p, out))
                                    continue
                                bash_executed = True
                            try:
                                out = _exec_call(p.function, p.arguments)
                            except Exception as e:
                                out = {"error": str(e), "function": p.function}
                            transcript.append({"tool": p.function, "result": out})
                            self._write_snapshot(output_json_path, model, messages, transcript)
                            executed_results.append((p, out))
                            if self._is_tool_failure(p.function, out):
                                failed_at_index = i
                                break
                        # CRITICAL: Enforce bash constraint (only one bash command per turn)
                        if p.function == "run_shell":
                            if bash_executed:
                                error_out = {
                                    "error": "Only one bash command allowed per turn (research constraint)",
                                    "function": p.function,
                                    "skipped": True
                                }
                                executed_results.append((p, error_out))
                                continue
                            bash_executed = True
                        try:
                            out = _exec_call(p.function, p.arguments)
                        except Exception as e:
                            out = {"error": str(e), "function": p.function}
                        
                        transcript.append({"tool": p.function, "result": out})
                        self._write_snapshot(output_json_path, model, messages, transcript)
                        executed_results.append((p, out))
                        
                        # SoTA COMPLETION DETECTION: Enhanced tool-based completion analysis
                        if isinstance(out, dict) and out.get("action") == "complete":
                            # Run SoTA completion detection with tool results
                            completion_analysis = self._detect_completion_sota(
                                msg_content="",  # No message content for tool-based completion
                                choice_finish_reason=None,
                                tool_results=[out]
                            )
                            
                            # Add enhanced completion tracking
                            transcript.append({
                                "tool_completion_detected": {
                                    "method": completion_analysis["method"],
                                    "confidence": completion_analysis["confidence"],
                                    "reason": completion_analysis["reason"],
                                    "tool_result": out
                                }
                            })
                            
                            # Format and show all executed results including the completion
                            chunks = []
                            for tool_parsed, tool_result in executed_results:
                                formatted_output = self._format_tool_output(tool_parsed.function, tool_result, tool_parsed.arguments)
                                chunks.append(formatted_output)
                            
                            provider_tool_msg = "\n\n".join(chunks)
                            provider_messages.append({"role": "user", "content": provider_tool_msg})
                            self._append_markdown(output_md_path, [{"role": "User", "content": provider_tool_msg}])
                            
                            if stream_responses:
                                print(f"[stop] reason={completion_analysis['method']} confidence={completion_analysis['confidence']:.2f} - {completion_analysis['reason']}")
                            self._write_snapshot(output_json_path, model, messages, transcript)
                            return {"messages": messages, "transcript": transcript}
                        
                        # Check for failure
                        if self._is_tool_failure(p.function, out):
                            failed_at_index = i
                            break
                    
                    # Format results with failure handling and relay according to strategy
                    chunks = []
                    
                    if failed_at_index >= 0:
                        # Tool failed - show executed results and cancellation message
                        for j, (tool_parsed, tool_result) in enumerate(executed_results):
                            if j == failed_at_index:
                                # Show the failed tool with error formatting
                                if tool_parsed.function == "run_shell":
                                    stdout = tool_result.get("stdout", "")
                                    stderr = tool_result.get("stderr", "")
                                    exit_code = tool_result.get("exit", 0)
                                    error_msg = f"Command failed with exit code {exit_code}"
                                    if stderr:
                                        error_msg += f"\nstderr: {stderr}"
                                    chunks.append(f"<BASH_RESULT>\n{stdout}\n{error_msg}\n</BASH_RESULT>")
                                else:
                                    error_msg = tool_result.get("error", "Unknown error")
                                    chunks.append(f"<TOOL_ERROR tool=\"{tool_parsed.function}\">\n{error_msg}\n</TOOL_ERROR>")
                            else:
                                # Show successful tool normally
                                formatted_output = self._format_tool_output(tool_parsed.function, tool_result, tool_parsed.arguments)
                                chunks.append(formatted_output)
                        
                        # Add cancellation message  
                        remaining_count = len(reordered_parsed) - failed_at_index - 1
                        if remaining_count > 0:
                            chunks.append(f"\nThe remaining {remaining_count} tool call(s) were cancelled due to the failure of the above call.")
                    else:
                        # All tools succeeded - show all results normally
                        for tool_parsed, tool_result in executed_results:
                            formatted_output = self._format_tool_output(tool_parsed.function, tool_result, tool_parsed.arguments)
                            chunks.append(formatted_output)

                    # Add tool result messages to main messages array for debugging
                    for tool_parsed, tool_result in executed_results:
                        formatted_output = self._format_tool_output(tool_parsed.function, tool_result, tool_parsed.arguments)
                        tool_result_entry = {
                            "role": "tool_result",
                            "content": formatted_output,
                            "tool_calls": [{
                                "id": f"text_call_{tool_parsed.function}",
                                "function": {"name": tool_parsed.function},
                                "result": tool_result,
                                "syntax_type": "custom-pythonic"
                            }]
                        }
                        messages.append(tool_result_entry)

                    # Get turn strategy flow configuration for text-based tools
                    turn_cfg = self.config.get("turn_strategy", {}) if isinstance(self.config, dict) else {}
                    flow_strategy = turn_cfg.get("flow", "assistant_continuation").lower()
                    
                    if flow_strategy == "assistant_continuation":
                        # ASSISTANT CONTINUATION PATTERN: Create assistant message with tool results
                        provider_tool_msg = "\n\n".join(chunks)
                        assistant_continuation = {
                            "role": "assistant",
                            "content": f"\n\nTool execution results:\n{provider_tool_msg}"
                        }
                        messages.append(assistant_continuation)
                        provider_messages.append(assistant_continuation.copy())
                        self._append_markdown(output_md_path, [{"role": "Assistant", "content": assistant_continuation["content"]}])
                    else:
                        # USER INTERLEAVED PATTERN: Traditional user message relay
                        provider_tool_msg = "\n\n".join(chunks)
                        provider_messages.append({"role": "user", "content": provider_tool_msg})
                        self._append_markdown(output_md_path, [{"role": "User", "content": provider_tool_msg}])
                    
                    # Continue next step
                    continue

            if msg.tool_calls:
                # Execute provider-native tool calls and relay results using provider-supported 'tool' role
                turn_cfg = self.config.get("turn_strategy", {}) if isinstance(self.config, dict) else {}
                relay_strategy = (turn_cfg.get("relay") or "tool_role").lower()

                tool_messages_to_relay: List[Dict[str, Any]] = []

                try:
                    # 1) Append the assistant message that contains tool_calls to maintain linkage
                    try:
                        tool_calls_payload = []
                        for tc in msg.tool_calls:
                            fn_name = getattr(getattr(tc, "function", None), "name", None)
                            arg_str = getattr(getattr(tc, "function", None), "arguments", "{}")
                            tool_calls_payload.append({
                                "id": getattr(tc, "id", None),
                                "type": "function",
                                "function": {"name": fn_name, "arguments": arg_str if isinstance(arg_str, str) else json.dumps(arg_str or {})},
                            })
                        
                        # CRITICAL: Add assistant message with tool calls to BOTH arrays
                        # Enhanced assistant entry with syntax type metadata
                        enhanced_tool_calls = []
                        for tc in tool_calls_payload:
                            enhanced_tc = tc.copy()
                            enhanced_tc["syntax_type"] = "openai"  # Native OpenAI function calling
                            enhanced_tool_calls.append(enhanced_tc)
                        
                        assistant_entry = {
                            "role": "assistant",
                            "content": msg.content,
                            "tool_calls": enhanced_tool_calls,
                        }
                        messages.append(assistant_entry)  # For complete session history in JSON output
                        provider_messages.append({"role": "assistant", "content": msg.content, "tool_calls": tool_calls_payload})  # For provider (no enhanced fields)
                        
                        # Add to transcript for debugging
                        transcript.append({
                            "assistant_with_tool_calls": {
                                "content": msg.content,
                                "tool_calls_count": len(msg.tool_calls),
                                "tool_calls": [tc["function"]["name"] for tc in tool_calls_payload]
                            }
                        })
                    except Exception:
                        pass

                    # 2) Execute calls and prepare relay of results (with nonblocking concurrency policy)
                    turn_cfg = self.config.get("turn_strategy", {}) if isinstance(self.config, dict) else {}
                    conc_cfg = self.config.get("concurrency", {}) if isinstance(self.config, dict) else {}
                    nonblocking_names = set(conc_cfg.get("nonblocking_tools", [])) or set([
                        'apply_unified_patch', 'apply_search_replace', 'create_file_from_block',
                        'read', 'read_file', 'glob', 'grep', 'list', 'list_dir', 'patch'
                    ])

                    calls: List[Dict[str, Any]] = []
                    for tc in msg.tool_calls:
                        fn = getattr(getattr(tc, "function", None), "name", None)
                        call_id = getattr(tc, "id", None)
                        arg_str = getattr(getattr(tc, "function", None), "arguments", "{}")
                        try:
                            args = json.loads(arg_str) if isinstance(arg_str, str) else (arg_str or {})
                        except Exception:
                            args = {}
                        if fn:
                            calls.append({"fn": fn, "call_id": call_id, "args": args})

                    only_nonblocking = all(c["fn"] in nonblocking_names for c in calls) if calls else False

                    def _exec_native_call(fn_name: str, args: Dict[str, Any]):
                        try:
                            if self.enhanced_executor:
                                import asyncio
                                async def async_exec_raw(tc_obj):
                                    return self._exec_raw(tc_obj)
                                try:
                                    loop = asyncio.get_event_loop()
                                    if loop.is_closed():
                                        raise RuntimeError("Loop is closed")
                                except RuntimeError:
                                    loop = asyncio.new_event_loop()
                                    asyncio.set_event_loop(loop)
                                return loop.run_until_complete(
                                    self.enhanced_executor.execute_tool_call({"function": fn_name, "arguments": args}, async_exec_raw)
                                )
                            else:
                                return self._exec_raw({"function": fn_name, "arguments": args})
                        except Exception as e:
                            return {"error": str(e), "function": fn_name}

                    results: List[Dict[str, Any]] = []
                    if only_nonblocking and len(calls) > 1:
                        # Run nonblocking tools concurrently
                        try:
                            import concurrent.futures
                            with concurrent.futures.ThreadPoolExecutor(max_workers=min(8, len(calls))) as pool:
                                future_map = {pool.submit(_exec_native_call, c["fn"], c["args"]): c for c in calls}
                                for fut in future_map:
                                    out = fut.result()
                                    c = future_map[fut]
                                    transcript.append({"tool": c["fn"], "result": out})
                                    self._write_snapshot(output_json_path, model, messages, transcript)
                                    results.append({"fn": c["fn"], "args": c["args"], "call_id": c["call_id"], "out": out})
                        except Exception:
                            # Fallback to sequential
                            for c in calls:
                                out = _exec_native_call(c["fn"], c["args"])
                                transcript.append({"tool": c["fn"], "result": out})
                                self._write_snapshot(output_json_path, model, messages, transcript)
                                results.append({"fn": c["fn"], "args": c["args"], "call_id": c["call_id"], "out": out})
                    else:
                        # Respect single-tool-per-turn policy unless explicitly multiple allowed
                        allow_multi = bool(turn_cfg.get("allow_multiple_per_turn", False))
                        run_list = calls if allow_multi else (calls[:1] if calls else [])
                        for c in run_list:
                            out = _exec_native_call(c["fn"], c["args"])
                            transcript.append({"tool": c["fn"], "result": out})
                            self._write_snapshot(output_json_path, model, messages, transcript)
                            results.append({"fn": c["fn"], "args": c["args"], "call_id": c["call_id"], "out": out})

                    # Get turn strategy flow configuration
                    flow_strategy = turn_cfg.get("flow", "assistant_continuation").lower()
                    
                    # Relay results based on flow strategy
                    if flow_strategy == "assistant_continuation":
                        # ASSISTANT CONTINUATION PATTERN: Append tool results to assistant messages
                        all_results_text = []
                        for r in results:
                            formatted_output = self._format_tool_output(r["fn"], r["out"], r["args"])
                            call_id = r.get("call_id")
                            
                            # Always add tool result to main messages array for debugging
                            tool_result_entry = {
                                "role": "tool_result",
                                "content": formatted_output,
                                "tool_calls": [{
                                    "id": call_id or f"native_call_{r['fn']}",
                                    "function": {"name": r["fn"]},
                                    "result": r["out"],
                                    "syntax_type": "openai"
                                }]
                            }
                            messages.append(tool_result_entry)
                            all_results_text.append(formatted_output)
                        
                        # Create assistant continuation message with tool results
                        continuation_content = f"\n\nTool execution results:\n" + "\n\n".join(all_results_text)
                        assistant_continuation = {
                            "role": "assistant", 
                            "content": continuation_content
                        }
                        messages.append(assistant_continuation)
                        provider_messages.append(assistant_continuation.copy())
                    else:
                        # USER INTERLEAVED PATTERN: Traditional relay via user/tool messages
                        for r in results:
                            formatted_output = self._format_tool_output(r["fn"], r["out"], r["args"])
                            call_id = r.get("call_id")
                            
                            # Always add tool result to main messages array for debugging
                            tool_result_entry = {
                                "role": "tool_result",
                                "content": formatted_output,
                                "tool_calls": [{
                                    "id": call_id or f"native_call_{r['fn']}",
                                    "function": {"name": r["fn"]},
                                    "result": r["out"],
                                    "syntax_type": "openai"
                                }]
                            }
                            messages.append(tool_result_entry)
                            
                            if relay_strategy == "tool_role" and call_id:
                                # Use provider adapter to create proper tool result message
                                provider_id = provider_router.parse_model_id(model)[0]
                                adapter = provider_adapter_manager.get_adapter(provider_id)
                                tool_result_msg = adapter.create_tool_result_message(call_id, r["fn"], r["out"])
                                tool_messages_to_relay.append(tool_result_msg)
                            else:
                                provider_messages.append({"role": "user", "content": formatted_output})

                        # If using tool-role relay, append all tool messages and continue loop
                        if tool_messages_to_relay:
                            provider_messages.extend(tool_messages_to_relay)
                            # Do not add any synthetic user messages; let the model continue the assistant turn
                    continue
                except Exception:
                    # On relay error, fall back to legacy behavior (append as user)
                    try:
                        if tool_messages_to_relay:
                            fallback_blob = "\n\n".join([m.get("content", "") for m in tool_messages_to_relay])
                            provider_messages.append({"role": "user", "content": fallback_blob})
                    except Exception:
                        pass
                    continue
            else:
                # normal assistant content
                if msg.content:
                    assistant_entry = {"role": "assistant", "content": msg.content}
                    messages.append(assistant_entry)
                    provider_messages.append(assistant_entry.copy())
                    transcript.append({"assistant": msg.content})
                    # Snapshot after assistant content
                    self._write_snapshot(output_json_path, model, messages, transcript)
                # SoTA COMPLETION DETECTION: Use comprehensive multi-method detection
                completion_analysis = self._detect_completion_sota(
                    msg_content=msg.content or "",
                    choice_finish_reason=getattr(choice, "finish_reason", None),
                    tool_results=[]  # Tool results handled separately above
                )
                
                # Log completion analysis for debugging
                transcript.append({"completion_analysis": completion_analysis})
                
                # Apply confidence threshold for non-tool completions
                confidence_threshold = self.completion_config.get("confidence_threshold", 0.6)
                meets_threshold = (completion_analysis["method"] == "tool_based" or 
                                 completion_analysis["confidence"] >= confidence_threshold)
                
                if completion_analysis["completed"] and meets_threshold:
                    if stream_responses:
                        print(f"[stop] reason={completion_analysis['method']} confidence={completion_analysis['confidence']:.2f} - {completion_analysis['reason']}")
                    # Add completion summary to transcript
                    transcript.append({
                        "completion_detected": {
                            "method": completion_analysis["method"],
                            "confidence": completion_analysis["confidence"],
                            "reason": completion_analysis["reason"],
                            "content_analyzed": bool(msg.content),
                            "threshold_met": meets_threshold
                        }
                    })
                    break
                elif completion_analysis["completed"] and not meets_threshold:
                    # Log low-confidence completion attempts
                    transcript.append({
                        "completion_below_threshold": {
                            "method": completion_analysis["method"],
                            "confidence": completion_analysis["confidence"],
                            "threshold": confidence_threshold,
                            "reason": completion_analysis["reason"]
                        }
                    })
        # Final snapshot
        self._write_snapshot(output_json_path, model, messages, transcript)
        # Emit stop reason for visibility
        if stream_responses:
            print(f"[stop] reason=end-of-loop steps={max_steps}")
        return {"messages": messages, "transcript": transcript}


