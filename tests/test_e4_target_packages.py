from pathlib import Path

import yaml

from agentic_coder_prototype.compilation.v2_loader import load_agent_config


REPO_ROOT = Path(__file__).resolve().parents[1]


def test_target_package_assets_exist() -> None:
    expected_files = [
        REPO_ROOT / "config/e4_targets/codex/0.107.0/gpt51mini_harness.yaml",
        REPO_ROOT / "config/e4_targets/codex/0.107.0/prompts/gpt_5_1_codex_prompt.md",
        REPO_ROOT / "config/e4_targets/claude_code/2.1.63/haiku45_harness.yaml",
        REPO_ROOT / "config/e4_targets/claude_code/2.1.63/prompts/system-vendor-logged.prompt.md",
        REPO_ROOT / "config/e4_targets/opencode/1.2.17/codexmini_responses_harness.yaml",
        REPO_ROOT / "config/e4_targets/opencode/1.2.17/prompts/system.md",
        REPO_ROOT / "config/e4_targets/oh_my_opencode/3.10.0/phase8_async_harness.yaml",
        REPO_ROOT / "config/e4_targets/oh_my_opencode/3.10.0/prompts/system.md",
        REPO_ROOT / "docs/conformance/E4_TARGET_PACKAGES.md",
        REPO_ROOT / "docs/conformance/e4_recalibration_evidence/codex_subagent_sync_20260306_v0110/replay_session.json",
        REPO_ROOT / "docs/conformance/e4_recalibration_evidence/codex_subagent_async_20260306_v0110/replay_session.json",
        REPO_ROOT / "docs/conformance/e4_recalibration_evidence/codex_subagent_sync_20260306_v0110/workspace/alpha.txt",
        REPO_ROOT / "docs/conformance/e4_recalibration_evidence/codex_subagent_async_20260306_v0110/workspace/gamma.txt",
    ]
    missing = [str(path) for path in expected_files if not path.exists()]
    assert not missing, f"missing target package files: {missing}"


def test_codex_public_dossier_carries_exercised_subagent_surface() -> None:
    path = REPO_ROOT / "agent_configs/codex_0-107-0_e4_3-6-2026.yaml"
    text = path.read_text(encoding="utf-8")
    assert "multi_agent:" in text
    assert "tool_packs:" in text
    assert "tool_bindings:" in text
    assert "terminal_sessions:" in text
    assert "exec_command" in text
    assert "write_stdin" in text
    assert "tool_name: spawn_agent" in text
    assert "docs/conformance/e4_recalibration_evidence/codex_subagent_sync_20260306_v0110/replay_session.json" in text
    assert "docs/conformance/e4_recalibration_evidence/codex_subagent_async_20260306_v0110/replay_session.json" in text


def test_latest_snapshot_configs_load_via_target_packages() -> None:
    snapshot_paths = [
        REPO_ROOT / "agent_configs/codex_0-107-0_e4_3-6-2026.yaml",
        REPO_ROOT / "agent_configs/claude_code_2-1-63_e4_3-6-2026.yaml",
        REPO_ROOT / "agent_configs/opencode_1-2-17_e4_3-6-2026.yaml",
        REPO_ROOT / "agent_configs/misc/opencode_e4_glob_grep_sentinel_replay__codex0_1070_claude2_1_63_opencode1_2_17_20260305.yaml",
        REPO_ROOT / "agent_configs/misc/opencode_e4_patch_todo_sentinel_replay__codex0_1070_claude2_1_63_opencode1_2_17_20260305.yaml",
        REPO_ROOT / "agent_configs/misc/opencode_e4_toolcall_repair_sentinel_replay__codex0_1070_claude2_1_63_opencode1_2_17_20260305.yaml",
        REPO_ROOT / "agent_configs/misc/opencode_e4_webfetch_sentinel_replay__codex0_1070_claude2_1_63_opencode1_2_17_20260305.yaml",
        REPO_ROOT / "agent_configs/misc/opencode_e4_oc_protofs_gpt5nano_replay__codex0_1070_claude2_1_63_opencode1_2_17_20260305.yaml",
        REPO_ROOT / "agent_configs/oh_my_opencode_3-10-0_e4_3-6-2026.yaml",
    ]

    loaded = [load_agent_config(str(path)) for path in snapshot_paths]

    assert loaded[0]["prompts"]["packs"]["base"]["system"].endswith(
        "config/e4_targets/codex/0.107.0/prompts/gpt_5_1_codex_prompt.md"
    )
    assert loaded[1]["prompts"]["packs"]["base"]["system"].endswith(
        "config/e4_targets/claude_code/2.1.63/prompts/system-vendor-logged.prompt.md"
    )
    for config in loaded[2:8]:
        assert config["prompts"]["packs"]["base"]["system"].endswith(
            "config/e4_targets/opencode/1.2.17/prompts/system.md"
        )
    assert loaded[8]["prompts"]["packs"]["base"]["system"].endswith(
        "config/e4_targets/oh_my_opencode/3.10.0/prompts/system.md"
    )


def test_top_level_e4_reference_configs_are_standalone_and_only_four_yaml_files_exist() -> None:
    top_level_yaml_paths = sorted((REPO_ROOT / "agent_configs").glob("*.yaml"))
    assert [path.name for path in top_level_yaml_paths] == [
        "claude_code_2-1-63_e4_3-6-2026.yaml",
        "codex_0-107-0_e4_3-6-2026.yaml",
        "oh_my_opencode_3-10-0_e4_3-6-2026.yaml",
        "opencode_1-2-17_e4_3-6-2026.yaml",
    ]

    for path in top_level_yaml_paths:
        payload = yaml.safe_load(path.read_text(encoding="utf-8"))
        assert isinstance(payload, dict), f"{path} must load as a mapping"
        assert "extends" not in payload, f"{path} must be standalone and not use extends"
