from __future__ import annotations

import asyncio
import hashlib
import json
from pathlib import Path
from typing import Any, Mapping

import pytest

from breadboard.rl.harness import contracts as c
from breadboard.rl.harness.headless import (
    HeadlessProviderInput,
    HeadlessProviderRouteAuthority,
    HeadlessRunRequest,
    HeadlessWorkspaceInput,
)
from breadboard.rl.harness.policy_provider import E4TargetPolicyProjection
from breadboard.rl.harness.qualification import (
    materialize_production_composition_fixture,
)
from breadboard.rl.harness.swe_bench_runner import (
    E4ProfileIdentity,
    E4_PROFILE_IDS,
    InstalledHeadlessInvocation,
    InstalledSweBenchRequest,
    OFFICIAL_EVALUATOR_DOCKER_DIGEST,
    OFFICIAL_EVALUATOR_ENVIRONMENT_DIGEST,
    OFFICIAL_EVALUATOR_PYTHON_DIGEST,
    OFFICIAL_SWE_BENCH_EVALUATOR,
    OfficialEvaluatorOutcome,
    PINNED_SWE_BENCH_TASK,
    SubprocessOfficialEvaluator,
    SweBenchEvaluatorBinding,
    SweBenchEvaluatorResult,
    SweBenchRunnerError,
    TrustedEvaluatorCommand,
    _canonical_digest,
    _controller_identity,
    _controller_model_name,
    _require_root_private_work_directory,
    _copy_verified_dataset,
    _validate_headless_result,
    _write_digest_bound_dataset,
    run_installed_swe_bench,
)
from breadboard.rl.harness.swe_bench_task import (
    BASE_COMMIT,
    EVALUATOR_COMMIT,
    EVALUATOR_VERSION,
    IMAGE_INDEX_DIGEST,
    IMAGE_LEAF_DIGEST,
    INSTANCE_ID,
    IMAGE_REFERENCE,
)


_HEX = "a" * 64
_SNAPSHOT = f"sha256:{'b' * 64}"


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
    return aggregate, {INSTANCE_ID: instance}


def _command(
    tmp_path: Path,
    patch_digest: str = f"sha256:{_HEX}",
    *,
    model_name: str = "controller.mini",
) -> TrustedEvaluatorCommand:
    return TrustedEvaluatorCommand.create(
        dataset_path=str(tmp_path / "dataset.parquet"),
        predictions_path=str(tmp_path / "predictions.jsonl"),
        report_directory=str(tmp_path / "reports"),
        run_id="episode-1",
        model_name=model_name,
        patch_digest=patch_digest,
    )


def _target(
    target_id: str = "mini-swe-agent@1.0.0",
) -> E4TargetPolicyProjection:
    return E4TargetPolicyProjection(
        target_id=target_id,
        overlay_id="fixture-headless.v1",
        descriptor_digest=f"sha256:{'1' * 64}",
        execution_config_digest=f"sha256:{'2' * 64}",
        overlay_digest=f"sha256:{'3' * 64}",
        rendered_prompt_digest=f"sha256:{'4' * 64}",
        system_prompt="fixture",
        ordered_tool_names=(),
        chat_tools=(),
    )


def _invocation(
    tmp_path: Path,
    *,
    composition: bytes = b'{"schema_version":"fixture-composition.v1"}',
) -> InstalledHeadlessInvocation:
    composition_path = tmp_path / "composition-ref.json"
    composition_path.write_bytes(composition)
    route = HeadlessProviderRouteAuthority(
        model="fixture-model",
        authority_model_id="fixture-authority",
        base_url="http://127.0.0.1:12345/v1",
        policy_observation_digest=f"sha256:{'5' * 64}",
    )
    return InstalledHeadlessInvocation(
        composition_ref_path=str(composition_path),
        secret_files={"composition-secret": str(tmp_path / "composition.secret")},
        provider_credentials={"policy-callback": str(tmp_path / "provider.secret")},
        provider_routes={"policy-callback": route},
        repository_base_commits={_SNAPSHOT: BASE_COMMIT},
    )


