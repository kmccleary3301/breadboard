from __future__ import annotations

import json
from pathlib import Path

import pytest

from breadboard_sdk.langflow_patch import make_langflow_v2_sync_override


class _Req:
    def __init__(self, flow_id: str, inputs: dict, background: bool = False, stream: bool = False):
        self.flow_id = flow_id
        self.inputs = inputs
        self.background = background
        self.stream = stream


class _Flow:
    def __init__(self, data: dict):
        self.data = data


def _fixture_payload() -> dict:
    root = Path(__file__).resolve().parents[1]
    fixture = root / "tests/fixtures/contracts/langflow/simple_agent_minimal.json"
    return json.loads(fixture.read_text(encoding="utf-8"))


@pytest.mark.asyncio
async def test_make_langflow_v2_sync_override_uses_breadboard_for_supported_slice() -> None:
    override = make_langflow_v2_sync_override(
        breadboard_runner=lambda payload: {"terminal_text": "4", "status": "completed", "errors": []},
        native_sync_executor=lambda *args, **kwargs: {"status": "native"},
    )
    response = await override(
        _Req(
            "flow-123",
            {
                "ChatInput-2M1cy.input_value": "2+2",
                "ChatInput-2M1cy.session_id": "sess-override",
            },
        ),
        _Flow(_fixture_payload()),
    )
    assert response["status"] == "completed"
    assert response["outputs"]["ChatOutput-z90NZ"]["content"] == "4"


@pytest.mark.asyncio
async def test_make_langflow_v2_sync_override_falls_back_to_native_for_unsupported_slice() -> None:
    payload = _fixture_payload()
    payload["data"]["edges"] = []
    seen = {}

    async def native(*args, **kwargs):
        seen["called"] = True
        return {"status": "native"}

    override = make_langflow_v2_sync_override(
        breadboard_runner=lambda payload: {"terminal_text": "unused", "status": "completed", "errors": []},
        native_sync_executor=native,
    )
    response = await override(_Req("flow-123", {}), _Flow(payload))
    assert seen["called"] is True
    assert response["status"] == "native"
