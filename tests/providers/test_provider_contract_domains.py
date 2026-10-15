"""Behavior locks for provider request/tool domain ownership."""
import pytest

from breadboard_engine.provider.contract_messages import ProviderMessage
from breadboard_engine.provider.contract_requests import ProviderRequest, normalize_request_messages
from breadboard_engine.provider.contract_tools import ProviderToolCall, normalize_tool_call_dict
from breadboard_engine.provider.contract_wire import ProviderContractError


def test_tool_and_request_domains_preserve_canonical_content():
    call = normalize_tool_call_dict({
        "id": "call", "name": "echo", "arguments": {"value": "café"},
    })
    message = ProviderMessage(role="assistant", content="", tool_calls=[call])
    request = ProviderRequest(stream=False, messages=[message.as_dict()], tools=[])
    block = request.messages[0]["content"][-1]
    assert block == {"type": "tool_call", **call.as_dict()}
    assert block["arguments"] == {"value": "café"}


def test_request_domain_preserves_native_raw_arguments():
    call = ProviderToolCall(id="call", name="echo", arguments="not JSON", allow_raw_arguments=True)
    message = ProviderMessage(role="assistant", content="", tool_calls=[call])
    request = ProviderRequest(stream=False, messages=[message.as_dict()], tools=[])
    assert request.messages[0]["content"][-1] == {
        "type": "tool_call", "call_id": "call", "name": "echo", "raw_arguments": "not JSON",
    }


def test_request_domain_retains_transport_validation():
    with pytest.raises(ProviderContractError):
        normalize_request_messages([{"role": "user", "content": "hello", "tool_call_id": "call"}])