def _headless_request(tmp_path: Path) -> HeadlessRunRequest:
    provider = HeadlessProviderInput(
        model="fixture-model",
        authority_model_id="fixture-authority",
        credential_handle="policy-callback",
        context_window=4096,
        max_output_tokens=1024,
        timeout_seconds=30,
    )
    schema_fixture = materialize_production_composition_fixture(
        tmp_path / "headless-schema-fixture"
    )
    resolution_payload = dict(schema_fixture.create_body["resolution"])
    resolution_payload["episode_id"] = "episode-1"
    return HeadlessRunRequest(
        target_id="mini-swe-agent@1.0.0",
        target_overlay_id="fixture-headless.v1",
        target_dynamic_fields={"fixture": "value"},
        resolve_request=c.ResolveEpisodeRequest.model_validate(resolution_payload),
        provider=provider,
        workspace=HeadlessWorkspaceInput(
            task_image_digest=IMAGE_LEAF_DIGEST,
            repository_snapshot_digest=_SNAPSHOT,
            base_commit=BASE_COMMIT,
        ),
        prompt=PINNED_SWE_BENCH_TASK.model_visible_task()["problem_statement"],
        tool_allowlist=("shell",),
        expected_resources=c.ResourceLimits(
            cpu_millis=1_000,
            memory_bytes=1_000_000,
            pids=32,
            storage_bytes=1_000_000,
            open_files=128,
            wall_time_ms=60_000,
        ),
        expected_limits=c.ExecutionLimits(
            max_turns=4,
            action_timeout_ms=9_000,
            observation_bytes=20_000,
            response_bytes=100_000,
            artifact_bytes_each=10_000,
            artifact_bytes_total=20_000,
            transcript_bytes=100_000,
            setup_timeout_ms=5_000,
            verifier_timeout_ms=17_000,
        ),
        expected_sandbox=c.SandboxGrant(
            runtime_id="sandbox",
            runtime_class=c.RuntimeClass.HARDENED_DOCKER,
            driver_implementation_digest=f"sha256:{'1' * 64}",
            runtime_binary_digest=f"sha256:{'2' * 64}",
            security_policy_digest=f"sha256:{'3' * 64}",
            image_digest=IMAGE_LEAF_DIGEST,
            network_policy_digest=f"sha256:{'4' * 64}",
            egress_route_ids=(),
            mounts=(),
        ),
        result_path=str(tmp_path / "result.json"),
        event_log_path=str(tmp_path / "events.jsonl"),
        patch_path=str(tmp_path / "workspace.patch"),
    )


def _request(tmp_path: Path) -> InstalledSweBenchRequest:
    return InstalledSweBenchRequest(
        profile=E4ProfileIdentity("mini-swe-agent"),
        headless_request=_headless_request(tmp_path),
        headless_invocation=_invocation(tmp_path),
        dataset_path=str(tmp_path / "dataset.parquet"),
        run_id="episode-1",
    )


