from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Mapping

import pytest

from breadboard.rl.harness.headless import HeadlessRunRequest
from breadboard.rl.harness.swe_bench_runner import (
    E4ControllerBinding,
    E4ProfileIdentity,
    E4_PROFILE_IDS,
    InstalledSweBenchRequest,
    OFFICIAL_SWE_BENCH_EVALUATOR,
    PINNED_SWE_BENCH_TASK,
    SweBenchEvaluatorBinding,
    SweBenchEvaluatorResult,
    SweBenchRunnerError,
    TrustedEvaluatorCommand,
    run_installed_swe_bench,
    select_e4_controller,
)
from breadboard.rl.harness.swe_bench_task import (
    EVALUATOR_COMMIT,
    EVALUATOR_VERSION,
    IMAGE_INDEX_DIGEST,
    IMAGE_LEAF_DIGEST,
    INSTANCE_ID,
)


_HEX = "a" * 64


def _reports(resolved: bool) -> tuple[dict[str, Any], dict[str, Any]]:
    aggregate = {
        "schema_version": 2,
        "total_instances": 1,
        "submitted_instances": 1,
        "completed_instances": 1,
        "resolved_instances": int(resolved),
        "unresolved_instances": int(not resolved),
        "submitted_ids": [INSTANCE_ID],
        "completed_ids": [INSTANCE_ID],
        "incomplete_ids": [],
        "resolved_ids": [INSTANCE_ID] if resolved else [],
        "unresolved_ids": [] if resolved else [INSTANCE_ID],
        "infra_failure_instances": 0,
        "infra_failure_ids": [],
        "ambiguous_failure_instances": 0,
        "ambiguous_failure_ids": [],
        "empty_patch_instances": 0,
        "empty_patch_ids": [],
        "error_instances": 0,
        "error_ids": [],
        "failure_reasons": {},
    }
    instance = {
        "instance_id": INSTANCE_ID,
        "patch_is_None": False,
        "patch_exists": True,
        "patch_successfully_applied": True,
        "resolved": resolved,
        "infra_failure": False,
    }
    return aggregate, instance


def _command(tmp_path: Path, patch_digest: str = f"sha256:{_HEX}") -> TrustedEvaluatorCommand:
    return TrustedEvaluatorCommand.create(
        dataset_path=str(tmp_path / "dataset.parquet"),
        predictions_path=str(tmp_path / "predictions.jsonl"),
        report_directory=str(tmp_path / "reports"),
        run_id="episode-1",
        patch_digest=patch_digest,
    )


def test_pins_profile_identity_and_command_are_generic_and_immutable(tmp_path: Path) -> None:
    assert E4_PROFILE_IDS == ("Pi", "OMP", "OpenHands", "mini-swe-agent")
    profile = E4ProfileIdentity("OpenHands")
    assert profile.identity_dict()["profile_id"] == "OpenHands"
    assert PINNED_SWE_BENCH_TASK.identity_dict()["image_leaf_digest"] == IMAGE_LEAF_DIGEST
    assert OFFICIAL_SWE_BENCH_EVALUATOR.version == EVALUATOR_VERSION
    assert OFFICIAL_SWE_BENCH_EVALUATOR.commit == EVALUATOR_COMMIT

    command = _command(tmp_path)
    assert command.argv[0:2] == ("swebench", "eval")
    assert command.evaluator == OFFICIAL_SWE_BENCH_EVALUATOR
    with pytest.raises((AttributeError, TypeError)):
        profile.profile_id = "Pi"  # type: ignore[misc]


def test_controller_selection_rejects_missing_or_mismatched_profiles() -> None:
    profile = E4ProfileIdentity("Pi")
    binding = E4ControllerBinding(profile, "controller.pi", f"sha256:{_HEX}")

    @dataclass
    class Controller:
        binding: E4ControllerBinding

        def produce_patch(self, task: Mapping[str, str], headless_result: Mapping[str, Any]) -> str:
            return ""

    controller = Controller(binding)
    assert select_e4_controller(profile, {"Pi": controller}) is controller
    with pytest.raises(SweBenchRunnerError, match="not installed"):
        select_e4_controller(profile, {})
    wrong = Controller(E4ControllerBinding(E4ProfileIdentity("OMP"), "controller.omp", f"sha256:{_HEX}"))
    with pytest.raises(SweBenchRunnerError, match="mismatch"):
        select_e4_controller(profile, {"Pi": wrong})


def test_evaluator_result_binds_command_reports_and_reward_without_raw_reports(tmp_path: Path) -> None:
    command = _command(tmp_path)
    aggregate, instance = _reports(True)
    result = SweBenchEvaluatorResult.from_reports(
        command,
        aggregate_report=aggregate,
        instance_report=instance,
    )
    assert result.reward == 1.0
    assert result.report_digest.startswith("sha256:")
    assert result.reward_digest.startswith("sha256:")
    assert "model_patch" not in result.public_projection()
    assert "test_patch" not in result.public_projection()
    with pytest.raises(SweBenchRunnerError, match="official report validation"):
        SweBenchEvaluatorResult(
            command=TrustedEvaluatorCommand.create(
                dataset_path=command.dataset_path,
                predictions_path=command.predictions_path,
                report_directory=command.report_directory,
                run_id="episode-2",
                patch_digest=command.patch_digest,
            ),
            aggregate_report_digest=result.aggregate_report_digest,
            instance_report_digest=result.instance_report_digest,
            reward=1.0,
        )


