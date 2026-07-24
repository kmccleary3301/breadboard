from __future__ import annotations

import datetime
import enum
import encodings.idna
import hashlib
import functools
import importlib
import importlib.machinery
import importlib.util
import inspect
import json
import os
import platform
import re
import signal
import socket
import stat
import subprocess
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
import sys
import uuid
from pathlib import Path
from typing import Any
from jsonschema import Draft202012Validator, SchemaError, ValidationError
from referencing import Registry, Resource

from agentic_coder_prototype.logging.provider_dump import provider_dump_logger
from agentic_coder_prototype.provider.runtime import (
    ProviderMessage,
    ProviderRuntimeContext,
    ProviderResult,
    ProviderToolCall,
    normalized_provider_usage,
    provider_result_evidence,
)
from breadboard.product.harness.lock import EffectiveHarnessLock
from breadboard.product.runtime.artifacts import AnchoredStorage, ArtifactRef, ArtifactStore
from breadboard.product.runtime.events import Clock, IdSource, JsonlEventSink, Session, SystemClock, UUIDSource
from breadboard.product.integrations.provider import ProviderRuntimeAdapter

from .replay_execution import ReplayArtifactManifest, ReplayExecution, _portable_id, problem, validate_provider_exchange
from .replay_plan import HASH_BINDING_NAMES, ReplayPlan, ReplayPlanError, canonical_json, sha256_json
from .workspace import BreadBoardWorkspace


class ReplayRunError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class ReplayScenario:
    task: str
    initial_messages: tuple[Mapping[str, Any], ...]
    initial_files: Mapping[str, bytes | str]
    tool_schemas: tuple[Mapping[str, Any], ...]
    interaction_script: tuple[Mapping[str, Any], ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.task, str) or not self.task.strip():
            raise ReplayRunError("scenario task must be populated")
        if any(not isinstance(row, Mapping) for row in (*self.initial_messages, *self.interaction_script)):
            raise ReplayRunError("scenario messages and interaction script must contain objects")
        _initial_file_rows(self.initial_files)
        canonical_json(list(self.initial_messages)); canonical_json(list(self.tool_schemas)); canonical_json(list(self.interaction_script))
        _declared_tool_names(self.tool_schemas)
        _tool_argument_validators(self.tool_schemas)

    def binding_inputs(self, frozen_inputs: Mapping[str, Any]) -> dict[str, Any]:
        result = dict(frozen_inputs)
        result.update({
            "scenario_sha256": {"task": self.task},
            "interaction_script_sha256": list(self.interaction_script),
            "initial_workspace_sha256": _initial_file_rows(self.initial_files),
            "initial_messages_sha256": list(self.initial_messages),
            "tool_schema_lock_sha256": list(self.tool_schemas),
        })
        if set(result) != set(HASH_BINDING_NAMES):
            raise ReplayRunError("scenario bindings must complete the frozen replay binding set")
        return result


@dataclass(frozen=True, slots=True)
class ReplayRunResult:
    execution: ReplayExecution
    execution_path: Path
    manifest: ReplayArtifactManifest


class _RuntimeFailure(Exception):
    def __init__(self, status: str, error_code: str, detail: str) -> None:
        super().__init__(detail); self.status, self.error_code, self.detail = status, error_code, detail

class _InvalidProviderResponse(ReplayRunError):
    pass
class _RemoteCallError(RuntimeError):
    pass
class _InvalidWorkerValue:
    pass


def _worker_envelope(status: str, value: Any = None, *, kind: str | None = None) -> bytes:
    return canonical_json({"status": status, "kind": kind, "value": value})


def _decode_canonical_json(payload: bytes) -> Any:
    def unique_object(rows: list[tuple[str, Any]]) -> dict[str, Any]:
        result = {}
        for name, value in rows:
            if name in result:
                raise ValueError("duplicate JSON key")
            result[name] = value
        return result
    try:
        record = json.loads(payload.decode("utf-8"), parse_constant=lambda value: (_ for _ in ()).throw(ValueError(value)), object_pairs_hook=unique_object)
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        raise _RemoteCallError("isolated replay IPC contained invalid JSON") from exc
    if canonical_json(record) != payload:
        raise _RemoteCallError("isolated replay IPC contained non-canonical JSON")
    return record


def _decode_worker_envelope(payload: bytes) -> tuple[str, str | None, Any]:
    record = _decode_canonical_json(payload)
    if not isinstance(record, dict) or set(record) != {"status", "kind", "value"}:
        raise _RemoteCallError("isolated replay call returned a malformed envelope")
    status, kind = record["status"], record["kind"]
    if status not in {"ready", "ok", "error", "invalid"} or (kind is not None and kind not in {"provider", "policy", "host", "tool"}):
        raise _RemoteCallError("isolated replay call returned an invalid envelope status")
    return status, kind, record["value"]


def _provider_result_from_evidence(value: Any) -> ProviderResult:
    if not isinstance(value, dict) or set(value) != {"messages", "usage", "encrypted_reasoning", "reasoning_summaries", "model", "metadata"} or not isinstance(value["messages"], list):
        raise _RemoteCallError("isolated provider returned malformed normalized evidence")
    messages = []
    for row in value["messages"]:
        if not isinstance(row, dict) or set(row) != {"role", "content", "tool_calls", "finish_reason", "index", "annotations"} or not isinstance(row["tool_calls"], list):
            raise _RemoteCallError("isolated provider returned a malformed normalized message")
        calls = []
        for call in row["tool_calls"]:
            if not isinstance(call, dict) or set(call) != {"id", "name", "arguments", "type"}:
                raise _RemoteCallError("isolated provider returned a malformed normalized tool call")
            calls.append(ProviderToolCall(call["id"], call["name"], call["arguments"], call["type"]))
        messages.append(ProviderMessage(row["role"], row["content"], calls, row["finish_reason"], row["index"], annotations=row["annotations"]))
    return ProviderResult(messages, None, value["usage"], encrypted_reasoning=value["encrypted_reasoning"], reasoning_summaries=value["reasoning_summaries"], model=value["model"], metadata=value["metadata"])


def _redirect_worker_stdio() -> None:
    descriptor = os.open(os.devnull, os.O_RDWR)
    try:
        os.dup2(descriptor, 1)
        os.dup2(descriptor, 2)
        sys.stdout = open(1, "w", buffering=1, encoding="utf-8", errors="backslashreplace", closefd=False)
        sys.stderr = open(2, "w", buffering=1, encoding="utf-8", errors="backslashreplace", closefd=False)
    finally:
        if descriptor not in (1, 2):
            os.close(descriptor)


def _close_inherited_descriptors(keep: set[int]) -> None:
    descriptor_root = "/proc/self/fd" if os.path.isdir("/proc/self/fd") else "/dev/fd"
    try:
        descriptors = tuple(int(name) for name in os.listdir(descriptor_root) if name.isdigit())
    except OSError:
        import resource
        soft_limit, _ = resource.getrlimit(resource.RLIMIT_NOFILE)
        descriptors = tuple(range(min(int(soft_limit), 65_536)))
    for descriptor in descriptors:
        if descriptor in keep or descriptor in (1, 2):
            continue
        try:
            os.close(descriptor)
        except OSError:
            continue


def _enforce_worker_sandbox(
    workspace: str,
    *,
    allow_network: bool,
    allow_process: bool,
    allow_write: bool,
    allow_workspace_read: bool,
    module_read_paths: Sequence[str] = (),
) -> None:
    import ctypes
    import ctypes.util
    import errno
    import ssl
    containment_root = os.path.realpath(workspace)
    candidates = [
        sys.base_prefix,
        sys.prefix,
        "/System/Library",
        "/Library/Apple",
        "/usr/lib",
        "/usr/lib64",
        "/lib",
        "/bin",
        "/usr/bin",
        "/lib64",
        "/etc/hosts",
        "/etc/resolv.conf",
        "/etc/nsswitch.conf",
        "/etc/gai.conf",
        "/etc/services",
        "/etc/ssl",
        "/dev/null",
        "/dev/random",
        "/dev/urandom",
    ]
    if allow_workspace_read:
        candidates.append(containment_root)
    verify_paths = ssl.get_default_verify_paths()
    candidates.extend((verify_paths.cafile, verify_paths.capath))
    candidates.extend(path for path in module_read_paths if os.path.isdir(path))
    read_roots = tuple(sorted({
        resolved
        for path in candidates
        if path and os.path.exists(path)
        for resolved in (os.path.abspath(path), os.path.realpath(path))
    }))
    module_files = tuple(sorted({
        resolved
        for path in module_read_paths
        if os.path.isfile(path)
        for resolved in (os.path.abspath(path), os.path.realpath(path))
    }))
    module_directories = tuple(sorted({
        str(parent)
        for path in module_files
        for parent in Path(path).parents
        if str(parent) != os.path.sep
    }))
    if sys.platform == "darwin":
        library = ctypes.CDLL("/usr/lib/libsandbox.dylib")
        error = ctypes.c_char_p()
        library.sandbox_init.argtypes = [ctypes.c_char_p, ctypes.c_uint64, ctypes.POINTER(ctypes.c_char_p)]
        library.sandbox_init.restype = ctypes.c_int
        readable = (
            "(literal \"/\") "
            + " ".join(f"(subpath {json.dumps(path)})" for path in read_roots)
            + " "
            + " ".join(f"(literal {json.dumps(path)})" for path in module_files)
        )
        profile = (
            "(version 1)(allow default)"
            + ("" if allow_process else "(deny process-fork)(deny process-exec)")
            + ("" if allow_network else "(deny network*)")
            + ("" if allow_write else "(deny file-write* (require-not (literal " + json.dumps(os.devnull) + ")))")
            + (f"(deny file-write* (require-not (require-any (subpath {json.dumps(containment_root)}) (literal {json.dumps(os.devnull)}))))" if allow_write else "")
            + f"(deny file-read-data (require-not (require-any {readable})))"
        )
        if library.sandbox_init(profile.encode("utf-8"), 0, ctypes.byref(error)) != 0:
            raise ReplayRunError("could not enforce the replay worker sandbox")
        return
    if sys.platform.startswith("linux"):
        library_name = ctypes.util.find_library("seccomp")
        if not library_name:
            raise ReplayRunError("libseccomp is required for replay process containment")
        library = ctypes.CDLL(library_name)
        library.seccomp_init.argtypes = [ctypes.c_uint32]
        library.seccomp_init.restype = ctypes.c_void_p
        library.seccomp_syscall_resolve_name.argtypes = [ctypes.c_char_p]
        library.seccomp_syscall_resolve_name.restype = ctypes.c_int
        library.seccomp_rule_add.argtypes = [ctypes.c_void_p, ctypes.c_uint32, ctypes.c_int, ctypes.c_uint]
        library.seccomp_rule_add.restype = ctypes.c_int
        library.seccomp_load.argtypes = [ctypes.c_void_p]
        library.seccomp_load.restype = ctypes.c_int
        library.seccomp_release.argtypes = [ctypes.c_void_p]
        context = library.seccomp_init(0x7FFF0000)
        if not context:
            raise ReplayRunError("could not initialize replay process containment")
        try:
            deny = 0x00050000 | errno.EPERM
            denied_syscalls = [] if allow_process else [b"fork", b"vfork", b"clone", b"clone3", b"execve", b"execveat"]
            if not allow_network:
                denied_syscalls.extend((b"socket", b"socketpair", b"connect", b"bind", b"listen", b"accept", b"accept4", b"sendto", b"sendmsg", b"recvfrom", b"recvmsg", b"shutdown", b"getsockopt", b"setsockopt", b"getpeername", b"getsockname"))
            for syscall_name in denied_syscalls:
                syscall = library.seccomp_syscall_resolve_name(syscall_name)
                if syscall < 0:
                    raise ReplayRunError(f"libseccomp cannot resolve required replay syscall {syscall_name.decode('ascii')}")
                if library.seccomp_rule_add(context, deny, syscall, 0) != 0:
                    raise ReplayRunError("could not configure replay process containment")
            if library.seccomp_load(context) != 0:
                raise ReplayRunError("could not enforce replay process containment")
        finally:
            library.seccomp_release(context)

        class _RulesetAttr(ctypes.Structure):
            _fields_ = [("handled_access_fs", ctypes.c_uint64)]

        class _PathBeneathAttr(ctypes.Structure):
            _fields_ = [("allowed_access", ctypes.c_uint64), ("parent_fd", ctypes.c_int32)]

        libc = ctypes.CDLL(None, use_errno=True)
        libc.syscall.restype = ctypes.c_long
        abi = libc.syscall(444, ctypes.c_void_p(), 0, 1)
        if abi < 1:
            raise ReplayRunError("Linux Landlock is required for replay filesystem containment")
        read_file, read_dir = 1 << 2, 1 << 3
        write_access = sum(1 << bit for bit in range(1, 13) if bit not in (2, 3))
        if abi >= 2:
            write_access |= 1 << 13
        if abi >= 3:
            write_access |= 1 << 14
        handled_access = read_file | read_dir | write_access
        ruleset_attr = _RulesetAttr(handled_access)
        ruleset_fd = libc.syscall(444, ctypes.byref(ruleset_attr), ctypes.sizeof(ruleset_attr), 0)
        if ruleset_fd < 0:
            raise ReplayRunError(f"could not create replay filesystem ruleset: {os.strerror(ctypes.get_errno())}")
        opened: list[int] = []
        try:
            for path in module_directories:
                path_fd = os.open(path, getattr(os, "O_PATH", os.O_RDONLY) | getattr(os, "O_CLOEXEC", 0))
                opened.append(path_fd)
                path_rule = _PathBeneathAttr(read_dir, path_fd)
                if libc.syscall(445, ruleset_fd, 1, ctypes.byref(path_rule), 0) < 0:
                    raise ReplayRunError(f"could not configure replay module search boundary: {os.strerror(ctypes.get_errno())}")
            for path in module_files:
                path_fd = os.open(path, getattr(os, "O_PATH", os.O_RDONLY) | getattr(os, "O_CLOEXEC", 0))
                opened.append(path_fd)
                path_rule = _PathBeneathAttr(read_file, path_fd)
                if libc.syscall(445, ruleset_fd, 1, ctypes.byref(path_rule), 0) < 0:
                    raise ReplayRunError(f"could not configure replay module file boundary: {os.strerror(ctypes.get_errno())}")
            for path in read_roots:
                path_fd = os.open(path, getattr(os, "O_PATH", os.O_RDONLY) | getattr(os, "O_CLOEXEC", 0))
                opened.append(path_fd)
                allowed_access = read_file | (read_dir if os.path.isdir(path) else 0)
                if path == containment_root and allow_write:
                    allowed_access |= write_access
                path_rule = _PathBeneathAttr(allowed_access, path_fd)
                if libc.syscall(445, ruleset_fd, 1, ctypes.byref(path_rule), 0) < 0:
                    raise ReplayRunError(f"could not configure replay filesystem boundary: {os.strerror(ctypes.get_errno())}")
            libc.prctl.argtypes = [ctypes.c_int, ctypes.c_ulong, ctypes.c_ulong, ctypes.c_ulong, ctypes.c_ulong]
            libc.prctl.restype = ctypes.c_int
            if libc.prctl(38, 1, 0, 0, 0) != 0:
                raise ReplayRunError(f"could not lock replay worker privileges: {os.strerror(ctypes.get_errno())}")
            if libc.syscall(446, ruleset_fd, 0) < 0:
                raise ReplayRunError(f"could not enforce replay filesystem boundary: {os.strerror(ctypes.get_errno())}")
        finally:
            for path_fd in opened:
                os.close(path_fd)
            os.close(ruleset_fd)
        return
    raise ReplayRunError("the current platform has no replay containment backend")


class _ProviderCapability:
    def __init__(self, provider: Any, client: Any, client_spec: Mapping[str, Any] | None, model: str, context: Any, environment: Mapping[str, str], secret_bindings: Mapping[str, str], expected_client_binding: Mapping[str, Any]) -> None:
        self.provider = provider
        self.client = client
        self.client_spec = dict(client_spec) if client_spec is not None else None
        self.model = model
        self.context = context
        self.environment = dict(environment)
        self.secret_bindings = dict(secret_bindings)
        self.expected_client_binding = dict(expected_client_binding)


class _BoundPolicyCapability:
    def __init__(self, target: Any, method_name: str) -> None:
        self.target = target
        self.method_name = method_name

    def __call__(self, *args: Any, **kwargs: Any) -> Any:
        return getattr(self.target, self.method_name)(*args, **kwargs)


class _PartialPolicyCapability:
    def __init__(self, function: Any, args: Sequence[Any], keywords: Mapping[str, Any]) -> None:
        self.function = function
        self.args = tuple(args)
        self.keywords = dict(keywords)

    def __call__(self, name: str, arguments: Mapping[str, Any]) -> Any:
        return self.function(*self.args, name, arguments, **self.keywords)


def _policy_capability(authorize: Any) -> Any:
    if isinstance(authorize, functools.partial):
        return _PartialPolicyCapability(_policy_capability(authorize.func), authorize.args, authorize.keywords or {})
    if inspect.ismethod(authorize) and authorize.__self__ is not None:
        return _BoundPolicyCapability(authorize.__self__, authorize.__name__)
    return authorize


def _isolated_provider(provider: Any) -> Any:
    if type(provider).__module__ != "breadboard.product.integrations.provider" or type(provider).__qualname__ != "ProviderRuntimeAdapter":
        return provider
    isolated_provider = type(provider).__new__(type(provider))
    for name, value in _plain_object_state(provider).items():
        object.__setattr__(isolated_provider, name, [] if name in {"_client_identities", "_client_replay_specs"} else value)
    return isolated_provider


