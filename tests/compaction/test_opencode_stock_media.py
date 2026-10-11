"""Native user media uses the existing turn capability, never MIME guessing."""
import base64

import pytest

from breadboard.product.runtime.artifacts import put_workspace_artifact
from breadboard_engine.execution.opencode_tools import StockExecutionError, stock_user_parts
from breadboard_engine.provider.contracts import ProviderContractError, ProviderRuntimeContext
from breadboard_engine.state.session_state import SessionState
from pathlib import Path
from types import SimpleNamespace
from breadboard_engine.provider.adapters import OpenAIAdapter
from breadboard_engine.provider.runtimes.openai.responses import OpenAIResponsesRuntime
from tests.compaction.test_opencode_stock_tools import binary, plain, direct_stock, response


def test_canonical_media_resolves_existing_authorized_artifact(tmp_path):
    content = b"authorized image bytes"
    artifact = put_workspace_artifact(tmp_path, content, media_type="image/png")
    uri = "attachment://" + artifact.digest
    state = SessionState(workspace=str(tmp_path), image="python-dev:latest", config={})
    state.set_provider_metadata("attachment_capabilities", {uri: artifact.as_dict()})
    context = ProviderRuntimeContext(session_state=state, agent_config={})
    block = {"type": "media", "kind": "image", "uri": uri, "mime": "image/png"}
    assert stock_user_parts([block], context=context) == [{
        "type": "file", "mime": "image/png", "filename": "attachment",
        "url": "data:image/png;base64," + base64.b64encode(content).decode(),
    }]
    state.set_provider_metadata("attachment_capabilities", {})
    with pytest.raises(ProviderContractError, match="not authorized"):
        stock_user_parts([block], context=context)


def test_native_image_url_never_invents_mime():
    with pytest.raises(StockExecutionError, match="URL and MIME"):
        stock_user_parts([{"type": "image_url", "image_url": {"url": "https://example.com/image"}}])


def test_image_read_reaches_parent_provider_as_exact_stock_media(plain, binary, tmp_path):
    png = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+/2ioAAAAASUVORK5CYII=")
    (Path(plain.workspace) / "pixel.png").write_bytes(png)
    call = ("call_image_read", "read", {"filePath": "pixel.png"})
    plain.user([{"type": "text", "text": "Execute the approved operation."}])
    plain.submit(response(plain, [call]))
    result = plain.result(call[0])
    assert result["__mvi_native_responses_output"] is True
    canonical = OpenAIAdapter().create_tool_result_message(call[0], call[1], result)
    context = ProviderRuntimeContext(session_state=None, agent_config={
        "provider_tools": {"openai": {"responses_tool_output_items": True}},
    })
    runtime = OpenAIResponsesRuntime(SimpleNamespace(provider_id="openai", runtime_id="openai_responses"))
    actual = runtime._convert_messages_to_input([canonical], context=context)
    plain.submit(response(plain))
    expected = direct_stock(binary, plain.workspace, tmp_path / "oracle", [call])
    assert actual == [{"type": "function_call_output", "call_id": call[0], "output": expected[call[0]]}]
    assert any(part["type"] == "input_image" for part in actual[0]["output"])


def test_auto_compaction_media_continuation_projects_exact_native_user_without_history_mutation(plain, tmp_path):
    import copy
    from breadboard_engine.compilation.context_builders.opencode_stock import project_inflight_current_user

    png = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+/2ioAAAAASUVORK5CYII=")
    artifact = put_workspace_artifact(plain.workspace, png, media_type="image/png")
    uri = "attachment://" + artifact.digest
    config = {"tools": {"native_semantics": "opencode_1_2_17"},
              "provider_tools": {"openai": {"responses_stateful": False}}}
    state = SessionState(workspace=plain.workspace, image="python-dev:latest", config=config)
    state.set_provider_metadata("attachment_capabilities", {uri: artifact.as_dict()})
    state._opencode_tool_session = plain
    context = ProviderRuntimeContext(state, config)
    media = {"type": "media", "kind": "image", "uri": uri, "mime": "image/png"}
    history = [{"role": "user", "content": [{"type": "text", "text": "Inspect this image."}, media]}]
    canonical = copy.deepcopy(history)
    plain.user(stock_user_parts(history[0]["content"], context=context))
    plain.submit(response(plain, text="Image inspected."))
    plain.compact("The image was inspected.", auto=True)
    native = plain.pending["body"]
    assert plain.is_compaction_continuation()
    projected = project_inflight_current_user(state, native, history)
    runtime = OpenAIResponsesRuntime(SimpleNamespace(provider_id="openai", runtime_id="openai_responses"))
    compiled = runtime.project_request_body(model=plain.model.split("/", 1)[1], messages=projected,
                                            tools=None, stream=False, context=context)
    source_user = next(item for item in reversed(native["input"]) if item.get("role") == "user")
    assert compiled["input"][-1] == source_user
    assert all(part["type"] == "input_text" for part in compiled["input"][-1]["content"])
    assert history == canonical
    plain.submit(response(plain, text="Continuation complete."))
