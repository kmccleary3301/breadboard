from __future__ import annotations

import pytest

from agentic_coder_prototype.api.cli_bridge.models import SessionCreateRequest
from agentic_coder_prototype.api.cli_bridge.service import SessionService


@pytest.mark.asyncio
async def test_session_service_prewarms_for_oneshot_sessions(monkeypatch) -> None:
    service = SessionService()
    called: list[tuple[str, dict]] = []

    def _fake_prewarm(request: SessionCreateRequest, metadata: dict) -> None:
        called.append((request.config_path, dict(metadata)))

    async def _fake_start(self) -> None:  # type: ignore[no-untyped-def]
        return None

    monkeypatch.setattr(service, "_prewarm_request_runtime_sync", _fake_prewarm)
    monkeypatch.setattr(
        "agentic_coder_prototype.api.cli_bridge.session_runner.SessionRunner.start",
        _fake_start,
    )

    request = SessionCreateRequest(
        config_path="agent_configs/misc/codex_cli_gpt54mini_e4_live.yaml",
        task="Say hi",
        metadata={"cli_session_kind": "oneshot", "non_interactive_cli_session": True},
        stream=True,
    )

    response = await service.create_session(request)

    assert len(called) == 1
    assert called[0][0] == "agent_configs/misc/codex_cli_gpt54mini_e4_live.yaml"
    assert called[0][1]["cli_session_kind"] == "oneshot"
    record = await service.ensure_session(response.session_id)
    if record.dispatcher_task:
        await record.event_queue.put(None)
        await record.dispatcher_task


@pytest.mark.asyncio
async def test_session_service_prewarms_for_interactive_sessions(monkeypatch) -> None:
    service = SessionService()
    called: list[tuple[str, dict]] = []

    def _fake_prewarm(request: SessionCreateRequest, metadata: dict) -> None:
        called.append((request.config_path, dict(metadata)))

    async def _fake_start(self) -> None:  # type: ignore[no-untyped-def]
        return None

    monkeypatch.setattr(service, "_prewarm_request_runtime_sync", _fake_prewarm)
    monkeypatch.setattr(
        "agentic_coder_prototype.api.cli_bridge.session_runner.SessionRunner.start",
        _fake_start,
    )

    request = SessionCreateRequest(
        config_path="agent_configs/misc/codex_cli_gpt54mini_e4_live.yaml",
        task="Say hi",
        metadata={"cli_session_kind": "interactive"},
        stream=True,
    )

    response = await service.create_session(request)

    assert len(called) == 1
    assert called[0][0] == "agent_configs/misc/codex_cli_gpt54mini_e4_live.yaml"
    assert called[0][1]["cli_session_kind"] == "interactive"
    record = await service.ensure_session(response.session_id)
    if record.dispatcher_task:
        await record.event_queue.put(None)
        await record.dispatcher_task
