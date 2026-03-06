from __future__ import annotations

import json
from pathlib import Path

from breadboard_sdk.langflow_shim import handle_langflow_v2_sync_request


def _fixture_payload() -> dict:
    root = Path(__file__).resolve().parents[1]
    fixture = root / "tests/fixtures/contracts/langflow/simple_agent_minimal.json"
    return json.loads(fixture.read_text(encoding="utf-8"))


def _contract_fixture(name: str) -> dict:
    root = Path(__file__).resolve().parents[1]
    fixture = root / f"tests/fixtures/contracts/langflow/{name}"
    return json.loads(fixture.read_text(encoding="utf-8"))


def test_handle_langflow_v2_sync_request_uses_breadboard_path() -> None:
    request = _contract_fixture("simple_agent_sync_request.json")
    result = handle_langflow_v2_sync_request(
        request,
        flow=_fixture_payload(),
        breadboard_runner=lambda payload: {"terminal_text": "4", "status": "completed", "errors": []},
    )
    assert result.mode == "breadboard"
    assert result.response["outputs"]["ChatOutput-z90NZ"]["content"] == "4"


def test_handle_langflow_v2_sync_request_uses_native_fallback_for_unsupported_slice() -> None:
    payload = _fixture_payload()
    payload["data"]["edges"] = []
    seen: dict = {}
    result = handle_langflow_v2_sync_request(
        {"flow_id": "flow-123", "inputs": {}},
        flow=payload,
        breadboard_runner=lambda adapter_payload: "unused",
        native_sync_executor=lambda request, flow_payload, exc: seen.setdefault(
            "response",
            {
                "status": "failed",
                "mode": "native",
                "flow_id": request.flow_id,
                "error": str(exc),
            },
        ),
    )
    assert result.mode == "fallback"
    assert "ChatInput -> Agent input_value" in (result.compile_error or "")
    assert seen["response"]["mode"] == "native"
    assert result.response["status"] == "failed"


def test_real_flow_can_run_through_langflow_shim_contract() -> None:
    root = Path(__file__).resolve().parents[1]
    real_flow = (
        root.parent
        / "other_harness_refs/oss_targets/langflow/src/backend/base/langflow/initial_setup/starter_projects/Simple Agent.json"
    )
    if not real_flow.exists():
        return
    request = _contract_fixture("simple_agent_sync_request.json")
    expected = _contract_fixture("simple_agent_sync_expected_response.json")
    result = handle_langflow_v2_sync_request(
        request,
        flow_path=real_flow,
        breadboard_runner=lambda payload: {"terminal_text": "4", "status": "completed", "errors": []},
    )
    assert result.mode == "breadboard"
    assert result.response == expected
