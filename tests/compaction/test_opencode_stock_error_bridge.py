"""Native failure forwarding follows the same classifiers as its preset."""
import json
from types import SimpleNamespace

import httpx
from openai import APIStatusError
import pytest

from breadboard_engine.compaction.controller import CompactionController
from breadboard_engine.execution import opencode_tools
from breadboard_engine.state.session_state import SessionState


@pytest.mark.parametrize("status", [400, 500])
@pytest.mark.parametrize("preset,semantics,message,forwarded", [
    ("opencode@1.2.17", "opencode_1_2_17", "too many tokens: 74000 tokens > 64000 maximum", False),
    ("oh-my-opencode@3.10.0", "oh_my_opencode_3_10_0", "too many tokens: 74000 tokens > 64000 maximum", True),
    ("oh-my-opencode@3.10.0", "oh_my_opencode_3_10_0", "prompt is too long for this model", True),
    ("oh-my-opencode@3.10.0", "oh_my_opencode_3_10_0", "request would exceed model context budget", False),
    ("opencode@1.2.17", "opencode_1_2_17", "context_length_exceeded", True),
])
def test_original_api_error_uses_recipe_native_or_plugin_parser(monkeypatch, tmp_path, preset, semantics, message, forwarded, status):
    body = {"error": {"message": message, "type": "invalid_request_error"}}
    response = httpx.Response(status, content=json.dumps(body).encode(),
                              headers={"content-type": "application/json"},
                              request=httpx.Request("POST", "http://localhost/v1/responses"))
    original = APIStatusError(message, response=response, body=body)
    wrapped = RuntimeError("sanitized context_length_exceeded")
    wrapped.__cause__ = original
    controller = CompactionController({"compaction": {"enabled": True, "preset": preset, "contextWindow": 64000}})
    conductor = SimpleNamespace(config={"tools": {"native_semantics": semantics}}, compaction_controller=controller)
    observed = []
    def submit_overflow(status, headers, payload):
        observed.append((status, headers, payload))
        return "summary"
    session = SimpleNamespace(submit_overflow=submit_overflow)
    monkeypatch.setattr(opencode_tools, "get_session", lambda owner, state: session)
    state = SessionState(workspace=str(tmp_path), image="python-dev:latest", config=conductor.config)
    opencode_tools.submit_stock_overflow(conductor, state, wrapped)
    assert state.get_provider_metadata("opencode_stock_recovery_outcome") == ("summary" if forwarded else None)
    assert bool(observed) is forwarded
    if forwarded:
        assert observed == [(status, dict(response.headers), response.content)]
    assert all(getattr(trigger, "pending", None) is None for trigger in controller.recipe.triggers)