def _provider_capability(provider: Any, client: Any, model: str, context: Any, environment: Mapping[str, str], secret_bindings: Mapping[str, str], expected_client_binding: Mapping[str, Any]) -> _ProviderCapability:
    spec_method = getattr(provider, "replay_worker_client_spec", None)
    client_spec = spec_method(client, secret_bindings) if callable(spec_method) else None
    if client_spec is not None and not callable(getattr(provider, "replay_worker_client", None)):
        raise ReplayRunError("provider client spec requires replay_worker_client reconstruction")
    isolated_provider = _isolated_provider(provider)
    return _ProviderCapability(isolated_provider, None if client_spec is not None else client, client_spec, model, context, environment, secret_bindings, expected_client_binding)


def _state_capability_kind(value: Any) -> str | None:
    if callable(getattr(value, "invoke", None)):
        return "provider"
    if callable(getattr(value, "execute", None)):
        if callable(getattr(value, "replay_process_containment", None)) and (callable(getattr(value, "workspace", None)) or callable(getattr(value, "get_workspace", None))):
            return "host"
        return "tool"
    return None
def _host_network_access(host: Any) -> bool:
    descriptor = getattr(host, "descriptor", None)
    capabilities = getattr(descriptor, "capabilities", ())
    effects = getattr(descriptor, "effects", ())
    return "network" in capabilities or "network" in effects




def _fixed_timezone_state(value: datetime.tzinfo | None) -> dict[str, Any] | None:
    if value is None:
        return None
    if type(value) is not datetime.timezone:
        raise ReplayRunError(f"timezone type {type(value).__module__}.{type(value).__qualname__} cannot be encoded losslessly")
    offset = value.utcoffset(None)
    if offset is None:
        raise ReplayRunError("fixed timezone does not expose an offset")
    offset_microseconds = ((offset.days * 86_400 + offset.seconds) * 1_000_000) + offset.microseconds
    return {"offset_microseconds": offset_microseconds, "name": value.tzname(None)}


def _scalar_state(value: Any) -> dict[str, Any] | None:
    if type(value) is socket.socket:
        return {"$closed_socket": True}
    if type(value) is object:
        return {"$object_sentinel": True}
    if type(value) is Decimal:
        return {"$decimal": str(value)}
    if type(value) is datetime.datetime:
        return {"$datetime": {
            "value": value.replace(tzinfo=None).isoformat(timespec="microseconds"),
            "fold": value.fold,
            "timezone": _fixed_timezone_state(value.tzinfo),
        }}
    if type(value) is datetime.date:
        return {"$date": value.isoformat()}
    if type(value) is datetime.time:
        return {"$time": {
            "value": value.replace(tzinfo=None).isoformat(timespec="microseconds"),
            "fold": value.fold,
            "timezone": _fixed_timezone_state(value.tzinfo),
        }}
    if type(value) is datetime.timedelta:
        return {"$timedelta": {"days": value.days, "seconds": value.seconds, "microseconds": value.microseconds}}
    if type(value) is datetime.timezone:
        return {"$timezone": _fixed_timezone_state(value)}
    if type(value) is uuid.UUID:
        return {"$uuid": {"hex": value.hex, "is_safe": value.is_safe.name}}
    if isinstance(value, enum.Enum):
        value_type = type(value)
        if value_type.__module__ in {"__main__", "__mp_main__"} or "<locals>" in value_type.__qualname__:
            raise ReplayRunError("capability enum types must be importable outside the entry-point module")
        common = {"module": value_type.__module__, "qualname": value_type.__qualname__}
        if isinstance(value.name, str) and value_type.__members__.get(value.name) is value:
            return {"$enum": {**common, "name": value.name}}
        if isinstance(value, enum.Flag) and type(value.value) is int:
            return {"$enum": {**common, "value": value.value}}
        raise ReplayRunError(f"enum value {value_type.__module__}.{value_type.__qualname__} cannot be encoded losslessly")
    return None


def _restore_fixed_timezone(value: Mapping[str, Any] | None) -> datetime.tzinfo | None:
    if value is None:
        return None
    return datetime.timezone(datetime.timedelta(microseconds=value["offset_microseconds"]), value["name"])


def _tool_state_value(value: Any, capability_kind: str, seen: frozenset[int] = frozenset(), *, allow_callable: bool = False) -> Any:
    if value is None or type(value) in (bool, int, float, str):
        return value
    if isinstance(value, Path):
        return {"$path": str(value)}
    if type(value) is bytes:
        return {"$bytes": value.hex()}
    scalar_state = _scalar_state(value)
    if scalar_state is not None:
        return scalar_state
    nested_kind = _state_capability_kind(value)
    if nested_kind is not None and nested_kind != capability_kind:
        raise ReplayRunError(f"{capability_kind} capability state contains a forbidden {nested_kind} capability")
    if id(value) in seen:
        raise ReplayRunError("capability executor state must not contain cycles")
    next_seen = seen | {id(value)}
    if allow_callable and isinstance(value, functools.partial):
        return {"$partial": {
            "function": _tool_state_value(value.func, capability_kind, next_seen, allow_callable=True),
            "args": _tool_state_value(value.args, capability_kind, next_seen),
            "keywords": _tool_state_value(value.keywords or {}, capability_kind, next_seen),
        }}
    if allow_callable and inspect.ismethod(value) and value.__self__ is not None:
        return {"$bound_method": {
            "target": _tool_state_value(value.__self__, capability_kind, next_seen),
            "name": value.__name__,
        }}
    if allow_callable and inspect.isbuiltin(value):
        module, qualname = getattr(value, "__module__", None), getattr(value, "__qualname__", None)
        if not isinstance(module, str) or not isinstance(qualname, str) or module in {"__main__", "__mp_main__"}:
            raise ReplayRunError("built-in capability callables must expose an importable identity outside the entry-point module")
        return {"$callable": {"module": module, "qualname": qualname}}
    if type(value) is dict:
        if any(type(name) is not str for name in value):
            raise ReplayRunError("capability executor state mappings require string keys")
        return {"$mapping": {name: _tool_state_value(item, capability_kind, next_seen) for name, item in sorted(value.items())}}
    if type(value) in (list, tuple):
        return {"$sequence": [_tool_state_value(item, capability_kind, next_seen) for item in value], "$tuple": type(value) is tuple}
    if type(value) in (set, frozenset):
        items = [_tool_state_value(item, capability_kind, next_seen) for item in value]
        return {"$set": sorted(items, key=canonical_json), "$frozen": type(value) is frozenset}
    if inspect.isfunction(value):
        if capability_kind != "policy" and not allow_callable:
            raise ReplayRunError(f"{capability_kind} capability state contains a forbidden policy callable")
        if value.__module__ in {"__main__", "__mp_main__"} or "<locals>" in value.__qualname__ or "<lambda>" in value.__qualname__:
            raise ReplayRunError("capability callables must be importable outside the entry-point module")
        return {"$callable": {"module": value.__module__, "qualname": value.__qualname__}}
    value_type = type(value)
    if value_type.__module__ in {"__main__", "__mp_main__"} or "<locals>" in value_type.__qualname__:
        raise ReplayRunError("capability state types must be importable outside the entry-point module")
    state = _plain_object_state(value)
    if value_type.__new__ is not object.__new__:
        raise ReplayRunError(f"capability value type {value_type.__module__}.{value_type.__qualname__} cannot be encoded losslessly")
    return {"$object": {
        "module": value_type.__module__,
        "qualname": value_type.__qualname__,
        "state": {name: _tool_state_value(item, capability_kind, next_seen) for name, item in sorted(state.items())},
    }}


def _plain_object_state(value: Any) -> dict[str, Any]:
    try:
        namespace = object.__getattribute__(value, "__dict__")
    except AttributeError:
        state: dict[str, Any] = {}
    else:
        state = dict(namespace) if isinstance(namespace, Mapping) else {}
    for owner_type in type(value).__mro__:
        slots = vars(owner_type).get("__slots__", ())
        for name in slots if isinstance(slots, (tuple, list)) else (slots,):
            if not isinstance(name, str) or name in {"__dict__", "__weakref__"}:
                continue
            owner_name = owner_type.__name__.lstrip("_")
            storage_name = f"_{owner_name}{name}" if owner_name and name.startswith("__") and not name.endswith("__") else name
            try:
                item = object.__getattribute__(value, storage_name)
            except AttributeError:
                continue
            state.setdefault(storage_name, item)
    return state


def _tool_executor_envelope(executor: Any, capability_kind: str) -> bytes:
    if inspect.isfunction(executor):
        if executor.__module__ in {"__main__", "__mp_main__"} or "<locals>" in executor.__qualname__ or "<lambda>" in executor.__qualname__:
            raise ReplayRunError("capability callables must be importable outside the entry-point module")
        return canonical_json({"kind": "symbol", "module": executor.__module__, "qualname": executor.__qualname__})
    executor_type = type(executor)
    qualname = executor_type.__qualname__
    if executor_type.__module__ in {"__main__", "__mp_main__"} or "<locals>" in qualname:
        raise ReplayRunError("capability executor types must be importable outside the entry-point module")
    callable_tool_adapter = capability_kind == "tool" and executor_type.__module__ == "breadboard.product.integrations.tool" and qualname == "ToolIntegrationAdapter"
    return canonical_json({
        "kind": "object",
        "module": executor_type.__module__,
        "qualname": qualname,
        "state": {name: _tool_state_value(value, capability_kind, allow_callable=callable_tool_adapter and name == "executor") for name, value in sorted(_plain_object_state(executor).items())},
    })


def _path_is_within(path: str, roots: Sequence[str]) -> bool:
    resolved = os.path.realpath(path)
    for root in roots:
        try:
            if os.path.commonpath((resolved, root)) == root:
                return True
        except ValueError:
            continue
    return False


def _add_package_module_allowlist(
    module_name: str,
    runtime_roots: Sequence[str],
    paths: set[str],
    exact_specs: set[tuple[str, str, tuple[str, ...]]],
    dependency_files: set[tuple[str, str]] | None = None,
) -> None:
    package_name = module_name
    package_locations: tuple[str, ...] = ()
    while package_name:
        module = sys.modules.get(package_name)
        spec = getattr(module, "__spec__", None)
        if spec is None:
            try:
                spec = importlib.util.find_spec(package_name)
            except (AttributeError, ImportError, ValueError):
                spec = None
        package_locations = tuple(
            os.path.abspath(location)
            for location in (() if spec is None or spec.submodule_search_locations is None else spec.submodule_search_locations)
            if isinstance(location, str) and not _path_is_within(location, runtime_roots)
        )
        if package_locations:
            break
        package_name = package_name.rpartition(".")[0]
    if not package_locations:
        return
    import_suffixes = tuple(sorted((".py", ".pyc", *importlib.machinery.EXTENSION_SUFFIXES), key=len, reverse=True))
    for package_location in package_locations:
        package_root = Path(package_location)
        for candidate in package_root.rglob("*"):
            if candidate.is_symlink() or not candidate.is_file():
                continue
            relative = candidate.relative_to(package_root)
            if "__pycache__" in relative.parts:
                continue
            location = os.path.abspath(candidate)
            paths.update((location, os.path.realpath(location)))
            if dependency_files is not None:
                dependency_files.add((f"{package_name}:{relative.as_posix()}", location))
            filename = relative.name
            suffix = next((item for item in import_suffixes if filename.endswith(item)), None)
            if suffix is None:
                continue
            stem = filename[:-len(suffix)]
            if stem == "__init__":
                components = relative.parts[:-1]
                discovered_name = ".".join((package_name, *components))
                search_locations = (os.path.abspath(candidate.parent),)
            else:
                components = (*relative.parts[:-1], stem)
                discovered_name = ".".join((package_name, *components))
                search_locations = ()
            exact_specs.add((discovered_name, location, search_locations))
            if suffix == ".py":
                cached = importlib.util.cache_from_source(location)
                if os.path.isfile(cached):
                    paths.update((os.path.abspath(cached), os.path.realpath(cached)))




def _add_top_level_sibling_allowlist(
    module_name: str,
    runtime_roots: Sequence[str],
    paths: set[str],
    exact_specs: set[tuple[str, str, tuple[str, ...]]],
    dependency_files: set[tuple[str, str]] | None = None,
) -> None:
    if "." in module_name:
        return
    module = sys.modules.get(module_name)
    spec = getattr(module, "__spec__", None)
    if spec is None:
        try:
            spec = importlib.util.find_spec(module_name)
        except (AttributeError, ImportError, ValueError):
            return
    location = getattr(module, "__file__", None) or getattr(spec, "origin", None)
    if not isinstance(location, str) or location in {"built-in", "frozen"}:
        return
    module_root = Path(os.path.realpath(location)).parent
    if _path_is_within(str(module_root), runtime_roots):
        return
    import_suffixes = tuple(sorted((".py", ".pyc", *importlib.machinery.EXTENSION_SUFFIXES), key=len, reverse=True))
    for candidate in module_root.iterdir():
        if candidate.is_symlink():
            continue
        if candidate.is_dir():
            package_name = candidate.name
            package_init = next(
                (candidate / f"__init__{suffix}" for suffix in import_suffixes if (candidate / f"__init__{suffix}").is_file()),
                None,
            ) if package_name.isidentifier() else None
            if package_init is not None:
                location = os.path.abspath(package_init)
                exact_specs.add((package_name, location, (os.path.abspath(candidate),)))
                paths.update((location, os.path.realpath(location)))
                if dependency_files is not None:
                    dependency_files.add((f"{module_name}:{candidate.name}/{package_init.name}", location))
                _add_package_module_allowlist(package_name, runtime_roots, paths, exact_specs, dependency_files)
                continue
            continue
        if not candidate.is_file():
            continue
        suffix = next((item for item in import_suffixes if candidate.name.endswith(item)), None)
        if suffix is None:
            continue
        location = os.path.abspath(candidate)
        paths.update((location, os.path.realpath(location)))
        if dependency_files is not None:
            dependency_files.add((f"{module_name}:{candidate.name}", location))
        sibling_name = candidate.name[:-len(suffix)]
        if sibling_name == "__init__" or not sibling_name.isidentifier():
            continue
        exact_specs.add((sibling_name, location, ()))
        if suffix == ".py":
            cached = importlib.util.cache_from_source(location)
            if os.path.isfile(cached):
                paths.update((os.path.abspath(cached), os.path.realpath(cached)))


def _payload_module_names(payload: bytes) -> set[str]:
    envelope = json.loads(payload)
    modules: set[str] = set()
    pending = [envelope]
    while pending:
        value = pending.pop()
        if isinstance(value, dict):
            module = value.get("module")
            qualname = value.get("qualname")
            if isinstance(module, str) and isinstance(qualname, str):
                modules.add(module)
            pending.extend(value.values())
        elif isinstance(value, list):
            pending.extend(value)
    return modules


def _runtime_module_roots() -> tuple[str, ...]:
    return tuple(
        os.path.realpath(path)
        for path in (
            sys.base_prefix,
            sys.prefix,
            "/System/Library",
            "/Library/Apple",
            "/usr/lib",
            "/usr/lib64",
            "/lib",
            "/lib64",
        )
        if os.path.isdir(path)
    )


def _executor_module_dependency_identity(payload: bytes) -> list[dict[str, Any]]:
    runtime_roots = _runtime_module_roots()
    paths: set[str] = set()
    specs: set[tuple[str, str, tuple[str, ...]]] = set()
    dependency_files: set[tuple[str, str]] = set()
    for module_name in _payload_module_names(payload):
        _add_package_module_allowlist(module_name, runtime_roots, paths, specs, dependency_files)
        _add_top_level_sibling_allowlist(module_name, runtime_roots, paths, specs, dependency_files)
    dependencies = []
    for logical_name, location in sorted(dependency_files):
        try:
            content = Path(location).read_bytes()
        except OSError as exc:
            raise ReplayRunError(f"capability dependency {logical_name} is not readable") from exc
        dependencies.append({
            "name": logical_name,
            "content_sha256": _digest(content),
        })
    return dependencies




def _capability_runtime_identity(value: Any, capability_kind: str, *, payload: bytes | None = None) -> dict[str, Any]:
    frozen_payload = _tool_executor_envelope(_isolated_provider(value) if capability_kind == "provider" else value, capability_kind) if payload is None else payload
    identity = _runtime_type_identity(value)
    identity["module_dependencies_sha256"] = sha256_json(_executor_module_dependency_identity(frozen_payload))
    return identity


def _executor_module_allowlist(
    payload: bytes,
) -> tuple[tuple[str, ...], tuple[tuple[str, str, tuple[str, ...]], ...]]:
    payload_modules = _payload_module_names(payload)
    modules = set(sys.modules) | payload_modules
    runtime_roots = _runtime_module_roots()
    paths: set[str] = set(runtime_roots)
    exact_specs: set[tuple[str, str, tuple[str, ...]]] = set()
    for module_name in modules:
        module = sys.modules.get(module_name)
        spec = getattr(module, "__spec__", None)
        if spec is None:
            try:
                spec = importlib.util.find_spec(module_name)
            except (AttributeError, ImportError, ValueError):
                spec = None
        location = getattr(module, "__file__", None)
        if not isinstance(location, str):
            location = None if spec is None else spec.origin
        search_locations = tuple(
            os.path.abspath(path)
            for path in (() if spec is None or spec.submodule_search_locations is None else spec.submodule_search_locations)
            if isinstance(path, str)
        )
        external_search_locations = tuple(
            path for path in search_locations if not _path_is_within(path, runtime_roots)
        )
        if not isinstance(location, str) or location in {"built-in", "frozen"}:
            if external_search_locations:
                exact_specs.add((module_name, "", external_search_locations))
            continue
        resolved_location = os.path.realpath(location)
        if _path_is_within(resolved_location, runtime_roots):
            continue
        exact_specs.add((module_name, os.path.abspath(location), external_search_locations))
        candidates = [location]
        if location.endswith(".py"):
            candidates.append(importlib.util.cache_from_source(location))
        for candidate in candidates:
            if os.path.isfile(candidate):
                paths.update((os.path.abspath(candidate), os.path.realpath(candidate)))
    for module_name in payload_modules:
        _add_package_module_allowlist(module_name, runtime_roots, paths, exact_specs)
        _add_top_level_sibling_allowlist(module_name, runtime_roots, paths, exact_specs)
    return tuple(sorted(paths)), tuple(sorted(exact_specs))


