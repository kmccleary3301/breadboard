from __future__ import annotations

import pytest

from agentic_coder_prototype.api.cli_bridge.models import SessionCreateRequest, SessionStatus
from agentic_coder_prototype.api.cli_bridge.registry import SessionRecord, SessionRegistry
from agentic_coder_prototype.api.cli_bridge.session_runner import SessionRunner


@pytest.mark.asyncio
async def test_session_child_next_switches_when_target_exists() -> None:
    registry = SessionRegistry()
    source = SessionRecord(session_id="sess-source", status=SessionStatus.RUNNING)
    target = SessionRecord(session_id="sess-child-1", status=SessionStatus.RUNNING)
    await registry.create(source)
    await registry.create(target)

    request = SessionCreateRequest(config_path="dummy.yml", task="hi", stream=False)
    runner = SessionRunner(session=source, registry=registry, request=request)

    result = await runner.handle_command(
        "session_child_next",
        {"child_session_id": "sess-child-1", "parent_session_id": "sess-source"},
    )

    assert result["status"] == "ok"
    assert result["switched"] is True
    assert result["target_session_id"] == "sess-child-1"
    assert result["active_session_id"] == "sess-child-1"


@pytest.mark.asyncio
async def test_session_nav_returns_not_found_for_unknown_target() -> None:
    registry = SessionRegistry()
    source = SessionRecord(session_id="sess-source", status=SessionStatus.RUNNING)
    await registry.create(source)

    request = SessionCreateRequest(config_path="dummy.yml", task="hi", stream=False)
    runner = SessionRunner(session=source, registry=registry, request=request)

    result = await runner.handle_command("session_parent", {"parent_session_id": "sess-missing"})

    assert result["status"] == "ok"
    assert result["switched"] is False
    assert result["reason"] == "target_not_found"
    assert result["target_session_id"] == "sess-missing"


@pytest.mark.asyncio
async def test_session_nav_returns_missing_when_no_target_provided() -> None:
    registry = SessionRegistry()
    source = SessionRecord(session_id="sess-source", status=SessionStatus.RUNNING)
    await registry.create(source)

    request = SessionCreateRequest(config_path="dummy.yml", task="hi", stream=False)
    runner = SessionRunner(session=source, registry=registry, request=request)

    result = await runner.handle_command("session_child_previous", {})

    assert result["status"] == "ok"
    assert result["switched"] is False
    assert result["reason"] == "target_missing"