def _headless_result(
    request: InstalledSweBenchRequest,
    target: E4TargetPolicyProjection,
) -> dict[str, Any]:
    patch = b"diff --git a/a.py b/a.py\n"
    patch_digest = "sha256:" + hashlib.sha256(patch).hexdigest()
    assert request.headless_request.patch_path is not None
    Path(request.headless_request.patch_path).write_bytes(patch)
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
    cleanup_receipt = {
        "schema_version": "bb.rl.test-cleanup-receipt.v1",
        "state": "released",
    }
    config_identity = {
        "schema_version": "bb.rl.test-headless-config.v1",
        "episode_id": "episode-1",
    }
    return {
        "schema_version": "bb.rl.headless-result.v1",
        "episode_id": "episode-1",
        "config_identity": config_identity,
        "config_digest": _canonical_digest(config_identity),
        "engine_identity": {
            "distribution": "breadboard-harness-cli",
            "version": "test",
            "headless_module_digest": f"sha256:{'6' * 64}",
            "policy_provider_module_digest": f"sha256:{'7' * 64}",
        },
        "provider_input_identity": request.headless_request.provider.identity_dict(),
        "target_identity": target.identity_dict(),
        "workspace_input": request.headless_request.workspace.model_dump(mode="json"),
        "terminal": {"status": "succeeded"},
        "sandbox_identity": {
            "image_digest": IMAGE_LEAF_DIGEST,
            "runtime_class": "hardened_docker",
        },
        "workspace_evidence": {
            "patch_digest": patch_digest,
            "patch_base_commit": BASE_COMMIT,
            "patch_git_executable_digest": f"sha256:{'8' * 64}",
            "patch_snapshot_root_digest": f"sha256:{'9' * 64}",
        },
        "patch": {
            "requested": True,
            "available": True,
            "destination": request.headless_request.patch_path,
            "digest": patch_digest,
            "size_bytes": len(patch),
        },
        "cleanup": {
            "disposition": "released",
            "receipt_digest": _canonical_digest(cleanup_receipt),
            "receipt": cleanup_receipt,
        },
        "cleanup_inventory": inventory,
        "cleanup_inventory_digest": _canonical_digest(inventory),
    }


