from pathlib import Path

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
        REPO_ROOT / "docs/conformance/E4_TARGET_PACKAGES.md",
    ]
    missing = [str(path) for path in expected_files if not path.exists()]
    assert not missing, f"missing target package files: {missing}"


def test_latest_snapshot_configs_load_via_target_packages() -> None:
    snapshot_paths = [
        REPO_ROOT / "agent_configs/codex_cli_gpt51mini_e4_live__codex0_1070_claude2_1_63_opencode1_2_17_20260305.yaml",
        REPO_ROOT / "agent_configs/claude_code_haiku45_e4_replay__codex0_1070_claude2_1_63_opencode1_2_17_20260305.yaml",
        REPO_ROOT / "agent_configs/opencode_e4_mvi_replay__codex0_1070_claude2_1_63_opencode1_2_17_20260305.yaml",
        REPO_ROOT / "agent_configs/opencode_e4_glob_grep_sentinel_replay__codex0_1070_claude2_1_63_opencode1_2_17_20260305.yaml",
        REPO_ROOT / "agent_configs/opencode_e4_patch_todo_sentinel_replay__codex0_1070_claude2_1_63_opencode1_2_17_20260305.yaml",
        REPO_ROOT / "agent_configs/opencode_e4_toolcall_repair_sentinel_replay__codex0_1070_claude2_1_63_opencode1_2_17_20260305.yaml",
        REPO_ROOT / "agent_configs/opencode_e4_webfetch_sentinel_replay__codex0_1070_claude2_1_63_opencode1_2_17_20260305.yaml",
        REPO_ROOT / "agent_configs/opencode_e4_oc_protofs_gpt5nano_replay__codex0_1070_claude2_1_63_opencode1_2_17_20260305.yaml",
    ]

    loaded = [load_agent_config(str(path)) for path in snapshot_paths]

    assert loaded[0]["prompts"]["packs"]["base"]["system"].endswith(
        "config/e4_targets/codex/0.107.0/prompts/gpt_5_1_codex_prompt.md"
    )
    assert loaded[1]["prompts"]["packs"]["base"]["system"].endswith(
        "config/e4_targets/claude_code/2.1.63/prompts/system-vendor-logged.prompt.md"
    )
    for config in loaded[2:]:
        assert config["prompts"]["packs"]["base"]["system"].endswith(
            "config/e4_targets/opencode/1.2.17/prompts/system.md"
        )
