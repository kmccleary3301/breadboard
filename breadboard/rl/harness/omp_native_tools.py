"""Pinned native-worker invocation contract for Oh My Pi 18.1.17.

This module deliberately does not reimplement Brush, EditStore, or EditSession.
A production controller must launch the pinned Bun workspace and load its real
Rust/NAPI leaves. Offline tests can validate the command, settings, admission,
and request/result projection without executing native code.
"""
from dataclasses import dataclass
import importlib.resources
import json
from pathlib import Path
import struct
import subprocess
from typing import Any, Mapping
OMP_COMMIT = "3b3a6dc9bbd85102ce19d0b1c11bf6870915f6ec"
OMP_SOURCE_ROOT = f"oh-my-pi-{OMP_COMMIT}"
OMP_CLI_RELATIVE = "packages/coding-agent/src/cli.ts"
OMP_BUN_LINUX_BASELINE = "/opt/omp/runtime/bun-linux-x64-baseline/bun"
OMP_SOURCE_ROOT_LINUX = f"/opt/omp/source/{OMP_SOURCE_ROOT}"
OMP_CLI_LINUX = f"{OMP_SOURCE_ROOT_LINUX}/{OMP_CLI_RELATIVE}"
OMP_TOOL_WORKER_RELATIVE = "breadboard/rl/harness/runners/omp_native_tool_worker.ts"
OMP_ARCHIVE_SHA256 = "sha256:67822418bad69de015d28a1bbd45fa7be689fdce367dfa3d584bdfcfbfcb5587"
OMP_LOCK_SHA256 = "sha256:9c0ed804704050ad4dbc3de84581694c7e505fdc39b0c13dea337300bae82a7e"

ALLOWED_TOOLS = ("read", "bash", "edit", "write")
EXCLUDED_CAPABILITIES = frozenset({"url", "ssh", "pty", "archive", "sqlite", "image", "video", "pdf", "document", "internal-resource"})


@dataclass(frozen=True)
class PinnedNativeWorkerSpec:
    """Identity and argv contract for the source-owned native worker."""

    bun: str = OMP_BUN_LINUX_BASELINE
    source_root: str = OMP_SOURCE_ROOT_LINUX
    cli: str = OMP_CLI_LINUX
    commit: str = OMP_COMMIT
    archive_sha256: str = OMP_ARCHIVE_SHA256
    lock_sha256: str = OMP_LOCK_SHA256
    platform: str = "linux-x64-baseline"

    def command(self, *, cwd: str, model: str, task: str, no_session: bool = True) -> tuple[str, ...]:
        """Exact supplier-compatible command used by the capture packet."""
        args = [self.bun, self.cli, "-p"]
        if no_session:
            args.append("--no-session")
        args.extend(("--model", model, task))
        return tuple(args)

    def worker_command(self, *, entrypoint: str, args: tuple[str, ...] = ()) -> tuple[str, ...]:
        """Invoke a worker entrypoint with the pinned Bun, never `/bin/sh`."""
        entry = str(Path(self.source_root) / entrypoint)
        return (self.bun, entry, *args)
    def tool_worker_command(self, *, cwd: str) -> tuple[str, ...]:
        """Run controller-owned SDK composition; it never starts model.prompt."""
        entry = str(Path(cwd) / OMP_TOOL_WORKER_RELATIVE)
        return (self.bun, entry, "--cwd", cwd)

    def as_dict(self) -> dict[str, Any]:
        return {
            "commit": self.commit,
            "source_root": self.source_root,
            "cli": self.cli,
            "bun": self.bun,
            "platform": self.platform,
            "archive_sha256": self.archive_sha256,
            "lock_sha256": self.lock_sha256,
            "tools": list(ALLOWED_TOOLS),
            "native_entrypoint": OMP_TOOL_WORKER_RELATIVE,
            "native_leaves": ["pi-natives EditStore/EditSession", "brush-core Shell", "uutils selection"],
            "offline_verifiable": ["argv", "identity digests", "settings", "tool admission", "result projection"],
            "requires_native_runtime": ["hashline patch application", "seen-anchor recovery", "brush shell execution", "filesystem lifecycle"],
        }