def _evaluator(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> SubprocessOfficialEvaluator:
    measurement = {
        "environment_digest": OFFICIAL_EVALUATOR_ENVIRONMENT_DIGEST,
        "python_path": "/usr/bin/python3",
        "python_digest": OFFICIAL_EVALUATOR_PYTHON_DIGEST,
        "docker_path": "/usr/bin/docker",
        "docker_digest": OFFICIAL_EVALUATOR_DOCKER_DIGEST,
        "file_count": 1,
        "total_bytes": 1,
    }
    monkeypatch.setattr(
        "breadboard.rl.harness.swe_bench_runner.measure_official_evaluator_environment",
        lambda _root: measurement,
    )
    monkeypatch.setattr(
        "breadboard.rl.harness.swe_bench_runner._require_root_private_work_directory",
        lambda _path: None,
    )
    work = tmp_path / "evaluator-work"
    work.mkdir(mode=0o700)
    return SubprocessOfficialEvaluator(
        environment_root=str(tmp_path / "evaluator-environment"),
        work_directory=str(work),
    )


def test_pins_profile_identity_command_and_controller_are_generic(
    tmp_path: Path,
) -> None:
    assert E4_PROFILE_IDS == ("Pi", "OMP", "OpenHands", "mini-swe-agent")
    profile = E4ProfileIdentity("OpenHands")
    assert profile.identity_dict()["profile_id"] == "OpenHands"
    assert PINNED_SWE_BENCH_TASK.identity_dict()["image_leaf_digest"] == IMAGE_LEAF_DIGEST
    assert OFFICIAL_SWE_BENCH_EVALUATOR.version == EVALUATOR_VERSION
    assert OFFICIAL_SWE_BENCH_EVALUATOR.commit == EVALUATOR_COMMIT
    command = _command(tmp_path)
    assert command.argv[0:2] == ("swebench", "eval")
    assert command.evaluator == OFFICIAL_SWE_BENCH_EVALUATOR
    controller = _controller_identity(profile, _target("openhands@1.0.0"))
    assert _controller_model_name(controller).startswith("breadboard-e4-")
    assert "/" not in _controller_model_name(controller)
    with pytest.raises((AttributeError, TypeError)):
        profile.profile_id = "Pi"  # type: ignore[misc]
    with pytest.raises(SweBenchRunnerError, match="does not match"):
        _controller_identity(profile, _target("pi@0.57.1"))


def test_evaluator_requires_root_owned_work_custody(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    work = tmp_path / "evaluator-work"
    work.mkdir(mode=0o700)
    monkeypatch.setattr(
        "breadboard.rl.harness.swe_bench_runner.os.geteuid",
        lambda: 501,
    )
    with pytest.raises(SweBenchRunnerError, match="root-owned"):
        _require_root_private_work_directory(str(work))
    monkeypatch.setattr(
        "breadboard.rl.harness.swe_bench_runner.os.geteuid",
        lambda: 0,
    )
    with pytest.raises(SweBenchRunnerError, match="authority directory"):
        _require_root_private_work_directory(str(work))




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
    flat_instance = instance[INSTANCE_ID]
    with pytest.raises(SweBenchRunnerError, match="envelope"):
        SweBenchEvaluatorResult.from_reports(
            command,
            aggregate_report=aggregate,
            instance_report=flat_instance,
        )
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


def test_evaluator_identity_cannot_be_self_declared(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    adapter = _evaluator(tmp_path, monkeypatch)
    assert (
        adapter.identity_dict()["environment_digest"]
        == OFFICIAL_EVALUATOR_ENVIRONMENT_DIGEST
    )
    with pytest.raises(TypeError):
        SubprocessOfficialEvaluator(  # type: ignore[call-arg]
            environment_root=str(tmp_path),
            work_directory=str(tmp_path),
            executable_digest=f"sha256:{'a' * 64}",
        )
    monkeypatch.setattr(
        "breadboard.rl.harness.swe_bench_runner.measure_official_evaluator_environment",
        lambda _root: {
            "environment_digest": f"sha256:{'f' * 64}",
            "python_digest": OFFICIAL_EVALUATOR_PYTHON_DIGEST,
            "docker_digest": OFFICIAL_EVALUATOR_DOCKER_DIGEST,
        },
    )
    with pytest.raises(SweBenchRunnerError, match="changed after admission"):
        adapter._verify_environment()
    with pytest.raises(SweBenchRunnerError, match="environment digest mismatch"):
        SubprocessOfficialEvaluator(
            environment_root=str(tmp_path / "other-environment"),
            work_directory=adapter.work_directory,
        )


def test_dataset_is_copied_from_verified_descriptor(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = b"canonical-dataset"
    monkeypatch.setattr(
        "breadboard.rl.harness.swe_bench_runner.DATASET_SIZE_BYTES",
        len(payload),
    )
    monkeypatch.setattr(
        "breadboard.rl.harness.swe_bench_runner.DATASET_SHA256",
        hashlib.sha256(payload).hexdigest(),
    )
    source = tmp_path / "source.parquet"
    destination = tmp_path / "private" / "dataset.parquet"
    destination.parent.mkdir(mode=0o700)
    source.write_bytes(payload)
    _copy_verified_dataset(str(source), str(destination))
    assert destination.read_bytes() == payload
    source.write_bytes(b"changed-dataset")
    with pytest.raises(SweBenchRunnerError, match="identity|digest"):
        _copy_verified_dataset(str(source), str(tmp_path / "changed.parquet"))


def test_evaluator_dataset_replaces_mutable_image_tag_with_leaf_digest(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import pyarrow as arrow
    import pyarrow.parquet as parquet

    row = {
        "instance_id": INSTANCE_ID,
        "image": IMAGE_REFERENCE,
        "problem_statement": PINNED_SWE_BENCH_TASK.model_visible_task()[
            "problem_statement"
        ],
    }
    source = tmp_path / "source.parquet"
    destination = tmp_path / "evaluation.parquet"
    parquet.write_table(arrow.Table.from_pylist([row]), source)
    monkeypatch.setattr(
        type(PINNED_SWE_BENCH_TASK),
        "load_verified_row",
        lambda _self, _path: row,
    )

    digest = _write_digest_bound_dataset(
        str(source),
        str(destination),
        PINNED_SWE_BENCH_TASK,
    )

    transformed = parquet.read_table(destination).to_pylist()
    assert transformed[0]["image"] == (
        IMAGE_REFERENCE.rsplit(":", 1)[0] + "@" + IMAGE_LEAF_DIGEST
    )
    assert digest.startswith("sha256:")
    assert destination.stat().st_mode & 0o777 == 0o400


def test_request_requires_real_base_and_launcher_binding(tmp_path: Path) -> None:
    request = _request(tmp_path)
    assert request.headless_request.workspace.base_commit == BASE_COMMIT
    broken_headless = request.headless_request.model_copy(
        update={
            "workspace": request.headless_request.workspace.model_copy(
                update={"base_commit": "0" * 40}
            )
        }
    )
    with pytest.raises(SweBenchRunnerError, match="base"):
        InstalledSweBenchRequest(
            profile=request.profile,
            headless_request=broken_headless,
            headless_invocation=request.headless_invocation,
            dataset_path=request.dataset_path,
            run_id=request.run_id,
        )

    missing_snapshot = request.headless_request.model_copy(
        update={
            "workspace": request.headless_request.workspace.model_copy(
                update={"repository_snapshot_digest": None}
            )
        }
    )
    with pytest.raises(SweBenchRunnerError, match="sealed task repository"):
        InstalledSweBenchRequest(
            profile=request.profile,
            headless_request=missing_snapshot,
            headless_invocation=request.headless_invocation,
            dataset_path=request.dataset_path,
            run_id=request.run_id,
        )

    with pytest.raises(SweBenchRunnerError, match="prompt"):
        InstalledSweBenchRequest(
            profile=request.profile,
            headless_request=request.headless_request.model_copy(
                update={"prompt": "untrusted benchmark prompt"}
            ),
            headless_invocation=request.headless_invocation,
            dataset_path=request.dataset_path,
            run_id=request.run_id,
        )
    with pytest.raises(SweBenchRunnerError, match="cleanup identity"):
        InstalledSweBenchRequest(
            profile=request.profile,
            headless_request=request.headless_request.model_copy(
                update={
                    "resolve_request": c.ResolveEpisodeRequest.model_validate(
                        {
                            **request.headless_request.resolve_request.model_dump(
                                mode="json"
                            ),
                            "episode_id": "episode.1",
                        }
                    )
                }
            ),
            headless_invocation=request.headless_invocation,
            dataset_path=request.dataset_path,
            run_id="episode.1",
        )


def test_invocation_binds_composition_contents_and_rejects_replacement(
    tmp_path: Path,
) -> None:
    invocation = _invocation(tmp_path)
    original_digest = invocation.identity_dict()["composition_ref_digest"]
    composition_path = Path(invocation.composition_ref_path)
    composition_path.write_bytes(b'{"schema_version":"replacement.v1"}')
    replacement = _invocation(
        tmp_path,
        composition=b'{"schema_version":"replacement.v1"}',
    )

    assert replacement.identity_dict()["composition_ref_digest"] != original_digest
    with pytest.raises(SweBenchRunnerError, match="changed after admission"):
        asyncio.run(invocation.run(_headless_request(tmp_path)))


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


def test_evaluator_cleans_resources_after_base_exception(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    evaluator = _evaluator(tmp_path, monkeypatch)
    cleanup_calls: list[dict[str, Any]] = []

    def interrupt_copy(_source: str, _destination: str) -> None:
        raise KeyboardInterrupt

    def cleanup_containers(
        _self: SubprocessOfficialEvaluator,
        **kwargs: Any,
    ) -> dict[str, Any]:
        cleanup_calls.append(kwargs)
        return {"containers": []}

    monkeypatch.setattr(
        "breadboard.rl.harness.swe_bench_runner._copy_verified_dataset",
        interrupt_copy,
    )
    monkeypatch.setattr(
        SubprocessOfficialEvaluator,
        "_cleanup_containers",
        cleanup_containers,
    )

    with pytest.raises(KeyboardInterrupt):
        evaluator.evaluate(
            task_binding=PINNED_SWE_BENCH_TASK,
            dataset_path=str(tmp_path / "dataset.parquet"),
            prediction=b"{}",
            run_id="episode-1",
            model_name="controller",
            patch_digest=f"sha256:{_HEX}",
        )

    assert len(cleanup_calls) == 1
    assert not (Path(evaluator.work_directory) / "episode-1").exists()


def test_installed_run_binds_canonical_headless_evaluator_and_both_cleanups(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = _target()
    monkeypatch.setattr(
        E4TargetPolicyProjection,
        "load",
        classmethod(lambda cls, target_id, dynamic_fields: target),
    )
    request = _request(tmp_path)
    canonical = _headless_result(request, target)

    async def run_headless(
        _self: InstalledHeadlessInvocation,
        _request: HeadlessRunRequest,
    ) -> Mapping[str, Any]:
        return canonical

    monkeypatch.setattr(InstalledHeadlessInvocation, "run", run_headless)
    evaluator = _evaluator(tmp_path, monkeypatch)

    def evaluate(
        _self: SubprocessOfficialEvaluator,
        *,
        task_binding: Any,
        dataset_path: str,
        prediction: bytes,
        run_id: str,
        model_name: str,
        patch_digest: str,
    ) -> OfficialEvaluatorOutcome:
        assert task_binding == PINNED_SWE_BENCH_TASK
        assert dataset_path == request.dataset_path
        assert json.loads(prediction)["instance_id"] == INSTANCE_ID
        command = TrustedEvaluatorCommand.create(
            dataset_path=str(tmp_path / "private-dataset.parquet"),
            predictions_path=str(tmp_path / "private-predictions.jsonl"),
            report_directory=str(tmp_path / "private-reports"),
            run_id=run_id,
            model_name=model_name,
            patch_digest=patch_digest,
        )
        aggregate, instance = _reports(False)
        return OfficialEvaluatorOutcome(
            evaluation=SweBenchEvaluatorResult.from_reports(
                command,
                aggregate_report=aggregate,
                instance_report=instance,
            ),
            cleanup_digest=f"sha256:{'d' * 64}",
            evaluation_dataset_digest=f"sha256:{'e' * 64}",
            image_observation_digest=f"sha256:{'f' * 64}",
        )

    monkeypatch.setattr(SubprocessOfficialEvaluator, "evaluate", evaluate)
    receipt = asyncio.run(run_installed_swe_bench(request, evaluator=evaluator))
    assert receipt.reward == 0.0
    assert receipt.dataset_row_digest == PINNED_SWE_BENCH_TASK.row_digest
    assert receipt.image_index_digest == IMAGE_INDEX_DIGEST
    assert receipt.headless_cleanup_digest.startswith("sha256:")
    assert receipt.evaluator_cleanup_digest == f"sha256:{'d' * 64}"
    assert receipt.evaluation_dataset_digest == f"sha256:{'e' * 64}"
    assert receipt.image_observation_digest == f"sha256:{'f' * 64}"
    assert receipt.cleanup_digest.startswith("sha256:")
    public = receipt.to_public_dict()
    assert "model_patch" not in public
    assert "api_key" not in str(public)
    assert public["evaluator_identity"]["environment_digest"] == evaluator.environment_digest

    leaked_mount = json.loads(json.dumps(canonical))
    leaked_mount["cleanup_inventory"]["mount_paths"] = ["/live-mount"]
    leaked_mount["cleanup_inventory_digest"] = _canonical_digest(
        leaked_mount["cleanup_inventory"]
    )
    with pytest.raises(SweBenchRunnerError, match="not empty"):
        _validate_headless_result(leaked_mount, request)

    forged_cleanup = json.loads(json.dumps(canonical))
    forged_cleanup["cleanup"]["receipt_digest"] = f"sha256:{'e' * 64}"
    with pytest.raises(SweBenchRunnerError, match="receipt digest mismatch"):
        _validate_headless_result(forged_cleanup, request)

    detached_patch = json.loads(json.dumps(canonical))
    detached_patch["patch"]["digest"] = f"sha256:{'e' * 64}"
    with pytest.raises(SweBenchRunnerError, match="canonical workspace patch"):
        _validate_headless_result(detached_patch, request)
