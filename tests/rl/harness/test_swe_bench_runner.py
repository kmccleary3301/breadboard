from __future__ import annotations

import hashlib
import json
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
    SubprocessOfficialEvaluator,
    SweBenchEvaluatorBinding,
    SweBenchEvaluatorResult,
    SweBenchRunnerError,
    TrustedEvaluatorCommand,
    _validate_headless_result,
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


def _command(
    tmp_path: Path, patch_digest: str = f"sha256:{_HEX}"
) -> TrustedEvaluatorCommand:
    return TrustedEvaluatorCommand.create(
        dataset_path=str(tmp_path / "dataset.parquet"),
        predictions_path=str(tmp_path / "predictions.jsonl"),
        report_directory=str(tmp_path / "reports"),
        run_id="episode-1",
        model_name="controller.mini",
        patch_digest=patch_digest,
    )


def test_pins_profile_identity_and_command_are_generic_and_immutable(
    tmp_path: Path,
) -> None:
    assert E4_PROFILE_IDS == ("Pi", "OMP", "OpenHands", "mini-swe-agent")
    profile = E4ProfileIdentity("OpenHands")
    assert profile.identity_dict()["profile_id"] == "OpenHands"
    assert (
        PINNED_SWE_BENCH_TASK.identity_dict()["image_leaf_digest"] == IMAGE_LEAF_DIGEST
    )
    assert OFFICIAL_SWE_BENCH_EVALUATOR.version == EVALUATOR_VERSION
    assert OFFICIAL_SWE_BENCH_EVALUATOR.commit == EVALUATOR_COMMIT

    command = _command(tmp_path)
    assert command.argv[0:2] == ("swebench", "eval")
    assert command.evaluator == OFFICIAL_SWE_BENCH_EVALUATOR
    with pytest.raises((AttributeError, TypeError)):
        profile.profile_id = "Pi"  # type: ignore[misc]


def test_controller_selection_rejects_missing_or_mismatched_profiles() -> None:
    profile = E4ProfileIdentity("Pi")
    binding = E4ControllerBinding(
        profile,
        "target",
        "controller.pi",
        f"sha256:{_HEX}",
    )

    @dataclass
    class Controller:
        binding: E4ControllerBinding

        def produce_patch(
            self, task: Mapping[str, str], headless_result: Mapping[str, Any]
        ) -> str:
            return ""

    controller = Controller(binding)
    assert select_e4_controller(profile, {"Pi": controller}) is controller
    with pytest.raises(SweBenchRunnerError, match="not installed"):
        select_e4_controller(profile, {})
    wrong = Controller(
        E4ControllerBinding(
            E4ProfileIdentity("OMP"),
            "target",
            "controller.omp",
            f"sha256:{_HEX}",
        )
    )
    with pytest.raises(SweBenchRunnerError, match="mismatch"):
        select_e4_controller(profile, {"Pi": wrong})


def test_evaluator_result_binds_command_reports_and_reward_without_raw_reports(
    tmp_path: Path,
) -> None:
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
                model_name=command.model_name,
                patch_digest=command.patch_digest,
            ),
            aggregate_report_digest=result.aggregate_report_digest,
            instance_report_digest=result.instance_report_digest,
            reward=1.0,
        )


def test_subprocess_evaluator_reads_pinned_official_report_locations(
    tmp_path: Path,
) -> None:
    aggregate, instance = _reports(True)
    work = tmp_path / "evaluator-work"
    work.mkdir()
    executable = tmp_path / "swebench"
    executable.write_text(
        "#!/usr/bin/env python3\n"
        "import json, os, sys\n"
        "def option(name): return sys.argv[sys.argv.index(name) + 1]\n"
        "run_id = option('--run-id')\n"
        "report_dir = option('--report-dir')\n"
        "model = 'controller.mini'\n"
        f"aggregate = {aggregate!r}\n"
        f"instance = {instance!r}\n"
        "os.makedirs(report_dir, exist_ok=True)\n"
        "with open(os.path.join(report_dir, f'{model}.{run_id}.json'), 'w') as f: "
        "json.dump(aggregate, f)\n"
        "instance_dir = os.path.join('logs', 'run_evaluation', run_id, model, "
        f"'{INSTANCE_ID}')\n"
        "os.makedirs(instance_dir, exist_ok=True)\n"
        "with open(os.path.join(instance_dir, 'report.json'), 'w') as f: "
        "json.dump(instance, f)\n",
        encoding="utf-8",
    )
    executable.chmod(0o700)
    adapter = SubprocessOfficialEvaluator(
        executable_path=str(executable),
        executable_digest=(
            "sha256:" + hashlib.sha256(executable.read_bytes()).hexdigest()
        ),
        installed_version=EVALUATOR_VERSION,
        source_commit=EVALUATOR_COMMIT,
        work_directory=str(work),
    )
    command = TrustedEvaluatorCommand.create(
        dataset_path=str(tmp_path / "dataset.parquet"),
        predictions_path=str(tmp_path / "predictions.jsonl"),
        report_directory=str(tmp_path / "reports"),
        run_id="episode-1",
        model_name="controller.mini",
        patch_digest=f"sha256:{_HEX}",
    )

    result = adapter.evaluate(command)

    assert result.reward == 1.0
    assert result.command == command
    assert adapter.identity_dict()["executable_digest"] == adapter.executable_digest


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
            model_name="controller.mini",
            patch_digest=f"sha256:{_HEX}",
        )


