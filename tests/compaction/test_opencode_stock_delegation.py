"""Real pinned-plugin child generations and authenticated cancellation."""
import json
from pathlib import Path

import pytest

from breadboard_engine.execution.opencode_tools import OpenCodeToolSession, responses_payload
from scripts.compaction_lanes.opencode_lane import ResponsesProvider
from tests.compaction.test_opencode_stock_tools import MODEL, binary, isolated_environment, response
from tests.compaction.test_opencode_stock_omo_groups import PLUGIN, PLUGIN_DIGEST, LSP_PIN, direct_omo


@pytest.fixture
def delegated_session(tmp_path, binary):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    config_dir = workspace / ".opencode"
    config_dir.mkdir()
    (config_dir / "oh-my-opencode.json").write_text(json.dumps({
        "agents": {name: {"model": MODEL} for name in ("multimodal-looker", "explore")},
    }))
    session = OpenCodeToolSession(str(workspace), MODEL, binary=binary, plugin_root=PLUGIN,
        timeout=120, stock_config={"agent": {"title": {"disable": True}}}, lsp_root=LSP_PIN,
        read_only_roots=[{"path": str(PLUGIN), "digest": PLUGIN_DIGEST}],
        environment=isolated_environment(tmp_path / "stock"))
    try:
        yield session
    finally:
        session.close()


def raw_descriptor(payload):
    return {"type": "raw", "headers": {"content-type": "text/event-stream"},
            "chunks": [responses_payload(payload, True).decode()]}


def connect_auxiliary(session, provider):
    def auxiliary(body, headers, path, provider_id, *, raw_body):
        assert provider_id == "openai"
        assert json.loads(raw_body) == body
        assert not any(key.lower().startswith("x-bb-stock-") for key in headers)
        return session.forward_auxiliary_http(provider.base_url + path, headers, raw_body)
    session.auxiliary = auxiliary


def test_real_look_at_child_result_matches_direct_stock(delegated_session, binary, tmp_path):
    session = delegated_session
    empty_root = session.api("GET", "/session/" + session.session_id)
    # A real, complete PNG is passed to stock's actual multimodal child prompt.
    image = "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+/fN8AAAAASUVORK5CYII="
    call = ("call_look_at", "look_at", {"image_data": image, "goal": "Describe the pixel."})
    child = raw_descriptor(response(session, text="Image inspected through stock."))
    with ResponsesProvider() as provider:
        provider.engine.set_child_script(session.session_id, [child])
        connect_auxiliary(session, provider)
        session.user([{"type": "text", "text": "Execute this stock tool group."}])
        session.submit(response(session, [call]))
        actual = session.result(call[0])["__mvi_text_output"]
        assert actual == "Image inspected through stock."
        children = session.api("GET", "/session/" + session.session_id + "/children")
        assert len(children) == 1 and children[0]["parentID"] == session.session_id
        native_child = provider.engine.recorded_requests
        assert len(native_child) == 1
        images = [part for item in native_child[0]["json"]["input"] for part in item.get("content", [])
                  if isinstance(part, dict) and part.get("type") == "input_image"]
        assert len(images) == 1 and images[0]["image_url"] == "data:image/png;base64," + image
        session.submit(response(session))
    direct_requests = []
    expected = direct_omo(binary, session.workspace, tmp_path / "oracle", [call],
                          root_session=empty_root, child_script=[child], captured_requests=direct_requests)
    assert actual == expected[call[0]]
    direct_child = next(row for row in direct_requests if row["json"].get("prompt_cache_key") != session.session_id)
    direct_images = [part for item in direct_child["json"]["input"] for part in item.get("content", [])
                     if isinstance(part, dict) and part.get("type") == "input_image"]
    assert images == direct_images


def test_real_background_cancel_aborts_child_and_cleans_up_transport(delegated_session):
    session = delegated_session
    with ResponsesProvider() as provider:
        provider.engine.set_child_script(session.session_id, [{"type": "delegated_pending"}])
        connect_auxiliary(session, provider)
        try:
            session.user([{"type": "text", "text": "Keep a real background inspection running."}])
            session.submit(response(session, [("call_delegated", "task", {
                "subagent_type": "explore", "load_skills": [], "description": "Held inspection",
                "prompt": "Inspect this workspace while the parent continues.", "run_in_background": True,
            })]))
            assert provider.engine.delegated_arrived.wait(10)
            launched = session.result("call_delegated")["__mvi_text_output"]
            assert "Background task launched." in launched
            children = session.api("GET", "/session/" + session.session_id + "/children")
            assert len(children) == 1
            child_id = children[0]["id"]
            assert child_id in launched
            session.submit(response(session, [("call_cancel", "background_cancel", {"all": True})]))
            cancelled = session.result("call_cancel")["__mvi_text_output"]
            assert "Cancelled 1 background task(s)" in cancelled and child_id in cancelled
            native = next(item for item in session.pending["body"]["input"]
                          if item.get("type") == "function_call_output" and item.get("call_id") == "call_cancel")
            assert native["output"] == cancelled
            assert child_id not in session.api("GET", "/session/status")
            assert not any(request.session_id == child_id for request in session.auxiliary_transport.requests)
            session.submit(response(session))
            session.close()
        finally:
            session.close()
            provider.engine.delegated_release.set()
        assert provider.engine.delegated_finished.wait(10)
