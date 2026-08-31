from __future__ import annotations

import argparse
import contextlib
import hashlib
import json
import os
import shutil
import socket
import subprocess
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import requests

from breadboard_sdk import BreadBoardClient
from breadboard_sdk.generated.public_bindings import PUBLIC_OPERATION_BINDINGS


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


def _tree_digest(root: Path) -> str:
    rows: list[tuple[str, str]] = []
    for path in sorted(
        candidate for candidate in root.rglob("*") if candidate.is_file()
    ):
        rows.append((path.relative_to(root).as_posix(), _file_digest(path)))
    return _digest(rows)


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
    if operation_id == "session.events":
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
    completed = subprocess.run(
        [str(bbh), "--json", namespace, "--workspace", str(workspace), *tail],
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


def _format_cli(arguments: list[str], values: dict[str, Any]) -> list[str]:
    return [str(argument).format(**values) for argument in arguments]


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
    parser.add_argument("--source-commit", required=True)
    parser.add_argument("--source-tree", required=True)
    arguments = parser.parse_args()

    matrix = json.loads(arguments.matrix.read_text(encoding="utf-8"))
    rows = matrix["operations"]
    by_id = {row["operation_id"]: row for row in rows}
    installed_ids = {binding.operation_id for binding in PUBLIC_OPERATION_BINDINGS}
    if len(rows) != len(by_id) or set(by_id) != installed_ids or len(rows) != 26:
        raise AssertionError("installed operation catalog does not match I6 matrix")

    arguments.workspace.mkdir(parents=True, exist_ok=False)
    arguments.evidence.mkdir(parents=True, exist_ok=True)
    installed_probe = arguments.node_project / "i6_ts_probe.mjs"
    shutil.copyfile(arguments.ts_probe, installed_probe)
    values: dict[str, Any] = {
        "integration_id": "capture:memory",
    }
    results: list[dict[str, Any]] = []
    cli_results: list[dict[str, Any]] = []
    error_results: list[dict[str, Any]] = []
    workspace_before = _tree_digest(arguments.workspace)

    with _server(arguments.python, arguments.workspace) as (base_url, server_pid):
        values["base_url"] = base_url
        transport = requests.Session()
        client = BreadBoardClient(base_url=base_url, timeout_s=30)

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

            if operation_id == "harness.create":
                values["harness_id"] = raw["data"]["path"]
            elif operation_id == "harness.lock":
                values["lock_id"] = raw["data"]["path"]
            elif operation_id == "session.start":
                values["start_session_id"] = "i6-differential-start"

        events = _raw_call(
            transport,
            base_url,
            by_id["session.events"],
            values,
            _input("session.events", values),
        )[1]
        sequences = [event["seq"] for event in events]
        if sequences != list(range(sequences[0], sequences[0] + len(sequences))):
            raise AssertionError("session event sequences are not contiguous")
        if events[-1]["kind"] != "session.canceled":
            raise AssertionError("session stream did not stop on terminal cancellation")
        if "i6-secret-cancel-reason" in json.dumps(events):
            raise AssertionError("session stream leaked the cancellation reason")
        if events[-1]["payload"].get("reason") != "<redacted>":
            raise AssertionError("session cancellation reason was not redacted")

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

        missing = transport.get(f"{base_url}/v1/harnesses/missing.yaml", timeout=30)
        invalid = transport.post(
            f"{base_url}/v1/sessions",
            json={"lock_id": values["lock_id"], "task": ""},
            timeout=30,
        )
        if (missing.status_code, invalid.status_code) != (404, 422):
            raise AssertionError("installed typed errors did not preserve 404/422")
        error_results.extend(
            [
                {"class": 404, "sha256": _digest(missing.json())},
                {"class": 422, "sha256": _digest(invalid.json())},
                {"class": 409, "count": len(conflict_inputs)},
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
            }
        )
        values["cli_harness_directory"] = "cli-created"
        values["harness_definition"] = values["harness_id"]
        for row in rows:
            operation_id = row["operation_id"]
            if operation_id in {
                "session.approve",
                "session.resume",
                "session.send_input",
                "session.cancel",
            }:
                continue
            cli = _run_cli(
                arguments.bbh,
                arguments.workspace,
                _format_cli(row["cli"], values),
                cli_env,
            )
            if cli.get("ok") is not True:
                raise AssertionError(f"CLI {operation_id} failed: {cli}")
            cli_results.append(
                {
                    "operation_id": operation_id,
                    "exit_code": cli["exit_code"],
                    "result_sha256": _digest(cli),
                }
            )

    auth_workspace = arguments.workspace.parent / f"{arguments.workspace.name}-auth"
    auth_workspace.mkdir()
    with _server(arguments.python, auth_workspace, token="i6-auth-token") as (
        auth_url,
        auth_pid,
    ):
        unauthorized = requests.get(f"{auth_url}/v1/health", timeout=30)
        if unauthorized.status_code != 401:
            raise AssertionError("authenticated installed server did not return 401")
        authorized = requests.get(
            f"{auth_url}/v1/health",
            headers={"Authorization": "Bearer i6-auth-token"},
            timeout=30,
        )
        if authorized.status_code != 200:
            raise AssertionError("authenticated installed server rejected its token")
        error_results.append(
            {
                "class": 401,
                "sha256": _digest(unauthorized.json()),
                "server_pid": auth_pid,
            }
        )
    shutil.rmtree(auth_workspace)

    if {row["operation_id"] for row in results} != installed_ids:
        raise AssertionError("not every operation has an installed differential row")

    manifest = {
        "schema_version": "bb.product_integration.i6_installed_differential.v1",
        "status": "pass",
        "source_commit": arguments.source_commit,
        "source_tree": arguments.source_tree,
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
        "typed_errors": error_results,
        "sse": {
            "raw_python_typescript_equal": True,
            "contiguous": True,
            "terminal_stop": True,
            "resume_token_precedence": True,
            "limit": True,
            "redaction": True,
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
