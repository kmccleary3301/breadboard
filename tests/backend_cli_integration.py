import asyncio
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient

# Ensure project root on path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from agentic_coder_prototype.api.cli_bridge.app import create_app
from agentic_coder_prototype.api.cli_bridge.events import EventType, SessionEvent

try:
    from tui_skeleton.src.commands.askLogic import runAsk
    from tui_skeleton.src.commands.resumeLogic import runResume
    from tui_skeleton.src.commands.sessions import mergeSessions
    from tui_skeleton.src.commands.files import formatFileList
    from tui_skeleton.src.api.types import SessionSummary
    CLI_IMPORTS_AVAILABLE = True
except ModuleNotFoundError:  # pragma: no cover - TypeScript sources not compiled
    CLI_IMPORTS_AVAILABLE = False
    runAsk = runResume = mergeSessions = formatFileList = SessionSummary = None  # type: ignore[assignment]


async def fake_event_stream(message: str):
    yield SessionEvent(
        type=EventType.ASSISTANT_MESSAGE,
        session_id="sess-1",
        payload={"text": message},
    )
    yield SessionEvent(
        type=EventType.COMPLETION,
        session_id="sess-1",
        payload={"summary": {"completed": True}},
    )


def run_backend_cli_integration():
    mock_service = MagicMock()
    mock_service.event_stream.side_effect = lambda session_id: fake_event_stream("Hello from backend")
    mock_service.ensure_session = AsyncMock(return_value=None)
    mock_service.list_sessions = AsyncMock(return_value=[
        SessionSummary(
            session_id="sess-1",
            status="completed",
            created_at="2025-01-01T00:00:00Z",
            last_activity_at="2025-01-01T00:10:00Z",
            completion_summary={"completed": True},
            reward_summary=None,
            logging_dir=None,
            metadata={"model": "test-model"},
        )
    ])
    mock_service.create_session = AsyncMock(return_value={"session_id": "sess-1", "status": "running", "created_at": "2025-01-01T00:00:00Z"})

    app = create_app(mock_service)
    client = TestClient(app)

    # Simple backend tests
    assert client.get("/sessions").status_code == 200
    stream_response = client.get("/sessions/sess-1/events")
    assert stream_response.status_code == 200
    assert "assistant_message" in stream_response.text

    # CLI logic tests
    os.environ["BREADBOARD_API_URL"] = "http://127.0.0.1:9099"  # not used directly but ensures config path

    ask_result = asyncio.run(runAsk({
        "prompt": "Say hello",
        "configPath": "cfg.yaml",
    }))
    assert ask_result.sessionId == "sess-1"
    assert ask_result.completion == {"completed": True}

    resume_result = asyncio.run(runResume({"sessionId": "sess-1"}))
    assert resume_result.completion == {"completed": True}

    merged = asyncio.run(mergeSessions(mock_service.list_sessions.return_value))
    assert merged[0].sessionId == "sess-1"

    table_output = formatFileList([
        {"path": "src/main.py", "type": "file", "size": 100},
        {"path": "docs", "type": "directory"},
    ])
    assert "src/main.py" in table_output


if __name__ == "__main__":
    run_backend_cli_integration()
