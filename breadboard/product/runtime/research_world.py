"""Private Ray bridge for the research-world worker.

The TypeScript worker owns the provider-neutral sandbox contract.  This module
only owns the real Ray actor lifecycle behind that contract; it is intentionally
not part of the public operation or SDK surface.
"""

from __future__ import annotations

import argparse
import base64
import binascii
import hashlib
import json
import os
import selectors
import shutil
import signal
import subprocess
import sys
from pathlib import Path
from tempfile import TemporaryDirectory
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
    for name in (
        "operation",
        "execution_id",
        "request_digest",
        "request_bytes",
        "ray_address",
        "ray_namespace",
    ):
        _required_text(payload.get(name), name)
    try:
        request_bytes = base64.b64decode(
            str(payload["request_bytes"]).encode("ascii"),
            validate=True,
        )
        request = json.loads(request_bytes.decode("utf-8"))
    except (UnicodeEncodeError, UnicodeDecodeError, binascii.Error, json.JSONDecodeError) as error:
        raise ValueError("Ray helper request bytes are invalid") from error
    if not isinstance(request, Mapping):
        raise ValueError("Ray helper request bytes must encode an object")
    command = request.get("command")
    if (
        not isinstance(command, list)
        or not command
        or any(type(part) is not str or not part for part in command)
    ):
        raise ValueError("Ray helper sandbox command is invalid")
    expected_digest = hashlib.sha256(request_bytes).hexdigest()
    if payload["request_digest"] != expected_digest:
        raise ValueError("Ray helper request digest does not match request bytes")
    operation = payload["operation"]
    receiver_identity = payload.get("receiver_identity")
    if operation not in {"submit", "resolve"}:
        _required_text(receiver_identity, "receiver_identity")
    elif receiver_identity is not None:
        _required_text(receiver_identity, "receiver_identity")
    max_output = payload.get("max_output_bytes")
    if type(max_output) is not int or not 1 <= max_output <= _MAX_OUTPUT_BYTES:
        raise ValueError("max_output_bytes must be between 1 and 4 MiB")
    payload["request"] = dict(request)
    payload["_request_bytes"] = request_bytes
    return payload


