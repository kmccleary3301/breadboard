from pathlib import Path

import yaml

from breadboard_engine.compilation.v2_loader import load_agent_config


REPO_ROOT = Path(__file__).resolve().parents[1]
CURRENT_AGENT_CONFIGS = {
    "codex": REPO_ROOT / "agent_configs/codex_0-139-0_gpt55_e4_9-10-2026.yaml",
    "claude": REPO_ROOT / "agent_configs/claude_code_2-1-63_e4_9-10-2026.yaml",
    "opencode": REPO_ROOT / "agent_configs/opencode_1-2-17_e4_9-10-2026.yaml",
    "oh-my-opencode": REPO_ROOT / "agent_configs/oh_my_opencode_3-10-0_e4_9-10-2026.yaml",
}


def test_current_agent_configs_load_with_their_accepted_package_assets() -> None:
    loaded = {family: load_agent_config(str(path)) for family, path in CURRENT_AGENT_CONFIGS.items()}

    assert loaded["codex"]["prompts"]["packs"]["base"]["system"].endswith(
        "config/e4_targets/codex/0.139.0/prompts/gpt_5_codex_prompt.md"
    )
    assert loaded["claude"]["prompts"]["packs"]["base"]["system"].endswith(
        "config/e4_targets/claude_code/2.1.63/prompts/system-vendor-logged.prompt.md"
    )
    assert loaded["opencode"]["prompts"]["packs"]["base"]["system"].endswith(
        "config/e4_targets/opencode/1.2.17/prompts/system.md"
    )
    assert loaded["oh-my-opencode"]["prompts"]["packs"]["base"]["system"].endswith(
        "config/e4_targets/oh_my_opencode/3.10.0/prompts/system.md"
    )


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
