from pathlib import Path

import yaml

from breadboard_engine.compilation.v2_loader import load_agent_config


REPO_ROOT = Path(__file__).resolve().parents[1]
CURRENT_AGENT_CONFIGS = {
    "codex": REPO_ROOT / "agent_configs/codex_0-139-0_gpt55_e4_10-9-2026.yaml",
    "claude": REPO_ROOT / "agent_configs/claude_code_2-1-63_e4_10-9-2026.yaml",
    "opencode": REPO_ROOT / "agent_configs/opencode_1-2-17_e4_10-9-2026.yaml",
    "oh-my-opencode": REPO_ROOT / "agent_configs/oh_my_opencode_3-10-0_e4_10-9-2026.yaml",
}
NATIVE_COMPACTION = {
    "codex": ("codex@0.139.0", 272000),
    "claude": ("claude_code@2.1.63", 200000),
    "opencode": ("opencode@1.2.17", 400000),
    "oh-my-opencode": ("oh-my-opencode@3.10.0", 400000),
}


def test_current_agent_configs_load_with_their_accepted_package_assets() -> None:
    loaded = {family: load_agent_config(str(path)) for family, path in CURRENT_AGENT_CONFIGS.items()}

    assert loaded["codex"]["prompts"]["packs"]["base"]["system"].endswith(
        "breadboard_engine/compilation/context_builders/assets/codex_0_139/gpt55_instructions.md"
    )
    assert loaded["claude"]["prompts"]["packs"]["base"]["system"].endswith(
        "config/e4_targets/claude_code/2.1.63/prompts/system-vendor-logged.prompt.md"
    )
    assert loaded["opencode"]["prompts"]["packs"]["base"]["system"].endswith(
        "implementations/system_prompts/opencode_1_2_17_codex_header.prompt.md"
    )
    assert loaded["oh-my-opencode"]["prompts"]["packs"]["base"]["system"].endswith(
        "implementations/system_prompts/omo_3_10_0_sisyphus.prompt.md"
    )


