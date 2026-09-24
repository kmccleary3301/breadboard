"""Native Hermes tool execution for the bounded BreadBoard profile.

The runtime delegates tool behavior and process execution to the pinned source
modules. Its process-side adapter wraps the source drain helper for only the
owned ``LocalEnvironment`` processes, counting raw bytes before UTF-8 decoding
and preserving the admitted source output prefix on overflow.
"""

from __future__ import annotations

import copy
import hashlib
import importlib
import json
import math
import os
import stat
import time
from pathlib import Path
from collections.abc import Callable, Mapping, Sequence
from functools import wraps
from types import SimpleNamespace
from typing import Any


TOOL_NAMES: tuple[str, ...] = (
    "patch",
    "read_file",
    "search_files",
    "skill_view",
    "skills_list",
    "terminal",
    "write_file",
)
MAX_RAW_COMMAND_BYTES = 1 * 1024 * 1024
MAX_OBSERVATION_BYTES = 16 * 1024 * 1024
NATIVE_TOOL_SECONDS = 35.0
TERMINAL_SECONDS = 30.0

# These are source patch parser targets; the ordinary ``path`` argument is not
# a substitute for checking every parsed operation endpoint.
_HIDDEN_CAPABILITIES = frozenset(
    {
        "cross_profile",
        "background",
        "pty",
        "remote",
        "elevation",
        "elevated",
        "sudo",
        "use_sudo",
        "remote_backend",
    }
)
_TERMINAL_SCHEMA_HIDDEN = frozenset(
    {
        "background",
        "pty",
        "notify",
        "notify_on_complete",
        "watch_patterns",
        "force",
        "_host_local",
        "session_id",
        "task_id",
    }
)


class HermesToolRuntimeError(RuntimeError):
    """A native runtime containment, snapshot, or resource-evidence failure."""


_MISSING = object()


class _RawCommandOverflow(Exception):
    def __init__(self, prefix: str) -> None:
        super().__init__("native raw output byte limit exceeded")
        self.prefix = prefix


class _RawByteBudgetDecoder:
    """Count bytes handed to the source POSIX drain before decoding."""

    def __init__(
        self, decoder: Any, *, limit: int, on_overflow: Callable[[int], None]
    ) -> None:
        self._decoder = decoder
        self._limit = limit
        self._count = 0
        self._on_overflow = on_overflow
        self._overflowed = False

    def decode(self, data: bytes, final: bool = False) -> str:
        if self._overflowed:
            return self._decoder.decode(b"", final=True) if final else ""
        remaining = self._limit - self._count
        if len(data) > remaining:
            admitted = data[: max(0, remaining)]
            self._count += len(data)
            self._overflowed = True
            prefix = self._decoder.decode(admitted, final=False) if admitted else ""
            self._on_overflow(self._count)
            raise _RawCommandOverflow(prefix)
        self._count += len(data)
        return self._decoder.decode(data, final=final)


def _source(name: str) -> Any:
    """Import a pinned source module lazily, after the native bootstrap."""
    return importlib.import_module(name)


def _canonical_root(path: Path) -> Path:
    try:
        return path.expanduser().resolve(strict=False)
    except (OSError, RuntimeError) as exc:
        raise HermesToolRuntimeError(f"cannot resolve runtime root {path!s}") from exc


def _is_under(path: Path, roots: Sequence[Path]) -> bool:
    for root in roots:
        try:
            path.relative_to(root)
            return True
        except ValueError:
            continue
    return False


def _deep_copy(value: Any) -> Any:
    try:
        return copy.deepcopy(value)
    except Exception:
        if isinstance(value, Mapping):
            return {str(k): _deep_copy(v) for k, v in value.items()}
        if isinstance(value, list):
            return [_deep_copy(v) for v in value]
        if isinstance(value, tuple):
            return [_deep_copy(v) for v in value]
        return value


def _json_size(value: Any) -> int:
    """Bound an observation without turning source objects into replacement rows."""
    try:
        return len(
            json.dumps(
                value, ensure_ascii=False, separators=(",", ":"), default=str
            ).encode("utf-8")
        )
    except (TypeError, ValueError, UnicodeError) as exc:
        raise HermesToolRuntimeError("native observation is not bounded JSON") from exc


