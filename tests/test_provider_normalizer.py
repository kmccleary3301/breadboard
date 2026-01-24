from agentic_coder_prototype.provider_normalizer import normalize_provider_result
from agentic_coder_prototype.provider_runtime import ProviderResult, ProviderMessage, ProviderToolCall


def test_normalize_provider_result_produces_events():
    result = ProviderResult(
        messages=[
            ProviderMessage(
                role="assistant",
                content="Hello",
                tool_calls=[
                    ProviderToolCall(id="1", name="run_tool", arguments="{}"),
                ],
                finish_reason="stop",
            )
        ],
        raw_response={},
        usage={"total_tokens": 10},
        metadata={"model": "test-model"},
        model="test-model",
    )

    events = normalize_provider_result(result)
    event_types = [event["type"] for event in events]
    assert "text" in event_types
    assert "tool_call" in event_types
    assert event_types[-1] == "finish"