def test_rejects_unpinned_image_and_evaluator_inputs(tmp_path: Path) -> None:
    with pytest.raises(SweBenchRunnerError, match="image"):
        type(PINNED_SWE_BENCH_TASK)(image_digest=f"sha256:{'b' * 64}")
    with pytest.raises(SweBenchRunnerError, match="official"):
        SweBenchEvaluatorBinding(commit="0" * 40)
    with pytest.raises(SweBenchRunnerError, match="official command"):
        TrustedEvaluatorCommand(
            evaluator=OFFICIAL_SWE_BENCH_EVALUATOR,
            argv=("swebench", "eval", "--fallback"),
            dataset_path=str(tmp_path / "dataset.parquet"),
            predictions_path=str(tmp_path / "predictions.jsonl"),
            report_directory=str(tmp_path / "reports"),
            run_id="episode-1",
            patch_digest=f"sha256:{_HEX}",
        )


def test_installed_run_binds_prediction_evaluator_and_cleanup_digests(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    profile = E4ProfileIdentity("mini-swe-agent")
    binding = E4ControllerBinding(profile, "controller.mini", f"sha256:{_HEX}")

    @dataclass
    class Controller:
        binding: E4ControllerBinding

        def produce_patch(self, task: Mapping[str, str], headless_result: Mapping[str, Any]) -> str:
            assert task["instance_id"] == INSTANCE_ID
            return "diff --git a/a.py b/a.py\n"

    class Headless:
        async def run(self, request: HeadlessRunRequest) -> Mapping[str, Any]:
            return {
                "schema_version": "bb.rl.headless-result.v1",
                "episode_id": "episode-1",
                "terminal": {"status": "succeeded"},
                "sandbox_identity": {
                    "image_digest": IMAGE_LEAF_DIGEST,
                    "runtime_class": "hardened_docker",
                },
                "cleanup": {
                    "disposition": "released",
                    "receipt_digest": f"sha256:{'c' * 64}",
                    "receipt": {"state": "released"},
                },
                "cleanup_inventory": {
                    "active_lease_ids": [],
                    "orphan_resource_ids": [],
                    "leaked_artifact_ids": [],
                    "cleanup_errors": [],
                    "container_ids": [],
                    "process_ids": [],
                    "cgroup_paths": [],
                    "mount_paths": [],
                    "workspace_paths": [],
                    "artifact_paths": [],
                    "secret_lease_ids": [],
                    "broker_descriptor_count": 0,
                },
                "cleanup_inventory_digest": f"sha256:{'d' * 64}",
            }

    @dataclass
    class Evaluator:
        binding: SweBenchEvaluatorBinding

        def evaluate(self, command: TrustedEvaluatorCommand) -> SweBenchEvaluatorResult:
            aggregate, instance = _reports(False)
            return SweBenchEvaluatorResult.from_reports(
                command,
                aggregate_report=aggregate,
                instance_report=instance,
            )

    monkeypatch.setattr(
        "breadboard.rl.harness.swe_bench_runner.SweBenchTaskBinding.load_verified_row",
        lambda self, path: {"instance_id": INSTANCE_ID},
    )
    headless_request = HeadlessRunRequest.model_construct(
        target_id="target",
        target_overlay_id="overlay",
        resolve_request=SimpleNamespace(episode_id="episode-1"),
        workspace=SimpleNamespace(
            task_image_digest=IMAGE_LEAF_DIGEST,
            repository_snapshot_digest=None,
            base_commit=None,
        ),
    )
    request = InstalledSweBenchRequest(
        profile=profile,
        headless_request=headless_request,
        dataset_path=str(tmp_path / "dataset.parquet"),
        predictions_path=str(tmp_path / "predictions.jsonl"),
        report_directory=str(tmp_path / "reports"),
        run_id="episode-1",
    )
    receipt = __import__("asyncio").run(
        run_installed_swe_bench(
            request,
            controllers={"mini-swe-agent": Controller(binding)},
            headless=Headless(),
            evaluator=Evaluator(OFFICIAL_SWE_BENCH_EVALUATOR),
        )
    )
    assert receipt.reward == 0.0
    assert receipt.dataset_row_digest == PINNED_SWE_BENCH_TASK.row_digest
    assert receipt.image_index_digest == IMAGE_INDEX_DIGEST
    assert receipt.cleanup_digest.startswith("sha256:")
    public = receipt.to_public_dict()
    assert "model_patch" not in public
    assert "api_key" not in str(public)
