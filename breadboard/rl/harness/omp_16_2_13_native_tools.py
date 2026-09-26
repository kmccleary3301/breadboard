"""Pinned native-worker invocation contract for Oh My Pi 16.2.13.

This module provides the Python client adapter for the framed Bun worker
`omp_16_2_13_native_tool_worker.ts` bound to the npm flat layout under
`node_modules/@oh-my-pi/*`. It performs fail-closed SHA-256 integrity verification
on pinned source files before worker invocation.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import importlib.resources
import json
import os
from pathlib import Path
import select
import shutil
import struct
import subprocess
from typing import Any, Final, Mapping, Sequence


OMP_COMMIT: Final[str] = "5356713eae60e67ee64d9b02e3b5e377d248ee7f"
OMP_VERSION: Final[str] = "16.2.13"
CONSUMER_ID: Final[str] = "breadboard.oh-my-pi.v16.2.13"
LOCAL_ADAPTER_ID: Final[str] = "oh-my-pi.local.v16.2.13"
TARGET_ID: Final[str] = "oh-my-pi-r2@16.2.13"
ALLOWED_TOOLS: Final[tuple[str, ...]] = ("read", "bash", "edit", "write", "generate_image")
EXCLUDED_CAPABILITIES: Final[frozenset[str]] = frozenset(
    {"url", "ssh", "pty", "archive", "sqlite", "image", "video", "pdf", "document", "internal-resource"}
)

PINNED_MODULE_SHA256: Final[Mapping[str, str]] = {
    "@oh-my-pi/pi-coding-agent/src/sdk.ts": "73c7fd74ea75489b630e1962c09c3a6d748e142555c6102055fd85d1e6a616b4",
    "@oh-my-pi/pi-coding-agent/src/config/settings.ts": "38c0bb319d3baa0ed23334599d761665211f53ae24207fa540f75f905163897f",
    "@oh-my-pi/pi-coding-agent/src/config/model-registry.ts": "8610d641efde6cf06a1242356f83c7252ed3860508577b01c5715e7cda8bc763",
    "@oh-my-pi/pi-ai/src/stream.ts": "6669d6e0df952a5592890f5c200005d37e4e5a5ae5f2eb8f37dbb9e5ac3656bc",
    "@oh-my-pi/pi-ai/src/providers/openai-completions.ts": "a1b151be08db377cb755db4cd9160a246c9a30aaed0fc766b454e7a511dc4d49",
    "@oh-my-pi/pi-ai/src/utils/validation.ts": "eafa719221faca75ac8ce5af7168400058ace1d6b8594b8fb61e72b7dcd8b26e",
    "@oh-my-pi/pi-catalog/src/hosts.ts": "74f0701fdee8b803af23034216583ec8fc5d4a8119836abc3c842aafc7cc5811",
    "@oh-my-pi/pi-agent-core/src/agent-loop.ts": "ec162638dd7d6585f4388fbaa92caace47cafc61b27fc9d2601b7cc050375465",
    # Upstream 5356713e patchedDependencies (patches/@ark%2Fschema@0.56.0.patch) applied to @ark/schema@0.56.0.
    "@ark/schema/out/constraint.js": "bdfe5b022a56dd40bf47a93864f18a4b326c21a9b8eca86b977e814bb11bcd98",
}


class NativeWorkerPhaseError(RuntimeError):
    """A framed OMP 16.2.13 phase failed in the pinned worker."""


def verify_pinned_root(node_modules_root: Path | str) -> None:
    """Verify all pinned files under node_modules/@oh-my-pi/*; fails closed on missing or digest mismatch."""
    root = Path(node_modules_root).resolve()
    if not root.is_dir():
        raise FileNotFoundError(f"pinned OMP 16.2.13 node_modules root is not a directory: {root}")
    for relative_path, expected_sha in PINNED_MODULE_SHA256.items():
        file_path = root / relative_path
        if not file_path.is_file():
            raise FileNotFoundError(f"pinned OMP 16.2.13 module file missing: {relative_path}")
        observed_sha = hashlib.sha256(file_path.read_bytes()).hexdigest()
        if observed_sha != expected_sha:
            raise ValueError(
                f"pinned OMP 16.2.13 module sha mismatch for {relative_path}: "
                f"expected {expected_sha}, got {observed_sha}"
            )


def verified_tool_worker_path() -> Path:
    """Return the package-installed worker path."""
    resource = importlib.resources.files("breadboard.rl.harness.runners").joinpath(
        "omp_16_2_13_native_tool_worker.ts"
    )
    path = Path(resource).resolve()
    if not path.is_file():
        raise FileNotFoundError(f"installed OMP 16.2.13 phase worker is missing: {path}")
    return path


@dataclass(frozen=True, slots=True)
class PinnedNativeWorkerSpec:
    """Identity and argv contract for the OMP 16.2.13 native worker."""

    bun: str
    node_modules: str
    commit: str = OMP_COMMIT
    version: str = OMP_VERSION

    @classmethod
    def discover(cls, *, node_modules: Path | str | None = None, bun: Path | str | None = None) -> PinnedNativeWorkerSpec:
        if node_modules is not None:
            resolved_modules = str(node_modules)
        elif "OMP16213_CODING_AGENT_NODE_MODULES" in os.environ:
            resolved_modules = os.environ["OMP16213_CODING_AGENT_NODE_MODULES"]
        else:
            raise ValueError("OMP 16.2.13 node_modules root is not specified and OMP16213_CODING_AGENT_NODE_MODULES is unset")

        verify_pinned_root(resolved_modules)

        if bun is not None:
            resolved_bun = str(bun)
        elif "BB_BUN" in os.environ:
            resolved_bun = os.environ["BB_BUN"]
        else:
            discovered = shutil.which("bun")
            resolved_bun = discovered if discovered is not None else "bun"

        if not shutil.which(resolved_bun) and not Path(resolved_bun).is_file():
            raise FileNotFoundError(f"bun executable not found: {resolved_bun}")

        return cls(bun=resolved_bun, node_modules=str(Path(resolved_modules).resolve()))

    def tool_worker_command(self, *, cwd: str) -> tuple[str, ...]:
        worker = str(verified_tool_worker_path())
        return (self.bun, worker, "--cwd", cwd)


@dataclass(frozen=True, slots=True)
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


def deny_excluded_capabilities(
    arguments: Mapping[str, Any],
    *,
    denial_policy: Mapping[str, Mapping[str, Any]] | None = None,
) -> None:
    """Apply capability denial checks before dispatch."""
    policy = denial_policy if denial_policy is not None else {}
    for key in ("pty", "async"):
        if key in arguments and arguments[key] is True:
            if key not in policy:
                raise PermissionError(f"OMP capability denial policy unavailable: {key}")
            entry = policy[key]
            if not isinstance(entry, Mapping) or "message" not in entry or type(entry["message"]) is not str:
                raise PermissionError(f"OMP capability denial policy unavailable: {key}")
            raise PermissionError(entry["message"])


class NativeToolWorker:
    """Persistent framed adapter for the OMP 16.2.13 tool worker."""

    def __init__(
        self,
        *,
        cwd: str,
        env: Mapping[str, str] | None = None,
        spec: PinnedNativeWorkerSpec | None = None,
        timeout_seconds: float = 35.0,
    ) -> None:
        self.cwd = str(Path(cwd).resolve())
        self.env = dict(env if env is not None else {})
        self.spec = spec if spec is not None else PinnedNativeWorkerSpec.discover()
        self.timeout_seconds = timeout_seconds
        self.started = False
        self._process: subprocess.Popen[bytes] | None = None
        self._request_id = 0

    def start(self) -> NativeInvocation:
        self.started = True
        return NativeInvocation(
            "omp-16-2-13-native-tool-core",
            self.spec.tool_worker_command(cwd=self.cwd),
            self.cwd,
            self.env,
        )

    def _ensure_process(self) -> subprocess.Popen[bytes]:
        if self._process is not None:
            return self._process
        invocation = self.start()
        environment = os.environ.copy()
        environment["NODE_PATH"] = self.spec.node_modules
        environment["OMP16213_CODING_AGENT_NODE_MODULES"] = self.spec.node_modules
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
        timeout_seconds: float | None = None,
    ) -> dict[str, Any]:
        timeout = timeout_seconds if timeout_seconds is not None else self.timeout_seconds
        process = self._ensure_process()
        if process.stdin is None or process.stdout is None:
            raise NativeWorkerPhaseError("native worker pipes are unavailable")
        self._request_id += 1
        request_id = self._request_id
        request_payload = dict(payload)
        if operation == "initialize" and "node_modules" not in request_payload:
            request_payload["node_modules"] = self.spec.node_modules
        body = json.dumps(
            {
                "schema_version": "bb.native-worker.rpc.v1",
                "request_id": request_id,
                "operation": operation,
                "payload": request_payload,
            },
            separators=(",", ":"),
        ).encode("utf-8")
        process.stdin.write(struct.pack(">I", len(body)) + body)
        process.stdin.flush()
        response_bytes = self._read_frame(process.stdout, timeout)
        response = json.loads(response_bytes.decode("utf-8"))
        if (
            "schema_version" not in response
            or response["schema_version"] != "bb.native-worker.rpc.v1"
            or "request_id" not in response
            or response["request_id"] != request_id
        ):
            raise NativeWorkerPhaseError("native worker returned an invalid phase envelope")
        if "error" in response:
            error = response["error"]
            if isinstance(error, Mapping) and "message" in error:
                raise NativeWorkerPhaseError(str(error["message"]))
            raise NativeWorkerPhaseError(str(error))
        if "result" not in response:
            raise NativeWorkerPhaseError("native worker returned a phase envelope without result")
        result = response["result"]
        if (
            not isinstance(result, dict)
            or "schema_version" not in result
            or result["schema_version"] != "bb.omp-native.v16.2.13"
        ):
            raise NativeWorkerPhaseError("native worker returned an invalid OMP 16.2.13 phase result")
        return result

    def initialize(
        self,
        *,
        runtime_inputs: Mapping[str, str],
        model_config: Mapping[str, Any],
        workspace: str,
        scratch: str,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "runtime_inputs": dict(runtime_inputs),
            "model_config": dict(model_config),
            "workspace": workspace,
            "scratch": scratch,
        }
        res = self.phase("initialize", payload)
        if "bootstrap" not in res or not isinstance(res["bootstrap"], dict):
            raise NativeWorkerPhaseError("initialize phase result missing bootstrap dict")
        for name in ("current_date_time", "model_config"):
            if name not in res["bootstrap"]:
                raise NativeWorkerPhaseError(f"initialize bootstrap lacks {name}")
        return res

    def project_request(
        self,
        messages: Sequence[Mapping[str, Any]],
        *,
        system_prompt: str | None = None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {"messages": list(messages)}
        if system_prompt is not None:
            payload["system_prompt"] = system_prompt
        return self.phase("project_request", payload)

    def prepare_tools(self, calls: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
        return self.phase("prepare_tools", {"calls": list(calls)})

    def execute_batch(self, calls: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
        self.prepare_tools(calls)
        result = self.phase("execute_batch", {})
        if (
            "kind" not in result
            or result["kind"] != "tool_results"
            or "results" not in result
            or not isinstance(result["results"], list)
        ):
            raise NativeWorkerPhaseError("execute_batch returned an invalid result")
        return [item for item in result["results"] if isinstance(item, dict)]

    def project_provider_failure(
        self,
        *,
        http_status: int,
        response_body_text: str,
        messages: Sequence[Mapping[str, Any]],
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "http_status": http_status,
            "response_body_text": response_body_text,
            "messages": list(messages),
        }
        return self.phase("project_provider_failure", payload)

    def parse_streaming_json_batch(self, inputs: Sequence[str | None]) -> list[Any]:
        result = self.phase("parse_streaming_json_batch", {"inputs": list(inputs)})
        if "results" not in result or not isinstance(result["results"], list):
            raise NativeWorkerPhaseError("parse_streaming_json_batch returned non-list results")
        return result["results"]

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
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)


__all__ = [
    "ALLOWED_TOOLS",
    "CONSUMER_ID",
    "EXCLUDED_CAPABILITIES",
    "LOCAL_ADAPTER_ID",
    "NativeInvocation",
    "NativeToolWorker",
    "NativeWorkerPhaseError",
    "OMP_COMMIT",
    "OMP_VERSION",
    "PINNED_MODULE_SHA256",
    "PinnedNativeWorkerSpec",
    "TARGET_ID",
    "deny_excluded_capabilities",
    "verify_pinned_root",
    "verified_tool_worker_path",
]
