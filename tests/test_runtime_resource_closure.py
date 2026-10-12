"""Consumer-visible compaction advertisement agrees with runtime configuration."""
from __future__ import annotations

import json

from breadboard_engine.e4_targets import load_e4_target

def test_openclaw_new_revision_advertises_actual_compaction() -> None:
    package = load_e4_target("openclaw-r2@2026.9.4")
    settings = {item["name"]: item["value"] for item in package.descriptor["overlay"]["settings"]}
    assert settings["model.compaction"] is True
    native = json.loads(package.read_asset_text("native-config.json"))
    assert native["agent_exec"]["compaction"] is True
    baseline = load_e4_target("openclaw@2026.9.4")
    baseline_settings = {item["name"]: item["value"] for item in baseline.descriptor["overlay"]["settings"]}
    assert baseline_settings["model.compaction"] is False


def test_installed_wheel_lowers_compaction_targets_and_discovers_native_workspace(
    installed_wheel_python,
) -> None:
    installed_wheel_python("""
        from breadboard_engine.e4_targets import list_e4_target_ids, load_e4_target
        from breadboard.product.harness.targets import lower_e4_target
        from breadboard.rl.harness.runners.base import thaw_json

        revisions = {
            "pi-r3@0.73.1": ("pi-r2@0.73.1", ("read", "bash", "edit", "write")),
            "pi-r4@0.57.1": ("pi-r3@0.57.1", ("read", "bash", "edit", "write", "grep", "find", "ls")),
            "oh-my-pi-r3@16.2.13": ("oh-my-pi-r2@16.2.13", ("read", "bash", "edit", "write", "generate_image")),
            "oh-my-pi-r2@18.1.17": ("oh-my-pi@18.1.17", ("read", "bash", "edit", "write")),
            "openclaw-r2@2026.9.4": ("openclaw@2026.9.4", ("edit", "exec", "ls", "process", "read", "write")),
            "hermes-agent-r2@2026.9.11": ("hermes-agent@2026.9.11", ("patch", "read_file", "search_files", "skill_view", "skills_list", "terminal", "write_file")),
        }
        catalog = list_e4_target_ids()
        for target_id, (predecessor_id, tools) in revisions.items():
            assert target_id in catalog, target_id
            target = load_e4_target(target_id)
            lowered = lower_e4_target(target, {})
            baseline = lower_e4_target(load_e4_target(predecessor_id), {})
            assert lowered.target_id == target_id
            assert lowered.ordered_tool_names == tools, target_id
            assert thaw_json(lowered.tools) == thaw_json(baseline.tools), target_id
            assert lowered.system_prompt == baseline.system_prompt, target_id
            assert lowered.runtime_profile["compaction"] is True, target_id
            native = json.loads(target.read_asset_text("native-config.json"))
            if target_id.startswith(("pi-", "oh-my-pi-")):
                assert native["agent"]["compaction_enabled"] is True, target_id
            elif target_id.startswith("openclaw-"):
                assert native["agent_exec"]["compaction"] is True, target_id
            else:
                assert native["agent"]["compression"] is True, target_id

        # Resolve the real installed workspace without building or launching helpers.
        # workspace_target reads every manifest, lockfile, and Rust entrypoint;
        # a wheel missing any member fails through NativeHelperUnavailable.
        target_dir = codex_native.workspace_target()
        paths = [codex_native.native_binary(helper) for helper in codex_native.HELPERS]
        assert target_dir == codex_native.workspace_target()
        assert tuple(path.name for path in paths) == (
            "breadboard-codex-patch", "breadboard-codex-search", "breadboard-codex-image",
        )
        for path in paths:
            assert path == target_dir / "release" / path.name or path.is_relative_to(codex_native.SOURCE_ROOT / "bin")

        from types import SimpleNamespace
        import os
        from breadboard_engine.execution import codex_agents, codex_search
        from breadboard_engine.compilation.context_builders import codex
        from breadboard_engine.state.session_state import SessionState

        # Import-time model capabilities are consumed by Codex child routing.
        assert codex_agents.STOCK_MODEL_INFO["gpt-5.5"] == {
            "supports_image_detail_original": True,
            "truncation_policy": {"mode": "tokens", "limit": 10000},
            "web_search_tool_type": "text_and_image",
        }
        assert codex_agents.STOCK_MODEL_INFO["gpt-5.2"]["truncation_policy"]["mode"] == "bytes"

        # Exercise catalog parsing through deferred discovery, without building
        # or running a helper. Missing catalog assets must not masquerade as the
        # normal, closed unavailable-helper response.
        os.environ["BREADBOARD_CODEX_SEARCH_BINARY"] = str(Path.cwd() / "unbuilt-search")
        search_owner = SimpleNamespace(config={"tools": {"native_semantics": "codex_0_139"}})
        search = codex_search.execute_codex_search_tool(search_owner, {"query": "spawn agent"})
        assert search["error"] == codex_native.missing_helper_message("search")
        assert json.loads(search["__mvi_text_output"]) == {
            "status": "completed", "execution": "client", "tools": [],
        }

        # Actual startup rendering must replace the initial system prompt and
        # assemble permissions, skill-template instructions and environment.
        workspace = Path.cwd() / "workspace"
        workspace.mkdir()
        (workspace / ".git").mkdir()
        home = Path.cwd() / "home"
        home.mkdir()
        os.environ["HOME"] = str(home)
        os.environ["CODEX_HOME"] = str(home / ".codex")
        skill = workspace / ".agents/skills/wheel-proof/SKILL.md"
        skill.parent.mkdir(parents=True)
        skill.write_text("---\\nname: wheel-proof\\ndescription: Review installed package behavior.\\n---\\nCheck consumer outputs.\\n")
        config = {
            "prompts": {"initial_context": {}},
            "compaction": {"contextWindow": 272000},
        }
        state = SessionState(str(workspace), "wheel-proof", config)
        state.add_message({"role": "system", "content": "initial"})
        owner = SimpleNamespace(workspace=str(workspace), config=config)
        context = codex.build_context(
            owner, state,
            {"native_environment": {"cwd": str(workspace), "shell": "bash", "current_date": "2026-10-11"}},
            fresh=True, user_message={"role": "user", "content": "Inspect this workspace."},
        )
        assert state.messages[0]["content"].startswith("You are Codex, a coding agent based on GPT-5.")
        assert state.provider_messages[0]["content"] == state.messages[0]["content"]
        developer = "\\n".join(block["text"] for block in context[0]["content"])
        assert "Network access is enabled." in developer
        assert "{{network_access}}" not in developer
        assert "### How to use skills" in developer
        assert "- Discovery:" in developer
        assert "- wheel-proof: Review installed package behavior." in developer
        assert str(skill) in developer
        from xml.etree import ElementTree
        assert context[1]["role"] == "user"
        environment = ElementTree.fromstring(context[1]["content"][0]["text"])
        assert environment.findtext("cwd") == str(workspace)
        assert environment.findtext("shell") == "bash"
        assert environment.findtext("current_date") == "2026-10-11"
        assert environment.findtext("filesystem/workspace_roots/root") == str(workspace)
        assert environment.find("filesystem/permission_profile").get("type") == "disabled"
    """)