class _ExactModuleFinder:
    def __init__(self, module_specs: Sequence[tuple[str, str, tuple[str, ...]]]) -> None:
        self._module_specs = {
            name: (location, search_locations)
            for name, location, search_locations in module_specs
        }

    def find_spec(self, fullname: str, path: Any = None, target: Any = None) -> Any:
        module_spec = self._module_specs.get(fullname)
        if module_spec is None:
            return None
        location, search_locations = module_spec
        if not location:
            spec = importlib.machinery.ModuleSpec(fullname, loader=None, is_package=True)
            spec.submodule_search_locations = list(search_locations)
            return spec
        return importlib.util.spec_from_file_location(
            fullname,
            location,
            submodule_search_locations=list(search_locations) or None,
        )


def _restore_tool_state(value: Any) -> Any:
    if not isinstance(value, dict) or not any(type(name) is str and name.startswith("$") for name in value):
        return value
    if set(value) == {"$path"}:
        return Path(value["$path"])
    if set(value) == {"$bytes"}:
        return bytes.fromhex(value["$bytes"])
    if set(value) == {"$closed_socket"} and value["$closed_socket"] is True:
        return socket.socket.__new__(socket.socket)
    if set(value) == {"$object_sentinel"} and value["$object_sentinel"] is True:
        return object()
    if set(value) == {"$decimal"}:
        return Decimal(value["$decimal"])
    if set(value) == {"$datetime"}:
        envelope = value["$datetime"]
        restored = datetime.datetime.fromisoformat(envelope["value"])
        return restored.replace(tzinfo=_restore_fixed_timezone(envelope["timezone"]), fold=envelope["fold"])
    if set(value) == {"$date"}:
        return datetime.date.fromisoformat(value["$date"])
    if set(value) == {"$time"}:
        envelope = value["$time"]
        restored = datetime.time.fromisoformat(envelope["value"])
        return restored.replace(tzinfo=_restore_fixed_timezone(envelope["timezone"]), fold=envelope["fold"])
    if set(value) == {"$timedelta"}:
        return datetime.timedelta(**value["$timedelta"])
    if set(value) == {"$timezone"}:
        return _restore_fixed_timezone(value["$timezone"])
    if set(value) == {"$uuid"}:
        envelope = value["$uuid"]
        return uuid.UUID(hex=envelope["hex"], is_safe=uuid.SafeUUID[envelope["is_safe"]])
    if set(value) == {"$enum"}:
        envelope = value["$enum"]
        enum_type: Any = importlib.import_module(envelope["module"])
        for component in envelope["qualname"].split("."):
            enum_type = getattr(enum_type, component)
        if set(envelope) == {"module", "qualname", "name"}:
            return enum_type[envelope["name"]]
        if set(envelope) == {"module", "qualname", "value"} and issubclass(enum_type, enum.Flag) and type(envelope["value"]) is int:
            return enum_type(envelope["value"])
        raise ReplayRunError("isolated capability enum state envelope is invalid")
    if set(value) == {"$mapping"}:
        return {name: _restore_tool_state(item) for name, item in value["$mapping"].items()}
    if set(value) == {"$sequence", "$tuple"}:
        items = [_restore_tool_state(item) for item in value["$sequence"]]
        return tuple(items) if value["$tuple"] else items
    if set(value) == {"$set", "$frozen"}:
        items = {_restore_tool_state(item) for item in value["$set"]}
        return frozenset(items) if value["$frozen"] else items
    if set(value) == {"$partial"}:
        partial = value["$partial"]
        return functools.partial(
            _restore_tool_state(partial["function"]),
            *_restore_tool_state(partial["args"]),
            **_restore_tool_state(partial["keywords"]),
        )
    if set(value) == {"$bound_method"}:
        bound = value["$bound_method"]
        return getattr(_restore_tool_state(bound["target"]), bound["name"])
    if set(value) == {"$callable"}:
        target: Any = importlib.import_module(value["$callable"]["module"])
        for component in value["$callable"]["qualname"].split("."):
            target = getattr(target, component)
        return target
    if set(value) == {"$object"}:
        envelope = value["$object"]
        object_type: Any = importlib.import_module(envelope["module"])
        for component in envelope["qualname"].split("."):
            object_type = getattr(object_type, component)
        restored = object_type.__new__(object_type)
        for name, item in envelope["state"].items():
            object.__setattr__(restored, name, _restore_tool_state(item))
        return restored
    raise ReplayRunError("isolated capability executor state envelope is invalid")
def _exec_tool_worker_bootstrap(
    command_fd: int,
    result_fd: int,
    payload_fd: int,
    workspace_descriptor: int,
    capability_kind: str,
    allow_network: bool,
    module_read_paths: Sequence[str],
    module_specs: Sequence[tuple[str, str, tuple[str, ...]]],
) -> None:
    from multiprocessing.connection import Connection
    command_recv = Connection(command_fd, readable=True, writable=False)
    result_send = Connection(result_fd, readable=False, writable=True)
    _redirect_worker_stdio()
    os.environ.clear()
    _close_inherited_descriptors({command_fd, result_fd, payload_fd, workspace_descriptor})
    workspace = str(_staging_descriptor_path(workspace_descriptor))
    _enforce_worker_sandbox(
        workspace,
        allow_network=allow_network,
        allow_process=capability_kind == "host",
        allow_workspace_read=capability_kind in {"host", "tool"},
        allow_write=capability_kind in {"host", "tool"},
        module_read_paths=module_read_paths,
    )
    runtime_search_roots = tuple(
        os.path.realpath(path) for path in module_read_paths if os.path.isdir(path)
    )
    sys.path[:] = [
        path
        for path in sys.path
        if isinstance(path, str) and path and _path_is_within(path, runtime_search_roots)
    ]
    sys.meta_path.insert(0, _ExactModuleFinder(module_specs))
    os.fchdir(workspace_descriptor)
    payload = bytearray()
    while True:
        chunk = os.read(payload_fd, 65536)
        if not chunk:
            break
        payload.extend(chunk)
    os.close(payload_fd)
    envelope = _decode_canonical_json(bytes(payload))
    if not isinstance(envelope, dict) or envelope.get("kind") not in {"symbol", "object"} or not isinstance(envelope.get("module"), str) or not isinstance(envelope.get("qualname"), str):
        raise ReplayRunError("isolated capability executor envelope is invalid")
    executor: Any = importlib.import_module(envelope["module"])
    for component in envelope["qualname"].split("."):
        executor = getattr(executor, component)
    if envelope["kind"] == "object":
        if not isinstance(envelope.get("state"), dict):
            raise ReplayRunError("isolated capability executor state is invalid")
        executor_type = executor
        executor = executor_type.__new__(executor_type)
        for name, value in envelope["state"].items():
            object.__setattr__(executor, name, _restore_tool_state(value))
    if capability_kind not in {"tool", "host", "policy", "provider"}:
        raise ReplayRunError("isolated capability kind is invalid")
    if capability_kind == "policy" and not callable(executor) or capability_kind in {"tool", "host"} and not callable(getattr(executor, "execute", None)) or capability_kind == "provider" and not isinstance(executor, _ProviderCapability):
        raise ReplayRunError("isolated capability does not expose its required entrypoint")
    provider_client = None
    def prepare_provider_environment() -> None:
        os.environ.update(executor.environment)
        for reserved_name in ("KC_PROVIDER_LOG_DIR", "KC_PROVIDER_WORKSPACE", "KC_PROVIDER_SESSION_ID"):
            os.environ.pop(reserved_name, None)
        provider_dump_logger.log_dir = None
        provider_dump_logger.workspace_override = None
        provider_dump_logger.session_override = None
        provider_dump_logger.enabled = False
    if capability_kind == "provider":
        prepare_provider_environment()
        try:
            client_factory = getattr(executor.provider, "replay_worker_client", None)
            provider_client = client_factory(executor.client_spec) if executor.client_spec is not None else executor.client
        finally:
            os.environ.clear()
    result_send.send_bytes(_worker_envelope("ready"))
    while True:
        try:
            command = _decode_canonical_json(command_recv.recv_bytes())
        except EOFError:
            return
        try:
            if not isinstance(command, dict):
                raise ReplayRunError("isolated capability command is invalid")
            os.fchdir(workspace_descriptor)
            if capability_kind == "provider" and set(command) == {"messages", "tools"}:
                prepare_provider_environment()
                try:
                    identity_method = getattr(executor.provider, "replay_client_identity", None)
                    if not callable(identity_method) or json.loads(canonical_json(identity_method(provider_client, executor.secret_bindings)).decode("utf-8")) != executor.expected_client_binding:
                        raise ReplayRunError("provider client state drifted before invocation")
                    result = executor.provider.invoke(client=provider_client, model=executor.model, messages=command["messages"], tools=command["tools"], stream=False, context=executor.context)
                    if json.loads(canonical_json(identity_method(provider_client, executor.secret_bindings)).decode("utf-8")) != executor.expected_client_binding:
                        raise ReplayRunError("provider client state drifted during invocation")
                    value = provider_result_evidence(result)
                    metadata_setter = getattr(getattr(executor.context, "session_state", None), "set_provider_metadata", None)
                    if callable(metadata_setter):
                        for metadata_name, metadata_value in value["metadata"].items():
                            metadata_setter(metadata_name, metadata_value)
                finally:
                    os.environ.clear()
            elif capability_kind == "policy" and set(command) == {"name", "arguments"} and isinstance(command["name"], str) and isinstance(command["arguments"], dict):
                value = executor(command["name"], command["arguments"])
            elif capability_kind == "tool" and set(command) == {"arguments"} and isinstance(command["arguments"], dict):
                value = executor.execute(command["arguments"])
            elif capability_kind == "host" and isinstance(command.get("command"), str) and isinstance(command.get("cwd"), str):
                host_arguments = dict(command)
                command_text = host_arguments.pop("command")
                command_cwd = host_arguments.pop("cwd")
                value = executor.execute(command_text, cwd=command_cwd, **host_arguments)
            else:
                raise ReplayRunError("isolated capability command is invalid")
        except BaseException as exc:
            try:
                result_send.send_bytes(_worker_envelope("error", type(exc).__name__, kind=capability_kind))
            except BaseException:
                return
            continue
        try:
            result_send.send_bytes(_worker_envelope("ok", value, kind=capability_kind))
        except BaseException:
            try:
                result_send.send_bytes(_worker_envelope("invalid", None, kind=capability_kind))
            except BaseException:
                return


