from __future__ import annotations

import json
from pathlib import Path

import pytest

from breadboard_sdk.langflow_executor import (
    execute_langflow_simple_agent_sync,
    execute_langflow_simple_agent_sync_from_path,
)


def _fixture_payload() -> dict:
    root = Path(__file__).resolve().parents[1]
    fixture = root / "tests/fixtures/contracts/langflow/simple_agent_minimal.json"
    return json.loads(fixture.read_text(encoding="utf-8"))


def test_execute_langflow_simple_agent_sync_uses_breadboard_runner_for_supported_slice() -> None:
    seen: dict = {}

    def runner(payload):
        seen["payload"] = payload.to_dict()
        return {"terminal_text": "4", "status": "completed", "errors": []}

    result = execute_langflow_simple_agent_sync(
        _fixture_payload(),
        flow_id="flow-123",
        flat_inputs={"ChatInput-2M1cy.input_value": "2+2"},
        breadboard_runner=runner,
    )

    assert result.mode == "breadboard"
    assert result.payload is not None
    assert seen["payload"]["input_text"] == "2+2"
    assert result.response["outputs"]["ChatOutput-z90NZ"]["content"] == "4"


def test_execute_langflow_simple_agent_sync_falls_back_when_compile_fails() -> None:
    payload = _fixture_payload()
    payload["data"]["edges"] = []
    seen: dict = {}

    def fallback(flow, flat_inputs, exc):
        seen["reason"] = str(exc)
        return {"object": "response", "status": "failed", "errors": [{"error": "fallback"}], "outputs": {}}

    result = execute_langflow_simple_agent_sync(
        payload,
        flow_id="flow-123",
        flat_inputs={},
        breadboard_runner=lambda payload: "never-used",
        native_fallback=fallback,
    )

    assert result.mode == "fallback"
    assert "ChatInput -> Agent input_value" in seen["reason"]
    assert result.compile_error is not None
    assert result.response["status"] == "failed"


def test_execute_langflow_simple_agent_sync_raises_without_fallback_for_unsupported_slice() -> None:
    payload = _fixture_payload()
    payload["data"]["edges"] = []
    with pytest.raises(Exception):
        execute_langflow_simple_agent_sync(
            payload,
            flow_id="flow-123",
            flat_inputs={},
            breadboard_runner=lambda payload: "never-used",
        )


def test_execute_langflow_simple_agent_sync_from_path_loads_fixture() -> None:
    root = Path(__file__).resolve().parents[1]
    fixture = root / "tests/fixtures/contracts/langflow/simple_agent_minimal.json"
    result = execute_langflow_simple_agent_sync_from_path(
        fixture,
        flow_id="flow-123",
        flat_inputs={"ChatInput-2M1cy.input_value": "hello"},
        breadboard_runner=lambda payload: "world",
    )
    assert result.mode == "breadboard"
    assert result.response["outputs"]["ChatOutput-z90NZ"]["content"] == "world"
