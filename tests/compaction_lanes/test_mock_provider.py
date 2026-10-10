"""Unit tests for the reusable recording mock provider."""

import json
import urllib.request
from pathlib import Path

import pytest

from scripts.compaction_lanes.mock_provider import MockProvider


def test_openai_responses_sse_and_recording(tmp_path: Path):
    record_file = tmp_path / "requests.jsonl"
    script = [
        {
            "type": "text",
            "text": "Hello from mock responses!",
            "usage": {"input_tokens": 120, "output_tokens": 30, "total_tokens": 150},
        },
        {
            "type": "tool_call",
            "call_id": "call_abc123",
            "name": "shell",
            "arguments": json.dumps({"command": "echo 1"}),
            "usage": {"input_tokens": 150, "output_tokens": 15, "total_tokens": 165},
        },
        {
            "type": "error",
            "error": "context_length_exceeded",
            "message": "Maximum context length exceeded.",
            "status_code": 400,
        },
    ]

    with MockProvider(script=script, record_path=record_file) as provider:
        # Request 1: Text
        req = urllib.request.Request(
            f"{provider.base_url}/v1/responses",
            data=json.dumps({"model": "gpt-4o", "input": [{"role": "user", "content": "hi"}]}).encode("utf-8"),
            headers={"Content-Type": "application/json", "Authorization": "Bearer secret-key"},
            method="POST",
        )
        with urllib.request.urlopen(req) as resp:
            assert resp.status == 200
            assert "text/event-stream" in resp.headers.get("Content-Type")
            body = resp.read().decode("utf-8")
            assert "response.created" in body
            assert "Hello from mock responses!" in body
            assert '"total_tokens": 150' in body

        # Request 2: Tool call
        req = urllib.request.Request(
            f"{provider.base_url}/v1/responses",
            data=json.dumps({"model": "gpt-4o", "input": []}).encode("utf-8"),
            headers={"Content-Type": "application/json", "Authorization": "Bearer secret-key"},
            method="POST",
        )
        with urllib.request.urlopen(req) as resp:
            assert resp.status == 200
            body = resp.read().decode("utf-8")
            assert "call_abc123" in body
            assert "shell" in body

        # Request 3: Error
        req = urllib.request.Request(
            f"{provider.base_url}/v1/responses",
            data=json.dumps({"model": "gpt-4o", "input": []}).encode("utf-8"),
            headers={"Content-Type": "application/json", "Authorization": "Bearer secret-key"},
            method="POST",
        )
        with pytest.raises(urllib.error.HTTPError) as exc_info:
            urllib.request.urlopen(req)
        assert exc_info.value.code == 400
        err_json = json.loads(exc_info.value.read().decode("utf-8"))
        assert err_json["error"]["code"] == "context_length_exceeded"

    # Check JSONL recordings
    assert record_file.exists()
    lines = [json.loads(line) for line in record_file.read_text(encoding="utf-8").strip().split("\n")]
    assert len(lines) == 3
    for line in lines:
        assert line["method"] == "POST"
        assert line["path"] == "/v1/responses"
        # Ensure auth header was stripped
        assert "authorization" not in {k.lower() for k in line["headers"]}
        assert line["raw_body_bytes"]
        assert line["json"] is not None


