from __future__ import annotations

import json
from pathlib import Path

import pytest

from breadboard_sdk.langflow_workflow import (
    LangflowWorkflowRequestError,
    execute_langflow_workflow_sync_request,
    parse_langflow_workflow_sync_request,
)


def _fixture_payload() -> dict:
    root = Path(__file__).resolve().parents[1]
    fixture = root / "tests/fixtures/contracts/langflow/simple_agent_minimal.json"
    return json.loads(fixture.read_text(encoding="utf-8"))


def _contract_fixture(name: str) -> dict:
    root = Path(__file__).resolve().parents[1]
    fixture = root / f"tests/fixtures/contracts/langflow/{name}"
    return json.loads(fixture.read_text(encoding="utf-8"))


def test_parse_langflow_workflow_sync_request_accepts_sync_payload() -> None:
    request = parse_langflow_workflow_sync_request(
        {
            "flow_id": "flow-123",
            "background": False,
            "stream": False,
            "inputs": {
                "ChatInput-2M1cy.input_value": "hi",
            },
        }
    )
    assert request.flow_id == "flow-123"
    assert request.inputs["ChatInput-2M1cy.input_value"] == "hi"


def test_parse_langflow_workflow_sync_request_rejects_stream_mode() -> None:
    with pytest.raises(LangflowWorkflowRequestError, match="Streaming mode"):
        parse_langflow_workflow_sync_request(
            {
                "flow_id": "flow-123",
                "stream": True,
            }
        )


def test_parse_langflow_workflow_sync_request_rejects_extra_keys() -> None:
    with pytest.raises(LangflowWorkflowRequestError, match="Unsupported Langflow workflow request keys"):
        parse_langflow_workflow_sync_request(
            {
                "flow_id": "flow-123",
                "inputs": {},
                "metadata": {},
            }
        )


def test_execute_langflow_workflow_sync_request_runs_supported_slice() -> None:
    result = execute_langflow_workflow_sync_request(
        {
            "flow_id": "flow-123",
            "inputs": {
                "ChatInput-2M1cy.input_value": "hello",
                "ChatInput-2M1cy.session_id": "sess-1",
            },
        },
        flow=_fixture_payload(),
        breadboard_runner=lambda payload: "world",
    )
    assert result.mode == "breadboard"
    assert result.response["outputs"]["ChatOutput-z90NZ"]["content"] == "world"
    assert result.response["inputs"]["ChatInput-2M1cy.session_id"] == "sess-1"


def test_execute_langflow_workflow_sync_request_uses_fallback_for_unsupported_slice() -> None:
    payload = _fixture_payload()
    payload["data"]["edges"] = []
    result = execute_langflow_workflow_sync_request(
        {"flow_id": "flow-123", "inputs": {}},
        flow=payload,
        breadboard_runner=lambda adapter_payload: "unused",
        native_fallback=lambda flow, inputs, exc: {"status": "failed", "errors": [{"error": str(exc)}]},
    )
    assert result.mode == "fallback"
    assert result.response["status"] == "failed"


def test_real_langflow_simple_agent_sync_contract_fixture() -> None:
    root = Path(__file__).resolve().parents[1]
    real_flow = (
        root.parent
        / "other_harness_refs/oss_targets/langflow/src/backend/base/langflow/initial_setup/starter_projects/Simple Agent.json"
    )
    if not real_flow.exists():
        pytest.skip("real Langflow starter flow not available in workspace")

    request = _contract_fixture("simple_agent_sync_request.json")
    expected = _contract_fixture("simple_agent_sync_expected_response.json")

    result = execute_langflow_workflow_sync_request(
        request,
        flow_path=real_flow,
        breadboard_runner=lambda payload: {"terminal_text": "4", "status": "completed", "errors": []},
    )

    assert result.mode == "breadboard"
    assert result.response == expected