class _ExecToolWorker:
    def __init__(self, executor: Any, workspace_descriptor: int, *, capability_kind: str = "tool", payload: bytes | None = None, startup_timeout_ms: int = 2_000, startup_timeout_code: str = "replay.tool_timeout", startup_timeout_detail: str = "capability worker startup exceeded its deadline", cancelled: Callable[[], bool] | None = None) -> None:
        from multiprocessing.connection import Connection
        self._capability_kind = capability_kind
        payload = _tool_executor_envelope(executor, capability_kind) if payload is None else payload
        allow_network = capability_kind == "provider" or capability_kind == "host" and _host_network_access(executor)
        module_read_paths, module_specs = _executor_module_allowlist(payload)
        command_read, command_write = os.pipe()
        result_read, result_write = os.pipe()
        payload_read, payload_write = os.pipe()
        self._terminated = False
        self._command_send = Connection(command_write, readable=False, writable=True)
        self._result_recv = Connection(result_read, readable=True, writable=False)
        module_paths = [os.path.abspath(path or os.getcwd()) for path in sys.path if isinstance(path, str)]
        environment = {"PATH": os.defpath, "PYTHONPATH": os.pathsep.join(dict.fromkeys(module_paths))}
        bootstrap = (
            "from breadboard.product.evidence.replay_runner import _exec_tool_worker_bootstrap as b;"
            f"b({command_read},{result_write},{payload_read},{workspace_descriptor},{capability_kind!r},{allow_network!r},{module_read_paths!r},{module_specs!r})"
        )
        try:
            self._process = subprocess.Popen(
                [sys.executable, "-S", "-c", bootstrap],
                close_fds=True,
                pass_fds=(command_read, result_write, payload_read, workspace_descriptor),
                env=environment,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
        except BaseException:
            for descriptor in (command_read, result_write, payload_read, payload_write):
                os.close(descriptor)
            self._command_send.close()
            self._result_recv.close()
            raise
        self._descendants: set[int] = set()
        self._closed = False
        os.close(command_read)
        os.close(result_write)
        os.close(payload_read)
        try:
            remaining = memoryview(payload)
            while remaining:
                written = os.write(payload_write, remaining)
                if written < 1:
                    raise OSError("short isolated capability payload write")
                remaining = remaining[written:]
        except BaseException:
            os.close(payload_write)
            self.close()
            raise
        else:
            os.close(payload_write)
        startup_deadline = time.monotonic() + max(1, startup_timeout_ms) / 1_000
        while not self._result_recv.poll(max(0, min(0.01, startup_deadline - time.monotonic()))):
            if cancelled is not None and cancelled():
                self.close()
                raise _RuntimeFailure("cancelled", "replay.cancelled", "replay was cancelled")
            if time.monotonic() >= startup_deadline:
                self.close()
                raise _RuntimeFailure("timed_out", startup_timeout_code, startup_timeout_detail)
        try:
            status, _, _ = _decode_worker_envelope(self._result_recv.recv_bytes())
        except BaseException as exc:
            self.close()
            raise ReplayRunError("isolated capability worker failed during startup") from exc
        if status != "ready":
            self.close()
            raise ReplayRunError("isolated capability worker failed during startup")
    @staticmethod
    def _children(pid: int) -> set[int]:
        try:
            result = subprocess.run(["/usr/bin/pgrep", "-P", str(pid)], check=False, capture_output=True, text=True, timeout=0.2)
        except (FileNotFoundError, subprocess.SubprocessError):
            return set()
        return {int(value) for value in result.stdout.split() if value.isdigit()}

    def _refresh_descendants(self) -> None:
        pending = [self._process.pid, *self._descendants]
        seen = set(pending)
        while pending:
            for child in self._children(pending.pop()):
                if child not in seen:
                    seen.add(child)
                    pending.append(child)
        seen.discard(self._process.pid)
        self._descendants.update(seen)

    def _signal_descendants(self, signum: int) -> None:
        for pid in self._descendants:
            try:
                os.kill(pid, signum)
            except ProcessLookupError:
                pass


    def invoke(self, command: Mapping[str, Any], *, timeout_ms: int, timeout_code: str, timeout_detail: str, cancelled: Callable[[], bool] | None, cancellation_grace_ms: int) -> Any:
        if cancelled is not None and cancelled():
            raise _RuntimeFailure("cancelled", "replay.cancelled", "replay was cancelled")
        if self._process.poll() is not None:
            raise _RemoteCallError("isolated capability worker is unavailable")
        self._command_send.send_bytes(canonical_json(dict(command)))
        deadline = time.monotonic() + timeout_ms / 1000
        cancellation_deadline: float | None = None
        next_descendant_scan = 0.0
        while True:
            now = time.monotonic()
            if now >= deadline:
                self._terminate()
                raise _RuntimeFailure("timed_out", timeout_code, timeout_detail)
            if self._result_recv.poll(min(0.01, deadline - now)):
                if time.monotonic() >= deadline:
                    self._terminate()
                    raise _RuntimeFailure("timed_out", timeout_code, timeout_detail)
                self._refresh_descendants()
                status, _, value = _decode_worker_envelope(self._result_recv.recv_bytes())
                if status == "ok":
                    return _provider_result_from_evidence(value) if self._capability_kind == "provider" else value
                if status == "invalid":
                    return _InvalidWorkerValue()
                if cancelled is not None and cancelled():
                    raise _RuntimeFailure("cancelled", "replay.cancelled", "replay was cancelled")
                raise _RemoteCallError(f"isolated capability execution failed with {value}")
            now = time.monotonic()
            if now >= next_descendant_scan:
                self._refresh_descendants()
                next_descendant_scan = now + 0.1
            if cancelled is not None and cancelled():
                cancellation_deadline = cancellation_deadline or now + cancellation_grace_ms / 1000
                if now >= cancellation_deadline:
                    self._terminate()
                    raise _RuntimeFailure("cancelled", "replay.cancellation_grace_exceeded", "replay cancellation exceeded its grace period")
            elif self._process.poll() is not None:
                raise _RemoteCallError("isolated capability worker exited without a result")
            if now >= deadline:
                self._terminate()
                raise _RuntimeFailure("timed_out", timeout_code, timeout_detail)

    def _terminate(self) -> None:
        if self._terminated:
            return
        self._refresh_descendants()
        try:
            os.killpg(self._process.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        self._signal_descendants(signal.SIGTERM)
        try:
            self._process.wait(timeout=0.25)
        except subprocess.TimeoutExpired:
            pass
        self._refresh_descendants()
        try:
            os.killpg(self._process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        self._signal_descendants(signal.SIGKILL)
        try:
            self._process.wait(timeout=1)
        except subprocess.TimeoutExpired as exc:
            raise ReplayRunError("isolated capability worker could not be terminated") from exc
        for _ in range(10):
            survivors: list[int] = []
            for pid in self._descendants:
                try:
                    os.kill(pid, 0)
                except ProcessLookupError:
                    continue
                survivors.append(pid)
            if not survivors:
                self._terminated = True
                return
            for pid in survivors:
                try:
                    os.kill(pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
            time.sleep(0.01)
        raise ReplayRunError("isolated capability descendants survived termination")

    def close(self) -> None:
        if self._closed:
            return
        self._terminate()
        self._command_send.close()
        self._result_recv.close()
        self._closed = True





def _digest(content: bytes) -> str:
    return "sha256:" + hashlib.sha256(content).hexdigest()


def _initial_file_rows(files: Mapping[str, bytes | str]) -> list[dict[str, Any]]:
    if not isinstance(files, Mapping):
        raise ReplayRunError("initial_files must be a mapping")
    if any(not isinstance(name, str) for name in files):
        raise ReplayRunError("initial workspace paths must be strings")
    rows: list[dict[str, Any]] = []
    for name, value in sorted(files.items()):
        path = Path(name)
        if not name or path.is_absolute() or path.as_posix() != name or ".." in path.parts or "\\" in name or any(part in ("", ".") for part in path.parts):
            raise ReplayRunError(f"unsafe initial workspace path: {name!r}")
        content = value.encode("utf-8") if isinstance(value, str) else value
        if not isinstance(content, bytes):
            raise ReplayRunError(f"initial workspace content must be bytes or text: {name}")
        rows.append({"path": path.as_posix(), "sha256": _digest(content), "size_bytes": len(content)})
    return rows


def _materialize_initial(parent_descriptor: int, name: str, files: Mapping[str, bytes | str]) -> int:
    if os.open not in os.supports_dir_fd or os.mkdir not in os.supports_dir_fd or not getattr(os, "O_NOFOLLOW", 0) or not getattr(os, "O_DIRECTORY", 0):
        raise ReplayRunError("secure anchored workspace materialization is unavailable on this platform")
    root_descriptor: int | None = None
    try:
        os.mkdir(name, mode=0o700, dir_fd=parent_descriptor)
        root_descriptor = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent_descriptor)
        for row in _initial_file_rows(files):
            content = files[row["path"]]
            payload = content.encode("utf-8") if isinstance(content, str) else content
            parent_fd = os.dup(root_descriptor)
            try:
                parts = Path(row["path"]).parts
                for directory in parts[:-1]:
                    try:
                        os.mkdir(directory, mode=0o700, dir_fd=parent_fd)
                    except FileExistsError:
                        pass
                    child_fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent_fd)
                    os.close(parent_fd)
                    parent_fd = child_fd
                file_fd = os.open(parts[-1], os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=parent_fd)
                try:
                    remaining = memoryview(payload)
                    while remaining:
                        written = os.write(file_fd, remaining)
                        if written < 1:
                            raise OSError("short workspace write")
                        remaining = remaining[written:]
                finally:
                    os.close(file_fd)
            except OSError as exc:
                raise ReplayRunError(f"failed to materialize initial workspace path: {row['path']}") from exc
            finally:
                os.close(parent_fd)
        return root_descriptor
    except BaseException:
        if root_descriptor is not None:
            os.close(root_descriptor)
        raise
class _AnchoredJsonlEventSink:
    def __init__(self, parent_descriptor: int, name: str) -> None:
        self._parent_descriptor = parent_descriptor
        self._name = name

    def append(self, event: object) -> None:
        payload = (json.dumps(event.as_dict(), allow_nan=False, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")  # type: ignore[attr-defined]
        descriptor = os.open(self._name, os.O_WRONLY | os.O_APPEND | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0), 0o600, dir_fd=self._parent_descriptor)
        try:
            remaining = memoryview(payload)
            while remaining:
                written = os.write(descriptor, remaining)
                if written < 1:
                    raise OSError("short replay event write")
                remaining = remaining[written:]
            os.fsync(descriptor)
            os.fsync(self._parent_descriptor)
        finally:
            os.close(descriptor)


def _remove_entry_at(parent_descriptor: int, name: str) -> None:
    metadata = os.stat(name, dir_fd=parent_descriptor, follow_symlinks=False)
    if not stat.S_ISDIR(metadata.st_mode):
        os.unlink(name, dir_fd=parent_descriptor)
        return
    descriptor = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent_descriptor)
    opened = os.fstat(descriptor)
    if (opened.st_dev, opened.st_ino) != (metadata.st_dev, metadata.st_ino):
        os.close(descriptor)
        raise ReplayRunError("staging entry changed during anchored cleanup")
    stack: list[tuple[int, str, int]] = [(parent_descriptor, name, descriptor)]
    try:
        while stack:
            entry_parent, entry_name, entry_descriptor = stack[-1]
            children = os.listdir(entry_descriptor)
            if children:
                child_name = children[0]
                child_metadata = os.stat(child_name, dir_fd=entry_descriptor, follow_symlinks=False)
                if stat.S_ISDIR(child_metadata.st_mode):
                    child_descriptor = os.open(child_name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=entry_descriptor)
                    child_opened = os.fstat(child_descriptor)
                    if (child_opened.st_dev, child_opened.st_ino) != (child_metadata.st_dev, child_metadata.st_ino):
                        os.close(child_descriptor)
                        raise ReplayRunError("staging entry changed during anchored cleanup")
                    stack.append((entry_descriptor, child_name, child_descriptor))
                else:
                    os.unlink(child_name, dir_fd=entry_descriptor)
                continue
            os.fsync(entry_descriptor)
            os.close(entry_descriptor)
            stack.pop()
            os.rmdir(entry_name, dir_fd=entry_parent)
    except BaseException:
        for _, _, open_descriptor in reversed(stack):
            try:
                os.close(open_descriptor)
            except OSError:
                pass
        raise






def _snapshot(root: Path, expected_identity: tuple[int, int], source_descriptor: int | None = None) -> dict[str, Any]:
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
    root_descriptor: int | None = None
    rows: list[dict[str, Any]] = []

    def walk(directory_descriptor: int, prefix: tuple[str, ...]) -> None:
        for name in sorted(os.listdir(directory_descriptor)):
            metadata = os.stat(name, dir_fd=directory_descriptor, follow_symlinks=False)
            relative = "/".join((*prefix, name))
            if stat.S_ISLNK(metadata.st_mode):
                raise _RuntimeFailure("host_failed", "replay.workspace_symlink", f"workspace contains a symlink: {relative}")
            if stat.S_ISDIR(metadata.st_mode):
                child_descriptor = os.open(name, flags, dir_fd=directory_descriptor)
                try:
                    opened = os.fstat(child_descriptor)
                    if (opened.st_dev, opened.st_ino) != (metadata.st_dev, metadata.st_ino):
                        raise _RuntimeFailure("host_failed", "replay.workspace_replaced", "workspace directory changed during snapshot")
                    walk(child_descriptor, (*prefix, name))
                finally:
                    os.close(child_descriptor)
                continue
            if not stat.S_ISREG(metadata.st_mode):
                raise _RuntimeFailure("host_failed", "replay.workspace_special_file", f"workspace contains an unsupported special file: {relative}")
            if metadata.st_nlink != 1:
                raise _RuntimeFailure("host_failed", "replay.workspace_hardlink", f"workspace contains a hard-linked file: {relative}")
            file_descriptor = os.open(name, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0) | getattr(os, "O_NOCTTY", 0), dir_fd=directory_descriptor)
            try:
                opened = os.fstat(file_descriptor)
                if (opened.st_dev, opened.st_ino) != (metadata.st_dev, metadata.st_ino) or not stat.S_ISREG(opened.st_mode) or opened.st_nlink != 1:
                    raise _RuntimeFailure("host_failed", "replay.workspace_replaced", "workspace file changed during snapshot")
                digest = hashlib.sha256()
                size_bytes = 0
                while True:
                    chunk = os.read(file_descriptor, 1024 * 1024)
                    if not chunk:
                        break
                    digest.update(chunk)
                    size_bytes += len(chunk)
                completed = os.fstat(file_descriptor)
                stable_fields = ("st_dev", "st_ino", "st_size", "st_mtime_ns", "st_ctime_ns", "st_nlink")
                if any(getattr(opened, field) != getattr(completed, field) for field in stable_fields):
                    raise _RuntimeFailure("host_failed", "replay.workspace_replaced", "workspace file changed during snapshot")
                rows.append({"path": relative, "sha256": "sha256:" + digest.hexdigest(), "size_bytes": size_bytes})
            finally:
                os.close(file_descriptor)

    try:
        root_descriptor = os.dup(source_descriptor) if source_descriptor is not None else os.open(root, flags)
        root_metadata = os.fstat(root_descriptor)
        if (root_metadata.st_dev, root_metadata.st_ino) != expected_identity:
            raise _RuntimeFailure("host_failed", "replay.workspace_replaced", "fresh workspace root identity changed during replay")
        walk(root_descriptor, ())
    except _RuntimeFailure:
        raise
    except OSError as exc:
        raise _RuntimeFailure("host_failed", "replay.workspace_replaced", "workspace changed during snapshot") from exc
    except RecursionError as exc:
        raise _RuntimeFailure("host_failed", "replay.workspace_depth", "workspace nesting exceeded the snapshot limit") from exc
    finally:
        if root_descriptor is not None:
            os.close(root_descriptor)
    return {"schema_version": "bb.replay_workspace_snapshot.v1", "files": rows, "snapshot_sha256": sha256_json(rows)}


def _workspace_diff(before: Mapping[str, Any], after: Mapping[str, Any]) -> dict[str, Any]:
    left = {row["path"]: row for row in before["files"]}; right = {row["path"]: row for row in after["files"]}
    added = sorted(set(right) - set(left)); removed = sorted(set(left) - set(right)); changed = sorted(path for path in set(left) & set(right) if left[path] != right[path])
    return {"schema_version": "bb.replay_workspace_diff.v1", "added": added, "removed": removed, "changed": changed}


@dataclass
class _UsageAccountant:
    token_limit: int
    cost_limit: Decimal
    tokens: int = 0
    cost: Decimal = Decimal(0)
    currency: str | None = None

    @classmethod
    def from_budgets(cls, budgets: Mapping[str, Any]) -> "_UsageAccountant":
        try:
            cost_limit = Decimal(str(budgets["cost"]))
        except (InvalidOperation, KeyError, ValueError) as exc:
            raise ReplayRunError("cost budget is not exactly representable") from exc
        if not cost_limit.is_finite() or cost_limit <= 0:
            raise ReplayRunError("cost budget must be finite and positive")
        return cls(token_limit=budgets["tokens"], cost_limit=cost_limit)

    def add(self, usage: Mapping[str, Any]) -> bool:
        tokens = usage.get("total_tokens")
        currency = usage.get("cost_currency")
        try:
            cost = Decimal(str(usage.get("cost_amount")))
        except (InvalidOperation, ValueError) as exc:
            raise ReplayRunError("provider cost is not exactly representable") from exc
        if type(tokens) is not int or tokens < 0 or not isinstance(currency, str) or not currency or not cost.is_finite() or cost < 0:
            raise ReplayRunError("provider usage cannot be accounted exactly")
        if self.currency is None:
            self.currency = currency
        elif self.currency != currency:
            raise _RuntimeFailure("provider_failed", "replay.cost_currency_mismatch", "provider usage changed cost currency during replay")
        self.tokens += tokens
        self.cost += cost
        return self.tokens > self.token_limit or self.cost > self.cost_limit


def _redact(value: Any, *, secrets: Sequence[str], workspace: Path, counter: list[int], seen: frozenset[int] = frozenset()) -> Any:
    secret_values = tuple(sorted({secret for secret in secrets if isinstance(secret, str) and secret}, key=lambda secret: (-len(secret), secret)))
    if value is None or type(value) in (bool, int, float):
        return value
    if isinstance(value, str):
        result = value.replace(str(workspace), "<workspace>")
        counter[0] += result != value
        for secret in secret_values:
            if secret in result:
                result = result.replace(secret, "<redacted>"); counter[0] += 1
        return result
    if isinstance(value, Mapping):
        if id(value) in seen:
            raise ReplayRunError("cannot redact cyclic evidence")
        result: dict[str, Any] = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise ReplayRunError("evidence keys must be strings")
            redacted_key = _redact(key, secrets=secret_values, workspace=workspace, counter=counter)
            if redacted_key in result:
                raise ReplayRunError("redacted evidence keys collide")
            normalized_key = key.lower().replace("-", "_")
            compact_key = "".join(character for character in normalized_key if character.isalnum())
            sensitive_names = {"apikey", "token", "password", "passphrase", "secret", "authorization", "proxyauthorization", "bearer", "cookie", "setcookie", "credential", "credentials", "privatekey", "accesskey", "secretkey", "sessionid", "clientsecret", "signingkey", "encryptionkey", "sshkey"}
            if compact_key in sensitive_names or any(compact_key.endswith(name) for name in sensitive_names):
                result[redacted_key] = "<redacted>"; counter[0] += 1
            else:
                result[redacted_key] = _redact(item, secrets=secret_values, workspace=workspace, counter=counter, seen=seen | {id(value)})
        return result
    if isinstance(value, (list, tuple)):
        if id(value) in seen:
            raise ReplayRunError("cannot redact cyclic evidence")
        return [_redact(item, secrets=secret_values, workspace=workspace, counter=counter, seen=seen | {id(value)}) for item in value]
    raise ReplayRunError(f"unsupported evidence value type: {type(value).__name__}")


@dataclass
class _ProviderSessionSnapshot:
    metadata: dict[str, Any]

    def get_provider_metadata(self, key: str, default: Any = None) -> Any:
        return self.metadata.get(key, default)

    def set_provider_metadata(self, key: str, value: Any) -> None:
        self.metadata[key] = value


def _provider_context_snapshot(context: Any) -> ProviderRuntimeContext:
    agent_config = getattr(context, "agent_config", {})
    extra = getattr(context, "extra", {})
    if not isinstance(agent_config, Mapping) or not isinstance(extra, Mapping):
        raise ReplayRunError("provider context configuration must be a mapping")
    try:
        safe_agent_config = json.loads(canonical_json(agent_config))
        safe_extra_source = {name: extra[name] for name in ("phase16_phase_label", "turn_index", "responses_extra") if name in extra}
        safe_extra = json.loads(canonical_json(safe_extra_source))
        session_state = getattr(context, "session_state", None)
        metadata_source: dict[str, Any] = {}
        snapshot = getattr(session_state, "provider_metadata_snapshot", None)
        if callable(snapshot):
            candidate = snapshot()
            if not isinstance(candidate, Mapping):
                raise ReplayRunError("provider metadata snapshot must be a mapping")
            metadata_source.update(candidate)
        else:
            getter = getattr(session_state, "get_provider_metadata", None)
            if callable(getter):
                for name in ("conversation_id", "previous_response_id"):
                    value = getter(name)
                    if value is not None:
                        metadata_source[name] = value
        safe_metadata = json.loads(canonical_json(metadata_source))
    except (TypeError, ValueError) as exc:
        raise ReplayRunError("provider context must contain only canonical provider data") from exc
    return ProviderRuntimeContext(session_state=_ProviderSessionSnapshot(safe_metadata), agent_config=safe_agent_config, stream=False, extra=safe_extra)


def _provider_context_request_payload(context: ProviderRuntimeContext) -> dict[str, Any]:
    return json.loads(canonical_json({
        "agent_config": context.agent_config,
        "extra": context.extra,
        "session_metadata": context.session_state.metadata,
    }))






def _host_identity(host: Any) -> str:
    method = getattr(host, "workspace", None)
    if not callable(method):
        method = getattr(host, "get_workspace", None)
    if not callable(method):
        raise _RuntimeFailure("host_failed", "replay.host_unavailable", "host does not expose a workspace identity")
    value = method()
    if not isinstance(value, str) or not value:
        raise _RuntimeFailure("host_failed", "replay.host_unavailable", "host returned no workspace identity")
    return value


def _host_containment_identity(host: Any) -> dict[str, Any]:
    method = getattr(host, "replay_process_containment", None)
    if not callable(method):
        raise ReplayRunError("host does not attest detached-descendant containment")
    value = json.loads(canonical_json(method()).decode("utf-8"))
    if not isinstance(value, dict) or value.get("detached_descendants") != "contained":
        raise ReplayRunError("host detached-descendant containment attestation is invalid")
    return value


def _provider_identity(provider: Any) -> tuple[str, str, str]:
    descriptor = getattr(provider, "port_descriptor", None) or getattr(provider, "descriptor", None)
    provider_id = getattr(provider, "provider_id", None) or getattr(descriptor, "provider_id", None)
    runtime_id = getattr(provider, "runtime_id", None) or getattr(descriptor, "runtime_id", None)
    endpoint = getattr(descriptor, "default_api_variant", None) or "completion"
    if not all(isinstance(value, str) and value for value in (provider_id, runtime_id, endpoint)):
        raise _RuntimeFailure("provider_failed", "replay.provider_identity", "provider does not expose a stable runtime identity")
    return provider_id, runtime_id, endpoint

def _validate_replay_provider_result(result: Any, request_id: str) -> ProviderResult:
    if not isinstance(result, ProviderResult) or not isinstance(result.messages, list) or not result.messages:
        raise ReplayRunError("provider must return at least one normalized message")
    if not isinstance(result.metadata, Mapping):
        raise ReplayRunError("provider metadata must be a mapping")
    used_call_ids: set[str] = set()
    calls_without_ids: list[tuple[int, int, ProviderToolCall]] = []
    for message_index, message in enumerate(result.messages):
        if not isinstance(message, ProviderMessage) or message.role != "assistant":
            raise ReplayRunError(f"provider message {message_index} must have assistant role")
        if message.content is not None and not isinstance(message.content, str):
            raise ReplayRunError(f"provider message {message_index} has invalid content")
        if not isinstance(message.tool_calls, list):
            raise ReplayRunError(f"provider message {message_index} has invalid tool_calls")
        if message.finish_reason is not None and (not isinstance(message.finish_reason, str) or not message.finish_reason):
            raise ReplayRunError(f"provider message {message_index} has an invalid finish_reason")
        if message.index is not None and (type(message.index) is not int or message.index < 0):
            raise ReplayRunError(f"provider message {message_index} has an invalid index")
        for call_index, call in enumerate(message.tool_calls):
            if not isinstance(call, ProviderToolCall):
                raise ReplayRunError(f"provider tool call {message_index}:{call_index} is invalid")
            valid_id = call.id is None or isinstance(call.id, str) and bool(call.id)
            if call.type != "function" or not valid_id or not all(isinstance(getattr(call, name, None), str) and getattr(call, name) for name in ("name", "arguments")):
                raise ReplayRunError(f"provider tool call {message_index}:{call_index} is invalid")
            if call.id is None:
                calls_without_ids.append((message_index, call_index, call))
            elif call.id in used_call_ids:
                raise ReplayRunError(f"provider tool call {message_index}:{call_index} has a duplicate id")
            else:
                used_call_ids.add(call.id)
    for message_index, call_index, call in calls_without_ids:
        candidate = f"{request_id}:message:{message_index + 1}:call:{call_index + 1}"
        suffix = 1
        while candidate in used_call_ids:
            candidate = f"{request_id}:message:{message_index + 1}:call:{call_index + 1}:{suffix}"
            suffix += 1
        call.id = candidate
        used_call_ids.add(candidate)
    return result

def _declared_tool_names(tool_schemas: Sequence[Mapping[str, Any]]) -> frozenset[str]:
    names: list[str] = []
    for index, schema in enumerate(tool_schemas):
        function = schema.get("function") if isinstance(schema, Mapping) else None
        name = function.get("name") if isinstance(function, Mapping) else None
        if not isinstance(schema, Mapping) or schema.get("type") != "function" or not isinstance(name, str) or not name:
            raise ReplayRunError(f"tool_schemas[{index}] must declare a populated function name")
        names.append(name)
    if len(names) != len(set(names)):
        raise ReplayRunError("tool_schemas must declare unique function names")
    return frozenset(names)
def _provider_tool_wire_schemas(tool_schemas: Sequence[Mapping[str, Any]]) -> tuple[list[dict[str, Any]], dict[str, str]]:
    wire_schemas = json.loads(canonical_json(list(tool_schemas)))
    used_names: set[str] = set()
    reverse_aliases: dict[str, str] = {}
    for index, schema in enumerate(wire_schemas):
        function = schema["function"]
        canonical_name = function["name"]
        candidate = canonical_name
        if re.fullmatch(r"[A-Za-z0-9_-]{1,64}", candidate) is None:
            candidate = re.sub(r"[^A-Za-z0-9_-]", "_", canonical_name)[:48] or "tool"
        if candidate in used_names:
            suffix = hashlib.sha256(f"{canonical_name}:{index}".encode("utf-8")).hexdigest()[:8]
            candidate = f"{candidate[:55]}_{suffix}"
            counter = 1
            while candidate in used_names:
                suffix = hashlib.sha256(f"{canonical_name}:{index}:{counter}".encode("utf-8")).hexdigest()[:8]
                candidate = f"{candidate[:55]}_{suffix}"
                counter += 1
        used_names.add(candidate)
        function["name"] = candidate
        if candidate != canonical_name:
            reverse_aliases[candidate] = canonical_name
    return wire_schemas, reverse_aliases
def _provider_wire_messages(messages: Sequence[Mapping[str, Any]], reverse_aliases: Mapping[str, str]) -> list[dict[str, Any]]:
    aliases = {canonical: wire for wire, canonical in reverse_aliases.items()}
    wire_messages = json.loads(canonical_json(list(messages)))
    for message in wire_messages:
        if message.get("name") in aliases and (message.get("role") == "tool" or message.get("type") in {"function_call", "tool_use", "tool_result"}):
            message["name"] = aliases[message["name"]]
        function_call = message.get("function_call")
        if isinstance(function_call, dict) and function_call.get("name") in aliases:
            function_call["name"] = aliases[function_call["name"]]
        tool_calls = message.get("tool_calls")
        if isinstance(tool_calls, list):
            for call in tool_calls:
                if isinstance(call, dict) and call.get("name") in aliases:
                    call["name"] = aliases[call["name"]]
                function = call.get("function") if isinstance(call, dict) else None
                if isinstance(function, dict) and function.get("name") in aliases:
                    function["name"] = aliases[function["name"]]
        content = message.get("content")
        if isinstance(content, list):
            for block in content:
                if isinstance(block, dict) and block.get("type") in {"function_call", "tool_use", "tool_result"} and block.get("name") in aliases:
                    block["name"] = aliases[block["name"]]
    return wire_messages


def _wire_tool_choice(value: Any, aliases: Mapping[str, str]) -> Any:
    if isinstance(value, str):
        return aliases.get(value, value)
    if isinstance(value, dict):
        return {name: _wire_tool_choice(item, aliases) for name, item in value.items()}
    if isinstance(value, list):
        return [_wire_tool_choice(item, aliases) for item in value]
    return value


def _provider_context_with_wire_tool_aliases(context: ProviderRuntimeContext, reverse_aliases: Mapping[str, str]) -> ProviderRuntimeContext:
    aliases = {canonical: wire for wire, canonical in reverse_aliases.items()}
    provider_tools = context.agent_config.get("provider_tools")
    if not aliases or not isinstance(provider_tools, dict):
        return context
    if "tool_choice" in provider_tools:
        provider_tools["tool_choice"] = _wire_tool_choice(provider_tools["tool_choice"], aliases)
    for provider_config in provider_tools.values():
        if isinstance(provider_config, dict) and "tool_choice" in provider_config:
            provider_config["tool_choice"] = _wire_tool_choice(provider_config["tool_choice"], aliases)
    return context






_REPLAY_SCHEMA_FILES = {
    "plan": "bb.replay_plan.v1.schema.json",
    "execution": "bb.replay_execution.v1.schema.json",
    "manifest": "bb.replay_artifact_manifest.v1.schema.json",
    "exchange": "bb.provider_exchange.v2.schema.json",
    "problem": "bb.problem.v1.schema.json",
}


def _replay_schema_registry() -> tuple[dict[str, Any], dict[str, Draft202012Validator]]:
    root = Path(__file__).resolve().parents[3] / "contracts/public/schemas"
    schemas = {name: json.loads((root / filename).read_text(encoding="utf-8")) for name, filename in _REPLAY_SCHEMA_FILES.items()}
    registry = Registry()
    for schema in schemas.values():
        registry = registry.with_resource(schema["$id"], Resource.from_contents(schema))
    binding = {"schemas": {schema["$id"]: sha256_json(schema) for schema in schemas.values()}}
    validators = {name: Draft202012Validator(schema, registry=registry) for name, schema in schemas.items() if name != "problem"}
    return binding, validators


def _tool_argument_validators(tool_schemas: Sequence[Mapping[str, Any]]) -> dict[str, Draft202012Validator]:
    validators: dict[str, Draft202012Validator] = {}
    for index, schema in enumerate(tool_schemas):
        function = schema.get("function") if isinstance(schema, Mapping) else None
        name = function.get("name") if isinstance(function, Mapping) else None
        parameters = function.get("parameters") if isinstance(function, Mapping) else None
        if not isinstance(name, str) or not isinstance(parameters, Mapping):
            raise ReplayRunError(f"tool_schemas[{index}] must contain an object parameters schema")
        try:
            Draft202012Validator.check_schema(parameters)
        except SchemaError as exc:
            raise ReplayRunError(f"tool_schemas[{index}] contains an invalid parameters schema") from exc
        validators[name] = Draft202012Validator(parameters)
    return validators


def _validated_arguments(call: ProviderToolCall, validators: Mapping[str, Draft202012Validator]) -> dict[str, Any]:
    arguments = _arguments(call.arguments)
    if call.name not in validators:
        return arguments
    try:
        validators[call.name].validate(arguments)
    except ValidationError as exc:
        raise _InvalidProviderResponse("provider tool arguments violate the frozen schema") from exc
    return arguments


def _provider_client_binding(provider: Any, provider_client: Any, secret_bindings: Mapping[str, str]) -> dict[str, Any]:
    method = getattr(provider, "replay_client_identity", None)
    if not callable(method):
        raise ReplayRunError("provider does not expose replay_client_identity")
    value = method(provider_client, secret_bindings)
    try:
        binding = json.loads(canonical_json(value))
    except (TypeError, ValueError) as exc:
        raise ReplayRunError("provider client identity must be canonical") from exc
    if not isinstance(binding, dict) or binding.get("verified") is not True:
        raise ReplayRunError("provider client identity must attest verified configuration")
    return binding

def _provider_route_binding(provider_id: str, runtime_id: str, endpoint_class: str, provider_model: str, model_revision: str | None, runtime_version: str, provider: Any, provider_client_identity: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "provider_id": provider_id,
        "runtime_id": runtime_id,
        "runtime_version": runtime_version,
        "runtime_implementation": _capability_runtime_identity(provider, "provider"),
        "client_identity": dict(provider_client_identity),
        "endpoint_class": endpoint_class,
        "model_id": provider_model,
        "model_revision": model_revision,
    }

def _identity_value(value: Any, seen: frozenset[int] = frozenset()) -> Any:
    if value is None or type(value) in (bool, int, float, str):
        return value
    if isinstance(value, Path):
        return {"path_sha256": _digest(str(value).encode("utf-8"))}
    if type(value) is bytes:
        return {"bytes_sha256": _digest(value)}
    scalar_state = _scalar_state(value)
    if scalar_state is not None:
        return scalar_state
    if inspect.ismodule(value):
        source_path = getattr(value, "__file__", None)
        source_sha = _digest(Path(source_path).read_bytes()) if isinstance(source_path, str) and Path(source_path).is_file() else _digest(value.__name__.encode("utf-8"))
        module_state = {name: item for name, item in vars(value).items() if not name.startswith("_") and (item is None or type(item) in (bool, int, float, str) or isinstance(item, (Mapping, list, tuple, set, frozenset)))}
        return {"module": value.__name__, "version": str(getattr(value, "__version__", "")), "source_sha256": source_sha, "state_sha256": sha256_json(_identity_value(module_state, seen | {id(value)}))}
    if isinstance(value, type):
        return {"type": f"{value.__module__}.{value.__qualname__}"}
    if isinstance(value, functools.partial) or inspect.isroutine(value):
        return {"callable": _callable_identity(value)}
    if id(value) in seen:
        return {"cycle": f"{type(value).__module__}.{type(value).__qualname__}"}
    next_seen = seen | {id(value)}
    if type(value) is dict:
        rows = [{"key": _identity_value(key, next_seen), "value": _identity_value(item, next_seen)} for key, item in value.items()]
        return {"mapping": sorted(rows, key=canonical_json)}
    if type(value) in (list, tuple, set, frozenset):
        rows = [_identity_value(item, next_seen) for item in value]
        return sorted(rows, key=canonical_json) if type(value) in (set, frozenset) else rows
    transient = {"_client_identities", "_client_replay_specs"} if type(value) is ProviderRuntimeAdapter else set()
    state = _plain_object_state(value)
    if state:
        return {
            "type": f"{type(value).__module__}.{type(value).__qualname__}",
            "state": {name: _identity_value(item, next_seen) for name, item in sorted(state.items()) if name not in transient},
        }
    return {"type": f"{type(value).__module__}.{type(value).__qualname__}"}


def _runtime_type_identity(value: Any, seen: frozenset[int] = frozenset()) -> dict[str, Any]:
    if inspect.isfunction(value):
        symbol = f"{value.__module__}.{value.__qualname__}"
        if value.__module__ in {"__main__", "__mp_main__"}:
            raise ReplayRunError("runtime implementations must be importable outside the entry-point module")
        try:
            source = inspect.getsource(value).encode("utf-8")
        except (OSError, TypeError) as exc:
            raise ReplayRunError(f"runtime implementation {symbol} has no inspectable source") from exc
        return {
            "symbol": symbol,
            "source_sha256": _digest(source),
            "configuration_sha256": sha256_json(_identity_value({"args": value.__defaults__, "kwargs": value.__kwdefaults__})),
            "methods_sha256": sha256_json({"call": _callable_identity(value)}),
        }
    if isinstance(value, functools.partial) or inspect.ismethod(value) or inspect.isbuiltin(value):
        callable_identity = _callable_identity(value)
        return {
            "symbol": callable_identity["symbol"],
            "source_sha256": sha256_json(callable_identity),
            "configuration_sha256": sha256_json(_identity_value(value)),
            "methods_sha256": sha256_json({"call": callable_identity}),
        }
    runtime_type = type(value)
    symbol = f"{runtime_type.__module__}.{runtime_type.__qualname__}"
    if runtime_type.__module__ in {"__main__", "__mp_main__"}:
        raise ReplayRunError("runtime implementation types must be importable outside the entry-point module")
    try:
        source = inspect.getsource(runtime_type).encode("utf-8")
    except (OSError, TypeError) as exc:
        raise ReplayRunError(f"runtime implementation {symbol} has no inspectable source") from exc
    methods: dict[str, Any] = {}
    for owner_type in runtime_type.__mro__:
        if owner_type is object:
            continue
        for method_name, raw_method in vars(owner_type).items():
            candidates = ()
            if inspect.isfunction(raw_method):
                candidates = (raw_method,)
            elif isinstance(raw_method, (staticmethod, classmethod)):
                candidates = (raw_method.__func__,)
            elif isinstance(raw_method, property):
                candidates = tuple(method for method in (raw_method.fget, raw_method.fset, raw_method.fdel) if method is not None)
            for index, method in enumerate(candidates):
                methods[f"{owner_type.__module__}.{owner_type.__qualname__}.{method_name}:{index}"] = _callable_identity(method)
    identity: dict[str, Any] = {
        "symbol": symbol,
        "source_sha256": _digest(source),
        "configuration_sha256": sha256_json(_identity_value(value)),
        "methods_sha256": sha256_json(methods),
    }
    if id(value) in seen:
        return identity
    delegates = {}
    for name in ("runtime", "sandbox", "executor"):
        delegate = getattr(value, name, None)
        if delegate is not None and delegate is not value:
            delegates[name] = _runtime_type_identity(delegate, seen | {id(value)})
    if delegates:
        identity["delegates"] = delegates
    return identity


def _callable_identity(value: Callable[..., Any]) -> dict[str, Any]:
    if isinstance(value, functools.partial):
        return {
            "symbol": "functools.partial",
            "callable": _callable_identity(value.func),
            "args_sha256": sha256_json(_identity_value(value.args)),
            "keywords_sha256": sha256_json(_identity_value(value.keywords or {})),
        }
    if inspect.isbuiltin(value):
        module, qualname = getattr(value, "__module__", None), getattr(value, "__qualname__", None)
        if not isinstance(module, str) or not isinstance(qualname, str):
            raise ReplayRunError("built-in policy gate does not expose a stable callable identity")
        symbol = f"{module}.{qualname}"
        implementation = {"symbol": symbol, "python": platform.python_version(), "implementation": platform.python_implementation()}
        return {
            "symbol": symbol,
            "source_sha256": sha256_json(implementation),
            "nonlocals_sha256": sha256_json({}),
            "globals_sha256": sha256_json(implementation),
            "defaults_sha256": sha256_json({}),
        }
    module = getattr(value, "__module__", None)
    qualname = getattr(value, "__qualname__", None)
    if not isinstance(module, str) or not module or not isinstance(qualname, str) or not qualname:
        raise ReplayRunError("policy gate does not expose a stable callable identity")
    if module in {"__main__", "__mp_main__"}:
        raise ReplayRunError("policy callables must be importable outside the entry-point module")
    symbol = f"{module}.{qualname}"
    try:
        source = inspect.getsource(value).encode("utf-8")
        closure = inspect.getclosurevars(value)
    except (OSError, TypeError) as exc:
        raise ReplayRunError(f"policy implementation {symbol} has no inspectable source") from exc
    identity: dict[str, Any] = {
        "symbol": symbol,
        "source_sha256": _digest(source),
        "nonlocals_sha256": sha256_json(_identity_value(closure.nonlocals)),
        "globals_sha256": sha256_json(_identity_value(closure.globals)),
        "defaults_sha256": sha256_json(_identity_value({"args": getattr(value, "__defaults__", None), "kwargs": getattr(value, "__kwdefaults__", None)})),
    }
    owner = getattr(value, "__self__", None)
    if owner is not None:
        identity["owner"] = _runtime_type_identity(owner)
    return identity


def _host_platform_binding() -> dict[str, str]:
    return {
        "system": platform.system(),
        "release": platform.release(),
        "machine": platform.machine(),
        "python_implementation": platform.python_implementation(),
        "python_version": platform.python_version(),
        "byteorder": sys.byteorder,
    }


def _environment_binding(environment_allowlist: Sequence[str], secret_bindings: Mapping[str, str]) -> tuple[dict[str, Any], dict[str, str]]:
    missing = [name for name in environment_allowlist if name not in secret_bindings and name not in os.environ]
    if missing:
        raise ReplayRunError("required replay environment values are absent: " + ", ".join(sorted(missing)))
    values = {name: secret_bindings[name] if name in secret_bindings else os.environ[name] for name in environment_allowlist}
    binding = {"names": sorted(environment_allowlist), "value_sha256": {name: _digest(values[name].encode("utf-8")) for name in sorted(values)}}
    return binding, values


def _assistant_messages(result: ProviderResult, reverse_aliases: Mapping[str, str]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for message in result.messages:
        row: dict[str, Any] = {"role": message.role, "content": message.content}
        if message.tool_calls:
            row["tool_calls"] = [{"id": call.id, "type": call.type, "function": {"name": reverse_aliases.get(call.name, call.name), "arguments": call.arguments}} for call in message.tool_calls]
        rows.append(row)
    return rows


def _arguments(value: str) -> dict[str, Any]:
    def reject_constant(_value: str) -> Any:
        raise _InvalidProviderResponse("provider tool arguments contain a non-JSON number")

    def unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, item in pairs:
            if key in result:
                raise _InvalidProviderResponse(f"provider tool arguments contain duplicate key {key!r}")
            result[key] = item
        return result

    try:
        parsed = json.loads(value, parse_constant=reject_constant, object_pairs_hook=unique_object)
        canonical_json(parsed)
    except _InvalidProviderResponse:
        raise
    except (TypeError, ValueError) as exc:
        raise _InvalidProviderResponse("provider emitted invalid tool arguments") from exc
    if not isinstance(parsed, dict):
        raise _InvalidProviderResponse("provider tool arguments must be an object")
    return parsed


def _open_directory_path(path: Path, *, create: bool) -> int:
    absolute = path.absolute()
    if not absolute.is_absolute():
        raise ReplayRunError("anchored storage root must be absolute")
    descriptor = os.open(absolute.anchor, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0))
    try:
        for component in absolute.parts[1:]:
            if create:
                try:
                    os.mkdir(component, mode=0o700, dir_fd=descriptor)
                except FileExistsError:
                    pass
            child = os.open(component, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0), dir_fd=descriptor)
            os.close(descriptor)
            descriptor = child
        return descriptor
    except BaseException:
        os.close(descriptor)
        raise