def test_current_agent_configs_are_their_previous_profiles_plus_native_compaction() -> None:
    for family, path in CURRENT_AGENT_CONFIGS.items():
        current = yaml.safe_load(path.read_text(encoding="utf-8"))
        previous_path = path.with_name(path.name.replace("_e4_10-9-2026", "_e4_9-10-2026"))
        previous = yaml.safe_load(previous_path.read_text(encoding="utf-8"))
        preset, window = NATIVE_COMPACTION[family]
        assert current.pop("compaction") == {"enabled": True, "preset": preset, "contextWindow": window}
        if family == "codex":
            # thread_id.rs:17-21; client.rs:375-412: UUIDv7/cache/client-local window.
            assert current["providers"].pop("session_uuid_version") == 7
            # session/session.rs:520-529,1049-1052 restores compacted history and generation.
            assert current.pop("resume") == {"restore_compaction_state": True}
            model = current["providers"]["models"][0]
            # models-manager/models.json: actual gpt-5.5, not unknown-model fallback.
            assert model.pop("tool_output_truncation") == {"mode": "tokens", "limit": 10000}
            assert model.pop("supports_image_detail_original") is True
            assert model.pop("web_search_tool_type") == "text_and_image"
            assert model["params"] == {}
            model["params"] = previous["providers"]["models"][0]["params"]
            # client.rs:755-815; models-manager/models.json:gpt-5.5 capabilities.
            assert current["provider_tools"].pop("openai") == {
                "responses_stateful": False, "responses_message_type": True,
                "responses_message_phase": True, "responses_namespaces": True,
                "responses_hosted_outputs": ["web_search_call"],
                "responses_custom_tools": ["apply_patch"], "responses_tool_search": True,
                "responses_deferred_tools": ["multi_agent_v1"], "include_reasoning": True,
                "responses_preserve_tool_call_items": True,
                # protocol/src/models.rs:FunctionCallOutputContentItem native image/text output.
                "responses_tool_output_items": True,
                "request_params": {"parallel_tool_calls": True, "tool_choice": "auto",
                                   "reasoning": {"effort": "medium"}, "text": {"verbosity": "low"},
                                   "store": False, "include": ["reasoning.encrypted_content"]},
                "session_params": {"prompt_cache_key": "{session_id}"},
                "client_metadata_session": {"x-codex-installation-id": "{installation_id}",
                                            "x-codex-window-id": "{session_id}:{compaction_boundary_count}"},
            }
            # session/mod.rs:2752-2977: native startup context; source template/pragmatic personality.
            assert current["prompts"].pop("initial_context") == {
                "module": "breadboard_engine.compilation.context_builders.codex", "callable": "build_context"}
            for field, asset in current["prompts"]["packs"]["base"].items():
                assert asset == "../breadboard_engine/compilation/context_builders/assets/codex_0_139/gpt55_instructions.md", field
            current["prompts"]["packs"] = previous["prompts"]["packs"]
            assert current["prompts"]["tool_prompt_mode"] == "none"
            current["prompts"]["tool_prompt_mode"] = previous["prompts"]["tool_prompt_mode"]
            assert current["provider_tools"]["suppress_prompts"] is True
            current["provider_tools"]["suppress_prompts"] = previous["provider_tools"]["suppress_prompts"]
            # tools/spec.rs: native unified execution, custom patch, goal/search/deferred agents.
            assert current["tools"].pop("native_semantics") == "codex_0_139"
            assert current["tools"]["mark_task_complete"] is False
            current["tools"]["mark_task_complete"] = previous["tools"]["mark_task_complete"]
            assert current["tools"]["registry"] == {
                "paths": ["../implementations/tools/defs_codex_139"],
                "include": ["exec_command", "write_stdin", "update_plan", "request_user_input",
                            "apply_patch", "view_image", "get_goal", "create_goal", "update_goal",
                            "tool_search", "web_search", "multi_agent_v1"]}
            current["tools"]["registry"] = previous["tools"]["registry"]
            # session/turn.rs: a final answer without a tool ends the source turn.
            assert current["completion"]["natural_finish"]["no_tool_turns_threshold"] == 1
            current["completion"]["natural_finish"]["no_tool_turns_threshold"] = previous["completion"]["natural_finish"]["no_tool_turns_threshold"]
            assert current["concurrency"]["at_most_one_of"] == ["exec_command"]
            current["concurrency"]["at_most_one_of"] = previous["concurrency"]["at_most_one_of"]
        if family in {"opencode", "oh-my-opencode"}:
            # Native small model: provider.ts:1242-1295. Plugin category/agent
            # candidates: model-requirements.ts:15-149. Limits are the pinned
            # initialized /provider inventory; transform.ts caps output at32k.
            expected_models = [
                {"id": "openai/gpt-5-nano", "max_input_tokens": 272000,
                 "max_output_tokens": 32000, "adapter": "openai_responses", "params": {}}
            ]
            if family == "oh-my-opencode":
                expected_models = [
                    {"id": "openai/gpt-5.3-codex", "max_input_tokens": 272000,
                     "max_output_tokens": 32000, "adapter": "openai_responses", "params": {}},
                    {"id": "openai/gpt-5.2", "max_input_tokens": 272000,
                     "max_output_tokens": 32000, "adapter": "openai_responses", "params": {}},
                    *expected_models,
                    {"id": "opencode/minimax-m2.5-free", "max_output_tokens": 32000,
                     "adapter": "anthropic", "params": {}},
                    {"id": "opencode/gpt-5-nano", "max_input_tokens": 272000,
                     "max_output_tokens": 32000, "adapter": "openai_responses", "params": {}},
                    {"id": "opencode/big-pickle", "max_output_tokens": 32000,
                     "adapter": "anthropic", "params": {}},
                ]
            assert current["providers"]["models"][1:] == expected_models
            del current["providers"]["models"][1:]
            # system.ts:19-53 chooses the GPT header/environment; the plugin
            # factory is agents/sisyphus.ts:550-615. Native runtime generates
            # dynamic systems; these tracked assets are source snapshots.
            prompt_asset = ("opencode_1_2_17_codex_header.prompt.md" if family == "opencode"
                            else "omo_3_10_0_sisyphus.prompt.md")
            assert current["prompts"]["packs"]["base"]["system"] == "../implementations/system_prompts/" + prompt_asset
            current["prompts"]["packs"]["base"]["system"] = previous["prompts"]["packs"]["base"]["system"]
            assert current["prompts"]["environment"].pop("include_tree") is False
            assert current["prompts"]["environment"].pop("include_model_identity") is True
            assert current["prompts"]["environment"].pop("files_tag") == "directories"
            # chat.message hooks run once in source. Summary projection reads
            # instruction/contributor only: compaction.ts:202-220 and plugin
            # compaction-context-injector/hook.ts:59-67.
            for field, callback in (("initial_context", "build_context"),
                                    ("summary_projection", "build_summary_projection")):
                assert current["prompts"].pop(field) == {
                    "module": "breadboard_engine.compilation.context_builders.opencode_stock",
                    "callable": callback}
            # SDK Responses string developer/full-history/grouped native calls;
            # transform.ts:688-695,720-722,759-763. Episode output cap is32k.
            assert current["provider_tools"].pop("openai") == {
                "responses_stateful": False, "responses_developer_content": "string",
                "responses_use_developer_role": True, "tool_choice": "auto",
                "responses_group_tool_results": True, "responses_max_output_tokens_from_context": True,
                "responses_preserve_tool_call_items": True, "responses_tool_output_items": True,
                "store": False, "reasoning": {"effort": "medium", "summary": "auto"},
                "session_params": {"prompt_cache_key": "{session_id}"}}
            # Stock processor persists assistant text without BB's suffix.
            assert current["completion"].pop("append_response_marker") is False
            # Stock prompt.ts:716-723 preserves the interactive session after
            # returning an errored assistant, allowing the next real message.
            assert current.pop("session") == {"failed_turn_continues": True}
            # registry.ts:103-124,147-151 and plugin tools/index.ts. All tools
            # execute in the unmodified source runtime, including its hooks.
            semantics = "opencode_1_2_17" if family == "opencode" else "oh_my_opencode_3_10_0"
            assert current["tools"].pop("native_semantics") == semantics
            assert current["tools"].pop("group_native_calls_by_turn") is True
            # prompt.ts:238-282 SessionPrompt.loop shares only same-session requests;
            # plugin manager.ts:1359-1367 starts an independent parent prompt.
            assert current["tools"].pop("native_concurrent_prompts") is True
            # execution/opencode/lifecycle.py launches the local source sidecar with
            # cwd=self.workspace; remote admission must reject this placement.
            assert current["tools"].pop("native_local_only") is True
            # Native interactive_bash/tmux needs the explicit restricted PTY capability.
            assert current["tools"].pop("pty") is True
            registry = current["tools"]["registry"]
            assert registry["paths"] == ["../implementations/tools/" + (
                "defs_opencode_1_2_17" if family == "opencode" else "defs_omo_3_10_0")]
            expected_tools = ["question", "bash", "read", "glob", "grep", "task", "webfetch",
                              "todowrite", "skill", "apply_patch"]
            if family == "oh-my-opencode":
                expected_tools += ["lsp_goto_definition", "lsp_find_references", "lsp_symbols",
                    "lsp_diagnostics", "lsp_prepare_rename", "lsp_rename", "ast_grep_search",
                    "ast_grep_replace", "session_list", "session_read", "session_search",
                    "session_info", "background_output", "background_cancel", "look_at",
                    "skill_mcp", "interactive_bash", "websearch_web_search_exa",
                    "grep_app_searchGitHub", "context7_resolve-library-id", "context7_query-docs"]
            assert registry["include"] == expected_tools
            current["tools"]["registry"] = previous["tools"]["registry"]
            # Source executable/bundle dependency is digest-pinned and mounted
            # read-only. No install path can implicitly grant writable access.
            pins = [{"path": "~/.cache/bb-compaction-e4/pkgs/opencode-darwin-arm64@1.2.17/package",
                     "digest": "10c0bd72c60867d8ecdd37ba70a812076e1e8e2a9e5a3971ea4a728ebf4e836d"}]
            if family == "oh-my-opencode":
                plugin_path = "~/.cache/bb-compaction-e4/pkgs/oh-my-opencode@3.10.0-5137df72"
                assert current["tools"].pop("stock_plugin_root") == plugin_path
                pins.append({"path": plugin_path,
                             "digest": "ec9c7f47c21ff078eb81231e0c0af22e8aea16b626e707f5e6d2fc128ae8e2dc"})
            lsp_pin = {"path": "~/.cache/bb-compaction-e4/pkgs/opencode-lsp-1.2.17",
                       "digest": "2b27ba95b4a5f4968141f3c353f0e4abd8e1e9be1572b8e0d99292604ffa7478"}
            assert current["tools"].pop("stock_lsp_root") == lsp_pin
            pins.append(lsp_pin)
            assert current["tools"].pop("stock_read_only_roots") == pins
            # Stock project discovery reads the enclosing git worktree without
            # extending write authority outside the configured workspace.
            assert current["tools"].pop("stock_worktree_read_only") is True
            assert "stock_session_imports" not in current["tools"]
            if family == "opencode":
                # agent.ts:77-79,266-280 defaults to build. Native source owns
                # its LSP, not the legacy local BB wrapper.
                assert current["loop"] == {"sequence": [{"mode": "build"}]}
                current["loop"] = previous["loop"]
                assert current["modes"] == [{"name": "build", "prompt": "", "tools_enabled": ["*"], "tools_disabled": []}]
                current["modes"] = previous["modes"]
                assert current["enhanced_tools"]["lsp_integration"]["enabled"] is False
                current["enhanced_tools"]["lsp_integration"]["enabled"] = previous["enhanced_tools"]["lsp_integration"]["enabled"]
        for model in current["providers"]["models"]:
            model.pop("max_input_tokens", None)
            model.pop("max_output_tokens", None)
        if family == "claude":
            # Pinned Claude Code 2.1.63 cli.js:6110 (D6): enabled thinking
            # omits temperature and clamps its budget to max_tokens - 1.
            # Ordinary stock Messages also omit tool_choice (claude_lane's
            # stock requests.jsonl, first request); no other keys may differ.
            previous_params = previous["providers"]["models"][0]["params"]
            assert previous_params.pop("temperature") == 0.1
            assert "temperature" not in current["providers"]["models"][0]["params"]
            previous_anthropic = previous["provider_tools"]["anthropic"]
            current_anthropic = current["provider_tools"]["anthropic"]
            assert previous_anthropic.pop("temperature") == 0.1
            assert previous_anthropic.pop("tool_choice") == "auto"
            assert "temperature" not in current_anthropic
            assert "tool_choice" not in current_anthropic
            assert current_anthropic.pop("thinking") == {"type": "enabled", "budget_tokens": 31999}
            # Live pinned SDK context and automatic restoration (cli.js:6110,
            # 2207-2211); each BB response continues the same stock print turn.
            assert current_anthropic.pop("request_layout") == {
                "module": "breadboard_engine.compilation.context_builders.claude_code",
                "callable": "build_request", "response_callable": "accept_response"}
            assert current_anthropic.pop("native_tool_result_replay") is True
            assert current_anthropic.pop("preserve_tool_names") == ["TodoWrite"]
            assert current_anthropic["system_reminders"]["enabled"] is False
            current_anthropic["system_reminders"]["enabled"] = previous_anthropic["system_reminders"]["enabled"]
            assert current["provider_tools"]["suppress_prompts"] is True
            current["provider_tools"]["suppress_prompts"] = previous["provider_tools"]["suppress_prompts"]
            for field, callback in (("initial_context", "build_context"),
                                    ("summary_projection", "build_summary_projection")):
                assert current["prompts"].pop(field) == {
                    "module": "breadboard_engine.compilation.context_builders.claude_code",
                    "callable": callback}
            assert current["prompts"]["tool_prompt_mode"] == "none"
            current["prompts"]["tool_prompt_mode"] = previous["prompts"]["tool_prompt_mode"]
            assert current["tools"].pop("native_semantics") == "claude_code_2_1_63"
            # claude_tools.py::get_claude_tool_session requires a local process
            # workspace; ClaudeToolSession launches the stock CLI with cwd=workspace.
            assert current["tools"].pop("native_local_only") is True
            assert current["tools"].pop("group_native_calls_by_turn") is True
            assert current["tools"]["registry"]["paths"] == ["../implementations/tools/defs_claude_code_2_1_63"]
            current["tools"]["registry"]["paths"] = previous["tools"]["registry"]["paths"]
        assert current == previous, family


