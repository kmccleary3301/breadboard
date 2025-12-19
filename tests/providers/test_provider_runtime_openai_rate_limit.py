import types

from agentic_coder_prototype.provider_runtime import OpenAIResponsesRuntime, ProviderRuntimeContext


def test_call_with_raw_response_retries_on_rate_limit(monkeypatch) -> None:
    runtime = OpenAIResponsesRuntime(types.SimpleNamespace(provider_id="openai", runtime_id="openai_responses"))

    class FakeHttpResponse:
        status_code = 429
        headers = {"retry-after": "0"}
        text = "rate limited"

    class FakeRateLimitError(Exception):
        def __init__(self, message: str) -> None:
            super().__init__(message)
            self.response = FakeHttpResponse()

    class FakeRawResponse:
        def __init__(self) -> None:
            self.headers = {"Content-Type": "application/json"}
            self.status_code = 200
            self.content = b"{\"ok\": true}"

        def parse(self):
            return {"ok": True}

    call_count = {"n": 0}

    class FakeWithRawResponse:
        def create(self, **_kwargs):
            call_count["n"] += 1
            if call_count["n"] == 1:
                raise FakeRateLimitError("Error code: 429 - rate_limit_exceeded (try again in 0ms)")
            return FakeRawResponse()

    class FakeCollection:
        with_raw_response = FakeWithRawResponse()

    # Avoid real sleeping in case fallback backoff kicks in.
    monkeypatch.setattr("agentic_coder_prototype.provider_runtime.time.sleep", lambda *_args, **_kwargs: None)

    context = ProviderRuntimeContext(session_state=types.SimpleNamespace(), agent_config={}, stream=False)
    result = runtime._call_with_raw_response(
        FakeCollection(),
        error_context="responses.create",
        context=context,
        model="gpt-test",
        input=[],
    )

    assert result == {"ok": True}
    assert call_count["n"] == 2