def _anchored_artifact_store(workspace: BreadBoardWorkspace) -> tuple[ArtifactStore, tuple[int, ...]]:
    artifact_path = workspace.path(".breadboard/artifacts")
    if os.name == "nt":
        return ArtifactStore(artifact_path), ()
    descriptors: list[int] = []
    try:
        descriptors.append(_open_directory_path(workspace.root, create=True))
        descriptors.append(AnchoredStorage.open_directory(descriptors[-1], ".breadboard"))
        descriptors.append(AnchoredStorage.open_directory(descriptors[-1], "artifacts"))
        return ArtifactStore(artifact_path, descriptor=descriptors[-1]), tuple(descriptors)
    except BaseException as exc:
        for descriptor in reversed(descriptors):
            os.close(descriptor)
        if isinstance(exc, OSError):
            raise ReplayRunError("artifact storage namespace is not a secure directory") from exc
        raise


def _anchored_replay_parent(workspace: BreadBoardWorkspace) -> tuple[Path, tuple[int, ...]]:
    replay_path = workspace.path(".breadboard/replays")
    if os.name == "nt":
        replay_path.mkdir(parents=True, exist_ok=True)
        return replay_path, ()
    descriptors: list[int] = []
    try:
        descriptors.append(_open_directory_path(workspace.root, create=True))
        descriptors.append(AnchoredStorage.open_directory(descriptors[-1], ".breadboard"))
        descriptors.append(AnchoredStorage.open_directory(descriptors[-1], "replays"))
        return replay_path, tuple(descriptors)
    except BaseException as exc:
        for descriptor in reversed(descriptors):
            os.close(descriptor)
        if isinstance(exc, OSError):
            raise ReplayRunError("replay storage namespace is not a secure directory") from exc
        raise