@dataclass(frozen=True)
class NativeInvocation:
    worker: str
    command: tuple[str, ...]
    cwd: str
    env: Mapping[str, str]
    lifecycle_owner: str = "breadboard"

    def as_dict(self) -> dict[str, Any]:
        return {
            "worker": self.worker,
            "command": list(self.command),
            "cwd": self.cwd,
            "env": dict(self.env),
            "lifecycle_owner": self.lifecycle_owner,
        }


def pinned_worker_spec() -> PinnedNativeWorkerSpec:
    return PinnedNativeWorkerSpec()


def build_native_invocation(*, cwd: str, entrypoint: str, args: tuple[str, ...] = (), env: Mapping[str, str] | None = None) -> NativeInvocation:
    spec = pinned_worker_spec()
    return NativeInvocation("omp-native-tool-core", spec.worker_command(entrypoint=entrypoint, args=args), cwd, dict(env or {}))


def supplier_cli_invocation(*, cwd: str, model: str, task: str) -> NativeInvocation:
    spec = pinned_worker_spec()
    return NativeInvocation("omp-supplier-cli", spec.command(cwd=cwd, model=model, task=task), cwd, {})


def validate_native_tool_name(name: str) -> None:
    if name not in ALLOWED_TOOLS:
        raise ValueError(f"OMP tool is not admitted: {name}")

def classify_capability(value: Any) -> str | None:
    """Classify excluded routes after literal local-path precedence."""
    if not isinstance(value, str):
        return None
    if value.startswith("file://"):
        return None
    for prefix, capability in (
        ("http://", "url"),
        ("https://", "url"),
        ("ssh://", "ssh"),
        ("artifact://", "internal-resource"),
        ("skill://", "internal-resource"),
        ("vault://", "internal-resource"),
        ("mcp://", "internal-resource"),
    ):
        if value.startswith(prefix):
            return capability
    return None


def deny_excluded_capabilities(arguments: Mapping[str, Any]) -> None:
    for key in ("path", "paths", "cwd", "command", "env", "input"):
        value = arguments.get(key)
        values = value if isinstance(value, list) else [value]
        for item in values:
            if isinstance(item, Mapping):
                nested = item.values()
            else:
                nested = (item,)
            for candidate in nested:
                capability = classify_capability(candidate)
                if capability is not None:
                    raise PermissionError(f"OMP capability denied before native resolution: {capability}")
    for key in ("pty", "async"):
        if arguments.get(key) is True:
            raise PermissionError(f"OMP capability denied: {key}")


class NativeToolWorker:
    """Lifecycle-owned adapter; real execution is intentionally explicit."""

    def __init__(self, *, cwd: str, env: Mapping[str, str] | None = None, spec: PinnedNativeWorkerSpec | None = None):
        self.cwd = cwd
        self.env = dict(env or {})
        self.spec = spec or pinned_worker_spec()
        self.started = False

    def invocation(self, entrypoint: str, args: tuple[str, ...] = ()) -> NativeInvocation:
        if not entrypoint or entrypoint.startswith("/"):
            raise ValueError("native entrypoint must be a relative pinned-workspace path")
        return build_native_invocation(cwd=self.cwd, entrypoint=entrypoint, args=args, env=self.env)

    def start(self) -> NativeInvocation:
        self.started = True
        return NativeInvocation(
            "omp-native-tool-core",
            self.spec.tool_worker_command(cwd=self.cwd),
            self.cwd,
            self.env,
        )

    def stop(self) -> None:
        self.started = False

    def execute(self, *_args: Any, **_kwargs: Any) -> Any:
        raise RuntimeError("native worker execution requires the pinned Bun/Rust assembly; offline controller has no shell fallback")


__all__ = [
    "ALLOWED_TOOLS",
    "EXCLUDED_CAPABILITIES",
    "NativeInvocation",
    "NativeToolWorker",
    "OMP_TOOL_WORKER_RELATIVE",
    "OMP_COMMIT",
    "OMP_LOCK_SHA256",
    "OMP_SOURCE_ROOT",
    "PinnedNativeWorkerSpec",
    "build_native_invocation",
    "classify_capability",
    "deny_excluded_capabilities",
    "pinned_worker_spec",
    "supplier_cli_invocation",
    "validate_native_tool_name",
]
