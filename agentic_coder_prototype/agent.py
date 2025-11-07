"""
Simplified agentic coder prototype.

This module provides a streamlined interface to the complex agent system,
abstracting away implementation details.
"""
from __future__ import annotations

import json
import os
import shutil
import logging
from typing import Any, Dict, List, Optional, Tuple, Callable
from pathlib import Path

import ray
from .agent_llm_openai import OpenAIConductor
from .compilation.v2_loader import load_agent_config
from .provider_routing import provider_router
from .provider_adapters import provider_adapter_manager
from .compilation.tool_yaml_loader import load_yaml_tools
from .compilation.system_prompt_compiler import get_compiler


logger = logging.getLogger(__name__)


class AgenticCoder:
    """Simplified agentic coder interface."""
    
    def __init__(self, config_path: str, workspace_dir: Optional[str] = None, overrides: Optional[Dict[str, Any]] = None):
        """Initialize the agentic coder with a config file."""
        self.config_path = config_path
        # Load config first so we can honor V2 workspace.root
        self.config = self._load_config()
        if overrides:
            self._apply_overrides(overrides)
        # Prefer v2 workspace.root if provided
        v2_ws_root = None
        try:
            v2_ws_root = (self.config.get("workspace", {}) or {}).get("root")
        except Exception:
            v2_ws_root = None
        self.workspace_dir = workspace_dir or v2_ws_root or f"agent_ws_{os.path.basename(config_path).split('.')[0]}"
        self.agent = None
        self._local_mode = os.environ.get("RAY_SCE_LOCAL_MODE", "0") == "1"
        
    def _load_config(self) -> Dict[str, Any]:
        """Load and validate configuration (v2-aware)."""
        try:
            return load_agent_config(self.config_path)
        except Exception:
            # Fallback to legacy loader for resilience
            with open(self.config_path, 'r') as f:
                return json.load(f) if self.config_path.endswith('.json') else __import__('yaml').safe_load(f)

    def _apply_overrides(self, overrides: Dict[str, Any]) -> None:
        for dotted_path, value in overrides.items():
            try:
                tokens = self._tokenize_path(dotted_path)
                self._set_nested_value(self.config, tokens, value)
            except Exception:
                continue

    @staticmethod
    def _tokenize_path(path: str) -> List[Any]:
        tokens: List[Any] = []
        parts = path.split('.')
        for part in parts:
            cursor = part
            while cursor:
                if '[' in cursor:
                    name, rest = cursor.split('[', 1)
                    if name:
                        tokens.append(name)
                    idx_str, _, remainder = rest.partition(']')
                    if idx_str.isdigit():
                        tokens.append(int(idx_str))
                    cursor = remainder.lstrip('.') if remainder.startswith('.') else remainder
                else:
                    tokens.append(cursor)
                    cursor = ''
        return tokens

    @staticmethod
    def _set_nested_value(config: Any, tokens: List[Any], value: Any) -> None:
        current = config
        parent_stack: List[Tuple[Any, Any]] = []
        for idx, token in enumerate(tokens):
            is_last = idx == len(tokens) - 1
            if isinstance(token, str):
                if not isinstance(current, dict):
                    if parent_stack:
                        parent, parent_token = parent_stack[-1]
                        replacement = {}
                        if isinstance(parent, dict):
                            parent[parent_token] = replacement
                        elif isinstance(parent, list) and isinstance(parent_token, int):
                            parent[parent_token] = replacement
                        current = replacement
                    else:
                        raise ValueError
                if is_last:
                    current[token] = value
                    return
                next_token = tokens[idx + 1]
                if token not in current or current[token] is None:
                    current[token] = [] if isinstance(next_token, int) else {}
                parent_stack.append((current, token))
                current = current[token]
            else:  # token is int
                if not isinstance(current, list):
                    replacement_list: List[Any] = []
                    if parent_stack:
                        parent, parent_token = parent_stack[-1]
                        if isinstance(parent, dict):
                            parent[parent_token] = replacement_list
                        elif isinstance(parent, list) and isinstance(parent_token, int):
                            parent[parent_token] = replacement_list
                    current = replacement_list
                while len(current) <= token:
                    next_token = tokens[idx + 1] if not is_last else None
                    current.append([] if isinstance(next_token, int) else {})
                if is_last:
                    current[token] = value
                    return
                parent_stack.append((current, token))
                current = current[token]

    def _resolve_tool_prompt_mode(self) -> Optional[str]:
        """Resolve desired tool prompt mode from configuration."""
        cfg = self.config or {}
        try:
            prompts_cfg = (cfg.get("prompts") or {})
            mode = prompts_cfg.get("tool_prompt_mode")
            if mode:
                return str(mode)
        except Exception:
            pass
        try:
            legacy_prompt_cfg = (cfg.get("prompt") or {})
            mode = legacy_prompt_cfg.get("mode")
            if mode:
                return str(mode)
        except Exception:
            pass
        return None

    def initialize(self) -> None:
        """Initialize the agent with the loaded configuration."""
        workspace_path = Path(self.workspace_dir)
        if workspace_path.exists():
            # Ensure each run starts from a clean clone workspace
            shutil.rmtree(workspace_path)
        workspace_path.mkdir(parents=True, exist_ok=True)
        
        # Initialize Ray and underlying actor
        if not self._local_mode:
            try:
                if not ray.is_initialized():
                    # Start an isolated local cluster with a nonstandard dashboard port
                    ray.init(address="local", include_dashboard=False)
            except Exception:
                self._local_mode = True

        if self._local_mode:
            print("[Ray disabled] Using local in-process execution mode.")
            conductor_cls = OpenAIConductor.__ray_metadata__.modified_class
            self.agent = conductor_cls(
                workspace=self.workspace_dir,
                config=self.config,
                local_mode=True,
            )
        else:
            self.agent = OpenAIConductor.remote(
                workspace=self.workspace_dir,
                config=self.config,
            )
    
    def run_task(
        self,
        task: str,
        max_iterations: Optional[int] = None,
        *,
        stream: bool = False,
        event_emitter: Optional[Callable[[str, Dict[str, Any], Optional[int]], None]] = None,
        event_queue: Optional[Any] = None,
    ) -> Dict[str, Any]:
        """Run a single task and return results."""
        if not self.agent:
            self.initialize()

        model = self._select_model()
        steps = int(max_iterations or self.config.get('max_iterations', 12))
        tool_prompt_mode = self._resolve_tool_prompt_mode() or "system_once"
        # If task is a file path, read it as the user prompt content; else use as-is
        user_prompt = task
        try:
            p = Path(task)
            if p.exists() and p.is_file():
                user_prompt = p.read_text(encoding="utf-8", errors="replace")
        except Exception:
            pass
        # Run empty system prompt to allow v2 compiler to inject packs; user prompt carries content
        effective_stream = bool(stream) and self._local_mode
        effective_emitter = event_emitter if self._local_mode else None
        if event_emitter and not self._local_mode:
            logger.warning(
                "Streaming event emitters are currently only supported in local mode; "
                "falling back to summary events."
            )
        if self._local_mode:
            return self.agent.run_agentic_loop(
                "",
                user_prompt,
                model,
                max_steps=steps,
                output_json_path=None,
                stream_responses=effective_stream,
                output_md_path=None,
                tool_prompt_mode=tool_prompt_mode,
                event_emitter=effective_emitter,
                event_queue=event_queue,
            )

        ref = self.agent.run_agentic_loop.remote(
            "",
            user_prompt,
            model,
            max_steps=steps,
            output_json_path=None,
            stream_responses=effective_stream,
            output_md_path=None,
            tool_prompt_mode=tool_prompt_mode,
            event_emitter=effective_emitter,
            event_queue=event_queue,
        )
        return ray.get(ref)
    
    def interactive_session(self) -> None:
        """Start an interactive session with the agent."""
        if not self.agent:
            self.initialize()
        
        print(f"Starting interactive session in {self.workspace_dir}")
        print("Type 'exit' to quit")
        
        while True:
            try:
                user_input = input("\n> ")
                if user_input.lower() in ['exit', 'quit']:
                    break
                
                model = self._select_model()
                tool_prompt_mode = self._resolve_tool_prompt_mode() or "system_once"
                if self._local_mode:
                    result = self.agent.run_agentic_loop(
                        "",
                        user_input,
                        model,
                        max_steps=5,
                        tool_prompt_mode=tool_prompt_mode,
                    )
                else:
                    ref = self.agent.run_agentic_loop.remote(
                        "",
                        user_input,
                        model,
                        max_steps=5,
                        tool_prompt_mode=tool_prompt_mode,
                    )
                    result = ray.get(ref)
                print(f"Agent completed with status: {result.get('completion_reason', 'unknown')}")
                
            except KeyboardInterrupt:
                print("\nSession interrupted by user")
                break
            except Exception as e:
                print(f"Error: {e}")
    
    def get_workspace_files(self) -> List[str]:
        """Get list of files in the agent workspace."""
        if not Path(self.workspace_dir).exists():
            return []
        
        files = []
        for root, _, filenames in os.walk(self.workspace_dir):
            for filename in filenames:
                files.append(os.path.relpath(os.path.join(root, filename), self.workspace_dir))
        return files

    def _select_model(self) -> str:
        try:
            providers = self.config.get("providers", {})
            default_model = providers.get("default_model")
            if default_model:
                return str(default_model)
        except Exception:
            pass
        # Legacy fallback
        return str(self.config.get("model", "gpt-4o-mini"))


def create_agent(config_path: str, workspace_dir: Optional[str] = None, overrides: Optional[Dict[str, Any]] = None) -> AgenticCoder:
    """Convenient factory function to create an agentic coder."""
    return AgenticCoder(config_path, workspace_dir, overrides=overrides)