def _close_descriptors(descriptors: Sequence[int]) -> None:
    for descriptor in reversed(descriptors):
        os.close(descriptor)


def _verify_staging_path(path: Path, expected_identity: tuple[int, int], descriptor: int) -> None:
    descriptor_stat = os.fstat(descriptor)
    if not path.exists() or path.is_symlink():
        raise ReplayRunError("replay staging path disappeared before finalization")
    path_stat = path.lstat()
    if (descriptor_stat.st_dev, descriptor_stat.st_ino) != expected_identity or (path_stat.st_dev, path_stat.st_ino) != expected_identity:
        raise ReplayRunError("replay staging path changed before finalization")


def _rename_no_replace(source: Path, destination: Path, *, parent_descriptor: int | None = None) -> None:
    import ctypes
    import errno
    libc = ctypes.CDLL(None, use_errno=True)
    if parent_descriptor is None:
        source_bytes, destination_bytes = os.fsencode(source), os.fsencode(destination)
        source_parent = destination_parent = -2 if sys.platform == "darwin" else -100
    else:
        source_bytes, destination_bytes = os.fsencode(source.name), os.fsencode(destination.name)
        source_parent = destination_parent = parent_descriptor
    if sys.platform == "darwin" and hasattr(libc, "renameatx_np"):
        result = libc.renameatx_np(source_parent, source_bytes, destination_parent, destination_bytes, 0x00000004)
    elif hasattr(libc, "renameat2"):
        result = libc.renameat2(source_parent, source_bytes, destination_parent, destination_bytes, 0x00000001)
    else:
        if parent_descriptor is None:
            if destination.exists():
                raise FileExistsError(destination)
            os.rename(source, destination)
        else:
            try:
                os.stat(destination.name, dir_fd=parent_descriptor, follow_symlinks=False)
            except FileNotFoundError:
                os.rename(source.name, destination.name, src_dir_fd=parent_descriptor, dst_dir_fd=parent_descriptor)
            else:
                raise FileExistsError(destination)
        return
    if result != 0:
        error = ctypes.get_errno()
        raise OSError(error or errno.EIO, os.strerror(error or errno.EIO), str(destination))


def _duration_ms(start: float, end: float) -> int:
    return max(0, int((end - start) * 1000))


def _staging_descriptor_path(descriptor: int) -> Path:
    proc_path = Path("/proc/self/fd") / str(descriptor)
    if proc_path.exists():
        return Path(os.readlink(proc_path))
    import fcntl
    return Path(fcntl.fcntl(descriptor, 50, b"\0" * 1024).split(b"\0", 1)[0].decode())


def _remove_tree_verified(parent_descriptor: int, name: str, expected_identity: tuple[int, int], descriptor: int) -> None:
    descriptor_stat = os.fstat(descriptor)
    if (descriptor_stat.st_dev, descriptor_stat.st_ino) != expected_identity:
        raise ReplayRunError("replay staging descriptor identity changed before cleanup")
    for child_name in os.listdir(descriptor):
        _remove_entry_at(descriptor, child_name)
    os.fsync(descriptor)
    identity_names: list[str] = []
    for candidate_name in os.listdir(parent_descriptor):
        candidate = os.stat(candidate_name, dir_fd=parent_descriptor, follow_symlinks=False)
        if (candidate.st_dev, candidate.st_ino) == expected_identity:
            identity_names.append(candidate_name)
    if not identity_names:
        if os.fstat(descriptor).st_nlink == 0:
            return
        raise ReplayRunError("replay staging inode remains linked at an unresolved path")
    if len(identity_names) != 1:
        raise ReplayRunError("replay staging inode has multiple namespace links")
    os.rmdir(identity_names[0], dir_fd=parent_descriptor)
    os.fsync(parent_descriptor)
    if any((metadata.st_dev, metadata.st_ino) == expected_identity for candidate_name in os.listdir(parent_descriptor) for metadata in (os.stat(candidate_name, dir_fd=parent_descriptor, follow_symlinks=False),)):
        raise ReplayRunError("replay staging cleanup did not unlink the verified inode")
    try:
        replacement = os.stat(name, dir_fd=parent_descriptor, follow_symlinks=False)
    except FileNotFoundError:
        return
    if stat.S_ISDIR(replacement.st_mode):
        os.rmdir(name, dir_fd=parent_descriptor)
    else:
        os.unlink(name, dir_fd=parent_descriptor)
    os.fsync(parent_descriptor)


