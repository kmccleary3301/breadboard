from __future__ import annotations

import base64
import hashlib
import json
import sys
import httpx
import pytest

from tests.rl.harness.native_dependencies import require_hermes


@pytest.fixture
def context_compressor():
    source = require_hermes()
    sys.path.insert(0, str(source))
    try:
        from agent.context_compressor import ContextCompressor
        yield ContextCompressor
    finally:
        sys.path.remove(str(source))
from breadboard.rl.harness.hermes_worker import HermesActor, HermesWorkerError, MAX_OUTPUT_TOKENS
from breadboard.rl.harness.native_stream_profiles import HERMES_RESPONSE_CONSUMER_ID, NATIVE_STREAM_PROFILES






def test_hermes_stream_profile_configuration():
    """Verify HERMES_RESPONSE_CONSUMER_ID profile has correct compaction configuration."""
    profile = NATIVE_STREAM_PROFILES[HERMES_RESPONSE_CONSUMER_ID]
    assert profile.implements_compaction_phases is False
    assert profile.compaction_checkpoints == frozenset()
    assert profile.compaction_in_source is True
    assert profile.compaction_overflow_attempts == 3
    assert profile.compaction_summary_system_prompt is None
    assert profile.phase_mode == "checkpointed"




def test_hermes_compaction_threshold_formula_parity(context_compressor):
    """Verify stock Hermes threshold calculation:
    1. _effective_threshold_percent floors at 0.75 for windows < 512K:
       max(0.50, 0.75) = 0.75
    2. _compute_threshold_tokens with max_tokens=2048:
       effective_window = 131072 - 2048 = 129024
       pct_value = int(129024 * 0.75) = 96768
       floored = max(96768, MINIMUM_CONTEXT_LENGTH=64000) = 96768
    """
    max_tokens = 2048
    cc = context_compressor(
        model="test-model",
        threshold_percent=0.5,
        max_tokens=max_tokens,
    )
    cc.context_length = 131072

    assert cc.threshold_percent == 0.75
    assert cc.threshold_tokens == 96768

    # Verify should_compress behavior against threshold
    assert cc.should_compress(prompt_tokens=96767) is False
    assert cc.should_compress(prompt_tokens=96768) is True

def test_compaction_disabled_by_default():
    """Verify HermesActor initializes with compaction disabled and stays disabled."""
    actor = HermesActor(channel=None)
    assert actor._compaction_enabled is False
    assert actor._summary_requests == 0



@pytest.fixture
def summary_exchange(tmp_path, monkeypatch):
    class Channel:
        def __init__(self):
            self.requests = []

        def respond(self, result):
            self.requests.append(result)

        def receive(self):
            return {
                "operation": "provider_response",
                "payload": {
                    "remaining_seconds": 60,
                    "body_b64": base64.b64encode(b"{}").decode("ascii"),
                    "status_code": 200,
                    "headers": [["content-type", "application/json"]],
                },
            }

    channel = Channel()
    actor = HermesActor(channel)
    actor._phase = "sampling"
    actor._source_root = tmp_path
    actor._source_modules["httpx"] = httpx
    actor._tool_schemas = [{"type": "function", "function": {"name": "read", "parameters": {}}}]
    monkeypatch.syspath_prepend(str(tmp_path))
    return actor, channel


@pytest.mark.parametrize(
    "stock_body",
    [
        b'{ "stream" : false, "model": "m", "messages": [{"role":"user","content":"\\u0061"}], "value":1e+03 }\r\n',
        b'{"model":"m","messages":[{"role":"user","content":"escaped \\" quote"}]}\n',
        b'{ \n }\t',
    ],
)
def test_summary_exchange_preserves_stock_raw_bytes(summary_exchange, stock_body):
    actor, channel = summary_exchange
    request = httpx.Request(
        "POST", "https://provider.test/v1/chat/completions",
        headers={"x-stock-header": "retained"}, content=stock_body,
    )

    response = actor._handle_http(request, is_summary=True)

    assert response.status_code == 200
    result = channel.requests[0]
    wire = result["http_request"]
    rewritten = base64.b64decode(wire["body_b64"])
    closing = len(stock_body.rstrip(b" \t\r\n")) - 1
    assert rewritten[:closing] == stock_body[:closing]
    assert rewritten.endswith(stock_body[closing:])
    stock = json.loads(stock_body)
    appended = rewritten[closing:len(rewritten) - len(stock_body[closing:])]
    overlay = json.loads(b"{" + (appended[1:] if stock else appended) + b"}")
    assert overlay == {"tools": actor._tool_schemas, "max_tokens": MAX_OUTPUT_TOKENS}
    assert json.loads(rewritten) == {**stock, **overlay}
    assert result["stock_body_sha256"] == hashlib.sha256(stock_body).hexdigest()
    assert result["purpose"] == "compaction_summary"
    assert actor._requests == 0
    assert actor._summary_requests == 1
    headers = {key.lower(): value for key, value in wire["headers"]}
    assert headers["content-length"] == str(len(rewritten))
    assert headers["x-stock-header"] == "retained"


@pytest.mark.parametrize(
    "stock_body",
    [b"[]", b'{"tools":[]}', b'{"max_tokens":8192}'],
)
def test_summary_exchange_rejects_stock_field_overrides(summary_exchange, stock_body):
    actor, channel = summary_exchange
    request = httpx.Request("POST", "https://provider.test/v1/chat/completions", content=stock_body)

    with pytest.raises(HermesWorkerError, match="stock summary request"):
        actor._handle_http(request, is_summary=True)

    assert channel.requests == []


def test_main_exchange_keeps_original_raw_body(summary_exchange):
    actor, channel = summary_exchange
    stock_body = b'{ "model" : "m", "messages": [], "max_tokens":4096 }\n'
    request = httpx.Request("POST", "https://provider.test/v1/chat/completions", content=stock_body)

    actor._handle_http(request)

    result = channel.requests[0]
    assert base64.b64decode(result["http_request"]["body_b64"]) == stock_body
    assert "stock_body_sha256" not in result
    assert actor._requests == 1
    assert actor._summary_requests == 0
