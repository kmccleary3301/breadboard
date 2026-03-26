from __future__ import annotations

from agentic_coder_prototype.ctrees.historical_runner_bridge import (
    build_prompt_centric_historical_runner_manifest,
    load_historical_runner_result,
    render_prompt_centric_historical_runner_commands,
)


def test_ctree_historical_runner_manifest_references_frozen_runner_and_scenarios() -> None:
    manifest = build_prompt_centric_historical_runner_manifest()

    assert manifest["schema_version"] == "ctree_historical_runner_manifest_v1"
    assert manifest["condition_id"] == "ctrees.task_tree_toolbudget_v1"
    assert len(manifest["scenario_paths"]) == 3
    assert manifest["runner_path"].endswith("scripts/ctlab_e2e_runner.py")


def test_ctree_historical_runner_commands_render_expected_condition() -> None:
    manifest = build_prompt_centric_historical_runner_manifest(repeat=2)
    commands = render_prompt_centric_historical_runner_commands(manifest)

    assert len(commands) == 3
    assert all("--conditions ctrees.task_tree_toolbudget_v1" in command for command in commands)
    assert all("--repeat 2" in command for command in commands)


def test_ctree_historical_runner_result_loader_reads_frozen_plain_baseline() -> None:
    payload = load_historical_runner_result(
        "/shared_folders/querylake_server/ray_testing/ray_SCE/breadboard_repo_ctrees_restore_20260310/"
        "logs/phase8_stage2b_transition_event_recording_v2_canary_r2_clean_20260310/"
        "ct_fc0_hidden_constraint_probe_AE_guard_openai_h1_selection_focus_cg1_toolbudget_v1_stage2b_transition_event_recording_v2_canary-"
        "ctrees.task_tree_toolbudget_v1-20260311-013102-r1"
    )

    assert all(bool(value) for value in payload["exists"].values())
    assert payload["observed_semantics"]["context_engine_mode"] == "replace_messages"
    assert payload["observed_semantics"]["render_mode"] == "debug_v1"
    assert payload["selection_delta_stage"] == "FROZEN"
