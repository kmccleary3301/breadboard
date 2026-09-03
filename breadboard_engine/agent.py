"""
Simplified agentic coder prototype.

This module provides a streamlined interface to the complex agent system,
abstracting away implementation details.
"""
from __future__ import annotations

import copy
import json
import os
import shutil
import logging
import threading
import uuid
from typing import Any, Dict, List, Optional, Tuple, Callable
from pathlib import Path
from .utils.safe_delete import is_disposable_workspace_path, validate_workspace_path


def _admit_standalone_run() -> Dict[str, str]:
    return {
        "session_id": f"run-{uuid.uuid4().hex}",
        "input_id": f"input-{uuid.uuid4().hex}",
        "turn_id": f"turn-{uuid.uuid4().hex}",
    }


_ray = None
_ray_attempted = False


def _get_ray():  # type: ignore[no-untyped-def]
    global _ray_attempted, _ray
    if _ray_attempted:
        return _ray
    _ray_attempted = True
    try:
        import ray as _ray_mod  # type: ignore
    except Exception:  # pragma: no cover - optional runtime
        _ray = None
    else:
        _ray = _ray_mod
    return _ray
from .agent_llm_openai import OpenAIConductor
from .compilation.v2_loader import _config_resolution_base_dirs, load_agent_config
from .provider.routing import provider_router
from .provider.contracts import OpenAICompletionsProviderProfile
from .provider import provider_adapter_manager
from .compilation.tool_yaml_loader import load_yaml_tools
from .compilation.system_prompt_compiler import get_compiler
from .security import (
    ProcessIsolationUnavailable,
    WorkspaceFilesystem,
    WorkspacePathError,
    ChildProcessPolicy,
    protected_credential_paths,
    redaction,
    sanitized_process_environment,
    validate_workspace_credential_boundary,
)


logger = logging.getLogger(__name__)
_REPO_ROOT = Path(__file__).resolve().parents[1]

class _UnsafeTaskSpecPath(RuntimeError):
    """A task-file locator crossed the model credential boundary."""

    def __init__(self) -> None:
        super().__init__("Task specification path is unsafe")


