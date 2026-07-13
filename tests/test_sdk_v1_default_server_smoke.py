from __future__ import annotations

import contextlib
import os
import socket
import sys
import threading
import time
from collections.abc import Iterator
from datetime import datetime, timezone
from typing import Any, AsyncIterator

import requests
import uvicorn

from agentic_coder_prototype.api.cli_bridge import app as app_module
from agentic_coder_prototype.api.cli_bridge.events import EventType, SessionEvent
from agentic_coder_prototype.api.cli_bridge.models import (
    SessionCreateResponse,
    SessionInputResponse,
    SessionStatus,
)
from breadboard_sdk import BreadboardClient


_SESSION_ID = "sdk-v1-smoke-session"


class _SmokeSessionService:
    """Deterministic service boundary for exercising the real default HTTP app."""

    def __init__(self) -> None:
        self._atp_repl_enabled = False
        self.input_content: str | None = None

    async def create_session(self, request: Any) -> SessionCreateResponse:
        return SessionCreateResponse(
            session_id=_SESSION_ID,
            status=SessionStatus.RUNNING,
            created_at=datetime.now(timezone.utc),
        )

    async def send_input(self, session_id: str, request: Any) -> SessionInputResponse:
        assert session_id == _SESSION_ID
        self.input_content = request.content
        return SessionInputResponse()

    async def list_session_records(
        self,
        session_id: str,
        *,
        schema_version: str | None = None,
        offset: int = 0,
        limit: int = 100,
    ) -> dict[str, Any]:
        assert session_id == _SESSION_ID
        record = {
            "schema_version": "bb.sdk_smoke.v1",
            "kind": "input",
            "content": self.input_content,
        }
        return {
            "session_id": session_id,
            "records": [{"schema_version": record["schema_version"], "record": record}],
            "offset": offset,
            "limit": limit,
            "total": 1,
        }

    async def event_stream(
        self,
        session_id: str,
        *,
        replay: bool = False,
        limit: int | None = None,
        from_id: str | None = None,
        validated: bool = False,
    ) -> AsyncIterator[SessionEvent]:
        assert session_id == _SESSION_ID
        yield SessionEvent(
            type=EventType.COMPLETION,
            session_id=session_id,
            payload={"status": "completed"},
            seq=1,
        )


@contextlib.contextmanager
def _running_default_server() -> Iterator[str]:
    os.environ.pop("BREADBOARD_LEGACY_ROUTES", None)
    os.environ["RAY_SCE_LOCAL_MODE"] = "1"

    original_service = app_module.SessionService
    app_module.SessionService = _SmokeSessionService
    try:
        app = app_module.create_app()
    finally:
        app_module.SessionService = original_service

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


def test_python_sdk_readme_flow_against_default_server() -> None:
    with _running_default_server() as base_url:
        assert requests.get(f"{base_url}/sessions", timeout=5).status_code == 404

        client = BreadboardClient(base_url=base_url, timeout_s=5)
        assert client.health()["status"] == "ok"
        session = client.create_session(
            config_path="agent_configs/templates/minimal_harness.v2.yaml",
            task="List files",
            stream=True,
        )
        session_id = session["session_id"]
        client.post_input(session_id, content="Continue")
        records = client.read_session_records(session_id)
        events = list(client.stream_events(session_id))

        assert records["total"] == 1
        assert records["records"][0]["record"]["content"] == "Continue"
        assert [event["type"] for event in events] == ["completion"]


def _serve_for_node_smoke() -> None:
    with _running_default_server() as base_url:
        print(base_url, flush=True)
        sys.stdin.read()


if __name__ == "__main__" and sys.argv[1:] == ["--serve"]:
    _serve_for_node_smoke()