def _bounded_command(
    command: Sequence[str], workspace: str, max_output_bytes: int
) -> dict[str, Any]:
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
            "state": "failed",
        }
    return {
        "exit_code": exit_code,
        "stdout": bytes(buffers["stdout"]).decode("utf-8", errors="strict"),
        "stderr": bytes(buffers["stderr"]).decode("utf-8", errors="strict"),
        "state": "completed" if exit_code == 0 else "failed",
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
        ray.init(
            address="local",
            namespace=namespace,
            num_cpus=1,
            include_dashboard=False,
            log_to_driver=False,
            logging_level="ERROR",
        )
    else:
        ray.init(
            address=address,
            namespace=namespace,
            log_to_driver=False,
            logging_level="ERROR",
        )
    return ray


def _run_operation(
    payload: dict[str, Any],
    ray: Any,
    receiver_identities: dict[str, str] | None = None,
) -> dict[str, Any]:
    operation = str(payload["operation"])
    execution_id = str(payload["execution_id"])
    namespace = str(payload["ray_namespace"])
    name = _actor_name(namespace, execution_id)
    request = payload["request"]
    request_bytes = bytes(payload["_request_bytes"])
    request_digest = str(payload["request_digest"])
    receiver_identities = receiver_identities if receiver_identities is not None else {}

    @ray.remote
    def run(command: list[str], max_output_bytes: int) -> dict[str, Any]:
        with TemporaryDirectory(prefix="breadboard-research-world-") as workspace:
            return _bounded_command(command, workspace, max_output_bytes)

    @ray.remote
    class ResearchWorldJob:
        def __init__(self, retained_request_digest: str, retained_request_bytes: bytes) -> None:
            self.request_digest = retained_request_digest
            self.request_bytes = bytes(retained_request_bytes)
            self.result_ref: Any | None = None
            self.cancelled = False
            self.command: tuple[str, ...] | None = None
            self.executable_digest: str | None = None

        def _receiver_identity(self) -> tuple[str, str, str]:
            context = ray.get_runtime_context()
            actor_id = str(context.get_actor_id())
            node_id = str(context.get_node_id())
            receiver_identity = f"ray://receiver/{actor_id}/{node_id}"
            return receiver_identity, actor_id, node_id

        def _identity(self) -> dict[str, Any]:
            receiver_identity, actor_id, node_id = self._receiver_identity()
            return {
                "request_digest": self.request_digest,
                "receiver_identity": receiver_identity,
                "request_bytes_digest": hashlib.sha256(self.request_bytes).hexdigest(),
                "evidence_refs": [
                    f"ray://actors/{actor_id}",
                    f"ray://nodes/{node_id}",
                ],
            }

        def identity(self) -> dict[str, Any]:
            return self._identity()

        def _verify_request(self, supplied_digest: str, supplied_bytes: bytes) -> None:
            if (
                supplied_digest != self.request_digest
                or bytes(supplied_bytes) != self.request_bytes
            ):
                raise RuntimeError("Ray request bytes do not match retained actor")

        @staticmethod
        def _executable_digest(command: Sequence[str]) -> str:
            executable = Path(command[0])
            if not executable.is_absolute():
                resolved = shutil.which(command[0])
                if resolved is None:
                    raise RuntimeError("Ray request executable is unavailable")
                executable = Path(resolved)
            try:
                executable_bytes = executable.resolve().read_bytes()
            except OSError as error:
                raise RuntimeError("Ray request executable is unavailable") from error
            return hashlib.sha256(executable_bytes).hexdigest()

        def _verify_command(self, command: list[str]) -> None:
            supplied_command = tuple(command)
            executable_digest = self._executable_digest(command)
            if self.command is None:
                self.command = supplied_command
                self.executable_digest = executable_digest
            elif (
                supplied_command != self.command
                or executable_digest != self.executable_digest
            ):
                raise RuntimeError("Ray request executable bytes changed")

        def start(
            self,
            supplied_digest: str,
            supplied_bytes: bytes,
            command: list[str],
            max_output_bytes: int,
        ) -> str:
            self._verify_request(supplied_digest, supplied_bytes)
            self._verify_command(command)
            if self.result_ref is None and not self.cancelled:
                self.result_ref = run.remote(command, max_output_bytes)
            return "running"

        def _verify_retained_command(self) -> None:
            if self.command is not None:
                self._verify_command(list(self.command))

        def observe(self) -> dict[str, Any]:
            self._verify_retained_command()
            if self.cancelled:
                return {"state": "cancelled", **self._identity()}
            if self.result_ref is None:
                return {"state": "accepted", **self._identity()}
            ready, _ = ray.wait([self.result_ref], timeout=0)
            if not ready:
                return {"state": "running", **self._identity()}
            result = ray.get(self.result_ref)
            return {**dict(result), **self._identity()}

        def cancel(self) -> dict[str, Any]:
            verification_error: str | None = None
            try:
                self._verify_retained_command()
            except RuntimeError as error:
                verification_error = str(error)
            if self.result_ref is not None:
                ray.cancel(self.result_ref, force=True)
            self.cancelled = True
            return {
                "state": "cancelled",
                **self._identity(),
                **({"error": verification_error} if verification_error else {}),
            }

    try:
        actor = ray.get_actor(name, namespace=namespace)
    except ValueError:
        if operation in {"release", "resolve"}:
            receiver_identity = (
                str(payload["receiver_identity"])
                if operation == "release"
                else f"ray://absent/{name}"
            )
            receiver_identities[execution_id] = receiver_identity
            return {
                "state": "completed",
                "execution_id": execution_id,
                "request_digest": request_digest,
                "receiver_identity": receiver_identity,
                "evidence_refs": [],
            }
        if operation != "submit":
            raise RuntimeError("retained Ray execution is unavailable")
        actor = ResearchWorldJob.options(
            name=name, namespace=namespace, lifetime="detached"
        ).remote(request_digest, request_bytes)
    identity = ray.get(actor.identity.remote())
    if (
        not isinstance(identity, Mapping)
        or identity.get("request_digest") != request_digest
        or identity.get("request_bytes_digest") != hashlib.sha256(request_bytes).hexdigest()
    ):
        raise RuntimeError("Ray request bytes do not match retained actor")
    receiver_identity = identity.get("receiver_identity")
    if type(receiver_identity) is not str or not receiver_identity.strip():
        raise RuntimeError("Ray actor did not provide a receiver identity")
    expected_receiver = payload.get("receiver_identity")
    if expected_receiver is not None and expected_receiver != receiver_identity:
        raise RuntimeError("Ray receiver identity does not match retained actor")
    receiver_identities[execution_id] = receiver_identity

    def reply(state: str, **fields: Any) -> dict[str, Any]:
        return {
            "state": state,
            "execution_id": execution_id,
            "request_digest": request_digest,
            "receiver_identity": receiver_identity,
            "evidence_refs": identity["evidence_refs"],
            **fields,
        }

    if operation == "submit":
        ray.get(
            actor.start.remote(
                request_digest,
                request_bytes,
                request["command"],
                payload["max_output_bytes"],
            )
        )
        return reply("accepted")
    if operation == "resolve":
        return reply("accepted")
    if operation == "observe":
        result = ray.get(actor.observe.remote())
        if (
            result.get("request_digest") != request_digest
            or result.get("receiver_identity") != receiver_identity
        ):
            raise RuntimeError("Ray actor returned mismatched transport identity")
        result["execution_id"] = execution_id
        result["request_digest"] = request_digest
        result["receiver_identity"] = receiver_identity
        result["evidence_refs"] = identity["evidence_refs"]
        return result
    if operation == "cancel":
        result = ray.get(actor.cancel.remote())
        if (
            result.get("request_digest") != request_digest
            or result.get("receiver_identity") != receiver_identity
        ):
            raise RuntimeError("Ray actor returned mismatched transport identity")
        result["execution_id"] = execution_id
        result["request_digest"] = request_digest
        result["receiver_identity"] = receiver_identity
        result["evidence_refs"] = identity["evidence_refs"]
        return result
    if operation == "release":
        ray.get(actor.cancel.remote())
        ray.kill(actor, no_restart=True)
        return reply("completed")
    raise ValueError(f"unsupported Ray helper operation: {operation}")


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="breadboard research-world-helper")
    parser.add_argument("--research-world-helper", action="store_true", required=True)
    parser.parse_args(argv)
    ray = None
    ray_endpoint = None
    receiver_identities: dict[str, str] = {}

    def terminate(_signal: int, _frame: object) -> None:
        raise SystemExit(143)

    signal.signal(signal.SIGTERM, terminate)
    try:
        while raw := sys.stdin.buffer.readline(_MAX_INPUT_BYTES + 1):
            if len(raw) > _MAX_INPUT_BYTES:
                raise ValueError("Ray helper input exceeds bounded limit")
            payload = _request_payload(json.loads(raw.decode("utf-8")))
            selected = payload["ray_address"], payload["ray_namespace"]
            if ray is None:
                ray = _ray_runtime(*selected)
                ray_endpoint = selected
            elif selected != ray_endpoint:
                raise ValueError("Ray helper cannot switch its cluster or namespace")
            result = _run_operation(payload, ray, receiver_identities)
            encoded = json.dumps(result, separators=(",", ":")).encode("utf-8")
            if len(encoded) > _MAX_OUTPUT_BYTES:
                raise ValueError("Ray helper result exceeds bounded limit")
            sys.stdout.buffer.write(encoded + b"\n")
            sys.stdout.buffer.flush()
            if payload["operation"] == "release":
                break
        return 0
    except Exception as error:
        sys.stderr.write(f"{error}\n")
        return 1
    finally:
        if ray is not None:
            ray.shutdown()


if __name__ == "__main__":
    raise SystemExit(main())
