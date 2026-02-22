from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from agentic_coder_prototype.api.cli_bridge.models import SessionCreateRequest, SessionStatus
from agentic_coder_prototype.api.cli_bridge.registry import SessionRecord, SessionRegistry
from agentic_coder_prototype.api.cli_bridge.session_runner import SessionRunner


@pytest.mark.asyncio
async def test_local_bridge_permission_revoke_smoke(tmp_path: Path) -> None:
    workspace = tmp_path / "ws"
    workspace.mkdir(parents=True, exist_ok=True)

    cfg_path = tmp_path / "cfg.yaml"
    cfg_path.write_text(
        f"""
version: 2
workspace:
  root: {workspace.as_posix()}
providers:
  default_model: openrouter/openai/gpt-5-nano
  models:
    - id: openrouter/openai/gpt-5-nano
      adapter: openai
modes:
  - name: build
    prompt: "noop"
loop:
  sequence:
    - mode: build
""",
        encoding="utf-8",
    )

    registry = SessionRegistry()
    session = SessionRecord(session_id="sess_revoke", status=SessionStatus.RUNNING)
    request = SessionCreateRequest(
        config_path=str(cfg_path),
        task="permission revoke smoke",
        workspace=str(workspace),
        stream=False,
        metadata={"debug_permissions": True},
    )
    runner = SessionRunner(session=session, registry=registry, request=request)

    request_result = await runner.handle_command("run_tests", {"suite": "webapp-permission-revoke"})
    assert request_result["status"] == "ok"
    request_id = str(request_result["request_id"])

    permission_request_event = await asyncio.wait_for(session.event_queue.get(), timeout=1)
    assert permission_request_event is not None
    assert permission_request_event.type.value == "permission_request"
    assert permission_request_event.payload["request_id"] == request_id

    revoke_result = await runner.handle_command(
        "permission_decision",
        {
            "request_id": request_id,
            "decision": "revoke",
            "scope": "project",
            "note": "webapp revoke smoke",
        },
    )
    assert revoke_result["status"] == "ok"
    delivered = revoke_result.get("delivered") or {}
    delivered_payload = delivered.get("delivered") if isinstance(delivered, dict) else {}
    assert isinstance(delivered_payload, dict)
    assert delivered_payload.get("response") == "revoke"

    permission_response_event = await asyncio.wait_for(session.event_queue.get(), timeout=1)
    assert permission_response_event is not None
    assert permission_response_event.type.value == "permission_response"
    assert permission_response_event.payload["request_id"] == request_id
    assert permission_response_event.payload["response"] == "revoke"
    assert permission_response_event.payload["decision"] == "revoke"