def _tool_name(schema: Mapping[str, Any]) -> str | None:
    function = schema.get("function")
    if isinstance(function, Mapping):
        name = function.get("name")
        return name if isinstance(name, str) else None
    name = schema.get("name")
    return name if isinstance(name, str) else None


def _call_fields(call: Any) -> tuple[str | None, Any]:
    """Use the native ToolCall function property (it is present in Hermes source)."""
    function = getattr(call, "function", None)
    return getattr(function, "name", None), getattr(function, "arguments", "{}")


def _parse_arguments(raw: Any) -> Mapping[str, Any] | None:
    if isinstance(raw, Mapping):
        return raw
    if not isinstance(raw, str):
        return None
    try:
        value = json.loads(raw)
    except (TypeError, ValueError):
        return None
    return value if isinstance(value, Mapping) else None


def _raw_path(value: Any) -> str | None:
    return value if isinstance(value, str) and value.strip() else None


class HermesToolRuntime:
    """Bounded adapter around one real Hermes ``LocalEnvironment`` and source state.

    ``state`` is initialized by the pinned source. The runtime owns one real
    LocalEnvironment, dispatch guards and segment execution; the source registry,
    session cwd and tool implementations remain authoritative.
    """

    def __init__(
        self,
        state: Any,
        *,
        workspace: Path,
        scratch: Path,
        hermes_home: Path,
        remaining: Callable[[], float],
    ) -> None:
        self.state = state
        self.workspace = _canonical_root(Path(workspace))
        self.scratch = _canonical_root(Path(scratch))
        self.hermes_home = _canonical_root(Path(hermes_home))
        self._remaining = remaining
        self._environment: Any | None = None
        self._task_id: str | None = None
        self._owns_registry_entry = False
        self._initialized = False
        self._closed = False
        self._env_execute_original: Any = _MISSING
        self._env_execute_guard: Any = None
        self._env_run_bash_original: Any = _MISSING
        self._env_run_bash_guard: Any = None
        self._drain_helper_original: Any = _MISSING
        self._drain_helper_guard: Any = None
        self._capture_processes: dict[int, Any] = {}
        self._model_handle_original: Any = _MISSING
        self._deadline_resolve_original: Any = _MISSING
        self._authority_failed = False
        self._hard_stop = False
        self._raw_overflow_bytes = 0
        self._resource_facts: dict[str, Any] | None = None

    @property
    def source_env(self) -> Any:
        if self._environment is None:
            raise HermesToolRuntimeError("native LocalEnvironment is not initialized")
        return self._environment

    @property
    def cwd(self) -> Path:
        """The actual current cwd maintained by source LocalEnvironment."""
        env = self.source_env
        value = getattr(env, "cwd", None)
        if not isinstance(value, str) or not value:
            raise HermesToolRuntimeError("native environment did not expose a cwd")
        path = self._checked_path(value, allow_empty=False)
        if not path.is_dir():
            raise HermesToolRuntimeError(
                f"native environment cwd is not a directory: {path}"
            )
        return path

    def _remaining_seconds(self) -> float:
        try:
            value = float(self._remaining())
        except (TypeError, ValueError) as exc:
            raise HermesToolRuntimeError("remaining deadline is not numeric") from exc
        return value

    def _registry_environment(self, task_id: str) -> Any | None:
        try:
            lifecycle = _source("tools.terminal_tool_lifecycle")
            return lifecycle.get_active_env(task_id)
        except Exception as exc:
            raise HermesToolRuntimeError(
                "native source environment registry is unavailable"
            ) from exc

    def _register_environment(self, task_id: str, environment: Any) -> None:
        try:
            terminal = _source("tools.terminal_tool")
            with terminal._env_lock:
                existing = terminal._active_environments.get(task_id)
                if existing is not None and existing is not environment:
                    raise HermesToolRuntimeError(
                        "native task registry already owns another environment"
                    )
                terminal._active_environments[task_id] = environment
                terminal._last_activity[task_id] = time.time()
            self._owns_registry_entry = existing is None
        except HermesToolRuntimeError:
            raise
        except Exception as exc:
            raise HermesToolRuntimeError(
                "cannot register native LocalEnvironment"
            ) from exc

    def _locate_environment(self) -> tuple[Any, str]:
        candidates: list[Any] = []
        for attr in ("environment", "_terminal_env", "terminal_env", "_environment"):
            value = getattr(self.state, attr, None)
            if value is not None and all(value is not item for item in candidates):
                candidates.append(value)
        session_id = getattr(self.state, "session_id", None)
        task_id = getattr(self.state, "task_id", None) or session_id
        if not isinstance(task_id, str) or not task_id:
            raise HermesToolRuntimeError("native state has no admitted session/task id")
        registered = self._registry_environment(task_id)
        if registered is not None:
            candidates.append(registered)
        local_type = getattr(
            _source("tools.environments.local"), "LocalEnvironment", None
        )
        if local_type is None:
            raise HermesToolRuntimeError("pinned LocalEnvironment type is unavailable")
        environment = next(
            (item for item in candidates if isinstance(item, local_type)), None
        )
        if registered is not None and environment is not registered:
            raise HermesToolRuntimeError(
                "state environment is not the registered native environment"
            )
        if environment is None:
            try:
                # Bind the owned run/execute and drain guards before the real
                # constructor invokes init_session(). Snapshot output must use
                # the same source capture boundary as tool commands.
                environment = local_type.__new__(local_type)
                self._environment = environment
                self._task_id = task_id
                self._install_environment_guard(environment)
                local_type.__init__(
                    environment,
                    cwd=str(self.workspace),
                    timeout=int(NATIVE_TOOL_SECONDS),
                )
            except Exception as exc:
                raise HermesToolRuntimeError(
                    "native LocalEnvironment initialization failed"
                ) from exc
            self._register_environment(task_id, environment)
        elif registered is None:
            self._register_environment(task_id, environment)
        if not isinstance(environment, local_type):
            raise HermesToolRuntimeError(
                "active native environment is not LocalEnvironment"
            )
        self.state.environment = environment
        self.state._terminal_env = environment
        return environment, task_id

    def _checked_path(self, raw: str | Path, *, allow_empty: bool = True) -> Path:
        if isinstance(raw, Path):
            candidate = raw
        elif isinstance(raw, str):
            if not raw and allow_empty:
                candidate = self.cwd
            else:
                candidate = Path(os.path.expanduser(raw))
        else:
            raise HermesToolRuntimeError("native path is not text")
        if not candidate.is_absolute():
            candidate = self.cwd / candidate
        try:
            resolved = candidate.resolve(strict=False)
        except (OSError, RuntimeError) as exc:
            raise HermesToolRuntimeError(f"cannot resolve native path {raw!r}") from exc
        roots = (self.workspace, self.scratch)
        if not _is_under(resolved, roots):
            raise HermesToolRuntimeError(
                f"native path escapes workspace/scratch: {raw!r}"
            )
        return resolved

    def _check_post_cwd(self, result: Mapping[str, Any] | None = None) -> None:
        """Check the environment's post-command cwd, not a caller-provided decoy."""
        env_cwd = getattr(self.source_env, "cwd", None)
        if not isinstance(env_cwd, str) or not env_cwd:
            raise HermesToolRuntimeError("native environment lost its cwd")
        actual = self._checked_path(env_cwd, allow_empty=False)
        if not actual.is_dir():
            raise HermesToolRuntimeError(
                f"native post-command cwd is not a directory: {actual}"
            )
        if isinstance(result, Mapping):
            reported = result.get("cwd")
            if isinstance(reported, str) and reported:
                reported_path = self._checked_path(reported, allow_empty=False)
                if reported_path != actual:
                    raise HermesToolRuntimeError(
                        "native cwd report disagrees with LocalEnvironment cwd"
                    )

    def _mark_raw_overflow(self, process: Any, byte_count: int) -> None:
        if not self._hard_stop:
            self._hard_stop = True
            self._raw_overflow_bytes = max(self._raw_overflow_bytes, byte_count)
            self.state._interrupt_requested = True
        try:
            self.source_env._kill_process(process)
        except BaseException:
            # The source wait path still owns timeout/cleanup reporting. The
            # hard-stop flag prevents another native action either way.
            pass

    def _install_environment_guard(self, environment: Any | None = None) -> None:
        if self._env_run_bash_guard is not None:
            return
        env = self.source_env if environment is None else environment
        drain_module = _source("tools.environments.base_output")
        original_drain = drain_module._drain_fd_select
        runtime = self

        def guarded_drain(
            proc: Any, fd: int, output: Any, decoder: Any, stop: Any = None
        ) -> Any:
            if runtime._capture_processes.get(id(proc)) is not proc:
                return original_drain(proc, fd, output, decoder, stop)
            bounded = _RawByteBudgetDecoder(
                decoder,
                limit=MAX_RAW_COMMAND_BYTES,
                on_overflow=lambda count: runtime._mark_raw_overflow(proc, count),
            )
            try:
                return original_drain(proc, fd, output, bounded, stop)
            except _RawCommandOverflow as exc:
                if exc.prefix:
                    output.append(exc.prefix)
                raise

        self._drain_helper_original = original_drain
        self._drain_helper_guard = guarded_drain
        drain_module._drain_fd_select = guarded_drain

        if "_run_bash" in getattr(env, "__dict__", {}):
            self._env_run_bash_original = env.__dict__["_run_bash"]
        original_run_bash = env._run_bash
        runtime = self

        def guarded_run_bash(*args: Any, **kwargs: Any) -> Any:
            if runtime._hard_stop:
                raise HermesToolRuntimeError("native raw output hard-stop is active")
            process = original_run_bash(*args, **kwargs)
            runtime._capture_processes[id(process)] = process
            return process

        guarded_run_bash.__name__ = "_run_bash"
        guarded_run_bash.__qualname__ = f"{type(env).__name__}._run_bash"
        self._env_run_bash_guard = guarded_run_bash
        setattr(env, "_run_bash", guarded_run_bash)

        if "execute" in getattr(env, "__dict__", {}):
            self._env_execute_original = env.__dict__["execute"]
        original = env.execute

        def guarded_execute(
            command: str,
            cwd: str = "",
            *,
            timeout: int | float | None = None,
            stdin_data: str | None = None,
            rewrite_compound_background: bool = True,
            bounded_capture: bool = False,
            yield_handler: Any = None,
        ) -> Any:
            if runtime._hard_stop:
                raise HermesToolRuntimeError("native raw output hard-stop is active")
            if (
                isinstance(timeout, (int, float))
                and not isinstance(timeout, bool)
                and timeout > 0
            ):
                remaining = runtime._remaining_seconds()
                if remaining <= 0:
                    raise HermesToolRuntimeError("native action deadline expired")
                timeout = min(float(timeout), TERMINAL_SECONDS, remaining)
            elif timeout is None:
                remaining = runtime._remaining_seconds()
                if remaining <= 0:
                    raise HermesToolRuntimeError("native action deadline expired")
                timeout = min(TERMINAL_SECONDS, remaining)
            result = original(
                command,
                cwd=cwd,
                timeout=timeout,
                stdin_data=stdin_data,
                rewrite_compound_background=rewrite_compound_background,
                bounded_capture=bounded_capture,
                yield_handler=yield_handler,
            )
            runtime._check_post_cwd(result)
            return result

        guarded_execute.__name__ = "execute"
        guarded_execute.__qualname__ = f"{type(env).__name__}.execute"
        self._env_execute_guard = guarded_execute
        setattr(env, "execute", guarded_execute)

    def _bounded_tool_schemas(self) -> list[dict[str, Any]]:
        source_tools = getattr(self.state, "tools", None)
        if not isinstance(source_tools, Sequence) or isinstance(
            source_tools, (str, bytes, bytearray)
        ):
            raise HermesToolRuntimeError(
                "native state did not provide source tool schemas"
            )
        by_name: dict[str, Mapping[str, Any]] = {}
        for schema in source_tools:
            if not isinstance(schema, Mapping):
                raise HermesToolRuntimeError("native tool schema is not an object")
            name = _tool_name(schema)
            if name not in TOOL_NAMES:
                raise HermesToolRuntimeError(f"unexpected native tool schema: {name!r}")
            if name in by_name:
                raise HermesToolRuntimeError(f"duplicate native tool schema: {name}")
            by_name[name] = schema
        if set(by_name) != set(TOOL_NAMES):
            missing = [name for name in TOOL_NAMES if name not in by_name]
            raise HermesToolRuntimeError(
                f"native tool schema surface mismatch: {missing!r}"
            )
        bounded: list[dict[str, Any]] = []
        for name in TOOL_NAMES:
            schema = _deep_copy(by_name[name])
            if not isinstance(schema, dict):
                raise HermesToolRuntimeError(
                    f"native tool schema cannot be copied: {name}"
                )
            bounded.append(schema)
        return bounded

    def initialize(self) -> dict[str, Any]:
        if self._closed:
            raise HermesToolRuntimeError("native tool runtime is closed")
        if self._initialized:
            raise HermesToolRuntimeError("native tool runtime is already initialized")
        environment, task_id = self._locate_environment()
        self._environment, self._task_id = environment, task_id
        if getattr(environment, "_snapshot_ready", False) is not True:
            raise HermesToolRuntimeError(
                "native LocalEnvironment snapshot initialization did not succeed"
            )
        self._check_post_cwd()
        schemas = self._bounded_tool_schemas()
        self.state.tools = schemas
        self._install_environment_guard()
        self._install_dispatch_guards()
        self._initialized = True
        env = self.source_env
        return {
            "tool_schemas": schemas,
            "source_runtime": {
                "environment": f"{type(env).__module__}.{type(env).__qualname__}",
                "session_id": getattr(env, "_session_id", None),
                "task_id": task_id,
                "cwd": str(self.cwd),
                "snapshot_ready": bool(getattr(env, "_snapshot_ready", False)),
                "snapshot_path": getattr(env, "_snapshot_path", None),
                "cwd_file": getattr(env, "_cwd_file", None),
                "workspace": str(self.workspace),
                "scratch": str(self.scratch),
                "hermes_home": str(self.hermes_home),
                "native_tool_timeout_seconds": NATIVE_TOOL_SECONDS,
                "terminal_timeout_seconds": TERMINAL_SECONDS,
                "raw_command_output_bytes": MAX_RAW_COMMAND_BYTES,
                "observation_bytes": MAX_OBSERVATION_BYTES,
            },
        }

    def _path_targets(self, name: str, args: Mapping[str, Any]) -> list[str]:
        paths: list[str] = []
        if name in {"read_file", "search_files", "write_file"}:
            value = _raw_path(args.get("path"))
            if value is not None:
                paths.append(value)
        if name == "terminal":
            value = _raw_path(args.get("workdir"))
            if value is not None:
                paths.append(value)
        if name == "patch":
            value = _raw_path(args.get("path"))
            if value is not None:
                paths.append(value)
            if (args.get("mode") or "replace") == "patch":
                body = args.get("patch")
                if isinstance(body, str):
                    parser = _source("tools.patch_parser")
                    operations, error = parser.parse_v4a_patch(body)
                    if error:
                        raise HermesToolRuntimeError(str(error))
                    for operation in operations:
                        paths.append(operation.file_path)
                        if operation.new_path:
                            paths.append(operation.new_path)
        return paths

    def _authorize_hidden(self, args: Mapping[str, Any]) -> None:
        for key in _HIDDEN_CAPABILITIES:
            if key in args and bool(args[key]):
                raise HermesToolRuntimeError(
                    f"hidden capability is not admitted: {key}"
                )

    def _authorize_document(self, name: str, path: Path) -> None:
        if name != "read_file":
            return
        try:
            extractor = _source("tools.read_extract")
            if extractor.is_extractable_document(str(path)):
                raise HermesToolRuntimeError(
                    "document extraction is not admitted by the bounded profile"
                )
        except HermesToolRuntimeError:
            raise
        except Exception as exc:
            raise HermesToolRuntimeError(
                "native document policy is unavailable"
            ) from exc

    def _authorize_arguments(
        self, name: str, args: Mapping[str, Any], remaining_seconds: float | None = None
    ) -> None:
        self._authorize_hidden(args)
        if name == "terminal":
            timeout = args.get("timeout")
            if isinstance(timeout, str):
                timeout = _source("tools.arg_coercion").coerce_tool_args(
                    name,
                    {"timeout": timeout},
                )["timeout"]
                if isinstance(timeout, str):
                    raise HermesToolRuntimeError(
                        "terminal timeout is not a bounded numeric value"
                    )
            if (
                isinstance(timeout, (int, float))
                and not isinstance(timeout, bool)
                and (
                    timeout > TERMINAL_SECONDS
                    or isinstance(timeout, float)
                    and math.isnan(timeout)
                )
            ):
                raise HermesToolRuntimeError(
                    "terminal timeout exceeds bounded foreground limit"
                )
        paths = self._path_targets(name, args)
        for raw in paths:
            if (
                name == "patch"
                and (args.get("mode") or "replace") == "patch"
                and ".." in Path(raw).parts
            ):
                raise HermesToolRuntimeError("V4A patch target contains traversal")
            target = (
                raw
                if name == "terminal"
                else _source(
                    "tools.file_tools_paths",
                )._resolve_path_for_task(raw, self._task_id)
            )
            resolved = self._checked_path(target)
            if name == "read_file":
                self._authorize_document(name, resolved)
            if name in {"write_file", "patch", "terminal"}:
                # Model effects are workspace-owned. Scratch/home remain
                # readable for native caches and spillover, but are never model
                # write targets or terminal working directories.
                if not _is_under(resolved, (self.workspace,)) or _is_under(
                    resolved, (self.hermes_home,)
                ):
                    raise HermesToolRuntimeError(
                        "model write/workdir is outside the admitted workspace"
                    )
        if name == "skill_view":
            file_path = args.get("file_path")
            if isinstance(file_path, str) and (
                Path(file_path).is_absolute() or ".." in Path(file_path).parts
            ):
                raise HermesToolRuntimeError("skill linked-file path must be relative")
        if remaining_seconds is not None and float(remaining_seconds) <= 0:
            raise HermesToolRuntimeError("native action deadline expired")
        self._check_post_cwd()

    def authorize_arguments(
        self, name: str, args: Mapping[str, Any], remaining_seconds: float | None = None
    ) -> None:
        if not self._initialized or self._closed:
            raise HermesToolRuntimeError("native tool runtime is not active")
        if name not in TOOL_NAMES:
            raise HermesToolRuntimeError(f"tool {name!r} is not admitted")
        self._authorize_arguments(name, args, remaining_seconds)

    def authorize_call(self, call: Any, remaining_seconds: float | None = None) -> None:
        """Authorize one recorded ToolCall immediately before its source effect."""
        if not self._initialized or self._closed:
            raise HermesToolRuntimeError("native tool runtime is not active")
        name, raw_arguments = _call_fields(call)
        if name not in TOOL_NAMES:
            raise HermesToolRuntimeError(f"tool {name!r} is not admitted")
        args = _parse_arguments(raw_arguments)
        if args is not None:
            self._authorize_arguments(name, args, remaining_seconds)

    def _install_dispatch_guards(self) -> None:
        model_tools = _source("model_tools")
        original_handle = model_tools.handle_function_call
        runtime = self

        def guarded_handle(
            function_name: str, function_args: Any, *args: Any, **kwargs: Any
        ) -> Any:
            try:
                if isinstance(function_args, Mapping):
                    runtime.authorize_arguments(
                        function_name, function_args, runtime._remaining_seconds()
                    )
            except BaseException:
                runtime._authority_failed = True
                runtime.state._interrupt_requested = True
                raise
            return original_handle(function_name, function_args, *args, **kwargs)

        self._model_handle_original = original_handle
        model_tools.handle_function_call = guarded_handle

        previous_invoke = getattr(self.state, "_invoke_tool", _MISSING)
        if not callable(previous_invoke):
            raise HermesToolRuntimeError(
                "native source _invoke_tool descriptor is missing"
            )
        self._state_invoke_original = previous_invoke

        @wraps(previous_invoke)
        def invoke_tool(
            function_name: str, function_args: Any, *args: Any, **kwargs: Any
        ) -> Any:
            try:
                if isinstance(function_args, Mapping):
                    runtime.authorize_arguments(
                        function_name, function_args, runtime._remaining_seconds()
                    )
            except BaseException:
                runtime._authority_failed = True
                runtime.state._interrupt_requested = True
                raise
            return previous_invoke(function_name, function_args, *args, **kwargs)

        self.state._invoke_tool = invoke_tool

        deadline = _source("agent.deadline")
        original_resolve = deadline.resolve_timeout

        def bounded_resolve_timeout(
            key: str, *, default: Any, env_var: str | None = None
        ) -> Any:
            value = original_resolve(key, default=default, env_var=env_var)
            if value is None:
                value = NATIVE_TOOL_SECONDS
            if (
                isinstance(value, (int, float))
                and not isinstance(value, bool)
                and value > 0
            ):
                return min(
                    float(value), NATIVE_TOOL_SECONDS, runtime._remaining_seconds()
                )
            return value

        self._deadline_resolve_original = original_resolve
        deadline.resolve_timeout = bounded_resolve_timeout

    def _restore_dispatch_guards(self) -> None:
        if self._model_handle_original is not _MISSING:
            try:
                _source(
                    "model_tools"
                ).handle_function_call = self._model_handle_original
            except Exception:
                pass
            self._model_handle_original = _MISSING
        if hasattr(self, "_state_invoke_original"):
            previous = self._state_invoke_original
            if previous is _MISSING:
                try:
                    delattr(self.state, "_invoke_tool")
                except AttributeError:
                    pass
            else:
                self.state._invoke_tool = previous
        if self._deadline_resolve_original is not _MISSING:
            try:
                _source(
                    "agent.deadline"
                ).resolve_timeout = self._deadline_resolve_original
            except Exception:
                pass
            self._deadline_resolve_original = _MISSING
        if self._drain_helper_original is not _MISSING:
            try:
                _source(
                    "tools.environments.base_output"
                )._drain_fd_select = self._drain_helper_original
            except Exception:
                pass
            self._drain_helper_original = _MISSING
            self._drain_helper_guard = None
        self._capture_processes.clear()

    def execute_segment(
        self,
        kind: str,
        calls: list[Any],
        messages: list[Any],
        task_id: str,
        api_call_count: int,
    ) -> list[dict[str, Any]]:
        if not self._initialized or self._closed:
            raise HermesToolRuntimeError("native tool runtime is not active")
        if self._hard_stop:
            raise HermesToolRuntimeError(
                f"native raw command output exceeded {MAX_RAW_COMMAND_BYTES} bytes "
                f"(observed {self._raw_overflow_bytes} bytes); dispatch is stopped"
            )
        if self._authority_failed:
            raise HermesToolRuntimeError(
                "native pre-effect authority rejected an earlier call"
            )
        if kind not in {"parallel", "sequential"}:
            raise HermesToolRuntimeError(f"unknown native execution segment: {kind!r}")
        if not isinstance(calls, list) or not isinstance(messages, list):
            raise HermesToolRuntimeError(
                "native execution segment has invalid containers"
            )
        if not isinstance(task_id, str) or not task_id:
            raise HermesToolRuntimeError("native execution segment has no task id")
        if task_id != self._task_id:
            # A different task would make source file/terminal helpers consult a
            # different registry entry, defeating the reused LocalEnvironment.
            raise HermesToolRuntimeError(
                "native execution task does not own the active environment"
            )
        if getattr(self.state, "_incremental_persistence_failed", False):
            raise HermesToolRuntimeError(
                "canonical native flush failed; dispatch is stopped"
            )
        remaining = self._remaining_seconds()
        if remaining <= 0:
            raise HermesToolRuntimeError("native action deadline expired")
        before = len(messages)
        executor = _source("agent.tool_executor")
        assistant = SimpleNamespace(tool_calls=list(calls))
        if kind == "parallel":
            executor.execute_tool_calls_concurrent(
                self.state, assistant, messages, task_id, api_call_count, finalize=False
            )
        else:
            executor.execute_tool_calls_sequential(
                self.state, assistant, messages, task_id, api_call_count, finalize=False
            )
        if self._hard_stop:
            raise HermesToolRuntimeError(
                f"native raw command output exceeded {MAX_RAW_COMMAND_BYTES} bytes "
                f"(observed {self._raw_overflow_bytes} bytes); dispatch is stopped"
            )
        if self._authority_failed:
            raise HermesToolRuntimeError("native pre-effect authority rejected a call")
        if getattr(self.state, "_incremental_persistence_failed", False):
            raise HermesToolRuntimeError(
                "canonical native flush failed; dispatch is stopped"
            )
        self._check_post_cwd()
        observations: list[dict[str, Any]] = []
        for row in messages[before:]:
            if not isinstance(row, Mapping) or row.get("role") != "tool":
                continue
            copied = _deep_copy(row)
            if _json_size(copied) > MAX_OBSERVATION_BYTES:
                raise HermesToolRuntimeError("native tool observation exceeds 16 MiB")
            if isinstance(copied, dict):
                observations.append(copied)
        return observations

    def capture_resources(self) -> dict[str, Any]:
        """Capture actual owned scratch entries before source cleanup, retaining aliases."""
        if not self.scratch.exists():
            raise HermesToolRuntimeError(
                "owned scratch root disappeared before cleanup"
            )
        entries: list[dict[str, Any]] = []
        inode_first: dict[tuple[int, int], str] = {}

        def visit(directory: Path) -> None:
            try:
                children = sorted(os.scandir(directory), key=lambda item: item.name)
            except OSError as exc:
                raise HermesToolRuntimeError(
                    f"cannot enumerate owned scratch: {directory}"
                ) from exc
            for item in children:
                path = Path(item.path)
                relative = path.relative_to(self.scratch).as_posix()
                try:
                    info = item.stat(follow_symlinks=False)
                except OSError as exc:
                    raise HermesToolRuntimeError(
                        f"cannot stat owned scratch entry: {relative}"
                    ) from exc
                mode = stat.S_IMODE(info.st_mode)
                if stat.S_ISLNK(info.st_mode):
                    try:
                        target = os.readlink(path)
                    except OSError as exc:
                        raise HermesToolRuntimeError(
                            f"cannot read owned scratch symlink: {relative}"
                        ) from exc
                    entries.append(
                        {
                            "path": relative,
                            "kind": "symlink",
                            "mode": mode,
                            "target": target,
                        }
                    )
                    continue
                if stat.S_ISDIR(info.st_mode):
                    entries.append(
                        {"path": relative, "kind": "directory", "mode": mode}
                    )
                    visit(path)
                    continue
                if not stat.S_ISREG(info.st_mode):
                    raise HermesToolRuntimeError(
                        f"unsupported owned scratch resource type: {relative}"
                    )
                key = (int(info.st_dev), int(info.st_ino))
                entry: dict[str, Any] = {
                    "path": relative,
                    "kind": "file",
                    "bytes": int(info.st_size),
                    "mode": mode,
                }
                first = inode_first.get(key)
                if first is not None:
                    entry["alias_of"] = first
                else:
                    digest = hashlib.sha256()
                    try:
                        with path.open("rb") as stream:
                            for block in iter(lambda: stream.read(1024 * 1024), b""):
                                digest.update(block)
                    except OSError as exc:
                        raise HermesToolRuntimeError(
                            f"cannot hash owned scratch resource: {relative}"
                        ) from exc
                    inode_first[key] = relative
                    entry["sha256"] = "sha256:" + digest.hexdigest()
                entries.append(entry)

        try:
            root_stat = self.scratch.stat()
        except OSError as exc:
            raise HermesToolRuntimeError("cannot stat owned scratch root") from exc
        visit(self.scratch)
        self._resource_facts = {
            "root": str(self.scratch),
            "mode": stat.S_IMODE(root_stat.st_mode),
            "entries": entries,
        }
        return _deep_copy(self._resource_facts)

    def close(self) -> None:
        if self._closed:
            return
        capture_error: BaseException | None = None
        cleanup_error: BaseException | None = None
        try:
            if self._initialized and self._resource_facts is None:
                try:
                    self.capture_resources()
                except BaseException as exc:
                    capture_error = exc
            self._restore_dispatch_guards()
            if self._environment is not None:
                try:
                    self._environment.cleanup()
                except BaseException as exc:
                    cleanup_error = exc
            if self._environment is not None and self._task_id:
                try:
                    terminal = _source("tools.terminal_tool")
                    with terminal._env_lock:
                        if (
                            terminal._active_environments.get(self._task_id)
                            is self._environment
                        ):
                            terminal._active_environments.pop(self._task_id, None)
                            terminal._last_activity.pop(self._task_id, None)
                except Exception as exc:
                    if cleanup_error is None:
                        cleanup_error = exc
        finally:
            if (
                self._environment is not None
                and self._env_run_bash_original is not _MISSING
            ):
                setattr(self._environment, "_run_bash", self._env_run_bash_original)
            elif self._environment is not None and self._env_run_bash_guard is not None:
                self._environment.__dict__.pop("_run_bash", None)
            if (
                self._environment is not None
                and self._env_execute_original is not _MISSING
            ):
                setattr(self._environment, "execute", self._env_execute_original)
            elif self._environment is not None and self._env_execute_guard is not None:
                self._environment.__dict__.pop("execute", None)
            self._closed = True
        if capture_error is not None:
            raise capture_error
        if cleanup_error is not None:
            raise cleanup_error


__all__ = ["HermesToolRuntime", "HermesToolRuntimeError", "TOOL_NAMES"]