def run_replay(
    plan: ReplayPlan,
    *,
    binding_inputs: Mapping[str, Any],
    runtime_bindings: Mapping[str, Any],
    lane_lock: Mapping[str, Any],
    harness_lock: EffectiveHarnessLock,
    workspace: BreadBoardWorkspace,
    scenario: ReplayScenario,
    provider: Any,
    provider_client: Any,
    provider_model: str,
    provider_context: Any,
    tools: Mapping[str, Any],
    host: Any,
    authorize: Callable[[str, Mapping[str, Any]], bool],
    secret_bindings: Mapping[str, str] | None = None,
    environment_allowlist: Sequence[str] = (),
    runtime_version: str = "unknown",
    model_revision: str | None = None,
    clock: Clock | None = None,
    ids: IdSource | None = None,
    monotonic: Callable[[], float] = time.monotonic,
    cancelled: Callable[[], bool] | None = None,
) -> ReplayRunResult:
    if not isinstance(plan, ReplayPlan) or plan.mode != "execute":
        raise ReplayPlanError("run_replay requires an execute ReplayPlan")
    if not isinstance(harness_lock, EffectiveHarnessLock):
        raise ReplayRunError("harness_lock must be an EffectiveHarnessLock")
    plan.verify_bindings(binding_inputs)
    expected_bindings = scenario.binding_inputs({name: binding_inputs[name] for name in HASH_BINDING_NAMES if name not in {"scenario_sha256", "interaction_script_sha256", "initial_workspace_sha256", "initial_messages_sha256", "tool_schema_lock_sha256"}})
    if dict(binding_inputs) != expected_bindings:
        raise ReplayPlanError("scenario content changed after plan creation")
    plan_record = plan.as_dict(); lane_sha = lane_lock.get("lock_sha256"); harness_sha = harness_lock.as_dict().get("graph_hash")
    runtime_binding_names = {"capability_probe_sha256", "model_policy_sha256", "normalizer_config_sha256", "comparator_config_sha256"}
    if not isinstance(runtime_bindings, Mapping) or set(runtime_bindings) != runtime_binding_names:
        raise ReplayRunError("runtime_bindings must provide the active capability, model policy, normalizer, and comparator configurations")
    for binding_name in sorted(runtime_binding_names):
        if sha256_json(runtime_bindings[binding_name]) != plan_record["hash_bindings"][binding_name]:
            raise ReplayPlanError(f"{binding_name} does not match the frozen replay plan")
    schema_binding, evidence_validators = _replay_schema_registry()
    if binding_inputs["schema_registry_sha256"] != schema_binding:
        raise ReplayPlanError("schema registry does not match the frozen replay plan")
    evidence_validators["plan"].validate(plan_record)
    if lane_sha != plan_record["lane_lock_sha256"] or harness_sha != plan_record["harness_lock_sha256"]:
        raise ReplayPlanError("lane or harness lock changed after plan creation")
    if not isinstance(provider_model, str) or not provider_model or not isinstance(runtime_version, str) or not runtime_version:
        raise ReplayRunError("provider model and runtime version must be populated")
    if not callable(authorize):
        raise ReplayRunError("authorize must be a callable policy gate")
    if binding_inputs["host_platform_sha256"] != _host_platform_binding():
        raise ReplayPlanError("host platform does not match the frozen replay plan")
    if not isinstance(tools, Mapping):
        raise ReplayRunError("tools must be a mapping")
    secret_bindings = {} if secret_bindings is None else secret_bindings
    if not isinstance(secret_bindings, Mapping) or any(type(name) is not str or not name or type(value) is not str or not value for name, value in secret_bindings.items()):
        raise ReplayRunError("secret_bindings must map plain populated reference names to plain populated values")
    if not isinstance(environment_allowlist, Sequence) or isinstance(environment_allowlist, (str, bytes)) or any(type(name) is not str or not name for name in environment_allowlist) or len(environment_allowlist) != len(set(environment_allowlist)):
        raise ReplayRunError("environment_allowlist must contain unique plain populated names")
    environment_binding, worker_environment = _environment_binding(environment_allowlist, secret_bindings)
    secret_values = tuple(sorted({*secret_bindings.values(), *worker_environment.values()}, key=lambda secret: (-len(secret), secret)))
    if binding_inputs["environment_allowlist_sha256"] != environment_binding:
        raise ReplayPlanError("environment values do not match the frozen replay plan")

    provider_tools, reverse_tool_aliases = _provider_tool_wire_schemas(scenario.tool_schemas)
    isolated_provider_context = _provider_context_with_wire_tool_aliases(
        _provider_context_snapshot(provider_context), reverse_tool_aliases
    )
    active_clock, active_ids = clock or SystemClock(), ids or UUIDSource()
    execution_id = "replay_execution." + _portable_id(active_ids.new_id(), "id_source execution value")
    fresh_nonce = _portable_id(active_ids.new_id(), "fresh_nonce")
    started_at, overall_start = active_clock.now(), monotonic()
    replay_descriptors: tuple[int, ...] = ()
    stage_descriptor: int | None = None
    stage_created = False
    try:
        _, replay_descriptors = _anchored_replay_parent(workspace)
        if not replay_descriptors:
            raise ReplayRunError("descriptor-anchored replay storage is unavailable on this platform")
        replay_parent_descriptor = replay_descriptors[-1]
        replay_parent = _staging_descriptor_path(replay_parent_descriptor)
        stage_name, final_name = f".{execution_id}.staging", execution_id
        for candidate_name in (stage_name, final_name):
            try:
                os.stat(candidate_name, dir_fd=replay_parent_descriptor, follow_symlinks=False)
            except FileNotFoundError:
                continue
            raise ReplayRunError(f"replay execution identity already exists: {execution_id}")
        os.mkdir(stage_name, mode=0o700, dir_fd=replay_parent_descriptor)
        stage_created = True
        stage_descriptor = os.open(stage_name, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0), dir_fd=replay_parent_descriptor)
        stage_metadata = os.fstat(stage_descriptor)
        stage_identity = (stage_metadata.st_dev, stage_metadata.st_ino)
        stage, final = _staging_descriptor_path(stage_descriptor), replay_parent / final_name
    except BaseException:
        try:
            if stage_descriptor is not None:
                failed_metadata = os.fstat(stage_descriptor)
                _remove_tree_verified(replay_parent_descriptor, stage_name, (failed_metadata.st_dev, failed_metadata.st_ino), stage_descriptor)
                os.close(stage_descriptor)
            elif stage_created:
                _remove_entry_at(replay_parent_descriptor, stage_name)
        finally:
            _close_descriptors(replay_descriptors)
        raise
    artifact_descriptors: tuple[int, ...] = ()
    fresh_workspace_descriptor: int | None = None
    try:
        fresh_workspace_descriptor = _materialize_initial(stage_descriptor, "workspace", scenario.initial_files)
        fresh_workspace_metadata = os.fstat(fresh_workspace_descriptor)
        fresh_workspace_identity = (fresh_workspace_metadata.st_dev, fresh_workspace_metadata.st_ino)
        fresh_workspace = _staging_descriptor_path(fresh_workspace_descriptor)
        before = _snapshot(fresh_workspace, fresh_workspace_identity, fresh_workspace_descriptor)
        session_name = "session.events.jsonl"
        session = Session.start(harness_lock, scenario.task, session_id=execution_id, clock=active_clock, sink=_AnchoredJsonlEventSink(stage_descriptor, session_name))
        store, artifact_descriptors = _anchored_artifact_store(workspace); created: set[ArtifactRef] = set(); artifacts: list[tuple[str, ArtifactRef, str | None, str]] = []
        provider_rows: list[dict[str, Any]] = []; tool_rows: list[dict[str, str]] = []; pending_provider_metadata: dict[str, Any] = {}
        messages = [dict(row) for row in (*scenario.initial_messages, *scenario.interaction_script)]
        redaction_count, terminal_status, completion_reason, failure = [0], "completed", "provider completed without tool calls", None
        policy_state = {"capability": "allowed", "policy": "allowed", "approval": "not_required"}; provider_calls = tool_calls = 0
        integrity_verified = True
    except BaseException:
        try:
            if fresh_workspace_descriptor is not None:
                os.close(fresh_workspace_descriptor)
                fresh_workspace_descriptor = None
            _close_descriptors(artifact_descriptors)
            _remove_tree_verified(replay_parent_descriptor, stage_name, stage_identity, stage_descriptor)
        finally:
            os.close(stage_descriptor)
            _close_descriptors(replay_descriptors)
        raise

    def put(role: str, value: Any, schema_id: str | None, sensitivity: str = "internal") -> ArtifactRef:
        redacted = _redact(value, secrets=secret_values, workspace=workspace.root, counter=redaction_count)
        ref = store.put_json(redacted, created=created); artifacts.append((role, ref, schema_id, sensitivity)); return ref

    try:
        with store.transaction(rollback_created=created):
            try:
                host_identity = _host_identity(host); provider_id, runtime_id, endpoint_class = _provider_identity(provider)
                declared_names = _declared_tool_names(scenario.tool_schemas)
                expected_executor_names = declared_names - {"host.execute"}
                if set(tools) != expected_executor_names:
                    raise ReplayPlanError("runtime tools do not match the frozen scenario toolset")
                client_binding = _provider_client_binding(provider, provider_client, secret_bindings)
                route_binding = _provider_route_binding(provider_id, runtime_id, endpoint_class, provider_model, model_revision, runtime_version, provider, client_binding)
                if binding_inputs["provider_route_lock_sha256"] != route_binding:
                    raise ReplayPlanError("provider runtime does not match the frozen provider route")
                host_worker_payload = _tool_executor_envelope(host, "host")
                tool_worker_payloads = {
                    name: _tool_executor_envelope(tools[name], "tool")
                    for name in sorted(tools)
                }
                provider_capability = _provider_capability(provider, provider_client, provider_model, isolated_provider_context, worker_environment, secret_bindings, client_binding)
                provider_worker_payload = _tool_executor_envelope(provider_capability, "provider")
                policy_capability = _policy_capability(authorize)
                policy_worker_payload = _tool_executor_envelope(policy_capability, "policy")
                executor_binding = {"executors": {name: _capability_runtime_identity(tools[name], "tool", payload=tool_worker_payloads[name]) for name in sorted(tools)}}
                if binding_inputs["tool_executor_identity_sha256"] != executor_binding:
                    raise ReplayPlanError("tool executors do not match the frozen runtime identities")
                host_binding = {"driver_type": _capability_runtime_identity(host, "host", payload=host_worker_payload), "workspace_identity": host_identity, "process_containment": _host_containment_identity(host)}
                if binding_inputs["host_driver_identity_sha256"] != host_binding:
                    raise ReplayPlanError("host driver does not match the frozen runtime identity")
                policy_binding = {"callable": _callable_identity(authorize)}
                if binding_inputs["operation_policy_sha256"] != policy_binding:
                    raise ReplayPlanError("policy gate does not match the frozen runtime identity")
                secret_binding = {"references": sorted(secret_bindings)}
                if binding_inputs["secret_references_sha256"] != secret_binding:
                    raise ReplayPlanError("resolved secrets do not match the frozen secret references")
                tool_argument_validators = _tool_argument_validators(scenario.tool_schemas)
                usage_accountant = _UsageAccountant.from_budgets(plan_record["budgets"])
                before_ref = put("workspace_before", before, None)
                provider_worker = None
                host_worker = None
                policy_worker = None
                tool_workers: dict[str, _ExecToolWorker] = {}
                budgets, deadlines = plan_record["budgets"], plan_record["deadlines_ms"]
                last_activity = overall_start

                def cancellation_state(_elapsed_ms: int = 0) -> tuple[str, str] | None:
                    if cancelled is None or not cancelled():
                        return None
                    return "replay.cancelled", "replay was cancelled"

                while True:
                    turn_start = monotonic()
                    cancellation = cancellation_state(_duration_ms(last_activity, turn_start))
                    if cancellation is not None:
                        raise _RuntimeFailure("cancelled", cancellation[0], cancellation[1])
                    if _duration_ms(overall_start, turn_start) > deadlines["total"]:
                        raise _RuntimeFailure("timed_out", "replay.total_timeout", "replay exceeded its total deadline")
                    if _duration_ms(last_activity, turn_start) > deadlines["idle"]:
                        raise _RuntimeFailure("timed_out", "replay.idle_timeout", "replay exceeded its idle deadline")
                    if provider_calls >= budgets["provider_calls"] or provider_calls >= budgets["turns"]:
                        raise _RuntimeFailure("budget_exhausted", "replay.provider_budget", "replay exhausted its provider or turn budget")
                    provider_calls += 1; request_id = f"{execution_id}:request:{provider_calls}"; attempt_id = f"{request_id}:attempt:1"
                    provider_messages = _provider_wire_messages(messages, reverse_tool_aliases)
                    request = {"model": provider_model, "messages": provider_messages, "tools": provider_tools, "stream": False, "context": _provider_context_request_payload(isolated_provider_context)}
                    request_payload_sha256 = sha256_json(request)
                    request_ref = put("provider_request", request, None, "secret_redacted")
                    call_start, call_started = monotonic(), active_clock.now()
                    usage = {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0, "cost_amount": 0, "cost_currency": "USD"}
                    prepared_calls: list[tuple[ProviderToolCall, dict[str, Any]]] = []
                    response_ref: ArtifactRef | None = None
                    response_payload_sha256: str | None = None
                    try:
                        total_remaining = max(1, deadlines["total"] - _duration_ms(overall_start, call_start))
                        idle_remaining = max(1, deadlines["idle"] - _duration_ms(last_activity, call_start))
                        call_limit = min(deadlines["provider_call"], total_remaining, idle_remaining)
                        if idle_remaining == call_limit:
                            timeout_code, timeout_detail = "replay.idle_timeout", "provider call exceeded the idle deadline"
                        elif total_remaining == call_limit:
                            timeout_code, timeout_detail = "replay.total_timeout", "replay exceeded its total deadline"
                        else:
                            timeout_code, timeout_detail = "replay.provider_timeout", "provider call exceeded its deadline"
                        if provider_worker is None:
                            provider_worker = _ExecToolWorker(
                                provider_capability,
                                fresh_workspace_descriptor,
                                capability_kind="provider",
                                payload=provider_worker_payload,
                                startup_timeout_ms=call_limit,
                                startup_timeout_code=timeout_code,
                                startup_timeout_detail=timeout_detail,
                                cancelled=cancelled,
                            )
                            provider_ready = monotonic()
                            cancellation = cancellation_state(_duration_ms(last_activity, provider_ready))
                            if cancellation is not None:
                                raise _RuntimeFailure("cancelled", cancellation[0], cancellation[1])
                            if _duration_ms(overall_start, provider_ready) > deadlines["total"]:
                                raise _RuntimeFailure("timed_out", "replay.total_timeout", "replay exceeded its total deadline")
                            if _duration_ms(last_activity, provider_ready) > deadlines["idle"]:
                                raise _RuntimeFailure("timed_out", "replay.idle_timeout", "provider call exceeded the idle deadline")
                            provider_elapsed = _duration_ms(call_start, provider_ready)
                            if provider_elapsed > deadlines["provider_call"]:
                                raise _RuntimeFailure("timed_out", "replay.provider_timeout", "provider call exceeded its deadline")
                            total_remaining = max(1, deadlines["total"] - _duration_ms(overall_start, provider_ready))
                            idle_remaining = max(1, deadlines["idle"] - _duration_ms(last_activity, provider_ready))
                            provider_remaining = max(1, deadlines["provider_call"] - provider_elapsed)
                            call_limit = min(provider_remaining, total_remaining, idle_remaining)
                            if idle_remaining == call_limit:
                                timeout_code, timeout_detail = "replay.idle_timeout", "provider call exceeded the idle deadline"
                            elif total_remaining == call_limit:
                                timeout_code, timeout_detail = "replay.total_timeout", "replay exceeded its total deadline"
                            else:
                                timeout_code, timeout_detail = "replay.provider_timeout", "provider call exceeded its deadline"
                        raw_result = provider_worker.invoke({"messages": provider_messages, "tools": provider_tools}, timeout_ms=call_limit, timeout_code=timeout_code, timeout_detail=timeout_detail, cancelled=cancelled, cancellation_grace_ms=plan_record["cancellation_grace_ms"])
                        result = _validate_replay_provider_result(raw_result, request_id)
                        evidence = provider_result_evidence(result)
                        response_payload_sha256 = sha256_json(evidence)
                        usage = normalized_provider_usage(result)
                        response_ref = put("provider_response", evidence, None, "secret_redacted")
                        prepared_calls = []
                        for message in result.messages:
                            for call in message.tool_calls:
                                canonical_call = ProviderToolCall(call.id, reverse_tool_aliases.get(call.name, call.name), call.arguments, call.type, call.raw)
                                prepared_calls.append((canonical_call, _validated_arguments(canonical_call, tool_argument_validators)))
                        finish_reason = next((message.finish_reason for message in result.messages if message.finish_reason), "completed")
                        exchange_problem, exchange_status = None, "completed"
                    except _RuntimeFailure as exc:
                        response_ref = put("provider_error", {"error_type": type(exc).__name__, "message": exc.detail}, None, "secret_redacted")
                        finish_reason, exchange_status = None, exc.status
                        exchange_problem = problem(exc.error_code, exc.detail, record_refs=(response_ref.digest,))
                    except _InvalidProviderResponse as exc:
                        response_ref = response_ref if response_ref is not None else put("provider_invalid_response", {"error_type": type(exc).__name__, "message": "provider returned invalid tool arguments"}, None, "secret_redacted")
                        finish_reason, exchange_status = None, "invalid_response"; exchange_problem = problem("replay.invalid_provider_response", "provider returned invalid tool arguments", record_refs=(response_ref.digest,))
                    except Exception as exc:
                        response_ref = put("provider_error", {"error_type": type(exc).__name__, "message": "provider invocation failed"}, None, "secret_redacted")
                        finish_reason, exchange_status = None, "provider_error"; exchange_problem = problem("replay.provider_failed", "provider invocation failed", record_refs=(response_ref.digest,))
                    call_end = monotonic(); call_duration = _duration_ms(call_start, call_end); last_activity = call_end
                    cancellation = None
                    if exchange_status == "completed":
                        cancellation = cancellation_state(call_duration)
                        if cancellation is not None:
                            finish_reason, exchange_status = None, "cancelled"
                            exchange_problem = problem(cancellation[0], cancellation[1], record_refs=(response_ref.digest,))
                        elif _duration_ms(overall_start, call_end) > deadlines["total"]:
                            finish_reason, exchange_status = None, "timed_out"
                            exchange_problem = problem("replay.total_timeout", "replay exceeded its total deadline", record_refs=(response_ref.digest,))
                        elif call_duration > deadlines["provider_call"]:
                            finish_reason, exchange_status = None, "timed_out"
                            exchange_problem = problem("replay.provider_timeout", "provider call exceeded its deadline", record_refs=(response_ref.digest,))
                    exchange = {
                        "schema_version": "bb.provider_exchange.v2", "exchange_id": f"{execution_id}:exchange:{provider_calls}", "execution_id": execution_id,
                        "attempt_id": attempt_id, "request_id": request_id, "route_lock_sha256": plan_record["hash_bindings"]["provider_route_lock_sha256"],
                        "provider_family": provider_id, "runtime_id": runtime_id, "runtime_version": runtime_version, "endpoint_class": endpoint_class,
                        "model_id": provider_model, "model_revision": model_revision, "started_at_utc": call_started, "completed_at_utc": active_clock.now(),
                        "duration_ms": call_duration, "status": exchange_status, "request_payload_sha256": request_payload_sha256,
                        "response_payload_sha256": response_payload_sha256 if exchange_status == "completed" else None, "finish_reason": finish_reason,
                        "usage": usage, "evidence_refs": [request_ref.digest, response_ref.digest], "fallback_used": False, "problem": exchange_problem,
                    }
                    exchange = validate_provider_exchange(exchange)
                    evidence_validators["exchange"].validate(exchange)
                    exchange_ref = put("provider_exchange", exchange, "bb.provider_exchange.v2", "secret_redacted")
                    provider_rows.append({"ref": exchange_ref.digest, "sha256": exchange_ref.digest, "request_id": request_id, "attempt_id": attempt_id, "normalized_usage": usage, "status": exchange_status})
                    if exchange_status != "completed":
                        if exchange_status == "cancelled":
                            if prepared_calls:
                                policy_state["policy"] = "denied"
                            raise _RuntimeFailure("cancelled", exchange_problem["error_code"], exchange_problem["message"])
                        if exchange_status == "timed_out":
                            error_code = exchange_problem["error_code"] if exchange_problem is not None else "replay.provider_timeout"
                            raise _RuntimeFailure("timed_out", error_code, exchange_problem["message"] if exchange_problem is not None else "provider call exceeded its deadline")
                        if exchange_status == "invalid_response":
                            raise _RuntimeFailure("provider_failed", "replay.invalid_provider_response", "provider returned invalid tool arguments")
                        raise _RuntimeFailure("provider_failed", "replay.provider_failed", "provider invocation failed")
                    usage_budget_exceeded = usage_accountant.add(usage)
                    pending_provider_metadata.update(evidence["metadata"])
                    session_state = isolated_provider_context.session_state
                    for metadata_name, metadata_value in evidence["metadata"].items():
                        session_state.set_provider_metadata(metadata_name, metadata_value)
                    if usage_budget_exceeded:
                        raise _RuntimeFailure("budget_exhausted", "replay.usage_budget", "replay exceeded its token or cost budget")
                    session.input(f"provider turn {provider_calls}", attachments=(response_ref,)); messages.extend(_assistant_messages(result, reverse_tool_aliases))
                    calls = prepared_calls
                    if not calls:
                        break
                    for call, arguments in calls:
                        if tool_calls >= budgets["tool_calls"]:
                            raise _RuntimeFailure("budget_exhausted", "replay.tool_budget", "replay exhausted its tool budget")
                        tool_calls += 1
                        if not isinstance(call.name, str) or not call.name:
                            raise _RuntimeFailure("provider_failed", "replay.invalid_tool_call", "provider emitted a tool call without a name")
                        if call.name not in declared_names:
                            policy_state["capability"] = "denied"
                            raise _RuntimeFailure("policy_denied", "replay.undeclared_tool", "provider requested a tool outside the frozen toolset")
                        policy_start = monotonic()
                        cancellation = cancellation_state(_duration_ms(last_activity, policy_start))
                        if cancellation is not None:
                            policy_state["policy"] = "denied"
                            raise _RuntimeFailure("cancelled", cancellation[0], cancellation[1])
                        if _duration_ms(overall_start, policy_start) > deadlines["total"]:
                            policy_state["policy"] = "denied"
                            raise _RuntimeFailure("timed_out", "replay.total_timeout", "replay exceeded its total deadline")
                        if _duration_ms(last_activity, policy_start) > deadlines["idle"]:
                            policy_state["policy"] = "denied"
                            raise _RuntimeFailure("timed_out", "replay.idle_timeout", "replay exceeded its idle deadline")
                        policy_total_remaining = max(1, deadlines["total"] - _duration_ms(overall_start, policy_start))
                        policy_idle_remaining = max(1, deadlines["idle"] - _duration_ms(last_activity, policy_start))
                        policy_limit = min(deadlines["tool_call"], policy_total_remaining, policy_idle_remaining)
                        if policy_idle_remaining == policy_limit:
                            policy_timeout_code, policy_timeout_detail = "replay.idle_timeout", "policy evaluation exceeded the idle deadline"
                        elif policy_total_remaining == policy_limit:
                            policy_timeout_code, policy_timeout_detail = "replay.total_timeout", "replay exceeded its total deadline"
                        else:
                            policy_timeout_code, policy_timeout_detail = "replay.policy_timeout", "policy evaluation exceeded its deadline"
                        try:
                            if policy_worker is None:
                                policy_worker = _ExecToolWorker(
                                    policy_capability,
                                    fresh_workspace_descriptor,
                                    capability_kind="policy",
                                    payload=policy_worker_payload,
                                    startup_timeout_ms=policy_limit,
                                    startup_timeout_code=policy_timeout_code,
                                    startup_timeout_detail=policy_timeout_detail,
                                    cancelled=cancelled,
                                )
                                policy_ready = monotonic()
                                cancellation = cancellation_state(_duration_ms(last_activity, policy_ready))
                                if cancellation is not None:
                                    raise _RuntimeFailure("cancelled", cancellation[0], cancellation[1])
                                if _duration_ms(overall_start, policy_ready) > deadlines["total"]:
                                    raise _RuntimeFailure("timed_out", "replay.total_timeout", "replay exceeded its total deadline")
                                if _duration_ms(last_activity, policy_ready) > deadlines["idle"]:
                                    raise _RuntimeFailure("timed_out", "replay.idle_timeout", "policy evaluation exceeded the idle deadline")
                                policy_elapsed = _duration_ms(policy_start, policy_ready)
                                if policy_elapsed > deadlines["tool_call"]:
                                    raise _RuntimeFailure("timed_out", "replay.policy_timeout", "policy evaluation exceeded its deadline")
                                policy_total_remaining = max(1, deadlines["total"] - _duration_ms(overall_start, policy_ready))
                                policy_idle_remaining = max(1, deadlines["idle"] - _duration_ms(last_activity, policy_ready))
                                policy_call_remaining = max(1, deadlines["tool_call"] - policy_elapsed)
                                policy_limit = min(policy_call_remaining, policy_total_remaining, policy_idle_remaining)
                                if policy_idle_remaining == policy_limit:
                                    policy_timeout_code, policy_timeout_detail = "replay.idle_timeout", "policy evaluation exceeded the idle deadline"
                                elif policy_total_remaining == policy_limit:
                                    policy_timeout_code, policy_timeout_detail = "replay.total_timeout", "replay exceeded its total deadline"
                                else:
                                    policy_timeout_code, policy_timeout_detail = "replay.policy_timeout", "policy evaluation exceeded its deadline"
                            allowed = policy_worker.invoke({"name": call.name, "arguments": arguments}, timeout_ms=policy_limit, timeout_code=policy_timeout_code, timeout_detail=policy_timeout_detail, cancelled=cancelled, cancellation_grace_ms=plan_record["cancellation_grace_ms"])
                        except _RuntimeFailure:
                            policy_state["policy"] = "denied"
                            raise
                        except Exception as exc:
                            policy_state["policy"] = "denied"
                            raise _RuntimeFailure("policy_denied", "replay.policy_error", "policy evaluation failed for a tool call") from exc
                        policy_end = monotonic()
                        cancellation = cancellation_state(_duration_ms(last_activity, policy_end))
                        if cancellation is not None:
                            policy_state["policy"] = "denied"
                            raise _RuntimeFailure("cancelled", cancellation[0], cancellation[1])
                        if _duration_ms(overall_start, policy_end) > deadlines["total"]:
                            policy_state["policy"] = "denied"
                            raise _RuntimeFailure("timed_out", "replay.total_timeout", "replay exceeded its total deadline")
                        if _duration_ms(last_activity, policy_end) > deadlines["idle"]:
                            policy_state["policy"] = "denied"
                            raise _RuntimeFailure("timed_out", "replay.idle_timeout", "replay exceeded its idle deadline")
                        if _duration_ms(policy_start, policy_end) > deadlines["tool_call"]:
                            policy_state["policy"] = "denied"
                            raise _RuntimeFailure("timed_out", "replay.policy_timeout", "policy evaluation exceeded its deadline")
                        if allowed is not True:
                            policy_state["policy"] = "denied"
                            raise _RuntimeFailure("policy_denied", "replay.policy_denied", "policy denied a tool call")
                        tool_start = monotonic()
                        cancellation = cancellation_state(_duration_ms(last_activity, tool_start))
                        if cancellation is not None:
                            raise _RuntimeFailure("cancelled", cancellation[0], cancellation[1])
                        if _duration_ms(overall_start, tool_start) > deadlines["total"]:
                            raise _RuntimeFailure("timed_out", "replay.total_timeout", "replay exceeded its total deadline")
                        if _duration_ms(last_activity, tool_start) > deadlines["idle"]:
                            raise _RuntimeFailure("timed_out", "replay.idle_timeout", "replay exceeded its idle deadline")
                        total_remaining = max(1, deadlines["total"] - _duration_ms(overall_start, tool_start))
                        idle_remaining = max(1, deadlines["idle"] - _duration_ms(last_activity, tool_start))
                        call_limit = min(deadlines["tool_call"], total_remaining, idle_remaining)
                        if idle_remaining == call_limit:
                            timeout_code, timeout_detail = "replay.idle_timeout", "tool call exceeded the idle deadline"
                        elif total_remaining == call_limit:
                            timeout_code, timeout_detail = "replay.total_timeout", "replay exceeded its total deadline"
                        else:
                            timeout_code, timeout_detail = "replay.tool_timeout", "tool call exceeded its deadline"
                        try:
                            started_worker = False
                            if call.name == "host.execute" and host_worker is None:
                                host_worker = _ExecToolWorker(
                                    host,
                                    fresh_workspace_descriptor,
                                    capability_kind="host",
                                    payload=host_worker_payload,
                                    startup_timeout_ms=call_limit,
                                    startup_timeout_code=timeout_code,
                                    startup_timeout_detail=timeout_detail,
                                    cancelled=cancelled,
                                )
                                started_worker = True
                            elif call.name != "host.execute" and call.name not in tool_workers:
                                tool_workers[call.name] = _ExecToolWorker(
                                    tools[call.name],
                                    fresh_workspace_descriptor,
                                    payload=tool_worker_payloads[call.name],
                                    startup_timeout_ms=call_limit,
                                    startup_timeout_code=timeout_code,
                                    startup_timeout_detail=timeout_detail,
                                    cancelled=cancelled,
                                )
                                started_worker = True
                            if started_worker:
                                worker_ready = monotonic()
                                cancellation = cancellation_state(_duration_ms(last_activity, worker_ready))
                                if cancellation is not None:
                                    raise _RuntimeFailure("cancelled", cancellation[0], cancellation[1])
                                if _duration_ms(overall_start, worker_ready) > deadlines["total"]:
                                    raise _RuntimeFailure("timed_out", "replay.total_timeout", "replay exceeded its total deadline")
                                if _duration_ms(last_activity, worker_ready) > deadlines["idle"]:
                                    raise _RuntimeFailure("timed_out", "replay.idle_timeout", "tool call exceeded the idle deadline")
                                tool_elapsed = _duration_ms(tool_start, worker_ready)
                                if tool_elapsed > deadlines["tool_call"]:
                                    raise _RuntimeFailure("timed_out", "replay.tool_timeout", "tool call exceeded its deadline")
                                total_remaining = max(1, deadlines["total"] - _duration_ms(overall_start, worker_ready))
                                idle_remaining = max(1, deadlines["idle"] - _duration_ms(last_activity, worker_ready))
                                tool_remaining = max(1, deadlines["tool_call"] - tool_elapsed)
                                call_limit = min(tool_remaining, total_remaining, idle_remaining)
                                if idle_remaining == call_limit:
                                    timeout_code, timeout_detail = "replay.idle_timeout", "tool call exceeded the idle deadline"
                                elif total_remaining == call_limit:
                                    timeout_code, timeout_detail = "replay.total_timeout", "replay exceeded its total deadline"
                                else:
                                    timeout_code, timeout_detail = "replay.tool_timeout", "tool call exceeded its deadline"
                            if call.name == "host.execute":
                                command = arguments.get("command")
                                if not isinstance(command, str) or not command:
                                    raise ValueError("host.execute requires a command")
                                host_arguments = dict(arguments)
                                host_arguments["cwd"] = str(fresh_workspace)
                                outcome = host_worker.invoke(host_arguments, timeout_ms=call_limit, timeout_code=timeout_code, timeout_detail=timeout_detail, cancelled=cancelled, cancellation_grace_ms=plan_record["cancellation_grace_ms"])
                            else:
                                executor = tools[call.name]
                                if not callable(getattr(executor, "execute", None)):
                                    raise TypeError("authorized tool does not expose execute")
                                outcome = tool_workers[call.name].invoke({"arguments": arguments}, timeout_ms=call_limit, timeout_code=timeout_code, timeout_detail=timeout_detail, cancelled=cancelled, cancellation_grace_ms=plan_record["cancellation_grace_ms"])
                        except Exception as exc:
                            tool_end = monotonic(); tool_duration = _duration_ms(tool_start, tool_end); last_activity = tool_end
                            cancellation = None; timed_out_code = None
                            if isinstance(exc, _RuntimeFailure) and exc.status == "cancelled":
                                cancellation = (exc.error_code, exc.detail); failure_status = "cancelled"
                            elif isinstance(exc, _RuntimeFailure) and exc.status == "timed_out":
                                failure_status, timed_out_code = "timed_out", exc.error_code
                            else:
                                cancellation = cancellation_state(tool_duration)
                                if cancellation is not None:
                                    failure_status = "cancelled"
                                elif _duration_ms(overall_start, tool_end) > deadlines["total"]:
                                    failure_status, timed_out_code = "timed_out", "replay.total_timeout"
                                elif tool_duration > deadlines["tool_call"]:
                                    failure_status, timed_out_code = "timed_out", "replay.tool_timeout"
                                else:
                                    failure_status = "failed"
                            error_ref = put("tool_outcome", {"schema_version": "bb.replay_tool_outcome.v1", "tool_call_id": call.id, "tool_name": call.name, "status": failure_status, "error_type": type(exc).__name__, "message": "tool execution failed"}, None, "secret_redacted")
                            tool_rows.append({"ref": error_ref.digest, "sha256": error_ref.digest})
                            if cancellation is not None:
                                raise _RuntimeFailure("cancelled", cancellation[0], cancellation[1]) from exc
                            if timed_out_code is not None:
                                detail = "replay exceeded its total deadline" if timed_out_code == "replay.total_timeout" else "tool call exceeded its deadline"
                                raise _RuntimeFailure("timed_out", timed_out_code, detail) from exc
                            raise _RuntimeFailure("host_failed" if call.name == "host.execute" else "tool_failed", "replay.host_failed" if call.name == "host.execute" else "replay.tool_failed", "authorized tool call failed") from exc
                        try:
                            canonical_json(outcome)
                        except Exception as exc:
                            tool_end = monotonic(); tool_duration = _duration_ms(tool_start, tool_end); last_activity = tool_end
                            cancellation = cancellation_state(tool_duration)
                            timed_out_code = None
                            if cancellation is not None:
                                failure_status = "cancelled"
                            elif _duration_ms(overall_start, tool_end) > deadlines["total"]:
                                failure_status, timed_out_code = "timed_out", "replay.total_timeout"
                            elif tool_duration > deadlines["tool_call"]:
                                failure_status, timed_out_code = "timed_out", "replay.tool_timeout"
                            else:
                                failure_status = "failed"
                            error_ref = put("tool_outcome", {"schema_version": "bb.replay_tool_outcome.v1", "tool_call_id": call.id, "tool_name": call.name, "status": failure_status, "error_type": type(exc).__name__, "message": "tool returned a non-canonical result"}, None, "secret_redacted")
                            tool_rows.append({"ref": error_ref.digest, "sha256": error_ref.digest})
                            if cancellation is not None:
                                raise _RuntimeFailure("cancelled", cancellation[0], cancellation[1]) from exc
                            if timed_out_code is not None:
                                detail = "replay exceeded its total deadline" if timed_out_code == "replay.total_timeout" else "tool call exceeded its deadline"
                                raise _RuntimeFailure("timed_out", timed_out_code, detail) from exc
                            raise _RuntimeFailure("host_failed" if call.name == "host.execute" else "tool_failed", "replay.host_invalid_result" if call.name == "host.execute" else "replay.tool_invalid_result", "authorized tool returned a non-canonical result") from exc
                        tool_end = monotonic(); tool_duration = _duration_ms(tool_start, tool_end); last_activity = tool_end
                        cancellation = cancellation_state(tool_duration)
                        timed_out_code = None
                        if cancellation is not None:
                            outcome_status = "cancelled"
                        elif _duration_ms(overall_start, tool_end) > deadlines["total"]:
                            outcome_status, timed_out_code = "timed_out", "replay.total_timeout"
                        elif tool_duration > deadlines["tool_call"]:
                            outcome_status, timed_out_code = "timed_out", "replay.tool_timeout"
                        else:
                            outcome_status = "completed"
                        outcome_record = {"schema_version": "bb.replay_tool_outcome.v1", "tool_call_id": call.id, "tool_name": call.name, "status": outcome_status}
                        if outcome_status == "completed":
                            outcome_record["result"] = outcome
                        outcome_ref = put("tool_outcome", outcome_record, None, "secret_redacted"); tool_rows.append({"ref": outcome_ref.digest, "sha256": outcome_ref.digest})
                        if cancellation is not None:
                            raise _RuntimeFailure("cancelled", cancellation[0], cancellation[1])
                        if timed_out_code is not None:
                            detail = "replay exceeded its total deadline" if timed_out_code == "replay.total_timeout" else "tool call exceeded its deadline"
                            raise _RuntimeFailure("timed_out", timed_out_code, detail)
                        messages.append({"role": "tool", "tool_call_id": call.id, "content": json.dumps(outcome, sort_keys=True)})
            except _RuntimeFailure as exc:
                safe_detail = _redact(exc.detail, secrets=secret_values, workspace=workspace.root, counter=redaction_count)
                terminal_status, completion_reason, failure = exc.status, safe_detail, problem(exc.error_code, safe_detail)
                if session.read_model.status not in ("completed", "failed", "canceled"):
                    session.cancel(safe_detail) if exc.status == "cancelled" else session.fail(exc.error_code, safe_detail)
            finally:
                for worker_name in ("tool_workers", "host_worker", "policy_worker", "provider_worker"):
                    worker_value = locals().get(worker_name)
                    if isinstance(worker_value, dict):
                        for worker in worker_value.values():
                            worker.close()
                    elif worker_value is not None:
                        worker_value.close()

            try:
                after = _snapshot(fresh_workspace, fresh_workspace_identity, fresh_workspace_descriptor)
            except _RuntimeFailure as exc:
                integrity_verified = False
                safe_detail = _redact(exc.detail, secrets=secret_values, workspace=workspace.root, counter=redaction_count)
                terminal_status, completion_reason, failure = exc.status, safe_detail, problem(exc.error_code, safe_detail)
                if session.read_model.status not in ("completed", "failed", "canceled"):
                    session.fail(exc.error_code, safe_detail)
                after = {"schema_version": "bb.replay_workspace_snapshot.v1", "files": [], "snapshot_sha256": sha256_json([])}
            if fresh_workspace_descriptor is not None:
                os.close(fresh_workspace_descriptor)
                fresh_workspace_descriptor = None
            if terminal_status == "completed" and session.read_model.status == "running":
                finalization_time = monotonic()
                cancellation = cancellation_state(_duration_ms(last_activity, finalization_time))
                if cancellation is not None:
                    terminal_status, completion_reason, failure = "cancelled", cancellation[1], problem(cancellation[0], cancellation[1])
                    session.cancel(cancellation[1])
                elif _duration_ms(overall_start, finalization_time) > deadlines["total"]:
                    completion_reason = "replay exceeded its total deadline"
                    terminal_status, failure = "timed_out", problem("replay.total_timeout", completion_reason)
                    session.fail("replay.total_timeout", completion_reason)
            if terminal_status == "completed" and session.read_model.status == "running":
                session.complete(completion_reason)
            after_ref = put("workspace_after", after, None); diff_ref = put("workspace_diff", _workspace_diff(before if "before" in locals() else {"files": []}, after), None)
            _verify_staging_path(stage, stage_identity, stage_descriptor)
            for child_name in os.listdir(stage_descriptor):
                if child_name == session_name:
                    continue
                _remove_entry_at(stage_descriptor, child_name)
            cleanup = ["fresh_workspace_removed", "staging_siblings_removed"]
            redacted_events = _redact(AnchoredStorage.read_at(stage_descriptor, session_name).decode("utf-8"), secrets=secret_values, workspace=workspace.root, counter=redaction_count)
            event_ref = store.put(str(redacted_events).encode("utf-8"), media_type="application/x-ndjson", created=created); artifacts.append(("kernel_event_stream", event_ref, None, "secret_redacted"))
            os.unlink(session_name, dir_fd=stage_descriptor)
            os.fsync(stage_descriptor)
            cleanup.append("raw_session_log_removed")
            policy_rows: list[dict[str, str]] = []
            for kind in ("policy", "approval", "capability"):
                decision_ref = put("policy_decision", {"schema_version": "bb.replay_policy_decision.v1", "kind": kind, "decision": policy_state[kind], "host_identity_sha256": _digest(host_identity.encode("utf-8")) if "host_identity" in locals() else None}, None)
                policy_rows.append({"kind": kind, "decision": policy_state[kind], "ref": decision_ref.digest, "sha256": decision_ref.digest})
            redaction_ref = put("redaction_report", {"schema_version": "bb.replay_redaction_report.v1", "redaction_count": redaction_count[0], "secret_values_stored": False, "absolute_workspace_paths_stored": False}, None, "secret_redacted")
            completed_at = active_clock.now(); duration = _duration_ms(overall_start, monotonic())
            entries = [{
                "artifact_id": f"artifact:{index}", "role": role, "location_kind": "object_ref", "location": ref.digest, "media_type": ref.media_type,
                "schema_id": schema_id, "size_bytes": ref.size_bytes, "sha256": ref.digest, "producer": "breadboard.product.evidence.replay_runner",
                "sensitivity": sensitivity, "created_at_utc": completed_at,
            } for index, (role, ref, schema_id, sensitivity) in enumerate(artifacts, 1)]
            manifest_unsigned = {"schema_version": "bb.replay_artifact_manifest.v1", "source_record_id": execution_id, "publish_status": "complete" if integrity_verified else "quarantined", "created_at_utc": completed_at, "entries": entries, "integrity_verified": integrity_verified}
            manifest_id = "replay_manifest:" + hashlib.sha256(canonical_json(manifest_unsigned)).hexdigest(); manifest = ReplayArtifactManifest.from_dict({"manifest_id": manifest_id, **manifest_unsigned})
            evidence_validators["manifest"].validate(manifest.as_dict())
            execution_record = {
                "schema_version": "bb.replay_execution.v1", "execution_id": execution_id, "fresh_nonce": fresh_nonce, "mode": "execute", "plan_sha256": plan.sha256,
                "lane_lock_sha256": plan_record["lane_lock_sha256"], "harness_lock_sha256": plan_record["harness_lock_sha256"], "started_at_utc": started_at,
                "completed_at_utc": completed_at, "duration_ms": duration, "terminal_status": terminal_status, "completion_reason": completion_reason,
                "kernel_event_stream": {"ref": event_ref.digest, "sha256": event_ref.digest}, "provider_exchanges": provider_rows, "tool_outcomes": tool_rows,
                "workspace_before": {"ref": before_ref.digest if "before_ref" in locals() else after_ref.digest, "sha256": before_ref.digest if "before_ref" in locals() else after_ref.digest},
                "workspace_after": {"ref": after_ref.digest, "sha256": after_ref.digest}, "workspace_diff": {"ref": diff_ref.digest, "sha256": diff_ref.digest},
                "artifact_manifest_id": manifest_id, "policy_decisions": policy_rows, "cleanup_ledger": cleanup,
                "nondeterminism_disclosures": ["host_platform", "provider_runtime", "wall_clock"], "redaction_report": {"ref": redaction_ref.digest, "sha256": redaction_ref.digest},
                "schema_validation_passed": True, "integrity_verified": integrity_verified, "claimable": False, "reuse_attestation_id": None,
                "normalization_evidence_ids": [], "comparison_report_id": None, "problem": failure,
            }
            evidence_validators["execution"].validate(execution_record)
            execution = ReplayExecution.from_dict(execution_record); execution.verify_plan(plan)
            AnchoredStorage.write_at(stage_descriptor, "execution.json", canonical_json(execution.as_dict()) + b"\n")
            AnchoredStorage.write_at(stage_descriptor, "manifest.json", canonical_json(manifest.as_dict()) + b"\n")
            _verify_staging_path(stage, stage_identity, stage_descriptor)
            publication_succeeded = False
            _rename_no_replace(replay_parent / stage_name, final, parent_descriptor=replay_parent_descriptor)
            publication_succeeded = True
            os.fsync(replay_parent_descriptor)
            final_stat = os.stat(final_name, dir_fd=replay_parent_descriptor, follow_symlinks=False)
            if (final_stat.st_dev, final_stat.st_ino) != stage_identity:
                raise ReplayRunError("published replay identity does not match the staging inode")
            if terminal_status == "completed" and integrity_verified:
                metadata_setter = getattr(getattr(provider_context, "session_state", None), "set_provider_metadata", None)
                if callable(metadata_setter):
                    for metadata_name, metadata_value in pending_provider_metadata.items():
                        metadata_setter(metadata_name, metadata_value)
            published_final = _staging_descriptor_path(replay_parent_descriptor) / final_name
            _close_descriptors(artifact_descriptors)
            os.close(stage_descriptor)
            _close_descriptors(replay_descriptors)
            return ReplayRunResult(execution, published_final / "execution.json", manifest)
    except BaseException as cause:
        cleanup_errors = []
        for worker_name in ("tool_workers", "host_worker", "policy_worker", "provider_worker"):
            worker_value = locals().get(worker_name)
            try:
                if isinstance(worker_value, dict):
                    for worker in worker_value.values():
                        worker.close()
                elif worker_value is not None:
                    worker_value.close()
            except BaseException as exc:
                cleanup_errors.append(exc)
        try:
            with store.transaction():
                for ref in tuple(created):
                    store.discard(ref)
        except BaseException as exc:
            cleanup_errors.append(exc)
        if "publication_succeeded" in locals() and publication_succeeded:
            try:
                final_metadata = os.stat(final_name, dir_fd=replay_parent_descriptor, follow_symlinks=False)
            except FileNotFoundError:
                pass
            except BaseException as exc:
                cleanup_errors.append(exc)
            else:
                if (final_metadata.st_dev, final_metadata.st_ino) != stage_identity:
                    try:
                        if stat.S_ISDIR(final_metadata.st_mode):
                            os.rmdir(final_name, dir_fd=replay_parent_descriptor)
                        else:
                            os.unlink(final_name, dir_fd=replay_parent_descriptor)
                    except BaseException as exc:
                        cleanup_errors.append(exc)
        if fresh_workspace_descriptor is not None:
            try:
                os.close(fresh_workspace_descriptor)
                fresh_workspace_descriptor = None
            except BaseException as exc:
                cleanup_errors.append(exc)
        try:
            _remove_tree_verified(replay_parent_descriptor, stage_name, stage_identity, stage_descriptor)
        except BaseException as exc:
            cleanup_errors.append(exc)
        finally:
            os.close(stage_descriptor)
        try:
            _close_descriptors(artifact_descriptors)
        except BaseException as exc:
            cleanup_errors.append(exc)
        try:
            _close_descriptors(replay_descriptors)
        except BaseException as exc:
            cleanup_errors.append(exc)
        if cleanup_errors:
            raise ReplayRunError("replay rollback cleanup failed") from cleanup_errors[0]
        raise cause
