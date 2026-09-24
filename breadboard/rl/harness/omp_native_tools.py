"""Pinned native-worker invocation contract for Oh My Pi 18.1.17.

This module deliberately does not reimplement Brush, EditStore, or EditSession.
A production controller must launch the pinned Bun workspace and load its real
Rust/NAPI leaves. Offline tests can validate the command, settings, admission,
and request/result projection without executing native code.
"""
from dataclasses import dataclass
import importlib.resources
import json
import os
from pathlib import Path
import select
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

def verified_tool_worker_path() -> Path:
    """Return the package-installed worker, never a cwd-relative copy."""
    resource = importlib.resources.files("breadboard.rl.harness.runners").joinpath(
        "omp_native_tool_worker.ts"
    )
    path = Path(resource).resolve()
    if not path.is_file():
        raise FileNotFoundError(f"installed OMP phase worker is missing: {path}")
    return path
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
        """Run controller-owned SDK composition from the installed package."""
        entry = str(verified_tool_worker_path())
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

def classify_capability(
    value: Any,
    *,
    denial_policy: Mapping[str, Mapping[str, Any]] | None = None,
) -> str | None:
    """Classify a route using the target-declared denial matchers."""
    if not isinstance(value, str):
        return None
    candidate = value
    if candidate.lower().startswith("file://"):
        return None
    policy = denial_policy or {}
    scheme = candidate.split("://", 1)[0].lower() if "://" in candidate else None
    for capability, entry in policy.items():
        if not isinstance(entry, Mapping):
            continue
        route = entry.get("route")
        if not isinstance(route, Mapping):
            continue
        prefixes = route.get("prefixes")
        if isinstance(prefixes, list) and any(
            isinstance(prefix, str) and candidate.lower().startswith(prefix.lower())
            for prefix in prefixes
        ):
            return str(capability)
        schemes = route.get("schemes")
        if isinstance(schemes, list) and scheme in {item.lower() for item in schemes if isinstance(item, str)}:
            return str(capability)
        extensions = route.get("extensions")
        if isinstance(extensions, list):
            base = candidate.lower().split("?", 1)[0].split(":", 1)[0]
            if any(
                isinstance(extension, str) and base.endswith(extension.lower())
                for extension in extensions
            ):
                return str(capability)
    if policy:
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
        if candidate.startswith(prefix):
            return capability
    return None


def deny_excluded_capabilities(
    arguments: Mapping[str, Any],
    *,
    denial_policy: Mapping[str, Mapping[str, Any]] | None = None,
) -> None:
    policy = denial_policy or {}
    for key in ("path", "paths", "cwd", "input"):
        value = arguments.get(key)
        values = value if isinstance(value, list) else [value]
        for item in values:
            nested = item.values() if isinstance(item, Mapping) else (item,)
            for candidate in nested:
                capability = classify_capability(candidate, denial_policy=policy)
                if capability is not None:
                    entry = policy.get(capability)
                    if not isinstance(entry, Mapping) or type(entry.get("message")) is not str:
                        raise PermissionError(
                            f"OMP capability denial policy unavailable: {capability}"
                        )
                    raise PermissionError(entry["message"])
    for key in ("pty", "async"):
        if arguments.get(key) is True:
            entry = policy.get(key)
            if not isinstance(entry, Mapping) or entry.get("capability") != key or type(entry.get("message")) is not str:
                raise PermissionError(f"OMP capability denial policy unavailable: {key}")
            raise PermissionError(entry["message"])
class NativeWorkerPhaseError(RuntimeError):
    """A framed OMP phase failed in the pinned worker."""


class NativeToolWorker:
    """Persistent framed adapter for the source-owned OMP tool worker."""

    def __init__(
        self,
        *,
        cwd: str,
        env: Mapping[str, str] | None = None,
        spec: PinnedNativeWorkerSpec | None = None,
    ):
        self.cwd = cwd
        self.env = dict(env or {})
        self.spec = spec or pinned_worker_spec()
        self.started = False
        self._process: subprocess.Popen[bytes] | None = None
        self._request_id = 0

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

    def _ensure_process(self) -> subprocess.Popen[bytes]:
        if self._process is not None:
            return self._process
        invocation = self.start()
        environment = os.environ.copy()
        environment.update(self.env)
        self._process = subprocess.Popen(
            invocation.command,
            cwd=self.cwd,
            env=environment,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        return self._process

    @staticmethod
    def _read_frame(stream: Any, timeout: float) -> bytes:
        ready, _, _ = select.select([stream], [], [], timeout)
        if not ready:
            raise NativeWorkerPhaseError("timed out waiting for native worker phase")
        header = stream.read(4)
        if len(header) != 4:
            raise NativeWorkerPhaseError("native worker closed before phase response")
        length = struct.unpack(">I", header)[0]
        payload = stream.read(length)
        if len(payload) != length:
            raise NativeWorkerPhaseError("native worker returned a truncated phase frame")
        return payload

    def phase(
        self,
        operation: str,
        payload: Mapping[str, Any],
        *,
        timeout_seconds: float = 35.0,
    ) -> dict[str, Any]:
        if operation not in {"initialize", "project_request", "prepare_tools", "execute_batch", "close"}:
            raise ValueError(f"unknown OMP phase: {operation}")
        process = self._ensure_process()
        if process.stdin is None or process.stdout is None:
            raise NativeWorkerPhaseError("native worker pipes are unavailable")
        self._request_id += 1
        request_id = self._request_id
        body = json.dumps(
            {
                "schema_version": "bb.native-worker.rpc.v1",
                "request_id": request_id,
                "operation": operation,
                "payload": dict(payload),
            },
            separators=(",", ":"),
        ).encode()
        process.stdin.write(struct.pack(">I", len(body)) + body)
        process.stdin.flush()
        response = json.loads(self._read_frame(process.stdout, timeout_seconds))
        if response.get("schema_version") != "bb.native-worker.rpc.v1" or response.get("request_id") != request_id:
            raise NativeWorkerPhaseError("native worker returned an invalid phase envelope")
        if "error" in response:
            error = response["error"]
            raise NativeWorkerPhaseError(str(error.get("message", error)))
        result = response.get("result")
        if not isinstance(result, dict) or result.get("schema_version") != "bb.omp-native.v1":
            raise NativeWorkerPhaseError("native worker returned an invalid OMP phase result")
        return result

    def execute_batch(self, calls: list[Mapping[str, Any]]) -> list[dict[str, Any]]:
        prepared = self.phase("prepare_tools", {"calls": calls})
        if prepared.get("kind") != "prepared":
            raise NativeWorkerPhaseError("prepare_tools returned an invalid result")
        result = self.phase("execute_batch", {})
        if result.get("kind") != "tool_results" or not isinstance(result.get("results"), list):
            raise NativeWorkerPhaseError("execute_batch returned an invalid result")
        return [item for item in result["results"] if isinstance(item, dict)]
    def close(self) -> dict[str, Any]:
        if self._process is None:
            self.stop()
            return {"kind": "closed", "cleanup": {"processes": [], "all_dead": True}}
        try:
            return self.phase("close", {})
        finally:
            self.stop()

    def stop(self) -> None:
        process = self._process
        self._process = None
        self.started = False
        if process is None:
            return
        if process.poll() is None:
            process.terminate()
            process.wait(timeout=5)



__all__ = [
    "ALLOWED_TOOLS",
    "EXCLUDED_CAPABILITIES",
    "NativeInvocation",
    "NativeToolWorker",
    "NativeWorkerPhaseError",
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
    "verified_tool_worker_path",
]
