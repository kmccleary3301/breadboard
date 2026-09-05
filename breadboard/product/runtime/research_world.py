"""Private Ray bridge for the research-world worker.

The TypeScript worker owns the provider-neutral sandbox contract.  This module
only owns the real Ray actor lifecycle behind that contract; it is intentionally
not part of the public operation or SDK surface.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import selectors
import signal
import subprocess
import sys
from typing import Any, Mapping, Sequence

_MAX_INPUT_BYTES = 1024 * 1024
_MAX_OUTPUT_BYTES = 4 * 1024 * 1024


def _required_text(value: object, name: str) -> str:
    if type(value) is not str or not value.strip() or "\0" in value:
        raise ValueError(f"{name} must be a non-empty string")
    return value


def _request_payload(value: object) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError("Ray helper request must be an object")
    payload = dict(value)
    for name in ("operation", "execution_id", "request_digest", "workspace", "ray_address", "ray_namespace"):
        _required_text(payload.get(name), name)
    request = payload.get("request")
    if not isinstance(request, Mapping):
        raise ValueError("Ray helper request has no sandbox request")
    command = request.get("command")
    if not isinstance(command, list) or not command or any(type(part) is not str or not part for part in command):
        raise ValueError("Ray helper sandbox command is invalid")
    max_output = payload.get("max_output_bytes")
    if type(max_output) is not int or max_output < 1:
        raise ValueError("max_output_bytes must be a positive integer")
    return payload


def _bounded_command(command: Sequence[str], workspace: str, max_output_bytes: int) -> dict[str, Any]:
    child = subprocess.Popen(
        list(command),
        cwd=workspace,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        start_new_session=True,
    )
    assert child.stdout is not None and child.stderr is not None
    selector = selectors.DefaultSelector()
    selector.register(child.stdout, selectors.EVENT_READ, "stdout")
    selector.register(child.stderr, selectors.EVENT_READ, "stderr")
    buffers: dict[str, bytearray] = {"stdout": bytearray(), "stderr": bytearray()}
    overflow = False
    while selector.get_map():
        for key, _ in selector.select(timeout=0.1):
            chunk = key.fileobj.read1(65536)
            if not chunk:
                selector.unregister(key.fileobj)
                key.fileobj.close()
                continue
            target = buffers[str(key.data)]
            target.extend(chunk)
            if len(target) > max_output_bytes:
                overflow = True
                try:
                    os.killpg(child.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                break
        if overflow:
            break
    exit_code = child.wait()
    if overflow:
        return {
            "exit_code": exit_code,
            "stdout": "",
            "stderr": "ray command output exceeded bounded limit",
            "status": "failed",
        }
    return {
        "exit_code": exit_code,
        "stdout": bytes(buffers["stdout"]).decode("utf-8", errors="strict"),
        "stderr": bytes(buffers["stderr"]).decode("utf-8", errors="strict"),
        "status": "completed" if exit_code == 0 else "failed",
    }


def _actor_name(namespace: str, execution_id: str) -> str:
    digest = hashlib.sha256(f"{namespace}\0{execution_id}".encode()).hexdigest()[:40]
    return f"bb-research-world-{digest}"


def _ray_runtime(address: str, namespace: str) -> Any:
    try:
        import ray
    except ImportError as error:
        raise RuntimeError("Ray is not installed for the requested world") from error
    if address == "local":
        ray.init(namespace=namespace, ignore_reinit_error=True)
    else:
        ray.init(address=address, namespace=namespace, ignore_reinit_error=True)
    return ray


def _run_operation(payload: dict[str, Any]) -> dict[str, Any]:
    operation = str(payload["operation"])
    execution_id = str(payload["execution_id"])
    namespace = str(payload["ray_namespace"])
    address = str(payload["ray_address"])
    name = _actor_name(namespace, execution_id)
    ray = _ray_runtime(address, namespace)

    @ray.remote
    def run(command: list[str], workspace: str, max_output_bytes: int) -> dict[str, Any]:
        return _bounded_command(command, workspace, max_output_bytes)

    @ray.remote
    class ResearchWorldJob:
        def __init__(self, request_digest: str) -> None:
            self.request_digest = request_digest
            self.result_ref: Any | None = None
            self.cancelled = False

        def start(self, request_digest: str, command: list[str], workspace: str, max_output_bytes: int) -> str:
            if request_digest != self.request_digest:
                raise RuntimeError("Ray request digest does not match retained actor")
            if self.result_ref is None and not self.cancelled:
                self.result_ref = run.remote(command, workspace, max_output_bytes)
            return "running"

        def observe(self) -> dict[str, Any]:
            if self.cancelled:
                return {"state": "cancelled"}
            if self.result_ref is None:
                return {"state": "accepted"}
            ready, _ = ray.wait([self.result_ref], timeout=0)
            if not ready:
                return {"state": "running"}
            result = ray.get(self.result_ref)
            return dict(result)

        def cancel(self) -> dict[str, Any]:
            if self.result_ref is not None:
                ray.cancel(self.result_ref, force=True)
            self.cancelled = True
            return {"state": "cancelled"}

    try:
        actor = ray.get_actor(name, namespace=namespace)
    except ValueError:
        actor = ResearchWorldJob.options(name=name, namespace=namespace, lifetime="detached").remote(str(payload["request_digest"]))
    try:
        if operation == "submit":
            request = dict(payload["request"])
            ray.get(actor.start.remote(str(payload["request_digest"]), list(request["command"]), str(payload["workspace"]), int(payload["max_output_bytes"])))
            return {"state": "accepted", "execution_id": execution_id}
        if operation == "observe":
            result = ray.get(actor.observe.remote())
            result["execution_id"] = execution_id
            return result
        if operation == "cancel":
            result = ray.get(actor.cancel.remote())
            result["execution_id"] = execution_id
            return result
        if operation == "release":
            ray.kill(actor, no_restart=True)
            return {"state": "completed", "execution_id": execution_id}
        raise ValueError(f"unsupported Ray helper operation: {operation}")
    finally:
        ray.shutdown()


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="breadboard research-world-helper")
    parser.add_argument("--research-world-helper", action="store_true")
    parser.add_argument("operation", choices=("submit", "observe", "cancel", "release"))
    parser.parse_args(argv)
    try:
        raw = sys.stdin.buffer.read(_MAX_INPUT_BYTES + 1)
        if len(raw) > _MAX_INPUT_BYTES:
            raise ValueError("Ray helper input exceeds bounded limit")
        payload = _request_payload(json.loads(raw.decode("utf-8")))
        result = _run_operation(payload)
        encoded = json.dumps(result, separators=(",", ":")).encode("utf-8")
        if len(encoded) > _MAX_OUTPUT_BYTES:
            raise ValueError("Ray helper result exceeds bounded limit")
        sys.stdout.buffer.write(encoded)
        return 0
    except Exception as error:
        sys.stderr.write(f"{error}\n")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