def test_openai_chat_completions_json_and_sse(tmp_path: Path):
    record_file = tmp_path / "chat_requests.jsonl"
    script = [
        # Non-streaming JSON response
        {
            "type": "text",
            "text": "Chat completion JSON",
            "usage": {"input_tokens": 10, "output_tokens": 5, "total_tokens": 15},
        },
        # Streaming SSE response
        {
            "type": "text",
            "text": "Chat completion SSE",
            "usage": {"input_tokens": 20, "output_tokens": 10, "total_tokens": 30},
        },
        # Chat completion error
        {
            "type": "error",
            "error": "context_length_exceeded",
            "message": "Chat context window limit reached.",
            "status_code": 400,
        },
    ]

    with MockProvider(script=script, record_path=record_file) as provider:
        # Non-streaming
        req = urllib.request.Request(
            f"{provider.base_url}/v1/chat/completions",
            data=json.dumps({"model": "gpt-4o", "messages": [{"role": "user", "content": "hi"}], "stream": False}).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req) as resp:
            assert resp.status == 200
            data = json.loads(resp.read().decode("utf-8"))
            assert data["choices"][0]["message"]["content"] == "Chat completion JSON"
            assert data["usage"]["total_tokens"] == 15

        # Streaming
        req = urllib.request.Request(
            f"{provider.base_url}/v1/chat/completions",
            data=json.dumps({"model": "gpt-4o", "messages": [{"role": "user", "content": "hi"}], "stream": True}).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req) as resp:
            assert resp.status == 200
            body = resp.read().decode("utf-8")
            assert "chat.completion.chunk" in body
            assert "Chat completion SSE" in body
            assert "data: [DONE]" in body

        # Error
        req = urllib.request.Request(
            f"{provider.base_url}/v1/chat/completions",
            data=json.dumps({"model": "gpt-4o", "messages": []}).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with pytest.raises(urllib.error.HTTPError) as exc_info:
            urllib.request.urlopen(req)
        assert exc_info.value.code == 400
        err_json = json.loads(exc_info.value.read().decode("utf-8"))
        assert err_json["error"]["code"] == "context_length_exceeded"


def test_anthropic_messages_sse_and_error(tmp_path: Path):
    record_file = tmp_path / "anthropic_requests.jsonl"
    script = [
        {
            "type": "text",
            "text": "Hello Claude user!",
            "usage": {"input_tokens": 40, "output_tokens": 12},
        },
        {
            "type": "tool_call",
            "call_id": "toolu_01",
            "name": "calc",
            "arguments": json.dumps({"x": 2}),
            "usage": {"input_tokens": 50, "output_tokens": 10},
        },
        {
            "type": "error",
            "error": "invalid_request_error",
            "message": "prompt is too long: 200001 tokens > 200000 maximum context length",
            "status_code": 400,
        },
    ]

    with MockProvider(script=script, record_path=record_file) as provider:
        # Request 1: Text SSE
        req = urllib.request.Request(
            f"{provider.base_url}/v1/messages",
            data=json.dumps({"model": "claude-3-5-sonnet", "messages": [{"role": "user", "content": "hi"}]}).encode("utf-8"),
            headers={"Content-Type": "application/json", "x-api-key": "secret-anthropic-key"},
            method="POST",
        )
        with urllib.request.urlopen(req) as resp:
            assert resp.status == 200
            body = resp.read().decode("utf-8")
            assert "message_start" in body
            assert "content_block_start" in body
            assert "Hello Claude user!" in body
            assert "message_stop" in body

        # Request 2: Tool call SSE
        req = urllib.request.Request(
            f"{provider.base_url}/v1/messages",
            data=json.dumps({"model": "claude-3-5-sonnet", "messages": []}).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req) as resp:
            assert resp.status == 200
            body = resp.read().decode("utf-8")
            assert "tool_use" in body
            assert "toolu_01" in body
            assert "calc" in body

        # Request 3: Error
        req = urllib.request.Request(
            f"{provider.base_url}/v1/messages",
            data=json.dumps({"model": "claude-3-5-sonnet", "messages": []}).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with pytest.raises(urllib.error.HTTPError) as exc_info:
            urllib.request.urlopen(req)
        assert exc_info.value.code == 400
        err_json = json.loads(exc_info.value.read().decode("utf-8"))
        assert err_json["error"]["type"] == "invalid_request_error"
        assert "prompt is too long" in err_json["error"]["message"]


def test_exhausted_script_is_not_a_successful_response():
    with MockProvider(script=[]) as provider:
        request = urllib.request.Request(
            f"{provider.base_url}/v1/chat/completions",
            data=b'{"model":"model-a","messages":[]}',
            headers={"Content-Type": "application/json"},
        )
        with pytest.raises(urllib.error.HTTPError) as error:
            urllib.request.urlopen(request)
        assert error.value.code == 409
        assert b"script exhausted" in error.value.read()
        assert len(provider.engine.recorded_requests) == 1


@pytest.mark.parametrize("endpoint", ["responses", "chat/completions", "messages"])
def test_mock_wire_identifiers_are_replay_stable(endpoint):
    script = [{"type": "tool_call", "name": "bash", "arguments": "{}"}]
    bodies = []
    recordings = []
    for _ in range(2):
        with MockProvider(script=script) as provider:
            request = urllib.request.Request(
                f"{provider.base_url}/v1/{endpoint}",
                data=b'{"model":"model-a","messages":[],"stream":true}',
                headers={"Content-Type": "application/json"},
            )
            with urllib.request.urlopen(request) as response:
                bodies.append(response.read())
            recordings.append(provider.engine.recorded_requests[0]["timestamp"])
    assert bodies[0] == bodies[1]
    assert recordings == [1700000000, 1700000000]
