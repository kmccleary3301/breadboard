from __future__ import annotations

import contextlib
import os
import socket
import sys
import tempfile
import threading
import time
from collections.abc import Iterator

import requests
import uvicorn

from agentic_coder_prototype.api.cli_bridge import app as app_module
from breadboard_sdk import BreadBoardClient




@contextlib.contextmanager
def _running_default_server() -> Iterator[str]:
    os.environ.pop("BREADBOARD_LEGACY_ROUTES", None)
    os.environ["RAY_SCE_LOCAL_MODE"] = "1"
    previous_workspace = os.environ.get("BREADBOARD_PUBLIC_WORKSPACE")
    workspace = tempfile.TemporaryDirectory()
    os.environ["BREADBOARD_PUBLIC_WORKSPACE"] = workspace.name
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
        if thread.is_alive():
            raise RuntimeError("default create_app server did not stop")
        if previous_workspace is None:
            os.environ.pop("BREADBOARD_PUBLIC_WORKSPACE", None)
        else:
            os.environ["BREADBOARD_PUBLIC_WORKSPACE"] = previous_workspace
        workspace.cleanup()


def test_python_sdk_readme_flow_against_default_server() -> None:
    with _running_default_server() as base_url:
        assert requests.get(f"{base_url}/sessions", timeout=5).status_code == 404

        client = BreadBoardClient(base_url=base_url, timeout_s=5)
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


def _serve_for_node_smoke() -> None:
    with _running_default_server() as base_url:
        print(base_url, flush=True)
        sys.stdin.read()


if __name__ == "__main__" and sys.argv[1:] == ["--serve"]:
    _serve_for_node_smoke()
