import json
from types import SimpleNamespace

from breadboard_engine.compilation.context_builders.opencode_stock import project_inflight_current_user
from breadboard_engine.execution.opencode_tools import OpenCodeToolSession
from breadboard_engine.provider.contracts import ProviderRuntimeContext
from breadboard_engine.provider.runtimes.openai.responses import OpenAIResponsesRuntime
from tests.compaction.test_opencode_stock_tools import MODEL, binary, isolated_environment, response
from tests.compaction.test_opencode_stock_omo_groups import PLUGIN, PLUGIN_DIGEST, LSP_PIN, direct_omo


def test_real_user_prompt_submit_context_is_ephemeral_and_matches_stock(tmp_path, binary):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    settings = workspace / ".claude/settings.json"
    settings.parent.mkdir()
    settings.write_text(json.dumps({"hooks": {"UserPromptSubmit": [{"hooks": [
        {"type": "command", "command": "printf NATIVE_USER_HOOK_CONTEXT"},
    ]}]}}))
    session = OpenCodeToolSession(str(workspace), MODEL, binary=binary, plugin_root=PLUGIN,
                                 read_only_roots=[{"path": str(PLUGIN), "digest": PLUGIN_DIGEST}],
                                 lsp_root=LSP_PIN, timeout=120,
                                 stock_config={"agent": {"title": {"disable": True}}},
                                 environment=isolated_environment(tmp_path / "stock"))
    try:
        # Stock run.ts:340 prefixes piped stdin with one newline.
        request = session.user([{"type": "text", "text": "\nExecute this stock tool group."}])
        persisted = session.latest_user_sequence()
        assert "NATIVE_USER_HOOK_CONTEXT" not in json.dumps(persisted)
        messages = [{"role": "user", "content": persisted}]
        state = SimpleNamespace(_opencode_tool_session=session)
        projected = project_inflight_current_user(state, request, messages)
        assert "NATIVE_USER_HOOK_CONTEXT" in json.dumps(projected)
        assert messages == [{"role": "user", "content": persisted}]
        runtime = OpenAIResponsesRuntime(SimpleNamespace(provider_id="openai", runtime_id="openai_responses"))
        converted = runtime._convert_messages_to_input(projected, context=ProviderRuntimeContext(None, {}))
        session.submit(response(session))
        direct_requests = []
        direct_omo(binary, str(workspace), tmp_path / "oracle", [], captured_requests=direct_requests)
        direct_user = next(item for item in reversed(direct_requests[0]["json"]["input"]) if item.get("role") == "user")
        assert converted == [direct_user]
        session.user([{"type": "text", "text": "Follow up after the source hook."}])
        followup = [{"role": "user", "content": session.latest_user_sequence()}]
        session.submit(response(session, [("call_hook_followup", "bash", {
            "command": "printf hook-followup", "description": "Continue after the user hook",
        })]))
        continued = project_inflight_current_user(state, session.pending["body"], followup)
        assert "NATIVE_USER_HOOK_CONTEXT" not in json.dumps(continued)
        summary_request = session.compaction_prompt(auto=False)
        assert "NATIVE_USER_HOOK_CONTEXT" not in json.dumps(summary_request["input"])
        session.compact("The source hook remained ephemeral.", auto=False)
        assert "NATIVE_USER_HOOK_CONTEXT" not in json.dumps(session.api("GET", "/session/" + session.session_id + "/message"))
    finally:
        session.close()