class AgenticCoder:
    """Simplified agentic coder interface."""
    
    def __init__(
        self,
        config_path: str,
        workspace_dir: Optional[str] = None,
        overrides: Optional[Dict[str, Any]] = None,
        *,
        force_local_mode: bool = False,
    ):
        """Initialize the agentic coder with a config file."""
        self.config_path = config_path
        # Load config first so we can honor V2 workspace.root
        self.config = self._load_config()
        if overrides:
            self._apply_overrides(overrides)
        if redaction.contains_provider_auth_runtime(self.config):
            logger.warning(
                "Ignoring inline provider credentials; attach credentials through the provider broker."
            )
            self.config = redaction.strip_provider_auth_runtime(self.config)
        # Prefer v2 workspace.root if provided
        v2_ws_root = None
        try:
            v2_ws_root = (self.config.get("workspace", {}) or {}).get("root")
        except Exception:
            v2_ws_root = None
        self.workspace_dir = workspace_dir or v2_ws_root or f"tmp/agent_ws_{os.path.basename(config_path).split('.')[0]}"
        # Keep config.workspace.root aligned with the effective workspace directory so
        # enhanced tool executors (which read from config) operate in the same root.
        try:
            ws_cfg = self.config.get("workspace")
            if not isinstance(ws_cfg, dict):
                ws_cfg = {}
            ws_cfg["root"] = self.workspace_dir
            self.config["workspace"] = ws_cfg
        except Exception:
            pass
        self.agent = None
        self._local_mode = force_local_mode or os.environ.get("RAY_SCE_LOCAL_MODE", "0") == "1"
        
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

    def _invoke_remote_config_update(
        self, method_name: str, payload: Dict[str, Any]
    ) -> bool:
        try:
            method = getattr(self.agent, method_name)
            reference = method.remote(payload)
            ray_mod = _get_ray()
            if ray_mod is None:
                return False
            return ray_mod.get(reference) is not False
        except Exception:
            return False

    def replace_runtime_config(self, config: Dict[str, Any]) -> bool:
        """Replace the active config exactly, including absent keys."""
        if not isinstance(config, dict):
            return False
        if redaction.contains_provider_auth_runtime(config):
            logger.warning(
                "Rejecting runtime provider credentials; attach credentials through the provider broker."
            )
            return False
        replacement = copy.deepcopy(config)
        self.config = replacement
        if not self.agent:
            return True
        if self._local_mode:
            try:
                if hasattr(self.agent, "replace_config"):
                    return self.agent.replace_config(replacement) is not False
                setattr(self.agent, "config", replacement)
                return True
            except Exception:
                return False
        if not hasattr(self.agent, "replace_config"):
            return False
        return self._invoke_remote_config_update("replace_config", replacement)

    def apply_runtime_overrides(self, overrides: Dict[str, Any]) -> bool:
        """Best-effort update to the active config (local or remote)."""
        if not isinstance(overrides, dict) or not overrides:
            return False
        if redaction.contains_provider_auth_runtime(overrides):
            logger.warning(
                "Rejecting runtime provider credentials; attach credentials through the provider broker."
            )
            return False
        try:
            self._apply_overrides(overrides)
        except Exception:
            pass
        if not self.agent:
            return True
        if self._local_mode:
            try:
                setattr(self.agent, "config", self.config)
                if hasattr(self.agent, "apply_config_overrides"):
                    return self.agent.apply_config_overrides(overrides) is not False
                return True
            except Exception:
                return False
        # Ray actor: wait for the actor to acknowledge the update so callers can
        # safely persist or roll back the corresponding generation change.
        if not hasattr(self.agent, "apply_config_overrides"):
            return False
        return self._invoke_remote_config_update("apply_config_overrides", overrides)

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

    @staticmethod
    def _is_within(base: Path, candidate: Path) -> bool:
        try:
            candidate.relative_to(base)
            return True
        except ValueError:
            return False

    @classmethod
    def _task_path_is_protected(cls, candidate: Path) -> bool:
        lexical = Path(os.path.abspath(candidate))
        try:
            resolved = candidate.resolve(strict=False)
        except OSError:
            resolved = lexical
        for protected_path in protected_credential_paths():
            protected = Path(os.path.abspath(protected_path))
            try:
                resolved_protected = protected.resolve(strict=False)
            except OSError:
                resolved_protected = protected
            if cls._is_within(protected, lexical) or cls._is_within(
                resolved_protected,
                resolved,
            ):
                return True
        return False

    def _read_task_spec(self, task: str) -> tuple[Path, str] | None:
        candidate = Path(task).expanduser()
        if not candidate.is_absolute():
            candidate = Path(os.path.abspath(candidate))
        workspace = Path(os.path.abspath(self.workspace_dir))
        if self._is_within(workspace, candidate):
            relative = candidate.relative_to(workspace)
            try:
                with WorkspaceFilesystem(workspace) as filesystem:
                    validate_workspace_credential_boundary(filesystem.root)
                    if not filesystem.exists(relative):
                        return None
                    if filesystem.stat(relative).kind != "file":
                        return None
                    return candidate, filesystem.read_text(relative)
            except (ProcessIsolationUnavailable, WorkspacePathError) as exc:
                raise _UnsafeTaskSpecPath() from exc
        if self._task_path_is_protected(candidate):
            raise _UnsafeTaskSpecPath()
        try:
            with WorkspaceFilesystem.open_anchored_root(
                candidate.parent
            ) as filesystem:
                if not filesystem.exists(candidate.name):
                    return None
                if filesystem.stat(candidate.name).kind != "file":
                    return None
                return candidate, filesystem.read_text(candidate.name)
        except WorkspacePathError as exc:
            if not candidate.parent.exists():
                return None
            raise _UnsafeTaskSpecPath() from exc

    def _resolve_workspace_path(self) -> Path:
        """Resolve and validate workspace path before any destructive operations.

        Acceptance rails:
        - refuse `/`, `$HOME`, and tmp roots.
        - allow preserved repo roots and git-backed project workspaces.

        Disposal rails are handled separately; only repo-root/tmp descendants are disposable.
        """
        workspace_path = Path(self.workspace_dir).expanduser()
        if not workspace_path.is_absolute():
            if workspace_path.parts[:1] == ("tmp",):
                workspace_path = _REPO_ROOT / workspace_path
            else:
                workspace_path = _REPO_ROOT / "tmp" / workspace_path
        return validate_workspace_path(workspace_path, repo_root=_REPO_ROOT)

    def initialize(self) -> None:
        """Initialize the agent with the loaded configuration."""
        workspace_path = self._resolve_workspace_path()
        self.workspace_dir = str(workspace_path)
        try:
            ws_cfg = self.config.get("workspace")
            if not isinstance(ws_cfg, dict):
                ws_cfg = {}
            ws_cfg["root"] = self.workspace_dir
            self.config["workspace"] = ws_cfg
        except Exception:
            pass
        preserve_seeded = os.environ.get("PRESERVE_SEEDED_WORKSPACE") in {"1", "true", "True"}
        disposable_workspace = is_disposable_workspace_path(workspace_path, repo_root=_REPO_ROOT)
        if not disposable_workspace:
            os.environ.setdefault("PRESERVE_SEEDED_WORKSPACE", "1")
            preserve_seeded = True
        if workspace_path.exists() and disposable_workspace and not preserve_seeded:
            # Ensure each run starts from a clean clone workspace
            shutil.rmtree(workspace_path)
        workspace_path.mkdir(parents=True, exist_ok=True)
        protected_paths = tuple(
            str(path) for path in protected_credential_paths()
        )
        
        # Initialize Ray and underlying actor
        if not self._local_mode:
            ray = _get_ray()
            if ray is None:
                raise RuntimeError(
                    "Remote execution requested, but Ray is unavailable. "
                    "Select local mode explicitly to run in-process."
                )
            if not ray.is_initialized():
                if threading.current_thread() is not threading.main_thread():
                    raise RuntimeError(
                        "Remote execution requires Ray initialization on the main thread. "
                        "Select local mode explicitly to run in-process."
                    )
                try:
                    with sanitized_process_environment():
                        ray.init(address="local", include_dashboard=False)
                except BaseException as exc:
                    raise RuntimeError(
                        "Remote execution initialization failed "
                        f"({exc.__class__.__name__}); local execution was not selected."
                    ) from None

        if self._local_mode:
            print("[Ray disabled] Using local in-process execution mode.")
            conductor_cls = OpenAIConductor.__ray_metadata__.modified_class
            self.agent = conductor_cls(
                workspace=self.workspace_dir,
                config=self.config,
                local_mode=True,
                prompt_base_dirs=list(_config_resolution_base_dirs(self.config_path)),
                protected_paths=protected_paths,
            )
        else:
            runtime_env = {
                "env_vars": ChildProcessPolicy().environment_only().as_dict()
            }
            self.agent = OpenAIConductor.options(runtime_env=runtime_env).remote(
                workspace=self.workspace_dir,
                config=self.config,
                prompt_base_dirs=list(_config_resolution_base_dirs(self.config_path)),
                protected_paths=protected_paths,
            )
    
    def run_task(
        self,
        task: str,
        max_iterations: Optional[int] = None,
        *,
        stream: bool = False,
        event_emitter: Optional[Callable[[str, Dict[str, Any], Optional[int]], None]] = None,
        event_queue: Optional[Any] = None,
        permission_queue: Optional[Any] = None,
        control_queue: Optional[Any] = None,
        replay_session: Optional[str] = None,
        parity_guardrails: Optional[str] = None,
        context: Optional[Dict[str, Any]] = None,
        kernel_emitter_run_dir: Optional[str] = None,
        kernel_emitter_mode: Optional[str] = None,
        provider_profile: Optional[OpenAICompletionsProviderProfile] = None,
    ) -> Dict[str, Any]:
        """Run a single task and return results."""
        if replay_session and self.agent is not None:
            raise RuntimeError("Replay/parity options must be set before agent initialization.")

        if replay_session:
            replay_cfg = self.config.setdefault("replay", {})
            replay_cfg["session_path"] = str(Path(replay_session).resolve())
            if parity_guardrails:
                try:
                    expected = json.loads(Path(parity_guardrails).read_text(encoding="utf-8"))
                except Exception:
                    expected = None
                allowlist: List[str] = []
                if isinstance(expected, list):
                    replay_cfg["guardrail_expected"] = expected
                    for entry in expected:
                        if isinstance(entry, dict) and entry.get("type"):
                            allowlist.append(str(entry["type"]))
                replay_cfg["guardrail_allowlist"] = sorted(set(allowlist))
        elif "replay" in self.config:
            self.config.pop("replay", None)
        is_replay = bool(replay_session)

        if not self.agent:
            self.initialize()
        if context is None:
            context = _admit_standalone_run()

        model = self._select_model()
        loop_cfg = self.config.get('loop') or {}
        steps = int(
            max_iterations
            or self.config.get('max_iterations')
            or loop_cfg.get('max_iterations')
            or loop_cfg.get('max_steps')
            or 12
        )
        tool_prompt_mode = self._resolve_tool_prompt_mode() or "system_once"
        # Existing files are accepted as task specifications, but every read is
        # descriptor-anchored and protected credential paths are never inputs.
        user_prompt = task
        task_seed: Optional[Tuple[str, str]] = None
        try:
            task_spec = self._read_task_spec(task)
            if task_spec is not None:
                source_path, user_prompt = task_spec
                if not is_replay:
                    task_seed = (source_path.name, user_prompt)
                    self._materialize_task_spec(source_path, user_prompt)
        except _UnsafeTaskSpecPath:
            raise
        except Exception:
            pass
        if task_seed and not is_replay:
            self._seed_agent_workspace_file(task_seed[0], task_seed[1])
        # Run empty system prompt to allow v2 compiler to inject packs; user prompt carries content
        effective_stream = bool(stream)
        effective_emitter = event_emitter if self._local_mode else None
        if event_emitter and not self._local_mode:
            logger.warning(
                "Streaming event emitters are currently only supported in local mode; "
                "falling back to queue-based streaming."
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
                permission_queue=permission_queue,
                control_queue=control_queue,
                kernel_emitter_run_dir=kernel_emitter_run_dir,
                kernel_emitter_mode=kernel_emitter_mode,
                context=context,
                provider_profile=provider_profile,
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
            permission_queue=permission_queue,
            control_queue=control_queue,
            kernel_emitter_run_dir=kernel_emitter_run_dir,
            kernel_emitter_mode=kernel_emitter_mode,
            context=context,
            provider_profile=provider_profile,
        )
        ray_mod = _get_ray()
        if ray_mod is None:
            raise RuntimeError("Ray is unavailable for remote execution.")
        return ray_mod.get(ref)
    
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
                        context=_admit_standalone_run(),
                    )
                else:
                    ref = self.agent.run_agentic_loop.remote(
                        "",
                        user_input,
                        model,
                        max_steps=5,
                        tool_prompt_mode=tool_prompt_mode,
                        context=_admit_standalone_run(),
                    )
                    ray_mod = _get_ray()
                    if ray_mod is None:
                        raise RuntimeError("Ray is unavailable for remote execution.")
                    result = ray_mod.get(ref)
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
        lock = self.config.get("model_role_lock") if isinstance(self.config, dict) else None
        if isinstance(lock, dict):
            role = str(self.config.get("active_model_role") or (lock.get("defaults") or {}).get("role") or "")
            binding = (lock.get("roles") or {}).get(role)
            target = binding.get("primary") if isinstance(binding, dict) else None
            if isinstance(target, dict) and target.get("route_id"):
                return str(target["route_id"])
            if isinstance(target, dict) and target.get("provider_id") and target.get("model_id"):
                return f"{target['provider_id']}/{target['model_id']}"
            raise RuntimeError("active model role has no exact target")
        try:
            providers = self.config.get("providers", {})
            default_model = providers.get("default_model")
            if default_model:
                return str(default_model)
        except Exception:
            pass
        return str(self.config.get("model", "gpt-4o-mini"))
    def _materialize_task_spec(self, source_path: Path, contents: str) -> None:
        """
        Copy the task specification into the workspace so shell/list/read guards have local context.
        """
        try:
            targets: List[Path] = []
            workspace_path = Path(self.workspace_dir)
            targets.append(workspace_path)
            try:
                cfg_ws = (self.config.get("workspace", {}) or {}).get("root")
                if cfg_ws:
                    cfg_path = Path(cfg_ws)
                    if cfg_path not in targets:
                        targets.append(cfg_path)
            except Exception:
                pass
            for target in targets:
                with WorkspaceFilesystem.open_anchored_root(
                    target,
                    create=True,
                ) as filesystem:
                    validate_workspace_credential_boundary(filesystem.root)
                    filesystem.write_text(source_path.name, contents)
        except Exception:
            # Best-effort only
            pass

    def _seed_agent_workspace_file(self, filename: str, contents: str) -> None:
        """Ensure the conductor's workspace also receives the task specification."""
        if not filename or contents is None:
            return
        try:
            if self._local_mode:
                self.agent.seed_workspace_file.remote(filename, contents)
            else:
                self._ray_get(self.agent.seed_workspace_file.remote(filename, contents))
        except Exception:
            pass


def create_agent(
    config_path: str,
    workspace_dir: Optional[str] = None,
    overrides: Optional[Dict[str, Any]] = None,
    *,
    force_local_mode: bool = False,
) -> AgenticCoder:
    """Convenient factory function to create an agentic coder."""
    return AgenticCoder(
        config_path,
        workspace_dir,
        overrides=overrides,
        force_local_mode=force_local_mode,
    )
