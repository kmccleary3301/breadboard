from __future__ import annotations

import contextlib
import json
import os
import socket
import sys
import tempfile
import threading
import time
from collections.abc import Iterator
from pathlib import Path

import requests
import uvicorn

from breadboard_engine.api.cli_bridge import app as app_module
from breadboard_engine.api.cli_bridge.session_runner import SessionRunner
from breadboard.product.runtime.artifacts import ArtifactStore
from breadboard_sdk import ApiError, BreadBoardClient


def _hold_fixture_completion_until_input() -> tuple[object, object]:
    original_execute = SessionRunner._execute_task
    original_enqueue = SessionRunner.enqueue_input

    def is_smoke_session(session) -> bool:
        return str(getattr(session, "session_id", "")).endswith("smoke-session")

    def gated_execute(self, *args, **kwargs):
        if not is_smoke_session(self.session):
            return original_execute(self, *args, **kwargs)
        if not getattr(self, "_smoke_input_admitted", False):
            gate = getattr(self, "_smoke_input_gate", None)
            if gate is None:
                gate = threading.Event()
                self._smoke_input_gate = gate
            if not gate.wait(timeout=10):
                raise RuntimeError(
                    "default smoke input was not admitted before completion"
                )
        return original_execute(self, *args, **kwargs)

    async def gated_enqueue(self, *args, **kwargs):
        result = await original_enqueue(self, *args, **kwargs)
        self._smoke_input_admitted = True
        gate = getattr(self, "_smoke_input_gate", None)
        if gate is not None:
            gate.set()
        return result

    SessionRunner._execute_task = gated_execute
    SessionRunner.enqueue_input = gated_enqueue
    return original_execute, original_enqueue


@contextlib.contextmanager
def _running_default_server(workspace_path: str | None = None) -> Iterator[str]:
    os.environ.pop("BREADBOARD_LEGACY_ROUTES", None)
    os.environ["RAY_SCE_LOCAL_MODE"] = "1"
    previous_workspace = os.environ.get("BREADBOARD_PUBLIC_WORKSPACE")
    previous_e4_api = os.environ.get("BREADBOARD_ENABLE_E4_API")
    root_env_names = (
        "BREADBOARD_SESSION_STATE_ROOT",
        "BREADBOARD_RUNTIME_RECORD_ROOT",
        "BREADBOARD_SESSION_EVENT_ROOT",
    )
    previous_roots = {name: os.environ.get(name) for name in root_env_names}
    owned_workspace = tempfile.TemporaryDirectory() if workspace_path is None else None
    active_workspace = Path(workspace_path or owned_workspace.name)
    os.environ["BREADBOARD_PUBLIC_WORKSPACE"] = str(active_workspace)
    os.environ["BREADBOARD_ENABLE_E4_API"] = "0"
    os.environ["BREADBOARD_SESSION_STATE_ROOT"] = str(
        active_workspace / ".breadboard/session_state"
    )
    os.environ["BREADBOARD_RUNTIME_RECORD_ROOT"] = str(
        active_workspace / ".breadboard/runtime_records"
    )
    os.environ["BREADBOARD_SESSION_EVENT_ROOT"] = str(
        active_workspace / ".breadboard/session_events"
    )
    original_execute, original_enqueue = _hold_fixture_completion_until_input()
    app = app_module.create_app()
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    listener.bind(("127.0.0.1", 0))
    listener.listen(128)
    port = listener.getsockname()[1]
    server = uvicorn.Server(
        uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error")
    )
    thread = threading.Thread(
        target=server.run,
        kwargs={"sockets": [listener]},
        daemon=True,
    )
    thread.start()
    deadline = time.monotonic() + 10
    while not server.started and thread.is_alive() and time.monotonic() < deadline:
        time.sleep(0.01)
    if not server.started:
        server.should_exit = True
        thread.join(timeout=5)
        listener.close()
        raise RuntimeError("default create_app server did not start")

    try:
        yield f"http://127.0.0.1:{port}"
    finally:
        server.should_exit = True
        thread.join(timeout=10)
        listener.close()
        SessionRunner._execute_task = original_execute
        SessionRunner.enqueue_input = original_enqueue
        if thread.is_alive():
            raise RuntimeError("default create_app server did not stop")
        if previous_workspace is None:
            os.environ.pop("BREADBOARD_PUBLIC_WORKSPACE", None)
        else:
            os.environ["BREADBOARD_PUBLIC_WORKSPACE"] = previous_workspace
        if previous_e4_api is None:
            os.environ.pop("BREADBOARD_ENABLE_E4_API", None)
        else:
            os.environ["BREADBOARD_ENABLE_E4_API"] = previous_e4_api
        for name, value in previous_roots.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value
        if owned_workspace is not None:
            owned_workspace.cleanup()


