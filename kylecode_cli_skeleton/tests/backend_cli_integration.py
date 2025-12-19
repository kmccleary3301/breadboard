import asyncio
from unittest.mock import MagicMock
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from kylecode_cli_backend.events import SessionEvent, EventType
from kylecode_cli_skeleton.src.commands import askLogic, resumeLogic, sessions, files


def make_event_stream():
    async def generator():
        yield SessionEvent(
            type=EventType.ASSISTANT_MESSAGE,
            session_id="sess-1",
            payload={"text": "hello"},
        )
        yield SessionEvent(
            type=EventType.COMPLETION,
            session_id="sess-1",
            payload={"summary": {"completed": True}},
        )
    return generator()


def run():
    askLogic.ApiClient = MagicMock()
    askLogic.ApiClient.createSession.return_value = {"session_id": "sess-1"}
    askLogic.streamSessionEvents = MagicMock(side_effect=lambda session, options=None: make_event_stream())

    resumeLogic.ApiClient = MagicMock()
    resumeLogic.ApiClient.getSession.return_value = {
        "session_id": "sess-1",
        "status": "completed",
        "created_at": "2025-01-01T00:00:00Z",
        "last_activity_at": "2025-01-01T00:05:00Z",
        "completion_summary": {"completed": True},
        "reward_summary": None,
        "logging_dir": None,
        "metadata": {},
    }
    resumeLogic.streamSessionEvents = MagicMock(side_effect=lambda session, options=None: make_event_stream())

    sessions.listCachedSessions = MagicMock(return_value=[])

    ask_result = asyncio.run(askLogic.runAsk({"prompt": "Test", "configPath": "cfg.yaml"}))
    assert ask_result.sessionId == "sess-1"
    assert ask_result.completion == {"completed": True}

    resume_result = asyncio.run(resumeLogic.runResume({"sessionId": "sess-1"}))
    assert resume_result.completion == {"completed": True}

    merged = asyncio.run(sessions.mergeSessions([]))
    assert isinstance(merged, list)

    table = files.formatFileList([
        {"path": "src/index.ts", "type": "file", "size": 1234},
        {"path": "docs", "type": "directory"},
    ])
    assert "src/index.ts" in table


if __name__ == "__main__":
    run()