def test_current_codex_profile_resolves_its_prompt_without_ignored_source_trees() -> None:
    config_path = CURRENT_AGENT_CONFIGS["codex"]
    config = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    prompt_ref = config["prompts"]["packs"]["base"]["system"]

    assert "industry_coder_refs" not in prompt_ref
    assert (config_path.parent / prompt_ref).resolve().is_file()


def test_current_pi_family_entries_preserve_the_target_config_contract() -> None:
    expected = {
        "agent_configs/oh_my_pi_16-2-13_e4_9-10-2026.yaml": "oh-my-pi@16.2.13",
        "agent_configs/pi_0-57-1_e4_9-10-2026.yaml": "pi@0.57.1",
    }

    for relative_path, target_id in expected.items():
        config_path = REPO_ROOT / relative_path
        config = yaml.safe_load(config_path.read_text(encoding="utf-8"))
        assert config["schema_version"] == "bb.e4.target_config.v1"
        assert config["target_id"] == target_id
        assert (config_path.parent / config["prompt"]["asset"]).resolve().is_file()
        assert (config_path.parent / config["tools"]["surface_asset"]).resolve().is_file()


def test_stock_lsp_root_is_optional_but_its_pin_is_closed_and_complete() -> None:
    import json
    from jsonschema import Draft202012Validator
    document = json.loads((REPO_ROOT / "contracts/kernel/schemas/bb.agent_config_surface.v2.schema.json").read_text())
    tools = document["properties"]["tools"]
    validator = Draft202012Validator(tools["properties"]["stock_lsp_root"])
    pin = {"path": "/pinned/source/lsp", "digest": "a" * 64}
    assert validator.is_valid(pin)
    assert "stock_lsp_root" not in tools.get("required", [])
    assert "default" not in tools["properties"]["stock_lsp_root"]
    for invalid in ({}, {"path": pin["path"]}, {"digest": pin["digest"]},
                    {**pin, "path": ""}, {**pin, "digest": "a" * 63},
                    {**pin, "digest": "A" * 64}, {**pin, "writable": True},
                    None, pin["path"]):
        assert not validator.is_valid(invalid), invalid


def test_opencode_profiles_replay_a_stock_image_read() -> None:
    from types import SimpleNamespace
    from breadboard_engine.provider.adapters import OpenAIAdapter
    from breadboard_engine.provider.contract_requests import normalize_request_messages
    from breadboard_engine.provider.runtime import OpenAIResponsesRuntime, ProviderRuntimeContext
    from breadboard_engine.state.session_state import SessionState

    output = [{"type": "input_image", "image_url": "data:image/png;base64,AA=="}]
    message = OpenAIAdapter().create_tool_result_message(
        "read-1", "read", {"__mvi_native_responses_output": True, "__mvi_content_output": output})
    runtime = OpenAIResponsesRuntime(SimpleNamespace(provider_id="openai", runtime_id="openai_responses"))
    for family in ("opencode", "oh-my-opencode"):
        config = load_agent_config(str(CURRENT_AGENT_CONFIGS[family]))
        context = ProviderRuntimeContext(SessionState("ws", "image", config), config)
        assert runtime._convert_messages_to_input(normalize_request_messages([message]), context=context) == [
            {"type": "function_call_output", "call_id": "read-1", "output": output}], family