def test_python_sdk_readme_flow_against_default_server() -> None:
    with _running_default_server() as base_url:
        assert requests.get(f"{base_url}/sessions", timeout=5).status_code == 404

        client = BreadBoardClient(base_url=base_url, timeout_s=5)
        expected_describe = json.loads(
            (
                Path(__file__).parent / "api/public/fixtures/system_describe.json"
            ).read_text(encoding="utf-8")
        )
        assert client.describe_system() == expected_describe
        assert client.health_system()["ok"] is True
        created = client.create_harness()
        locked = client.lock_harness(created["data"]["path"])
        started = client.start_session(
            {
                "lock_id": locked["data"]["path"],
                "task": "Exercise the installed client",
                "session_id": "sdk-v1-smoke-session",
            },
            idempotency_key="start-smoke",
        )
        session_id = started["data"]["session"]["session_id"]
        deadline = time.monotonic() + 5
        while True:
            snapshot = client.get_session(session_id)
            if snapshot["data"]["session"]["status"] == "running":
                break
            assert time.monotonic() < deadline, snapshot
            time.sleep(0.01)
        sent = client.send_input_session(
            session_id, "Continue", idempotency_key="input-smoke"
        )
        canceled = client.cancel_session(
            session_id, "smoke complete", idempotency_key="cancel-smoke"
        )
        events = list(client.events_session(session_id))

        assert sent["data"]["session"]["event_count"] >= 2
        assert canceled["data"]["session"]["status"] == "canceled"
        assert events[-1]["kind"] == "session.canceled"


def test_public_session_readback_survives_service_restart() -> None:
    with tempfile.TemporaryDirectory() as workspace:
        with _running_default_server(workspace) as base_url:
            client = BreadBoardClient(base_url=base_url, timeout_s=5)
            created = client.create_harness()
            locked = client.lock_harness(created["data"]["path"])
            started = client.start_session(
                {
                    "lock_id": locked["data"]["path"],
                    "task": "Prove durable public readback",
                    "session_id": "sdk-v1-restart-session",
                },
                idempotency_key="restart-start",
            )
            session_id = started["data"]["session"]["session_id"]
            attachment_id = "restart-proof"
            canceled = client.cancel_session(
                session_id,
                "restart smoke complete",
                idempotency_key="restart-cancel",
            )
            assert canceled["data"]["session"]["status"] == "canceled"

        artifact_store = ArtifactStore(Path(workspace) / ".breadboard" / "artifacts")
        artifact_ref = artifact_store.put(
            b"durable artifact",
            media_type="text/plain",
        )
        manifest_ref = artifact_store.put_json(
            artifact_store.manifest(session_id, {attachment_id: artifact_ref})
        )
        artifact_store.materialize(
            manifest_ref,
            Path(workspace)
            / ".breadboard"
            / "artifacts"
            / "manifests"
            / f"{session_id}.{manifest_ref.digest.removeprefix('sha256:')}.json",
        )

        orphaned_session_id = "sdk-v1-orphaned-session"
        orphaned_dir = (
            Path(workspace) / ".breadboard" / "sessions" / orphaned_session_id
        )
        orphaned_dir.mkdir(parents=True)
        orphaned_event = {
            "schema_version": "bb.session_event.v1",
            "session_id": orphaned_session_id,
            "sequence": 1,
            "kind": "session.started",
            "occurred_at": "2026-08-18T00:00:00Z",
            "payload": {
                "effective_lock_hash": "sha256:" + "0" * 64,
                "task_hash": "sha256:" + "1" * 64,
            },
        }
        (orphaned_dir / "session_events.jsonl").write_text(
            json.dumps(orphaned_event) + "\n",
            encoding="utf-8",
        )

        with _running_default_server(workspace) as base_url:
            client = BreadBoardClient(base_url=base_url, timeout_s=5)
            recovered = client.get_session(session_id)
            listed = client.list_session()
            events = list(client.events_session(session_id))
            artifacts = client.artifacts_session(session_id)
            try:
                list(client.events_session(orphaned_session_id))
            except ApiError as error:
                orphaned_status = error.status
            else:
                raise AssertionError(
                    "orphaned running session was exposed as resumable"
                )

            assert recovered["data"]["session"]["status"] == "canceled"
            assert any(
                row["session_id"] == session_id for row in listed["data"]["sessions"]
            )
            assert all(
                row["session_id"] != orphaned_session_id
                for row in listed["data"]["sessions"]
            )
            assert events[-1]["kind"] == "session.canceled"
            assert len(artifacts["data"]["artifacts"]) == 1
            artifact = artifacts["data"]["artifacts"][0]
            assert artifact["name"] == attachment_id
            assert (
                client.verify_artifact(artifact["digest"])["data"]["verified"] is True
            )
            assert orphaned_status == 409


def _serve_for_node_smoke() -> None:
    with _running_default_server() as base_url:
        print(base_url, flush=True)
        sys.stdin.read()


if __name__ == "__main__" and sys.argv[1:] == ["--serve"]:
    _serve_for_node_smoke()
