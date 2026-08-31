from __future__ import annotations

import argparse
import contextlib
import datetime as dt
import hashlib
import importlib.metadata
import inspect
import json
import os
import shutil
import shlex
import socket
import subprocess
import sys
import tarfile
import tempfile
import time
import zipfile
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import requests
from jsonschema import Draft202012Validator
from referencing import Registry, Resource

from breadboard_sdk import ApiError, BreadBoardClient
from breadboard_sdk.generated.public_bindings import PUBLIC_OPERATION_BINDINGS

I6_MATRIX_SHA256 = "bb044516c0ca195c9ff81606995fb8abe8fb6668a9bc24b097d4b24855136fe1"


def _bytes(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _digest(value: object) -> str:
    return hashlib.sha256(_bytes(value)).hexdigest()


def _file_digest(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _tree_files(root: Path) -> dict[str, str]:
    return {
        path.relative_to(root).as_posix(): _file_digest(path)
        for path in root.rglob("*")
        if path.is_file()
    }


def _tree_digest(root: Path) -> str:
    return _digest(sorted(_tree_files(root).items()))


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


@contextlib.contextmanager
def _server(
    python: Path,
    workspace: Path,
    *,
    token: str | None = None,
) -> Iterator[tuple[str, int]]:
    port = _free_port()
    env = {
        key: value
        for key, value in os.environ.items()
        if key not in {"PYTHONHOME", "PYTHONPATH", "BREADBOARD_API_TOKEN"}
    }
    env.update(
        {
            "BREADBOARD_CLI_HOST": "127.0.0.1",
            "BREADBOARD_CLI_PORT": str(port),
            "BREADBOARD_CLI_LOG_LEVEL": "error",
            "BREADBOARD_PUBLIC_WORKSPACE": str(workspace),
            "BREADBOARD_ENABLE_PUBLIC_API": "1",
            "BREADBOARD_ENABLE_E4_API": "0",
            "BREADBOARD_SESSION_STATE_ROOT": str(
                workspace / ".breadboard/session_state"
            ),
            "BREADBOARD_RUNTIME_RECORD_ROOT": str(
                workspace / ".breadboard/runtime_records"
            ),
            "BREADBOARD_SESSION_EVENT_ROOT": str(
                workspace / ".breadboard/session_events"
            ),
            "RAY_SCE_LOCAL_MODE": "1",
            "SESSION_TOKEN": "i6-secret-cancel-reason",
        }
    )
    if token is not None:
        env["BREADBOARD_API_TOKEN"] = token
    process = subprocess.Popen(
        [str(python), "-I", "-m", "breadboard_engine.api.cli_bridge.server"],
        cwd=workspace,
        env=env,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=True,
    )
    base_url = f"http://127.0.0.1:{port}"
    headers = {"Authorization": f"Bearer {token}"} if token is not None else {}
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        if process.poll() is not None:
            stderr = process.stderr.read() if process.stderr is not None else ""
            raise RuntimeError(f"installed server exited before readiness: {stderr}")
        try:
            response = requests.get(
                f"{base_url}/v1/health", headers=headers, timeout=0.5
            )
            if response.status_code == 200:
                break
        except requests.RequestException:
            pass
        time.sleep(0.05)
    else:
        raise RuntimeError("installed server did not become ready")
    try:
        yield base_url, process.pid
    finally:
        process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)
        if process.returncode not in {0, -15}:
            stderr = process.stderr.read() if process.stderr is not None else ""
            raise RuntimeError(f"installed server cleanup failed: {stderr}")


def _node_probe(
    node: Path,
    probe: Path,
    base_url: str,
    action_id: str,
    input_value: dict[str, Any],
    *,
    auth_token: str | None = None,
) -> tuple[int, dict[str, Any]]:
    environment = {
        key: value
        for key, value in os.environ.items()
        if key not in {"NODE_OPTIONS", "NODE_PATH"}
    }
    completed = subprocess.run(
        [str(node), str(probe)],
        input=json.dumps(
            {
                "base_url": base_url,
                "action_id": action_id,
                "input": input_value,
                "auth_token": auth_token,
            }
        ),
        text=True,
        env=environment,
        capture_output=True,
        check=False,
        timeout=30,
        cwd=probe.parent,
    )
    if not completed.stdout:
        raise RuntimeError(f"TypeScript probe produced no JSON: {completed.stderr}")
    return completed.returncode, json.loads(completed.stdout)


def _sse_events(response: requests.Response) -> list[dict[str, Any]]:
    response.raise_for_status()
    events: list[dict[str, Any]] = []
    wire_id: str | None = None
    data: list[str] = []
    for line in response.text.splitlines():
        if not line:
            if data:
                event = json.loads("\n".join(data))
                if wire_id != str(event["seq"]):
                    raise AssertionError("raw SSE id does not match event seq")
                events.append(event)
                wire_id = None
                data = []
            continue
        if line.startswith("id:"):
            wire_id = line[3:].lstrip()
        elif line.startswith("data:"):
            data.append(line[5:].lstrip())
    return events


def _input(operation_id: str, values: dict[str, Any]) -> dict[str, Any]:
    if operation_id == "harness.create":
        return {"directory": "shared"}
    if operation_id.startswith("harness.") and operation_id != "harness.list":
        result = {"harness_id": values["harness_id"]}
        if operation_id == "harness.update":
            result["definition"] = values["harness_definition"]
        return result
    if operation_id == "harness_lock.get":
        return {"lock_id": values["lock_id"]}
    if operation_id == "integration.get":
        return {"integration_id": values["integration_id"]}
    if operation_id == "integration.probe":
        return {
            "integration_id": values["integration_id"],
            "idempotency_key": "i6-integration-probe",
        }
    if operation_id.startswith("artifact.") and operation_id != "artifact.list":
        return {"artifact_id": values["artifact_id"]}
    if operation_id == "session.start":
        return {
            "lock_id": values["lock_id"],
            "task": "I6 differential session start",
            "session_id": "i6-differential-start",
            "idempotency_key": "i6-session-start",
        }
    if operation_id == "session.send_input":
        return {
            "session_id": values["input_session_id"],
            "content": "continue",
            "idempotency_key": "i6-session-input",
        }
    if operation_id == "session.approve":
        return {
            "session_id": values["approval_session_id"],
            "request_id": values["approval_request_id"],
            "decision": "deny",
            "idempotency_key": "i6-session-approve",
        }
    if operation_id == "session.resume":
        return {
            "session_id": values["runtime_session_id"],
            "idempotency_key": "i6-session-resume",
        }
    if operation_id == "session.cancel":
        return {
            "session_id": values["cancel_session_id"],
            "reason": "i6-secret-cancel-reason",
            "idempotency_key": "i6-session-cancel",
        }
    if operation_id == "session.events":
        return {"session_id": values["cancel_session_id"], "limit": 1000}
    if operation_id == "session.get":
        return {"session_id": values["start_session_id"]}
    if operation_id == "session.artifacts":
        return {"session_id": values["runtime_session_id"]}
    return {}


def _path(row: dict[str, Any], values: dict[str, Any]) -> str:
    return str(row["path"]).format(**values)


def _raw_call(
    session: requests.Session,
    base_url: str,
    row: dict[str, Any],
    values: dict[str, Any],
    input_value: dict[str, Any],
) -> tuple[int, Any]:
    operation_id = row["operation_id"]
    headers: dict[str, str] = {}
    body: dict[str, Any] | None = None
    params: dict[str, Any] | None = None
    key = input_value.get("idempotency_key")
    if key is not None:
        headers["Idempotency-Key"] = str(key)
    if operation_id == "harness.create":
        body = {"directory": input_value["directory"]}
    elif operation_id == "harness.update":
        body = {"definition": input_value["definition"]}
    elif operation_id == "session.start":
        body = {name: input_value[name] for name in ("lock_id", "task", "session_id")}
    elif operation_id == "session.send_input":
        body = {"content": input_value["content"]}
    elif operation_id == "session.approve":
        body = {
            "request_id": input_value["request_id"],
            "decision": input_value["decision"],
        }
    elif operation_id == "session.cancel":
        body = {"reason": input_value["reason"]}
    elif operation_id == "session.events":
        params = {"limit": input_value["limit"]}
    response = session.request(
        row["http_method"],
        f"{base_url}{_path(row, values)}",
        headers=headers,
        json=body,
        params=params,
        timeout=30,
    )
    if operation_id == "session.events" and response.status_code < 400:
        return response.status_code, _sse_events(response)
    return response.status_code, response.json()


def _python_call(
    client: BreadBoardClient,
    operation_id: str,
    input_value: dict[str, Any],
) -> Any:
    if operation_id == "harness.create":
        return client.create_harness(str(input_value["directory"]))
    if operation_id == "harness.list":
        return client.list_harness()
    if operation_id == "harness.get":
        return client.get_harness(str(input_value["harness_id"]))
    if operation_id == "harness.update":
        return client.update_harness(
            str(input_value["harness_id"]), input_value["definition"]
        )
    if operation_id == "harness.validate":
        return client.validate_harness(str(input_value["harness_id"]))
    if operation_id == "harness.explain":
        return client.explain_harness(str(input_value["harness_id"]))
    if operation_id == "harness.lock":
        return client.lock_harness(str(input_value["harness_id"]))
    if operation_id == "harness_lock.get":
        return client.get_harness_lock(str(input_value["lock_id"]))
    if operation_id == "integration.list":
        return client.list_integration()
    if operation_id == "integration.get":
        return client.get_integration(str(input_value["integration_id"]))
    if operation_id == "integration.probe":
        return client.probe_integration(
            str(input_value["integration_id"]),
            idempotency_key=str(input_value["idempotency_key"]),
        )
    if operation_id == "artifact.list":
        return client.list_artifact()
    if operation_id == "artifact.get":
        return client.get_artifact(str(input_value["artifact_id"]))
    if operation_id == "artifact.verify":
        return client.verify_artifact(str(input_value["artifact_id"]))
    if operation_id == "session.start":
        payload = {
            name: input_value[name] for name in ("lock_id", "task", "session_id")
        }
        return client.start_session(
            payload, idempotency_key=str(input_value["idempotency_key"])
        )
    if operation_id == "session.list":
        return client.list_session()
    if operation_id == "session.get":
        return client.get_session(str(input_value["session_id"]))
    if operation_id == "session.send_input":
        return client.send_input_session(
            str(input_value["session_id"]),
            str(input_value["content"]),
            idempotency_key=str(input_value["idempotency_key"]),
        )
    if operation_id == "session.approve":
        return client.approve_session(
            str(input_value["session_id"]),
            str(input_value["request_id"]),
            str(input_value["decision"]),
            idempotency_key=str(input_value["idempotency_key"]),
        )
    if operation_id == "session.resume":
        return client.resume_session(
            str(input_value["session_id"]),
            idempotency_key=str(input_value["idempotency_key"]),
        )
    if operation_id == "session.cancel":
        return client.cancel_session(
            str(input_value["session_id"]),
            str(input_value["reason"]),
            idempotency_key=str(input_value["idempotency_key"]),
        )
    if operation_id == "session.events":
        return list(client.events_session(str(input_value["session_id"]), limit=1000))
    if operation_id == "session.artifacts":
        return client.artifacts_session(str(input_value["session_id"]))
    return getattr(
        client,
        {
            "system.describe": "describe_system",
            "system.health": "health_system",
            "system.schemas": "schemas_system",
        }[operation_id],
    )()


def _wait_status(
    session: requests.Session,
    base_url: str,
    session_id: str,
    statuses: set[str],
    timeout: float = 10,
) -> dict[str, Any]:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        response = session.get(f"{base_url}/v1/sessions/{session_id}", timeout=5)
        response.raise_for_status()
        view = response.json()["data"]["session"]
        if view["status"] in statuses:
            return view
        time.sleep(0.025)
    raise AssertionError(f"session {session_id} did not reach {sorted(statuses)}")


def _start(
    session: requests.Session,
    base_url: str,
    lock_id: str,
    session_id: str,
    key: str,
    task: str,
) -> dict[str, Any]:
    response = session.post(
        f"{base_url}/v1/sessions",
        json={"lock_id": lock_id, "task": task, "session_id": session_id},
        headers={"Idempotency-Key": key},
        timeout=30,
    )
    if response.status_code != 202:
        raise AssertionError(response.text)
    return response.json()


def _setup_approval(
    session: requests.Session,
    base_url: str,
    *,
    directory: str,
    session_id: str,
    key: str,
) -> tuple[str, str]:
    created = session.post(
        f"{base_url}/v1/harnesses", json={"directory": directory}, timeout=30
    )
    created.raise_for_status()
    harness_id = created.json()["data"]["path"]
    definition = session.get(
        f"{base_url}/v1/harnesses/{harness_id}", timeout=30
    ).json()["data"]["definition"]
    definition["permissions"]["edit"] = {"default": "ask"}
    updated = session.put(
        f"{base_url}/v1/harnesses/{harness_id}",
        json={"definition": definition},
        timeout=30,
    )
    updated.raise_for_status()
    lock_id = session.post(
        f"{base_url}/v1/harnesses/{harness_id}/lock", timeout=30
    ).json()["data"]["path"]
    _start(
        session,
        base_url,
        lock_id,
        session_id,
        key,
        "Reach a configured edit approval.",
    )
    view = _wait_status(session, base_url, session_id, {"awaiting_approval"})
    return session_id, str(view["pending_approval"])


def _record_result(operation_id: str, status: int, value: Any) -> dict[str, Any]:
    row: dict[str, Any] = {
        "operation_id": operation_id,
        "status": status,
        "result_sha256": _digest(value),
        "raw_python_typescript_equal": True,
    }
    if isinstance(value, dict):
        session = value.get("data", {}).get("session")
        if isinstance(session, dict) and isinstance(session.get("event_count"), int):
            row["event_count"] = session["event_count"]
    return row


def _run_cli(
    bbh: Path,
    workspace: Path,
    arguments: list[str],
    env: dict[str, str],
) -> dict[str, Any]:
    namespace, *tail = arguments
    namespace_options = ["--workspace", str(workspace)]
    if namespace == "session":
        namespace_options.extend(["--server", env["BREADBOARD_I6_SERVER"]])
    completed = subprocess.run(
        [str(bbh), "--json", namespace, *namespace_options, *tail],
        cwd=workspace,
        env=env,
        text=True,
        capture_output=True,
        check=False,
        timeout=130,
    )
    try:
        value = json.loads(completed.stdout)
    except json.JSONDecodeError as error:
        raise AssertionError(
            f"CLI emitted non-JSON for {arguments}: {completed.stdout} {completed.stderr}"
        ) from error
    if completed.returncode != value.get("exit_code"):
        raise AssertionError(f"CLI exit mismatch for {arguments}: {value}")
    return value


def _cli_projection_contract(value: dict[str, Any]) -> dict[str, Any]:
    return {
        "schema_version": value["schema_version"],
        "ok": value["ok"],
        "status": value["status"],
        "command": value["command"],
        "exit_code": value["exit_code"],
        "error": value["error"],
        "stage_outcomes": value["stage_outcomes"],
        "record_refs": value["record_refs"],
        "hashes": value["hashes"],
        "warnings": value["warnings"],
        "next_actions": value["next_actions"],
        "data": value["data"],
    }


def _sha256_value(value: object) -> bool:
    if not isinstance(value, str) or not value.startswith("sha256:"):
        return False
    digest = value.removeprefix("sha256:")
    return len(digest) == 64 and all(
        character in "0123456789abcdef" for character in digest
    )


def _installed_relative_file(value: object, workspace: Path) -> str:
    if not isinstance(value, str):
        raise TypeError("CLI file projection is not a string")
    path = Path(value)
    candidate = (workspace / path).resolve()
    if path.is_absolute() or not candidate.is_relative_to(workspace.resolve()):
        raise AssertionError("CLI file projection escaped its workspace")
    if not candidate.is_file():
        raise AssertionError(f"CLI file projection does not exist: {value}")
    return path.name


def _normalize_cli_projection(
    operation_id: str, value: dict[str, Any], workspace: Path
) -> dict[str, Any]:
    normalized = json.loads(json.dumps(_cli_projection_contract(value)))
    if operation_id == "harness.create":
        data = normalized["data"]
        keys = ("path", "prompt_path", "model_roles_path")
        if not isinstance(data, dict) or normalized["record_refs"] != [
            data.get(key) for key in keys
        ]:
            raise AssertionError(
                "harness.create record references do not match its data"
            )
        names = [_installed_relative_file(data[key], workspace) for key in keys]
        for key, name in zip(keys, names, strict=True):
            data[key] = name
        normalized["record_refs"] = names
    elif operation_id == "harness.lock":
        data = normalized["data"]
        actions = normalized["next_actions"]
        if (
            not isinstance(data, dict)
            or not isinstance(data.get("path"), str)
            or not isinstance(actions, list)
            or len(actions) != 1
            or not isinstance(actions[0], str)
        ):
            raise AssertionError("harness.lock omitted its run action")
        lock_path = Path(data["path"])
        if not lock_path.name.endswith(".lock.json"):
            raise AssertionError("harness.lock returned a non-lock path")
        source_path = lock_path.with_name(
            f"{lock_path.name.removesuffix('.lock.json')}.yaml"
        )
        tokens = shlex.split(actions[0])
        if (
            len(tokens) != 5
            or tokens[:3] != ["breadboard", "harness", "run"]
            or tokens[4] != "--local"
        ):
            raise AssertionError("harness.lock returned an invalid run action")
        action_path = Path(tokens[3])
        resolved_action = (
            action_path.resolve()
            if action_path.is_absolute()
            else (workspace / action_path).resolve()
        )
        if resolved_action != (workspace / source_path).resolve():
            raise AssertionError("harness.lock run action targets another harness")
        normalized["next_actions"] = [
            f"breadboard harness run {source_path.as_posix()} --local"
        ]
    elif operation_id == "integration.probe":
        probe = normalized.get("data", {}).get("probe")
        checked_at = probe.get("checked_at_utc") if isinstance(probe, dict) else None
        if not isinstance(checked_at, str):
            raise AssertionError("integration.probe omitted checked_at_utc")
        try:
            parsed = dt.datetime.fromisoformat(checked_at)
        except ValueError as error:
            raise AssertionError(
                "integration.probe returned an invalid timestamp"
            ) from error
        if parsed.tzinfo is None:
            raise AssertionError("integration.probe timestamp has no timezone")
        probe["checked_at_utc"] = "<checked_at_utc>"
    elif operation_id in {
        "session.approve",
        "session.cancel",
        "session.resume",
        "session.send_input",
    }:
        session = normalized.get("data", {}).get("session")
        hashes = normalized.get("hashes")
        if not isinstance(session, dict) or not isinstance(hashes, dict):
            raise AssertionError(f"{operation_id} omitted its session projection")
        if (
            not isinstance(session.get("session_id"), str)
            or not session["session_id"]
            or type(session.get("event_count")) is not int
            or session["event_count"] < 0
            or not _sha256_value(session.get("effective_lock_hash"))
            or not _sha256_value(session.get("task_hash"))
            or hashes
            != {
                "lock": session["effective_lock_hash"],
                "task": session["task_hash"],
            }
        ):
            raise AssertionError(f"{operation_id} returned inconsistent session values")
        session["session_id"] = "<session_id>"
        session["event_count"] = 0
        session["effective_lock_hash"] = "<effective_lock_hash>"
        session["task_hash"] = "<task_hash>"
        normalized["hashes"] = {
            "lock": "<effective_lock_hash>",
            "task": "<task_hash>",
        }
    return normalized


def _verify_cli_session_identity(
    operation_id: str,
    value: dict[str, Any],
    observed: dict[str, Any] | None,
    validator: Draft202012Validator,
) -> None:
    if observed is None:
        raise AssertionError(f"{operation_id} has no raw session identity observation")
    errors = tuple(validator.iter_errors(observed))
    cli_session = value.get("data", {}).get("session")
    raw_session = observed.get("data", {}).get("session")
    if (
        errors
        or not isinstance(cli_session, dict)
        or not isinstance(raw_session, dict)
        or any(
            cli_session.get(field) != raw_session.get(field)
            for field in ("session_id", "effective_lock_hash", "task_hash")
        )
        or type(raw_session.get("event_count")) is not int
        or raw_session["event_count"] < cli_session.get("event_count", -1)
    ):
        raise AssertionError(f"{operation_id} returned an unobserved session identity")
    if cli_session.get("status") in {"completed", "failed", "canceled"} and (
        raw_session.get("status") != cli_session["status"]
        or raw_session.get("terminal_outcome") != cli_session.get("terminal_outcome")
    ):
        raise AssertionError(
            f"{operation_id} terminal projection changed after delivery"
        )


def _validate_event_stream(
    value: Any,
    event_validator: Draft202012Validator,
    payload_validators: dict[str, Draft202012Validator],
    *,
    session_id: str | None = None,
) -> list[dict[str, Any]]:
    if not isinstance(value, list) or not value:
        raise AssertionError("installed SSE stream is empty or invalid")
    events: list[dict[str, Any]] = []
    for event in value:
        if not isinstance(event, dict):
            raise TypeError("installed SSE event is not an object")
        event_errors = tuple(event_validator.iter_errors(event))
        if event_errors:
            raise AssertionError(
                f"installed SSE envelope is invalid: {event_errors[0].message}"
            )
        if session_id is not None and event["session_id"] != session_id:
            raise AssertionError("installed SSE event belongs to another session")
        schema_id = event["payload_schema_version"]
        payload_validator = payload_validators.get(schema_id)
        if payload_validator is None:
            raise AssertionError(
                f"installed SSE payload schema is unresolved: {schema_id}"
            )
        payload_errors = tuple(payload_validator.iter_errors(event["payload"]))
        if payload_errors:
            raise AssertionError(
                f"installed SSE payload violates {schema_id}: "
                f"{payload_errors[0].message}"
            )
        events.append(event)
    sequences = [event["seq"] for event in events]
    if sequences != list(range(sequences[0], sequences[0] + len(sequences))):
        raise AssertionError("session event sequences are not contiguous")
    forged_terminal = {
        **events[0],
        "kind": "session.completed",
        "payload": {},
        "payload_schema_version": "bb.payload.product_session.lifecycle.v1",
    }
    if event_validator.is_valid(forged_terminal):
        raise AssertionError("SSE kind is not bound to its lifecycle payload shape")
    return events


def _verify_cli_projection(
    operation_id: str,
    value: dict[str, Any],
    raw: Any,
    validator: Draft202012Validator,
    event_validator: Draft202012Validator,
    payload_validators: dict[str, Draft202012Validator],
    *,
    workspace: Path,
    observed_session: dict[str, Any] | None = None,
) -> str:
    errors = tuple(validator.iter_errors(value))
    if errors:
        raise AssertionError(
            f"CLI {operation_id} violates bb.cli.result.v1: {errors[0].message}"
        )
    contract = _cli_projection_contract(value)
    if operation_id == "session.start":
        raw_errors = tuple(validator.iter_errors(raw))
        data = value["data"]
        raw_session = raw.get("data", {}).get("session")
        if (
            raw_errors
            or value["command"] != ["harness", "run"]
            or value["status"] != "ok"
            or value["exit_code"] != 0
            or value["error"] is not None
            or value["stage_outcomes"]
            != [
                {
                    "stage": "harness.run",
                    "status": "passed",
                    "report_ref": None,
                    "next_action": None,
                }
            ]
            or not isinstance(data, dict)
            or set(data) != {"session_id", "record_count", "event_count"}
            or not isinstance(raw_session, dict)
            or data["session_id"] != raw_session.get("session_id")
            or type(data["event_count"]) is not int
            or data["record_count"] != data["event_count"]
            or data["event_count"] != raw_session.get("event_count")
            or value["hashes"] != raw.get("hashes")
        ):
            raise AssertionError(
                "CLI harness.run does not project its raw session.start result"
            )
        return _digest(
            {
                "cli": contract,
                "raw_session": _cli_projection_contract(raw),
            }
        )
    if operation_id == "session.events":
        data = value["data"]
        controls = {
            "schema_version": value["schema_version"],
            "ok": value["ok"],
            "status": value["status"],
            "command": value["command"],
            "exit_code": value["exit_code"],
            "error": value["error"],
            "record_refs": value["record_refs"],
            "hashes": value["hashes"],
            "warnings": value["warnings"],
            "next_actions": value["next_actions"],
            "stage_outcomes": value["stage_outcomes"],
        }
        expected_controls = {
            "schema_version": "bb.cli.result.v1",
            "ok": True,
            "status": "ok",
            "command": ["session", "events"],
            "exit_code": 0,
            "error": None,
            "record_refs": [],
            "hashes": {},
            "warnings": [],
            "next_actions": [],
            "stage_outcomes": [
                {
                    "stage": "session.events",
                    "status": "passed",
                    "report_ref": None,
                    "next_action": None,
                }
            ],
        }
        if (
            controls != expected_controls
            or not isinstance(data, dict)
            or set(data) != {"session_id", "events"}
        ):
            raise AssertionError(
                "CLI session.events projection differs from the raw SSE contract"
            )
        session_id = data["session_id"]
        if not isinstance(session_id, str) or not session_id:
            raise AssertionError("CLI session.events omitted its session identity")
        raw_events = _validate_event_stream(raw, event_validator, payload_validators)
        cli_events = _validate_event_stream(
            data["events"],
            event_validator,
            payload_validators,
            session_id=session_id,
        )
        if cli_events != raw_events:
            raise AssertionError(
                "CLI session.events values differ from the raw SSE stream"
            )
        for observed in (raw_events, cli_events):
            kinds = {event["kind"] for event in observed}
            if (
                not {"session.started", "session.canceled"} <= kinds
                or observed[-1]["kind"] != "session.canceled"
            ):
                raise AssertionError(
                    "CLI session.events does not preserve terminal SSE semantics"
                )
        return _digest(contract)
    if operation_id in {
        "session.approve",
        "session.cancel",
        "session.resume",
        "session.send_input",
    }:
        _verify_cli_session_identity(
            operation_id,
            value,
            observed_session,
            validator,
        )
    contract = _normalize_cli_projection(operation_id, value, workspace)
    expected = _normalize_cli_projection(operation_id, raw, workspace)
    if contract != expected:
        raise AssertionError(
            f"CLI {operation_id} projection differs from raw public contract: "
            f"{_digest(contract)} != {_digest(expected)}"
        )
    return _digest(contract)


def _python_error(operation: Callable[[], Any]) -> tuple[int, Any]:
    try:
        operation()
    except ApiError as error:
        return error.status, error.body
    raise AssertionError("Python SDK accepted an operation expected to fail")


def _validate_public_error(
    *,
    body: Any,
    status: int,
    error_code: str,
    validator: Draft202012Validator,
    forbidden: tuple[str, ...],
) -> dict[str, Any]:
    if not isinstance(body, dict):
        raise TypeError(f"HTTP {status} error body is not an object")
    errors = tuple(validator.iter_errors(body))
    if errors:
        raise AssertionError(
            f"HTTP {status} error violates bb.cli.result.v1: {errors[0].message}"
        )
    expected_exit = {404: 3, 409: 6, 422: 2, 500: 4}[status]
    if (
        body.get("ok") is not False
        or body.get("status") != "error"
        or body.get("exit_code") != expected_exit
        or not isinstance(body.get("error"), dict)
        or body["error"].get("schema_version") != "bb.problem.v1"
        or body["error"].get("error_code") != error_code
    ):
        raise AssertionError(f"HTTP {status} error is not typed as {error_code}")
    serialized = json.dumps(body, sort_keys=True)
    if any(value and value in serialized for value in forbidden):
        raise AssertionError(f"HTTP {status} error leaked a forbidden value")
    return body


def _format_cli(arguments: list[str], values: dict[str, Any]) -> list[str]:
    return [str(argument).format(**values) for argument in arguments]


def _typed_error_input(
    operation_id: str,
    fixture: str,
    values: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    error_values = {
        **values,
        "approval_session_id": "i6-error-missing",
        "cancel_session_id": "i6-error-missing",
        "input_session_id": "i6-error-missing",
        "runtime_session_id": "i6-error-missing",
        "session_id": "i6-error-missing",
        "start_session_id": "i6-error-missing",
    }
    if fixture == "invalid_artifact":
        error_values["artifact_id"] = "not-a-digest"
    elif fixture == "missing_artifact":
        error_values["artifact_id"] = f"sha256:{'0' * 64}"
    elif fixture == "missing_harness":
        error_values["harness_id"] = "i6-error-missing.yaml"
    elif fixture == "missing_lock":
        error_values["lock_id"] = "i6-error-missing.lock.json"
    elif fixture == "invalid_harness_definition":
        error_values["harness_definition"] = values["invalid_harness_definition"]
    elif fixture == "missing_integration":
        error_values["integration_id"] = "i6-error-missing"
    elif fixture not in {"missing_session", "runtime_filename"}:
        raise AssertionError(f"unknown typed-error fixture: {fixture}")

    input_value = _input(operation_id, error_values)
    if fixture == "invalid_harness_definition":
        input_value["definition"] = {}
    if "idempotency_key" in input_value:
        input_value["idempotency_key"] = f"i6-error-{operation_id.replace('.', '-')}"
    return error_values, input_value


def _verify_installed_typed_error(
    *,
    row: dict[str, Any],
    error_case: dict[str, Any],
    values: dict[str, Any],
    transport: requests.Session,
    client: BreadBoardClient,
    base_url: str,
    node: Path,
    installed_probe: Path,
    bbh: Path,
    workspace: Path,
    cli_env: dict[str, str],
    validator: Draft202012Validator,
    forbidden: tuple[str, ...],
) -> dict[str, Any]:
    operation_id = str(row["operation_id"])
    error_values, input_value = _typed_error_input(
        operation_id,
        str(error_case["fixture"]),
        values,
    )
    workspace_files_before = _tree_files(workspace)
    raw_status, raw_value = _raw_call(
        transport,
        base_url,
        row,
        error_values,
        input_value,
    )
    raw_error = _validate_public_error(
        body=raw_value,
        status=raw_status,
        error_code=str(error_case["error_code"]),
        validator=validator,
        forbidden=forbidden,
    )
    if raw_status != error_case["status"]:
        raise AssertionError(
            f"{operation_id} typed-error status differs: "
            f"{raw_status} != {error_case['status']}"
        )

    python_error = _python_error(
        lambda: _python_call(client, operation_id, input_value)
    )
    typescript_code, typescript_error = _node_probe(
        node,
        installed_probe,
        base_url,
        str(row["typescript_action"]),
        input_value,
    )
    cli_arguments = _format_cli(row["cli"], error_values)
    if "--idempotency-key" in cli_arguments:
        key_index = cli_arguments.index("--idempotency-key") + 1
        cli_arguments[key_index] = str(input_value["idempotency_key"])
    cli_error = _run_cli(
        bbh,
        workspace,
        cli_arguments,
        cli_env,
    )
    cli_projection = error_case.get("cli_projection", "exact")
    cli_contract = cli_error
    if cli_projection == "harness_run_to_session_start":
        cli_contract = json.loads(json.dumps(cli_error))
        cli_contract["command"] = ["session", "start"]
        cli_contract["error"]["failed_stage"] = "session.start"
        for outcome in cli_contract["stage_outcomes"]:
            outcome["stage"] = "session.start"
    elif cli_projection != "exact":
        raise AssertionError(f"unknown CLI error projection: {cli_projection}")
    if (
        python_error != (raw_status, raw_error)
        or typescript_code != 2
        or typescript_error.get("error") != {"status": raw_status, "body": raw_error}
        or cli_contract != raw_error
    ):
        raise AssertionError(
            f"{operation_id} raw/Python/TypeScript/CLI typed-error parity failed"
        )

    workspace_files_after = _tree_files(workspace)
    added_paths = sorted(workspace_files_after.keys() - workspace_files_before.keys())
    removed_paths = workspace_files_before.keys() - workspace_files_after.keys()
    changed_paths = {
        path
        for path in workspace_files_before.keys() & workspace_files_after.keys()
        if workspace_files_before[path] != workspace_files_after[path]
    }
    lock_effects_only = "idempotency_key" in input_value and all(
        path.startswith(".breadboard/public_api/idempotency/.")
        and path.endswith(".json.lock")
        and workspace.joinpath(path).stat().st_size == 0
        for path in added_paths
    )
    if removed_paths or changed_paths or (added_paths and not lock_effects_only):
        raise AssertionError(
            f"{operation_id} typed error had unexpected workspace effects"
        )

    stage_status = error_case.get("stage_status", "failed")
    observed_stage_statuses = {
        outcome.get("status")
        for outcome in raw_error.get("stage_outcomes", ())
        if isinstance(outcome, dict)
    }
    if stage_status not in observed_stage_statuses:
        raise AssertionError(
            f"{operation_id} typed error omitted {stage_status!r} stage outcome"
        )
    return {
        "operation_id": operation_id,
        "fixture": error_case["fixture"],
        "class": raw_status,
        "error_code": error_case["error_code"],
        "stage_status": stage_status,
        "schema_valid": True,
        "sanitized": True,
        "raw_python_typescript_cli_equal": True,
        "cli_projection": cli_projection,
        "workspace_effects": added_paths,
        "workspace_unchanged": not added_paths,
        "result_sha256": _digest(raw_error),
    }


def _git_value(source_root: Path, *arguments: str) -> str:
    completed = subprocess.run(
        ["git", "-C", str(source_root), *arguments],
        text=True,
        capture_output=True,
        check=True,
        timeout=30,
    )
    return completed.stdout.strip()


def _verify_tracked_source_input(
    source_root: Path,
    commit: str,
    path: Path,
    expected_relative: str,
) -> dict[str, str]:
    expected_path = (source_root / expected_relative).resolve()
    if path.resolve() != expected_path or not expected_path.is_file():
        raise AssertionError(
            f"source evidence input must be the canonical tracked {expected_relative}"
        )
    entry = _git_value(
        source_root,
        "ls-tree",
        "--full-tree",
        commit,
        "--",
        expected_relative,
    )
    metadata, separator, tracked_path = entry.partition("\t")
    fields = metadata.split()
    if (
        separator != "\t"
        or tracked_path != expected_relative
        or len(fields) != 3
        or fields[1] != "blob"
    ):
        raise AssertionError(
            f"source evidence input is not tracked: {expected_relative}"
        )
    actual_blob = _git_value(source_root, "hash-object", str(expected_path))
    if actual_blob != fields[2]:
        raise AssertionError(
            f"source evidence input differs from {commit}: {expected_relative}"
        )
    return {
        "path": expected_relative,
        "git_blob": fields[2],
        "sha256": _file_digest(expected_path),
    }


def _run_build(command: list[str], cwd: Path, environment: dict[str, str]) -> None:
    completed = subprocess.run(
        command,
        cwd=cwd,
        env=environment,
        text=True,
        capture_output=True,
        check=False,
        timeout=600,
    )
    if completed.returncode:
        raise RuntimeError(
            f"source rebuild failed: {command}\n{completed.stdout}\n{completed.stderr}"
        )


def _only_artifact(directory: Path, pattern: str) -> Path:
    artifacts = tuple(directory.glob(pattern))
    if len(artifacts) != 1:
        raise AssertionError(
            f"source rebuild produced {len(artifacts)} artifacts matching {pattern}"
        )
    return artifacts[0]


def _restore_git_file_modes(source_root: Path, commit: str, checkout: Path) -> None:
    completed = subprocess.run(
        ["git", "-C", str(source_root), "ls-tree", "-r", "-z", commit],
        check=True,
        capture_output=True,
        timeout=60,
    )
    for raw_entry in completed.stdout.split(b"\0"):
        if not raw_entry:
            continue
        metadata, raw_path = raw_entry.split(b"\t", 1)
        mode = metadata.split(b" ", 1)[0]
        if mode not in {b"100644", b"100755"}:
            continue
        path = checkout / raw_path.decode("utf-8")
        if not path.is_file() or path.is_symlink():
            raise AssertionError(f"Git archive file is missing: {raw_path!r}")
        path.chmod(0o644 if mode == b"100644" else 0o755)


def _archive_digest(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _verify_installed_python(wheel: Path, python: Path, bbh: Path) -> dict[str, Any]:
    python = python.absolute()
    bbh = bbh.absolute()
    if not sys.flags.isolated:
        raise AssertionError(
            "installed differential runner must use Python isolated mode"
        )
    if not Path(sys.executable).samefile(python):
        raise AssertionError("--python is not the interpreter executing the runner")
    if Path(sys.prefix).resolve() != python.parent.parent.resolve():
        raise AssertionError(
            "--python is not rooted in the active isolated environment"
        )

    distribution = importlib.metadata.distribution("breadboard-harness-cli")
    site_packages = Path(distribution.locate_file("")).resolve()
    expected: dict[str, str] = {}
    with zipfile.ZipFile(wheel) as archive:
        for member in archive.infolist():
            if member.is_dir():
                continue
            if member.filename.startswith(("..", "/")) or "/../" in member.filename:
                raise AssertionError("wheel contains an unsafe member path")
            if ".data/" in member.filename:
                raise AssertionError("wheel relocation members are not supported by I6")
            expected[member.filename] = _archive_digest(archive.read(member))

    record_paths = {name for name in expected if name.endswith(".dist-info/RECORD")}
    for name, digest in expected.items():
        if name in record_paths:
            continue
        installed = site_packages / name
        if not installed.is_file() or _file_digest(installed) != digest:
            raise AssertionError(f"installed Python file differs from wheel: {name}")

    package_roots = {
        name.split("/", 1)[0]
        for name in expected
        if "/" in name and not name.split("/", 1)[0].endswith(".dist-info")
    }
    expected_package_files = {
        name for name in expected if name.split("/", 1)[0] in package_roots
    }
    actual_package_files: set[str] = set()
    for root_name in package_roots:
        root = site_packages / root_name
        if not root.is_dir():
            raise AssertionError(
                f"installed Python package root is absent: {root_name}"
            )
        actual_package_files.update(
            path.relative_to(site_packages).as_posix()
            for path in root.rglob("*")
            if path.is_file()
            and "__pycache__" not in path.parts
            and path.suffix != ".pyc"
        )
    if actual_package_files != expected_package_files:
        raise AssertionError("installed Python package file set differs from wheel")

    client_path = Path(inspect.getfile(BreadBoardClient)).resolve()
    if client_path != (site_packages / "breadboard_sdk/client.py").resolve():
        raise AssertionError(
            "runner imported the Python SDK outside the installed wheel"
        )

    expected_launcher = (
        f"#!{python}\n"
        "# -*- coding: utf-8 -*-\n"
        "import sys\n"
        "from scripts.breadboard_cli import main\n"
        'if __name__ == "__main__":\n'
        '    if sys.argv[0].endswith("-script.pyw"):\n'
        "        sys.argv[0] = sys.argv[0][:-11]\n"
        '    elif sys.argv[0].endswith(".exe"):\n'
        "        sys.argv[0] = sys.argv[0][:-4]\n"
        "    sys.exit(main())\n"
    )
    if (
        bbh.parent != python.parent
        or not bbh.is_file()
        or bbh.read_text(encoding="utf-8") != expected_launcher
    ):
        raise AssertionError("--bbh is not the wheel's isolated console launcher")
    return {
        "python_package_tree_sha256": _digest(sorted(expected.items())),
        "bbh_sha256": _file_digest(bbh),
    }


def _verify_installed_typescript(tarball: Path, node_project: Path) -> dict[str, Any]:
    package_root = node_project.absolute() / "node_modules" / "@breadboard" / "sdk"
    expected: dict[str, str] = {}
    with tarfile.open(tarball, mode="r:gz") as archive:
        for member in archive.getmembers():
            if not member.isfile():
                continue
            if not member.name.startswith("package/"):
                raise AssertionError("TypeScript package contains an unexpected root")
            relative = member.name.removeprefix("package/")
            if not relative or relative.startswith(("..", "/")) or "/../" in relative:
                raise AssertionError(
                    "TypeScript package contains an unsafe member path"
                )
            extracted = archive.extractfile(member)
            if extracted is None:
                raise AssertionError(
                    f"cannot read TypeScript package member: {relative}"
                )
            expected[relative] = _archive_digest(extracted.read())
    actual = {
        path.relative_to(package_root).as_posix(): _file_digest(path)
        for path in package_root.rglob("*")
        if path.is_file()
    }
    if actual != expected:
        raise AssertionError(
            "installed @breadboard/sdk file set or content differs from tarball"
        )
    return {"typescript_package_tree_sha256": _digest(sorted(expected.items()))}


def _verify_provenance(arguments: argparse.Namespace) -> dict[str, Any]:
    source_root = arguments.source_root.resolve()
    commit = _git_value(source_root, "rev-parse", "HEAD")
    tree = _git_value(source_root, "rev-parse", "HEAD^{tree}")
    if (commit, tree) != (arguments.source_commit, arguments.source_tree):
        raise AssertionError("source identity does not match the checked-out Git tree")
    if _git_value(source_root, "status", "--porcelain", "--untracked-files=no"):
        raise AssertionError("source tree has tracked changes")
    tracked_inputs = {
        "matrix": _verify_tracked_source_input(
            source_root,
            commit,
            arguments.matrix,
            "tests/api/public/fixtures/i6_operation_matrix.json",
        ),
        "typescript_probe": _verify_tracked_source_input(
            source_root,
            commit,
            arguments.ts_probe,
            "scripts/quality/i6_ts_probe.mjs",
        ),
    }

    wheel_sha256 = _file_digest(arguments.wheel)
    tarball_sha256 = _file_digest(arguments.tarball)
    source_date_epoch = int(
        _git_value(source_root, "show", "-s", "--format=%ct", "HEAD")
    )
    environment = dict(os.environ)
    environment["SOURCE_DATE_EPOCH"] = str(source_date_epoch)
    environment["PYTHONHASHSEED"] = "0"
    environment["BREADBOARD_BUILD_SOURCE_REPOSITORY"] = _git_value(
        source_root, "remote", "get-url", "origin"
    )
    environment["BREADBOARD_BUILD_SOURCE_COMMIT"] = commit
    environment["BREADBOARD_BUILD_SOURCE_TREE"] = tree
    with tempfile.TemporaryDirectory(prefix="breadboard-i6-source-rebuild-") as raw:
        rebuild_root = Path(raw)
        source_archive = rebuild_root / "source.tar"
        rebuilt_source = rebuild_root / "source"
        rebuilt_source.mkdir()
        subprocess.run(
            [
                "git",
                "-C",
                str(source_root),
                "archive",
                "--format=tar",
                f"--output={source_archive}",
                commit,
            ],
            check=True,
            timeout=60,
        )
        shutil.unpack_archive(source_archive, rebuilt_source)
        _restore_git_file_modes(source_root, commit, rebuilt_source)
        wheel_directory = rebuild_root / "wheel"
        tarball_directory = rebuild_root / "tarball"
        wheel_directory.mkdir()
        tarball_directory.mkdir()
        _run_build(
            [
                str(arguments.uv),
                "build",
                "--wheel",
                "--out-dir",
                str(wheel_directory),
            ],
            rebuilt_source,
            environment,
        )
        _run_build(
            [str(arguments.npm), "ci", "--ignore-scripts"],
            rebuilt_source / "sdk/ts",
            environment,
        )
        _run_build(
            [
                str(arguments.node),
                str(arguments.typescript_compiler),
                "-p",
                str(rebuilt_source / "sdk/ts/tsconfig.json"),
            ],
            rebuilt_source / "sdk/ts",
            environment,
        )
        _run_build(
            [
                str(arguments.npm),
                "pack",
                "--pack-destination",
                str(tarball_directory),
            ],
            rebuilt_source / "sdk/ts",
            environment,
        )
        rebuilt_wheel_sha256 = _file_digest(_only_artifact(wheel_directory, "*.whl"))
        rebuilt_tarball_sha256 = _file_digest(
            _only_artifact(tarball_directory, "*.tgz")
        )
        source_archive_sha256 = _file_digest(source_archive)
    if wheel_sha256 != rebuilt_wheel_sha256:
        raise AssertionError("wheel does not match a build from the pinned source")
    if tarball_sha256 != rebuilt_tarball_sha256:
        raise AssertionError(
            "TypeScript package does not match a build from the pinned source"
        )
    installed_python = _verify_installed_python(
        arguments.wheel, arguments.python, arguments.bbh
    )
    installed_typescript = _verify_installed_typescript(
        arguments.tarball, arguments.node_project
    )
    return {
        "source_identity_verified": True,
        "tracked_tree_clean": True,
        "source_date_epoch": source_date_epoch,
        "source_archive_sha256": source_archive_sha256,
        "source_rebuild_verified": True,
        "wheel_reproduced": True,
        "typescript_tarball_reproduced": True,
        "wheel_sha256": wheel_sha256,
        "typescript_tarball_sha256": tarball_sha256,
        "installed_artifacts_bound": True,
        **installed_python,
        **installed_typescript,
        "tracked_inputs": tracked_inputs,
        "builders": {
            "uv_sha256": _file_digest(arguments.uv),
            "node_sha256": _file_digest(arguments.node),
            "npm_sha256": _file_digest(arguments.npm),
            "typescript_compiler_sha256": _file_digest(arguments.typescript_compiler),
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--matrix", type=Path, required=True)
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--evidence", type=Path, required=True)
    parser.add_argument("--python", type=Path, required=True)
    parser.add_argument("--bbh", type=Path, required=True)
    parser.add_argument("--node", type=Path, required=True)
    parser.add_argument("--node-project", type=Path, required=True)
    parser.add_argument("--ts-probe", type=Path, required=True)
    parser.add_argument("--wheel", type=Path, required=True)
    parser.add_argument("--tarball", type=Path, required=True)
    parser.add_argument("--uv", type=Path, required=True)
    parser.add_argument("--npm", type=Path, required=True)
    parser.add_argument("--typescript-compiler", type=Path, required=True)
    parser.add_argument("--source-root", type=Path, required=True)
    parser.add_argument("--source-commit", required=True)
    parser.add_argument("--source-tree", required=True)
    arguments = parser.parse_args()
    provenance = _verify_provenance(arguments)
    public_schema_root = arguments.source_root / "contracts/public/schemas"
    problem_schema = json.loads(
        (public_schema_root / "bb.problem.v1.schema.json").read_text(encoding="utf-8")
    )
    schema_registry = Registry().with_resource(
        problem_schema["$id"], Resource.from_contents(problem_schema)
    )
    cli_result_schema = json.loads(
        (public_schema_root / "bb.cli.result.v1.schema.json").read_text(
            encoding="utf-8"
        )
    )
    cli_result_validator = Draft202012Validator(
        cli_result_schema, registry=schema_registry
    )
    public_event_schema = json.loads(
        (
            arguments.source_root
            / "contracts/public/schemas/bb.public_session_event.v1.schema.json"
        ).read_text(encoding="utf-8")
    )
    lifecycle_schema = json.loads(
        (
            arguments.source_root
            / "contracts/public/schemas/bb.payload.product_session.lifecycle.v1.schema.json"
        ).read_text(encoding="utf-8")
    )
    event_registry = Registry().with_resource(
        lifecycle_schema["$id"], Resource.from_contents(lifecycle_schema)
    )
    public_event_validator = Draft202012Validator(
        public_event_schema, registry=event_registry
    )
    payload_schema_paths = {
        "bb.payload.product_session.lifecycle.v1": (
            arguments.source_root
            / "contracts/public/schemas/bb.payload.product_session.lifecycle.v1.schema.json"
        ),
        "bb.payload.message.assistant.v1": (
            arguments.source_root
            / "contracts/kernel/schemas/payloads/bb.payload.message.assistant.v1.schema.json"
        ),
        "bb.payload.tool.called.v1": (
            arguments.source_root
            / "contracts/kernel/schemas/payloads/bb.payload.tool.called.v1.schema.json"
        ),
        "bb.payload.tool.completed.v1": (
            arguments.source_root
            / "contracts/kernel/schemas/payloads/bb.payload.tool.completed.v1.schema.json"
        ),
    }
    payload_validators = {
        schema_id: Draft202012Validator(json.loads(path.read_text(encoding="utf-8")))
        for schema_id, path in payload_schema_paths.items()
    }

    matrix_sha256 = _file_digest(arguments.matrix)
    matrix = json.loads(arguments.matrix.read_text(encoding="utf-8"))
    if (
        matrix_sha256 != I6_MATRIX_SHA256
        or matrix.get("schema_version")
        != "bb.product_integration.i6_operation_matrix.v1"
    ):
        raise AssertionError("I6 operation matrix identity does not match the verifier")
    rows = matrix["operations"]
    by_id = {row["operation_id"]: row for row in rows}
    bindings_by_id = {
        binding.operation_id: binding for binding in PUBLIC_OPERATION_BINDINGS
    }
    installed_ids = set(bindings_by_id)
    if len(rows) != len(by_id) or set(by_id) != set(bindings_by_id) or len(rows) != 26:
        raise AssertionError("installed operation catalog does not match I6 matrix")
    for operation_id, row in by_id.items():
        binding = bindings_by_id[operation_id]
        canonical = {
            "http_method": binding.http_method,
            "path": binding.path,
            "python_method": binding.python_method,
            "typescript_action": binding.action_id,
        }
        if any(row[field] != value for field, value in canonical.items()):
            raise AssertionError(
                f"I6 matrix metadata differs from installed binding: {operation_id}"
            )
        cli_prefix = binding.cli_command.split()
        if (
            cli_prefix[:1] != ["bbh"]
            or row["cli"][: len(cli_prefix) - 1] != cli_prefix[1:]
        ):
            raise AssertionError(
                f"I6 matrix CLI differs from installed binding: {operation_id}"
            )

    typed_error_rows = [row for row in rows if isinstance(row.get("typed_error"), dict)]
    typed_error_exemptions = sorted(
        row["operation_id"] for row in rows if row.get("typed_error") is None
    )
    if (
        any("typed_error" not in row for row in rows)
        or len(typed_error_rows) != 18
        or {row["typed_error"]["status"] for row in typed_error_rows} != {404, 409, 422}
        or any(
            not {"fixture", "status", "error_code"} <= set(row["typed_error"])
            for row in typed_error_rows
        )
    ):
        raise AssertionError("I6 matrix typed-error coverage is incomplete")

    arguments.workspace.mkdir(parents=True, exist_ok=False)
    arguments.evidence.mkdir(parents=True, exist_ok=True)
    installed_probe = arguments.node_project / "i6_ts_probe.mjs"
    shutil.copyfile(arguments.ts_probe, installed_probe)
    values: dict[str, Any] = {
        "integration_id": "capture:memory",
    }
    results: list[dict[str, Any]] = []
    raw_results: dict[str, dict[str, Any]] = {}
    cli_results: list[dict[str, Any]] = []
    error_results: list[dict[str, Any]] = []
    workspace_before = _tree_digest(arguments.workspace)

    with _server(arguments.python, arguments.workspace) as (base_url, server_pid):
        values["base_url"] = base_url
        transport = requests.Session()
        client = BreadBoardClient(base_url=base_url, timeout_s=30)
        surface_returncode, surface_probe = _node_probe(
            arguments.node,
            installed_probe,
            base_url,
            "__surface.audit",
            {},
        )
        expected_public_methods = sorted(
            ["invokePublicAction"]
            + [binding.typescript_method for binding in PUBLIC_OPERATION_BINDINGS]
        )
        if (
            surface_returncode != 0
            or surface_probe.get("result", {}).get("public_methods")
            != expected_public_methods
            or surface_probe.get("result", {}).get("internal_e4_available") is not True
            or surface_probe.get("result", {}).get("terminal_stream_requests") != 1
            or surface_probe.get("result", {}).get("terminal_callback_error_observed")
            is not True
            or surface_probe.get("result", {}).get("resume_query_advanced") is not True
            or surface_probe.get("result", {}).get("mismatched_lifecycle_rejected")
            is not True
        ):
            raise AssertionError(
                "installed TypeScript public/internal boundary is invalid"
            )
        typescript_surface = surface_probe["result"]

        openapi = transport.get(f"{base_url}/openapi.json", timeout=30).json()
        observed_ids = {
            operation["operationId"]
            for path_item in openapi["paths"].values()
            for operation in path_item.values()
            if isinstance(operation, dict) and "operationId" in operation
        }
        if not installed_ids.issubset(observed_ids):
            raise AssertionError("installed OpenAPI is missing catalog operations")

        order = [
            "system.describe",
            "system.health",
            "system.schemas",
            "harness.create",
            "harness.list",
            "harness.get",
            "harness.validate",
            "harness.explain",
            "harness.update",
            "harness.lock",
            "harness_lock.get",
            "integration.list",
            "integration.get",
            "integration.probe",
            "session.start",
            "session.list",
            "session.get",
            "session.send_input",
            "session.approve",
            "session.resume",
            "session.artifacts",
            "session.cancel",
            "session.events",
            "artifact.list",
            "artifact.get",
            "artifact.verify",
        ]

        for operation_id in order:
            if operation_id == "harness.get":
                definition_response = transport.get(
                    f"{base_url}/v1/harnesses/{values['harness_id']}", timeout=30
                ).json()
                values["harness_definition"] = definition_response["data"]["definition"]
            elif operation_id == "session.send_input":
                values["input_session_id"] = "i6-input"
                _start(
                    transport,
                    base_url,
                    values["lock_id"],
                    values["input_session_id"],
                    "i6-input-start",
                    "Wait for one additional input.",
                )
            elif operation_id == "session.approve":
                (
                    values["approval_session_id"],
                    values["approval_request_id"],
                ) = _setup_approval(
                    transport,
                    base_url,
                    directory="approval",
                    session_id="i6-approval",
                    key="i6-approval-start",
                )
            elif operation_id == "session.resume":
                values["runtime_session_id"] = "i6-runtime"
                _start(
                    transport,
                    base_url,
                    values["lock_id"],
                    values["runtime_session_id"],
                    "i6-runtime-start",
                    "Exercise pause, resume, and artifact installation.",
                )
                paused = transport.post(
                    f"{base_url}/v1/sessions/{values['runtime_session_id']}/pause",
                    timeout=30,
                )
                if paused.status_code != 202:
                    raise AssertionError(paused.text)
                uploaded = transport.post(
                    f"{base_url}/v1/sessions/{values['runtime_session_id']}/attachments",
                    data={"metadata": json.dumps({"source": "i6"})},
                    files={
                        "files": ("fixture.txt", b"i6 artifact bytes\n", "text/plain")
                    },
                    timeout=30,
                )
                uploaded.raise_for_status()
                artifacts = transport.get(
                    f"{base_url}/v1/sessions/{values['runtime_session_id']}/artifacts",
                    timeout=30,
                ).json()["data"]["artifacts"]
                values["artifact_id"] = artifacts[0]["digest"]
            elif operation_id == "session.cancel":
                values["cancel_session_id"] = "i6-cancel"
                _start(
                    transport,
                    base_url,
                    values["lock_id"],
                    values["cancel_session_id"],
                    "i6-cancel-start",
                    "Remain active until canceled.",
                )

            session_value_keys = {
                "session.approve": "approval_session_id",
                "session.artifacts": "runtime_session_id",
                "session.cancel": "cancel_session_id",
                "session.events": "cancel_session_id",
                "session.get": "start_session_id",
                "session.resume": "runtime_session_id",
                "session.send_input": "input_session_id",
            }
            session_value_key = session_value_keys.get(operation_id)
            if session_value_key is not None:
                values["session_id"] = values[session_value_key]

            row = by_id[operation_id]
            input_value = _input(operation_id, values)
            status, raw = _raw_call(transport, base_url, row, values, input_value)
            if status != row["success_status"]:
                raise AssertionError(f"{operation_id} returned {status}: {raw}")
            python = _python_call(client, operation_id, input_value)
            ts_code, ts = _node_probe(
                arguments.node,
                installed_probe,
                base_url,
                row["typescript_action"],
                input_value,
            )
            if ts_code != 0 or ts.get("ok") is not True:
                raise AssertionError(f"TypeScript {operation_id} failed: {ts}")
            if raw != python or raw != ts["result"]:
                raise AssertionError(
                    f"{operation_id} surface mismatch: "
                    f"raw={_digest(raw)} python={_digest(python)} ts={_digest(ts['result'])}"
                )
            results.append(_record_result(operation_id, status, raw))
            raw_results[operation_id] = raw

            if operation_id == "harness.create":
                values["harness_id"] = raw["data"]["path"]
            elif operation_id == "harness.lock":
                values["lock_id"] = raw["data"]["path"]
            elif operation_id == "session.start":
                values["start_session_id"] = "i6-differential-start"
                view = _wait_status(
                    transport,
                    base_url,
                    values["start_session_id"],
                    {"running", "awaiting_approval", "completed", "failed", "canceled"},
                )
                if view["status"] in {"running", "awaiting_approval"}:
                    stopped = transport.post(
                        f"{base_url}/v1/sessions/{values['start_session_id']}/cancel",
                        json={"reason": "i6 stabilization"},
                        headers={"Idempotency-Key": "i6-start-stabilization"},
                        timeout=30,
                    )
                    if stopped.status_code != 202:
                        raise AssertionError(stopped.text)
                    _wait_status(
                        transport,
                        base_url,
                        values["start_session_id"],
                        {"completed", "failed", "canceled"},
                    )

        events = _raw_call(
            transport,
            base_url,
            by_id["session.events"],
            values,
            _input("session.events", values),
        )[1]
        events = _validate_event_stream(
            events, public_event_validator, payload_validators
        )
        sequences = [event["seq"] for event in events]
        if events[-1]["kind"] != "session.canceled":
            raise AssertionError("session stream did not stop on terminal cancellation")
        if "i6-secret-cancel-reason" in json.dumps(events):
            raise AssertionError("session stream leaked the cancellation reason")
        if events[-1]["payload"].get("reason") != "<redacted>":
            raise AssertionError("session cancellation reason was not redacted")
        if events[-1]["visibility"].get("redaction_state") != "redacted":
            raise AssertionError("session event did not report its redaction state")

        limited = transport.get(
            f"{base_url}/v1/sessions/{values['cancel_session_id']}/events",
            params={"limit": 1},
            timeout=30,
        )
        if len(_sse_events(limited)) != 1:
            raise AssertionError("SSE limit was not enforced")
        precedence = transport.get(
            f"{base_url}/v1/sessions/{values['cancel_session_id']}/events",
            params={"resume_token": sequences[-2]},
            headers={"Last-Event-ID": "0"},
            timeout=30,
        )
        if [event["seq"] for event in _sse_events(precedence)] != [sequences[-1]]:
            raise AssertionError(
                "resume_token did not take precedence over Last-Event-ID"
            )

        live_session_id = "i6-live-disconnect"
        _start(
            transport,
            base_url,
            values["lock_id"],
            live_session_id,
            "i6-live-disconnect-start",
            "Wait for I6 stream disconnect.",
        )
        live_stream = transport.get(
            f"{base_url}/v1/sessions/{live_session_id}/events",
            stream=True,
            timeout=30,
        )
        live_stream.raise_for_status()
        try:
            first_event_complete = False
            for line in live_stream.iter_lines(chunk_size=1, decode_unicode=True):
                if line == "":
                    first_event_complete = True
                    break
            if not first_event_complete:
                raise AssertionError("SSE did not flush its first event")
        finally:
            live_stream.close()
        health_after_disconnect = transport.get(f"{base_url}/v1/health", timeout=30)
        if health_after_disconnect.status_code != 200:
            raise AssertionError("server was unhealthy after SSE disconnect")
        live_cancel = transport.post(
            f"{base_url}/v1/sessions/{live_session_id}/cancel",
            json={"reason": "i6 stream cleanup"},
            headers={"Idempotency-Key": "i6-live-disconnect-cancel"},
            timeout=30,
        )
        if live_cancel.status_code != 202:
            raise AssertionError(live_cancel.text)
        _wait_status(
            transport,
            base_url,
            live_session_id,
            {"completed", "failed", "canceled"},
        )

        conflict_inputs = {
            "integration.probe": {
                "path": "/v1/integrations/capture:json/probe",
                "headers": {"Idempotency-Key": "i6-integration-probe"},
                "json": None,
            },
            "session.start": {
                "path": "/v1/sessions",
                "headers": {"Idempotency-Key": "i6-session-start"},
                "json": {
                    "lock_id": values["lock_id"],
                    "task": "different task",
                    "session_id": "i6-differential-start",
                },
            },
            "session.send_input": {
                "path": f"/v1/sessions/{values['input_session_id']}/input",
                "headers": {"Idempotency-Key": "i6-session-input"},
                "json": {"content": "different"},
            },
            "session.approve": {
                "path": f"/v1/sessions/{values['approval_session_id']}/approve",
                "headers": {"Idempotency-Key": "i6-session-approve"},
                "json": {
                    "request_id": values["approval_request_id"],
                    "decision": "allow",
                },
            },
            "session.resume": {
                "path": f"/v1/sessions/{values['cancel_session_id']}/resume",
                "headers": {"Idempotency-Key": "i6-session-resume"},
                "json": None,
            },
            "session.cancel": {
                "path": f"/v1/sessions/{values['cancel_session_id']}/cancel",
                "headers": {"Idempotency-Key": "i6-session-cancel"},
                "json": {"reason": "different"},
            },
        }
        forbidden_error_values = (
            str(arguments.workspace),
            str(arguments.source_root),
            str(Path.home()),
            "i6-auth-token",
        )
        conflict_bodies: dict[str, dict[str, Any]] = {}
        for operation_id, conflict in conflict_inputs.items():
            response = transport.request(
                "POST",
                f"{base_url}{conflict['path']}",
                headers=conflict["headers"],
                json=conflict["json"],
                timeout=30,
            )
            if response.status_code != 409:
                raise AssertionError(
                    f"{operation_id} idempotency conflict returned {response.status_code}"
                )
            conflict_bodies[operation_id] = _validate_public_error(
                body=response.json(),
                status=409,
                error_code="idempotency_conflict",
                validator=cli_result_validator,
                forbidden=forbidden_error_values,
            )

        missing = transport.get(
            f"{base_url}/v1/sessions/i6-error-missing",
            timeout=30,
        )
        missing_body = _validate_public_error(
            body=missing.json(),
            status=missing.status_code,
            error_code="path_unavailable",
            validator=cli_result_validator,
            forbidden=forbidden_error_values,
        )
        python_missing = _python_error(lambda: client.get_session("i6-error-missing"))
        ts_missing_code, ts_missing = _node_probe(
            arguments.node,
            installed_probe,
            base_url,
            "public.session.get",
            {"session_id": "i6-error-missing"},
        )
        if (
            python_missing != (404, missing_body)
            or ts_missing_code != 2
            or ts_missing.get("error") != {"status": 404, "body": missing_body}
        ):
            raise AssertionError("404 raw/Python/TypeScript error parity failed")

        invalid_input = {
            "session_id": "i6-error-missing",
            "request_id": "i6-error-request",
            "decision": "invalid",
            "idempotency_key": "i6-error-422",
        }
        invalid = transport.post(
            f"{base_url}/v1/sessions/{invalid_input['session_id']}/approve",
            json={
                "request_id": invalid_input["request_id"],
                "decision": invalid_input["decision"],
            },
            headers={"Idempotency-Key": invalid_input["idempotency_key"]},
            timeout=30,
        )
        invalid_body = _validate_public_error(
            body=invalid.json(),
            status=invalid.status_code,
            error_code="invalid_request",
            validator=cli_result_validator,
            forbidden=forbidden_error_values,
        )
        python_invalid = _python_error(
            lambda: client.approve_session(
                str(invalid_input["session_id"]),
                str(invalid_input["request_id"]),
                str(invalid_input["decision"]),
                idempotency_key=str(invalid_input["idempotency_key"]),
            )
        )
        ts_invalid_code, ts_invalid = _node_probe(
            arguments.node,
            installed_probe,
            base_url,
            "public.session.approve",
            invalid_input,
        )
        if (
            python_invalid != (422, invalid_body)
            or ts_invalid_code != 2
            or ts_invalid.get("error") != {"status": 422, "body": invalid_body}
        ):
            raise AssertionError("422 raw/Python/TypeScript error parity failed")

        conflict_input = {
            "session_id": "i6-input",
            "content": "different",
            "idempotency_key": "i6-session-input",
        }
        conflict_body = conflict_bodies["session.send_input"]
        python_conflict = _python_error(
            lambda: client.send_input_session(
                conflict_input["session_id"],
                conflict_input["content"],
                idempotency_key=conflict_input["idempotency_key"],
            )
        )
        ts_conflict_code, ts_conflict = _node_probe(
            arguments.node,
            installed_probe,
            base_url,
            "public.session.send_input",
            conflict_input,
        )
        if (
            python_conflict != (409, conflict_body)
            or ts_conflict_code != 2
            or ts_conflict.get("error") != {"status": 409, "body": conflict_body}
        ):
            raise AssertionError("409 raw/Python/TypeScript error parity failed")

        missing_events = transport.get(
            f"{base_url}/v1/sessions/i6-error-missing/events",
            timeout=30,
        )
        missing_events_body = _validate_public_error(
            body=missing_events.json(),
            status=missing_events.status_code,
            error_code="path_unavailable",
            validator=cli_result_validator,
            forbidden=forbidden_error_values,
        )
        python_missing_events = _python_error(
            lambda: list(client.events_session("i6-error-missing"))
        )
        ts_events_code, ts_events = _node_probe(
            arguments.node,
            installed_probe,
            base_url,
            "public.session.events",
            {"session_id": "i6-error-missing", "limit": 256},
        )
        if (
            python_missing_events != (404, missing_events_body)
            or ts_events_code != 2
            or ts_events.get("error") != {"status": 404, "body": missing_events_body}
        ):
            raise AssertionError("SSE 404 raw/Python/TypeScript error parity failed")

        error_results.extend(
            [
                {
                    "operation_id": "session.approve",
                    "fixture": "invalid_decision",
                    "class": 422,
                    "error_code": "invalid_state",
                    "schema_valid": True,
                    "sanitized": True,
                    "raw_python_typescript_equal": True,
                    "result_sha256": _digest(invalid_body),
                },
                {
                    "operation_id": "session.send_input",
                    "fixture": "idempotency_conflict",
                    "class": 409,
                    "error_code": "idempotency_conflict",
                    "count": len(conflict_inputs),
                    "schema_valid": True,
                    "sanitized": True,
                    "raw_python_typescript_equal": True,
                    "result_sha256": _digest(conflict_body),
                },
            ]
        )

        cli_env = {
            key: value
            for key, value in os.environ.items()
            if key not in {"PYTHONHOME", "PYTHONPATH"}
        }
        cli_env.update(
            {
                "BREADBOARD_PUBLIC_WORKSPACE": str(arguments.workspace),
                "BREADBOARD_SESSION_STATE_ROOT": str(
                    arguments.workspace / ".breadboard/session_state"
                ),
                "BREADBOARD_RUNTIME_RECORD_ROOT": str(
                    arguments.workspace / ".breadboard/runtime_records"
                ),
                "BREADBOARD_SESSION_EVENT_ROOT": str(
                    arguments.workspace / ".breadboard/session_events"
                ),
                "RAY_SCE_LOCAL_MODE": "1",
                "BREADBOARD_ENABLE_E4_API": "0",
                "BREADBOARD_I6_SERVER": base_url,
            }
        )
        values["cli_harness_directory"] = "cli-created"
        values["harness_definition"] = values["harness_id"]
        for row in rows:
            operation_id = row["operation_id"]
            if operation_id == "session.send_input":
                values["input_session_id"] = "i6-cli-input"
                _start(
                    transport,
                    base_url,
                    values["lock_id"],
                    values["input_session_id"],
                    "i6-cli-input-start",
                    "Wait for CLI input.",
                )
            elif operation_id == "session.cancel":
                values["cancel_session_id"] = "i6-cli-cancel"
                _start(
                    transport,
                    base_url,
                    values["lock_id"],
                    values["cancel_session_id"],
                    "i6-cli-cancel-start",
                    "Wait for CLI cancellation.",
                )
            elif operation_id == "session.resume":
                values["runtime_session_id"] = "i6-cli-resume"
                _start(
                    transport,
                    base_url,
                    values["lock_id"],
                    values["runtime_session_id"],
                    "i6-cli-resume-start",
                    "Wait for CLI resume.",
                )
                paused = transport.post(
                    f"{base_url}/v1/sessions/{values['runtime_session_id']}/pause",
                    timeout=30,
                )
                if paused.status_code != 202:
                    raise AssertionError(paused.text)
            elif operation_id == "session.approve":
                (
                    values["approval_session_id"],
                    values["approval_request_id"],
                ) = _setup_approval(
                    transport,
                    base_url,
                    directory="cli-approval",
                    session_id="i6-cli-approval",
                    key="i6-cli-approval-start",
                )
            raw_projection = raw_results[operation_id]
            if operation_id == "session.get":
                projection_response = transport.get(
                    f"{base_url}/v1/sessions/{values['runtime_session_id']}",
                    timeout=30,
                )
                if projection_response.status_code != row["success_status"]:
                    raise AssertionError(projection_response.text)
                raw_projection = projection_response.json()
            cli = _run_cli(
                arguments.bbh,
                arguments.workspace,
                _format_cli(row["cli"], values),
                cli_env,
            )
            if cli.get("ok") is not True:
                raise AssertionError(f"CLI {operation_id} failed: {cli}")
            if operation_id in {
                "harness.list",
                "session.events",
                "session.list",
                "system.health",
            }:
                projection_values = values
                if operation_id == "session.events":
                    projection_values = {
                        **values,
                        "session_id": values["cancel_session_id"],
                    }
                status, raw_projection = _raw_call(
                    transport,
                    base_url,
                    row,
                    projection_values,
                    _input(operation_id, values),
                )
                if status != row["success_status"]:
                    raise AssertionError(
                        f"fresh {operation_id} projection returned {status}: "
                        f"{raw_projection}"
                    )
            if operation_id == "session.start":
                session_id = cli.get("data", {}).get("session_id")
                if not isinstance(session_id, str) or not session_id:
                    raise AssertionError("CLI harness.run omitted its session identity")
                projection_response = transport.get(
                    f"{base_url}/v1/sessions/{session_id}",
                    timeout=30,
                )
                if projection_response.status_code != 200:
                    raise AssertionError(projection_response.text)
                raw_projection = projection_response.json()
            observed_session = None
            if operation_id in {
                "session.approve",
                "session.cancel",
                "session.resume",
                "session.send_input",
            }:
                session_id = cli.get("data", {}).get("session", {}).get("session_id")
                if not isinstance(session_id, str) or not session_id:
                    raise AssertionError(
                        f"CLI {operation_id} omitted its session identity"
                    )
                projection_response = transport.get(
                    f"{base_url}/v1/sessions/{session_id}",
                    timeout=30,
                )
                if projection_response.status_code != 200:
                    raise AssertionError(projection_response.text)
                observed_session = projection_response.json()
            projection_sha256 = _verify_cli_projection(
                operation_id,
                cli,
                raw_projection,
                cli_result_validator,
                public_event_validator,
                payload_validators,
                workspace=arguments.workspace,
                observed_session=observed_session,
            )
            cli_results.append(
                {
                    "operation_id": operation_id,
                    "exit_code": cli["exit_code"],
                    "result_sha256": _digest(cli),
                    "raw_contract_equal": True,
                    "projection_sha256": projection_sha256,
                }
            )

        invalid_definition_path = arguments.workspace / "i6-error-invalid-harness.yaml"
        invalid_definition_path.write_text("{}\n", encoding="utf-8")
        values["invalid_harness_definition"] = invalid_definition_path.relative_to(
            arguments.workspace
        ).as_posix()
        try:
            for row in rows:
                error_case = row["typed_error"]
                if error_case is None:
                    continue
                error_results.append(
                    _verify_installed_typed_error(
                        row=row,
                        error_case=error_case,
                        values=values,
                        transport=transport,
                        client=client,
                        base_url=base_url,
                        node=arguments.node,
                        installed_probe=installed_probe,
                        bbh=arguments.bbh,
                        workspace=arguments.workspace,
                        cli_env=cli_env,
                        validator=cli_result_validator,
                        forbidden=forbidden_error_values,
                    )
                )
        finally:
            invalid_definition_path.unlink(missing_ok=True)

        runtime_workspace_before = _tree_digest(arguments.workspace)
        runtime_sentinel = "i6-internal-secret"
        runtime_stem = runtime_sentinel + "-" + "x" * (237 - len(runtime_sentinel) - 1)
        runtime_source = (
            arguments.workspace
            / Path(str(values["harness_id"])).parent
            / f"{runtime_stem}.yaml"
        )
        if len(runtime_source.name) != 242:
            raise AssertionError("runtime-failure fixture filename length drifted")
        shutil.copyfile(arguments.workspace / values["harness_id"], runtime_source)
        runtime_values = {
            **values,
            "harness_id": runtime_source.relative_to(arguments.workspace).as_posix(),
        }
        try:
            error_results.append(
                _verify_installed_typed_error(
                    row=by_id["harness.lock"],
                    error_case={
                        "fixture": "runtime_filename",
                        "status": 500,
                        "error_code": "runtime_failure",
                    },
                    values=runtime_values,
                    transport=transport,
                    client=client,
                    base_url=base_url,
                    node=arguments.node,
                    installed_probe=installed_probe,
                    bbh=arguments.bbh,
                    workspace=arguments.workspace,
                    cli_env=cli_env,
                    validator=cli_result_validator,
                    forbidden=(
                        *forbidden_error_values,
                        runtime_sentinel,
                        str(arguments.workspace),
                    ),
                )
            )
        finally:
            runtime_source.unlink(missing_ok=True)
        if _tree_digest(arguments.workspace) != runtime_workspace_before:
            raise AssertionError("runtime-failure fixture left workspace mutations")

    auth_workspace = arguments.workspace.parent / f"{arguments.workspace.name}-auth"
    auth_workspace.mkdir()
    shutil.copytree(arguments.workspace / "shared", auth_workspace / "shared")
    with _server(arguments.python, auth_workspace, token="i6-auth-token") as (
        auth_url,
        auth_pid,
    ):
        unauthorized = requests.get(f"{auth_url}/v1/sessions", timeout=30)
        unauthorized_body = unauthorized.json()
        expected_unauthorized = {
            "error": "unauthorized",
            "detail": "unauthorized",
            "path": None,
        }
        if (
            unauthorized.status_code != 401
            or unauthorized_body != expected_unauthorized
        ):
            raise AssertionError(
                "authenticated installed server did not return the typed 401 envelope"
            )
        if "i6-auth-token" in json.dumps(unauthorized_body, sort_keys=True):
            raise AssertionError("401 response leaked the configured credential")
        python_unauthorized = _python_error(
            lambda: BreadBoardClient(auth_url, timeout_s=30).list_session()
        )
        ts_auth_code, ts_unauthorized = _node_probe(
            arguments.node,
            installed_probe,
            auth_url,
            "public.session.list",
            {},
        )
        if (
            python_unauthorized != (401, unauthorized_body)
            or ts_auth_code != 2
            or ts_unauthorized.get("error")
            != {"status": 401, "body": unauthorized_body}
        ):
            raise AssertionError("401 raw/Python/TypeScript error parity failed")
        auth_cli_env = dict(cli_env)
        auth_cli_env["BREADBOARD_I6_SERVER"] = auth_url
        cli_unauthorized = _run_cli(
            arguments.bbh,
            auth_workspace,
            ["session", "list"],
            auth_cli_env,
        )
        cli_unauthorized_errors = tuple(
            cli_result_validator.iter_errors(cli_unauthorized)
        )
        if (
            cli_unauthorized_errors
            or cli_unauthorized.get("exit_code") != 2
            or cli_unauthorized.get("error", {}).get("error_code") != "unauthorized"
            or cli_unauthorized.get("error", {}).get("message") != "unauthorized"
        ):
            raise AssertionError("CLI did not preserve the typed 401 failure")
        auth_transport = requests.Session()
        auth_transport.headers["Authorization"] = "Bearer i6-auth-token"
        _start(
            auth_transport,
            auth_url,
            values["lock_id"],
            "i6-auth-visible",
            "i6-auth-visible-start",
            "Wait for authenticated listing.",
        )
        auth_canceled = auth_transport.post(
            f"{auth_url}/v1/sessions/i6-auth-visible/cancel",
            headers={"Idempotency-Key": "i6-auth-visible-cancel"},
            json={"reason": "authenticated fixture ready"},
            timeout=30,
        )
        if auth_canceled.status_code != 202:
            raise AssertionError(auth_canceled.text)
        authorized = requests.get(
            f"{auth_url}/v1/sessions",
            headers={"Authorization": "Bearer i6-auth-token"},
            timeout=30,
        )
        if authorized.status_code != 200:
            raise AssertionError("authenticated installed server rejected its token")
        authorized_body = authorized.json()
        authorized_sessions = authorized_body.get("data", {}).get("sessions")
        if not isinstance(authorized_sessions, list) or [
            session.get("session_id") for session in authorized_sessions
        ] != ["i6-auth-visible"]:
            raise AssertionError(
                "authenticated session.list did not expose its distinguishing fixture"
            )
        python_authorized = BreadBoardClient(
            auth_url, auth_token="i6-auth-token", timeout_s=30
        ).list_session()
        ts_authorized_code, ts_authorized = _node_probe(
            arguments.node,
            installed_probe,
            auth_url,
            "public.session.list",
            {},
            auth_token="i6-auth-token",
        )
        authorized_cli_env = dict(auth_cli_env)
        authorized_cli_env["BREADBOARD_API_TOKEN"] = "i6-auth-token"
        cli_authorized = _run_cli(
            arguments.bbh,
            auth_workspace,
            ["session", "list"],
            authorized_cli_env,
        )
        _verify_cli_projection(
            "session.list",
            cli_authorized,
            authorized_body,
            cli_result_validator,
            public_event_validator,
            payload_validators,
            workspace=auth_workspace,
        )
        if (
            python_authorized != authorized_body
            or ts_authorized_code != 0
            or ts_authorized.get("result") != authorized_body
            or cli_authorized != authorized_body
            or "i6-auth-token" in json.dumps(cli_authorized, sort_keys=True)
        ):
            raise AssertionError(
                "authenticated raw/Python/TypeScript/CLI parity failed"
            )
        error_results.append(
            {
                "operation_id": "session.list",
                "fixture": "missing_bearer",
                "class": 401,
                "error_code": "unauthorized",
                "raw_error_envelope_valid": True,
                "sanitized": True,
                "raw_python_typescript_equal": True,
                "cli_typed": True,
                "authorized_four_way_equal": True,
                "result_sha256": _digest(unauthorized_body),
                "server_pid": auth_pid,
            }
        )
    shutil.rmtree(auth_workspace)

    if {row["operation_id"] for row in results} != installed_ids:
        raise AssertionError("not every operation has an installed differential row")

    four_way_error_ids = {
        row["operation_id"]
        for row in error_results
        if row.get("raw_python_typescript_cli_equal") is True
    }
    expected_four_way_error_ids = {row["operation_id"] for row in typed_error_rows}
    if (
        four_way_error_ids != expected_four_way_error_ids
        or {row["class"] for row in error_results} != {401, 404, 409, 422, 500}
        or not any(row.get("stage_status") == "blocked" for row in error_results)
    ):
        raise AssertionError("installed typed-error coverage is incomplete")

    manifest = {
        "schema_version": "bb.product_integration.i6_installed_differential.v1",
        "status": "pass",
        "source_commit": arguments.source_commit,
        "source_tree": arguments.source_tree,
        "provenance": provenance,
        "catalog_operation_count": 26,
        "server_pid": server_pid,
        "artifacts": {
            "wheel_sha256": _file_digest(arguments.wheel),
            "typescript_tarball_sha256": _file_digest(arguments.tarball),
            "python_sha256": _file_digest(arguments.python),
            "bbh_sha256": _file_digest(arguments.bbh),
            "matrix_sha256": _file_digest(arguments.matrix),
            "typescript_probe_sha256": _file_digest(arguments.ts_probe),
        },
        "workspace_before_sha256": workspace_before,
        "workspace_after_sha256": _tree_digest(arguments.workspace),
        "operations": sorted(results, key=lambda row: row["operation_id"]),
        "cli_projections": sorted(cli_results, key=lambda row: row["operation_id"]),
        "typescript_surface": typescript_surface,
        "typed_errors": error_results,
        "typed_error_exemptions": typed_error_exemptions,
        "sse": {
            "raw_python_typescript_equal": True,
            "envelope_schema_valid": True,
            "payload_schemas_resolved": True,
            "kind_payload_bound": True,
            "typescript_resume_query_advanced": True,
            "contiguous": True,
            "terminal_stop": True,
            "resume_token_precedence": True,
            "limit": True,
            "redaction": True,
            "first_event_flushed": True,
            "disconnect_cleanup": True,
        },
        "keyed_replay": {
            "integration_probe": True,
            "session_start": True,
            "session_send_input": True,
            "session_approve": True,
            "session_resume": True,
            "session_cancel": True,
            "different_input_conflicts": 6,
        },
        "cleanup": {"servers_stopped": True, "authenticated_workspace_removed": True},
    }
    output = arguments.evidence / "installed-differential.json"
    output.write_bytes(_bytes(manifest) + b"\n")
    print(
        json.dumps(
            {"status": "pass", "manifest": str(output), "sha256": _file_digest(output)}
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