def test_installed_run_binds_prediction_evaluator_and_cleanup_digests(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    profile = E4ProfileIdentity("mini-swe-agent")
    binding = E4ControllerBinding(
        profile,
        "target",
        "controller.mini",
        f"sha256:{_HEX}",
    )

    @dataclass
    class Controller:
        binding: E4ControllerBinding

    class Headless:
        async def run(self, request: HeadlessRunRequest) -> Mapping[str, Any]:
            patch = b"diff --git a/a.py b/a.py\n"
            patch_digest = "sha256:" + hashlib.sha256(patch).hexdigest()
            assert request.patch_path is not None
            Path(request.patch_path).write_bytes(patch)
            inventory = {
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
                "broker_close_receipt_ref": None,
            }
            inventory_digest = (
                "sha256:"
                + hashlib.sha256(
                    json.dumps(
                        inventory,
                        ensure_ascii=True,
                        allow_nan=False,
                        separators=(",", ":"),
                        sort_keys=True,
                    ).encode()
                ).hexdigest()
            )
            return {
                "schema_version": "bb.rl.headless-result.v1",
                "episode_id": "episode-1",
                "terminal": {"status": "succeeded"},
                "sandbox_identity": {
                    "image_digest": IMAGE_LEAF_DIGEST,
                    "runtime_class": "hardened_docker",
                },
                "workspace_evidence": {"patch_digest": patch_digest},
                "patch": {
                    "requested": True,
                    "available": True,
                    "destination": request.patch_path,
                    "digest": patch_digest,
                    "size_bytes": len(patch),
                },
                "cleanup": {
                    "disposition": "released",
                    "receipt_digest": f"sha256:{'c' * 64}",
                    "receipt": {"state": "released"},
                },
                "cleanup_inventory": inventory,
                "cleanup_inventory_digest": inventory_digest,
            }

    monkeypatch.setattr(
        "breadboard.rl.harness.swe_bench_runner.SweBenchTaskBinding.load_verified_row",
        lambda self, path: {"instance_id": INSTANCE_ID},
    )
    evaluator_executable = tmp_path / "swebench"
    evaluator_executable.write_bytes(b"#!/bin/sh\nexit 0\n")
    evaluator_executable.chmod(0o700)
    evaluator = SubprocessOfficialEvaluator(
        executable_path=str(evaluator_executable),
        executable_digest=(
            "sha256:" + hashlib.sha256(evaluator_executable.read_bytes()).hexdigest()
        ),
        installed_version=EVALUATOR_VERSION,
        source_commit=EVALUATOR_COMMIT,
        work_directory=str(tmp_path),
    )

    def evaluate(
        _self: SubprocessOfficialEvaluator,
        command: TrustedEvaluatorCommand,
    ) -> SweBenchEvaluatorResult:
        aggregate, instance = _reports(False)
        return SweBenchEvaluatorResult.from_reports(
            command,
            aggregate_report=aggregate,
            instance_report=instance,
        )

    monkeypatch.setattr(SubprocessOfficialEvaluator, "evaluate", evaluate)
    headless_request = HeadlessRunRequest.model_construct(
        target_id="target",
        target_overlay_id="overlay",
        resolve_request=SimpleNamespace(episode_id="episode-1"),
        workspace=SimpleNamespace(
            task_image_digest=IMAGE_LEAF_DIGEST,
            repository_snapshot_digest=None,
            base_commit=None,
        ),
        patch_path=str(tmp_path / "workspace.patch"),
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
            evaluator=evaluator,
        )
    )
    assert receipt.reward == 0.0
    assert receipt.dataset_row_digest == PINNED_SWE_BENCH_TASK.row_digest
    assert receipt.image_index_digest == IMAGE_INDEX_DIGEST
    assert receipt.cleanup_digest.startswith("sha256:")
    public = receipt.to_public_dict()
    assert "model_patch" not in public
    assert "api_key" not in str(public)
    assert (
        public["evaluator_identity"]["executable_digest"] == evaluator.executable_digest
    )

    canonical = __import__("asyncio").run(Headless().run(headless_request))
    leaked_mount = json.loads(json.dumps(canonical))
    leaked_mount["cleanup_inventory"]["mount_paths"] = ["/live-mount"]
    leaked_mount["cleanup_inventory_digest"] = (
        "sha256:"
        + hashlib.sha256(
            json.dumps(
                leaked_mount["cleanup_inventory"],
                ensure_ascii=True,
                allow_nan=False,
                separators=(",", ":"),
                sort_keys=True,
            ).encode()
        ).hexdigest()
    )
    with pytest.raises(SweBenchRunnerError, match="not empty"):
        _validate_headless_result(leaked_mount, request)

    incomplete = json.loads(json.dumps(canonical))
    incomplete["cleanup_inventory"].pop("mount_paths")
    with pytest.raises(SweBenchRunnerError, match="schema is incomplete"):
        _validate_headless_result(incomplete, request)

    detached_patch = json.loads(json.dumps(canonical))
    detached_patch["patch"]["digest"] = f"sha256:{'e' * 64}"
    with pytest.raises(SweBenchRunnerError, match="canonical workspace patch"):
        _validate_headless_result(detached_patch, request)
