from __future__ import annotations

import asyncio
import hashlib
import os
import stat
from pathlib import Path
from typing import Any, Mapping

import pytest
from breadboard_engine.compilation.contracts import canonical_json_bytes, canonical_json_loads
from pydantic import ValidationError

from breadboard.rl.harness import contracts as c
from breadboard.rl.phase5.f4_campaign import ImmutableRef
import scripts.rl_phase5.run_f6_restart_replay as f6_runner_module
from scripts.rl_phase5.run_f6_restart_replay import (
    BreadBoardF6Runtime,
    F6CleanupObservation,
    F6DeterministicResult,
    F6DurableBinding,
    F6EpisodeBinding,
    F6FileIdentity,
    F6ImmutableIdentity,
    F6LifecycleObservation,
    F6ProductionBinding,
    F6ProductionReportIdentity,
    F6RestartObservation,
    F6RestartReplayError,
    F6RestartReplayInput,
    F6SecretFileRef,
    F6TargetIdentity,
    _component_report_line,
    _publish_report,
    _read_input,
    _validate_f6_restart_replay,
    run_f6_restart_replay,
)
from tests.rl.harness.test_config_selection import _resolution_fixture


def _d(label: str) -> str:
    return "sha256:" + hashlib.sha256(label.encode("utf-8")).hexdigest()


def _canonical_digest(value: Any) -> str:
    return "sha256:" + hashlib.sha256(canonical_json_bytes(value)).hexdigest()


def _ref(label: str, digest: str | None = None) -> ImmutableRef:
    value = digest or _d(label)
    return ImmutableRef(reference=f"cas://f6-target/{label}@{value}", digest=value)


def _fixture_identity() -> tuple[c.ResolveEpisodeRequest, F6ImmutableIdentity]:
    fixture = _resolution_fixture(
        algorithm="direct-v1",
        candidate_count=1,
        candidate_names=("f6-family-neutral", "unused-a", "unused-b"),
    )
    request_payload = fixture.request.model_dump(mode="json")
    request_payload["episode_id"] = "f6-original-episode"
    request = c.ResolveEpisodeRequest.model_validate(request_payload)
    resolved = fixture.runtime.resolve_episode(request)
    selection = c.SelectionRecord.model_validate_json(
        fixture.store.records[resolved.selection_record_ref.sha256], strict=True
    )
    plan = resolved.effective_plan
    assert len(plan.policy_slots) == 1
    assert plan.task.repository_snapshot_digest is not None
    slot = plan.policy_slots[0]
    identity = F6ImmutableIdentity(
        selection_algorithm=selection.algorithm,
        selected_candidate_id=selection.selected_candidate_id,
        selector_digest=selection.selector_digest,
        config_set_digest=selection.config_set_digest,
        compiled_manifest_digest=plan.base_compiled.manifest_digest,
        config_bundle_digest=plan.base_compiled.bundle_digest,
        dependency_closure_digest=plan.base_compiled.closure_digest,
        compiler_identity_digest=plan.base_compiled.compiler.canonical_digest(),
        base_receipt_digest=plan.base_receipt_digest,
        final_receipt_digest=plan.final_receipt_digest,
        runner_adapter_id=plan.runner.adapter_id,
        runner_runtime_abi=plan.runner.runtime_abi,
        runner_implementation_digest=plan.runner.implementation_digest,
        task_binding_digest=plan.task.task_binding_digest,
        task_contract_digest=plan.task.task_contract_digest,
        repository_snapshot_digest=plan.task.repository_snapshot_digest,
        model_digest=slot.model_digest,
        tokenizer_digest=slot.tokenizer_digest,
        checkpoint_digest=slot.checkpoint_digest,
        primary_image_digest=plan.sandbox.image_digest,
        verifier_image_digest=plan.verifier.image_digest,
        verifier_implementation_digest=plan.verifier.implementation_digest,
    )
    return request, identity


def _spec(tmp_path: Path) -> F6RestartReplayInput:
    original, identity = _fixture_identity()
    fresh_payload = original.model_dump(mode="json")
    fresh_payload["episode_id"] = "f6-fresh-live-episode"
    fresh = c.ResolveEpisodeRequest.model_validate(fresh_payload)
    return F6RestartReplayInput(
        schema_version="bb.rl.phase5-f6-restart-replay-input.v1",
        production=F6ProductionBinding(
            composition_ref_path=str(tmp_path / "composition.ref.json"),
            composition_descriptor_ref=_ref("composition-descriptor"),
            composition_manifest_ref=_ref("composition-manifest"),
            authority_bundle_ref=_ref("authority-bundle"),
            secret_files={
                "policy": F6SecretFileRef(
                    path=str(tmp_path / "policy.secret"),
                    sha256=_d("policy-secret"),
                    identity=F6FileIdentity(
                        device=1,
                        inode=1,
                        size_bytes=1,
                        mtime_ns="1",
                        ctime_ns="1",
                        owner_uid=0,
                        mode=0o400,
                        nlink=1,
                    ),
                )
            },
        ),
        target=F6TargetIdentity(
            target_run_id="phase3-target-run",
            slurm_job_id="12345",
            slurm_nodelist="worker-01",
            local_hostname="worker-01",
        ),
        immutable_identity=identity,
        original_request=original,
        fresh_live_request=fresh,
        task_input={"query": "repair the exact repository"},
        run_context={"campaign": "f6-restart-replay"},
        report_path=str(tmp_path / "f6-report.json"),
    )


def _persisted_input(
    tmp_path: Path,
) -> tuple[Path, str, F6FileIdentity]:
    spec = _spec(tmp_path)
    path = tmp_path / "f6-input.json"
    raw = canonical_json_bytes(spec.model_dump(mode="json"))
    path.write_bytes(raw)
    path.chmod(0o400)
    observed = path.stat(follow_symlinks=False)
    return (
        path,
        "sha256:" + hashlib.sha256(raw).hexdigest(),
        F6FileIdentity(
            device=observed.st_dev,
            inode=observed.st_ino,
            size_bytes=observed.st_size,
            mtime_ns=str(observed.st_mtime_ns),
            ctime_ns=str(observed.st_ctime_ns),
            owner_uid=observed.st_uid,
            mode=0o400,
            nlink=1,
        ),
    )


def _deterministic(identity: F6ImmutableIdentity) -> F6DeterministicResult:
    return F6DeterministicResult(
        immutable_identity_digest=_canonical_digest(identity.model_dump(mode="json")),
        selection_episode_neutral_digest=_d("stable-selection"),
        effective_plan_episode_neutral_digest=_d("stable-plan"),
        create_state="ready",
        base_receipt_digest=identity.base_receipt_digest,
        final_receipt_digest=identity.final_receipt_digest,
        policy_observation_digest=_d("policy-observation"),
        sandbox_preflight_identity_digest=_d("sandbox-preflight-identity"),
        primary_disposition="succeeded",
        response_digest=_d("deterministic-run-response"),
        termination="assistant_complete",
        turn_count=3,
        reward_and_components_digest=_d("reward-and-components"),
        close_state="closed",
        cleanup_disposition="released",
    )


def _episode_binding(episode_id: str) -> F6EpisodeBinding:
    selection = _d(f"{episode_id}:selection")
    binding = _d(f"{episode_id}:selection-binding")
    plan = _d(f"{episode_id}:effective-plan")
    result = _d(f"{episode_id}:result")
    return F6EpisodeBinding(
        episode_id=episode_id,
        selection_record_digest=selection,
        selection_record_ref_digest=selection,
        selection_commit_binding_digest=binding,
        selection_commit_binding_ref_digest=binding,
        effective_plan_digest=plan,
        effective_plan_ref_digest=plan,
        create_fingerprint=_d(f"{episode_id}:create"),
        run_fingerprint=_d(f"{episode_id}:run"),
        policy_binding_digest=_d(f"{episode_id}:policy-binding"),
        materialization_plan_digest=_d(f"{episode_id}:materialization-plan"),
        result_ref_digest=result,
        evidence_manifest_ref_digest=_d(f"{episode_id}:evidence-manifest"),
        evidence_root=_d(f"{episode_id}:evidence-root"),
        artifact_manifest_ref_digest=_d(f"{episode_id}:artifact-manifest"),
        primary_measurement_digest=_d(f"{episode_id}:primary-measurement"),
        verifier_measurement_digest=_d(f"{episode_id}:verifier-measurement"),
        verifier_result_digest=_d(f"{episode_id}:verifier-result"),
    )


def _durable(binding: F6EpisodeBinding) -> F6DurableBinding:
    episode_id = binding.episode_id
    completed_envelope = _d(f"{episode_id}:completed-envelope")
    completed_tombstone = _d(f"{episode_id}:completed-tombstone")
    closed_envelope = _d(f"{episode_id}:closed-envelope")
    closed_tombstone = _d(f"{episode_id}:closed-tombstone")
    return F6DurableBinding(
        episode_id=episode_id,
        current_state="closed",
        quarantined=False,
        locator_digest=_d(f"{episode_id}:locator"),
        locator_completed_tombstone_ref_digest=completed_tombstone,
        locator_closed_tombstone_ref_digest=closed_tombstone,
        completed_tombstone_digest=completed_tombstone,
        completed_tombstone_envelope_ref_digest=completed_envelope,
        completed_tombstone_response_ref_digest=binding.result_ref_digest,
        closed_tombstone_digest=closed_tombstone,
        closed_tombstone_envelope_ref_digest=closed_envelope,
        closed_tombstone_response_ref_digest=binding.result_ref_digest,
        closed_tombstone_completed_ref_digest=completed_tombstone,
        completed_envelope_digest=completed_envelope,
        completed_envelope_ref_digest=completed_envelope,
        completed_envelope_run_response_ref_digest=binding.result_ref_digest,
        closed_envelope_digest=closed_envelope,
        closed_envelope_ref_digest=closed_envelope,
        closed_envelope_completed_ref_digest=completed_envelope,
        cleanup_receipt_digest=_d(f"{episode_id}:cleanup"),
        create_fingerprint=binding.create_fingerprint,
        run_fingerprint=binding.run_fingerprint,
        reconciliation_event_head=_d(f"{episode_id}:closed-event"),
    )


class _DurableFakeRuntime:
    def __init__(self, spec: F6RestartReplayInput, tamper: str | None = None) -> None:
        self.production_identity = F6ProductionReportIdentity(
            composition_descriptor_digest=spec.production.composition_descriptor_ref.digest,
            composition_manifest_digest=spec.production.composition_manifest_ref.digest,
            authority_bundle_digest=spec.production.authority_bundle_ref.digest,
        )
        self.target_identity = spec.target
        self.spec = spec
        self.tamper = tamper
        self.generation = 0
        self.service_instance = _d("service-generation-0")
        self.process_memory: dict[str, F6LifecycleObservation] = {}
        self.durable_store: dict[str, tuple[F6EpisodeBinding, F6DurableBinding]] = {}
        self.runner_calls = 0
        self.started = False
        self.closed = False
        self.restart_replaced_memory = False
        self.cached_rehydrated = False
        self.final_cleanup = F6CleanupObservation(
            active_lease_ids=(),
            orphan_resource_ids=(),
            leaked_artifact_ids=(),
            cleanup_errors=(),
        )

    async def start(self) -> None:
        self.started = True

    async def restart(self) -> F6RestartObservation:
        assert self.started
        assert self.spec.original_request.episode_id in self.process_memory
        old_memory = self.process_memory
        self.process_memory = {}
        self.restart_replaced_memory = old_memory is not self.process_memory
        self.generation = 1
        old_instance = self.service_instance
        self.service_instance = _d("service-generation-1")
        return F6RestartObservation(
            previous_generation=0,
            new_generation=1,
            previous_service_instance_digest=old_instance,
            new_service_instance_digest=self.service_instance,
            durable_authority_digest_before=_d("durable-authority"),
            durable_authority_digest_after=_d("durable-authority"),
            prior_service_closed=True,
            process_memory_retained=False,
            recovered_from_durable_state=True,
        )

    async def execute_episode(
        self,
        request: c.ResolveEpisodeRequest,
        *,
        task_input: Mapping[str, Any],
        context: Mapping[str, Any],
        phase: str,
        immutable_identity: F6ImmutableIdentity,
    ) -> F6LifecycleObservation:
        assert task_input == self.spec.task_input
        assert context == self.spec.run_context
        assert immutable_identity == self.spec.immutable_identity
        before = self.runner_calls
        if phase == "cached" and self.tamper == "counter_discontinuity":
            before = 0
        if phase == "cached":
            assert request.episode_id not in self.process_memory
            if self.tamper == "missing_durable":
                raise F6RestartReplayError("durable episode state is missing")
            binding, durable = self.durable_store[request.episode_id]
            self.cached_rehydrated = True
            create_disposition = run_disposition = close_disposition = "cached"
            if self.tamper == "cached_rerun":
                self.runner_calls += 1
            if self.tamper == "cached_disposition":
                run_disposition = "fresh"
            if self.tamper == "changed_ref":
                binding = binding.model_copy(update={"evidence_root": _d("drifted-ref")})
            if self.tamper == "corrupt_tombstone":
                payload = durable.model_dump(mode="python")
                payload["closed_tombstone_digest"] = _d("corrupt-tombstone")
                durable = F6DurableBinding.model_construct(**payload)
        else:
            self.runner_calls += 1
            binding = _episode_binding(request.episode_id)
            durable = _durable(binding)
            self.durable_store[request.episode_id] = (binding, durable)
            create_disposition = run_disposition = "fresh"
            close_disposition = "cached"
            if phase == "fresh_live" and self.tamper == "fresh_cache_hit":
                self.runner_calls -= 1
                create_disposition = run_disposition = close_disposition = "cached"
        deterministic = _deterministic(immutable_identity)
        if phase == "fresh_live" and self.tamper == "identity_drift":
            deterministic = deterministic.model_copy(
                update={"immutable_identity_digest": _d("drifted-immutable-identity")}
            )
        cleanup = F6CleanupObservation(
            active_lease_ids=(),
            orphan_resource_ids=(),
            leaked_artifact_ids=(),
            cleanup_errors=(),
        )
        if phase == "fresh_live" and self.tamper == "cleanup_leak":
            cleanup = F6CleanupObservation.model_construct(
                active_lease_ids=("lease-left-running",),
                orphan_resource_ids=(),
                leaked_artifact_ids=(),
                cleanup_errors=(),
            )
        observation = F6LifecycleObservation(
            phase=phase,
            episode_id=request.episode_id,
            runtime_generation=self.generation,
            service_instance_digest=self.service_instance,
            create_disposition=create_disposition,
            run_disposition=run_disposition,
            close_disposition=close_disposition,
            runner_calls_before=before,
            runner_calls_after=self.runner_calls,
            deterministic=deterministic,
            episode_binding=binding,
            durable=durable,
            cleanup=cleanup,
        )
        self.process_memory[request.episode_id] = observation
        return observation

    async def close(self) -> None:
        self.closed = True
        if self.tamper == "final_orphan":
            self.final_cleanup = F6CleanupObservation(
                active_lease_ids=(),
                orphan_resource_ids=("detached-runner-resource",),
                leaked_artifact_ids=(),
                cleanup_errors=(),
            )


def test_f6_protocol_fake_validates_only_without_publishing(
    tmp_path: Path,
) -> None:
    spec = _spec(tmp_path)
    runtime = _DurableFakeRuntime(spec)

    report = asyncio.run(
        _validate_f6_restart_replay(
            spec,
            input_digest=_d("input"),
            runtime=runtime,
        )
    )

    assert runtime.restart_replaced_memory is True
    assert runtime.cached_rehydrated is True
    assert runtime.runner_calls == 2
    assert runtime.closed is True
    assert report.original.runner_calls_before == 0
    assert report.original.runner_calls_after == 1
    assert report.cached.runner_calls_before == 1
    assert report.cached.runner_calls_after == 1
    assert report.fresh_live.runner_calls_before == 1
    assert report.fresh_live.runner_calls_after == 2
    assert report.cached.create_disposition == "cached"
    assert report.cached.run_disposition == "cached"
    assert report.cached.close_disposition == "cached"
    assert report.cached_runner_calls == 0
    assert report.fresh_live.create_disposition == "fresh"
    assert report.fresh_live.run_disposition == "fresh"
    assert report.fresh_live_runner_calls == 1
    assert report.original.deterministic == report.cached.deterministic
    assert report.original.deterministic == report.fresh_live.deterministic
    assert report.original.episode_binding == report.cached.episode_binding
    assert report.original.durable == report.cached.durable
    assert report.original.episode_id != report.fresh_live.episode_id
    assert report.restart.process_memory_retained is False
    assert report.final_cleanup == runtime.final_cleanup
    assert report.promotion_authority is False
    assert report.scorecard_authority is False
    assert not Path(spec.report_path).exists()
    line = _component_report_line(report)
    assert line.startswith(b"PHASE3_COMPONENT_REPORT_JSON={")
    assert line.count(b"\n") == 1
    assert canonical_json_loads(
        line.split(b"=", 1)[1].rstrip(b"\n")
    ) == report.model_dump(mode="json")


@pytest.mark.parametrize(
    "tamper",
    [
        "cached_disposition",
        "cached_rerun",
        "fresh_cache_hit",
        "identity_drift",
        "changed_ref",
        "corrupt_tombstone",
        "missing_durable",
        "cleanup_leak",
        "counter_discontinuity",
        "final_orphan",
    ],
)
def test_f6_false_wins_fail_closed_without_report(tmp_path: Path, tamper: str) -> None:
    spec = _spec(tmp_path)
    runtime = _DurableFakeRuntime(spec, tamper=tamper)

    with pytest.raises((F6RestartReplayError, ValidationError)):
        asyncio.run(
            _validate_f6_restart_replay(
                spec,
                input_digest=_d("input"),
                runtime=runtime,
            )
        )

    assert runtime.closed is True
    assert not Path(spec.report_path).exists()


def test_f6_input_drift_is_rejected_before_execution(tmp_path: Path) -> None:
    spec = _spec(tmp_path)
    payload = spec.model_dump(mode="json")
    payload["fresh_live_request"]["subject"]["principal_id"] = "drifted-principal"

    with pytest.raises(ValidationError, match="differ beyond episode ID"):
        F6RestartReplayInput.model_validate(payload)

    assert not Path(spec.report_path).exists()


def test_f6_report_write_is_exclusive(tmp_path: Path) -> None:
    spec = _spec(tmp_path)
    report = asyncio.run(
        _validate_f6_restart_replay(
            spec,
            input_digest=_d("input"),
            runtime=_DurableFakeRuntime(spec),
        )
    )
    Path(spec.report_path).write_bytes(b"preexisting")

    with pytest.raises(FileExistsError):
        _publish_report(spec.report_path, report)

    assert Path(spec.report_path).read_bytes() == b"preexisting"


def _validated_report(tmp_path: Path) -> tuple[F6RestartReplayInput, Any]:
    spec = _spec(tmp_path)
    report = asyncio.run(
        _validate_f6_restart_replay(
            spec,
            input_digest=_d("input"),
            runtime=_DurableFakeRuntime(spec),
        )
    )
    return spec, report


def test_public_report_path_denies_protocol_runtime_injection(
    tmp_path: Path,
) -> None:
    spec = _spec(tmp_path)
    runtime = _DurableFakeRuntime(spec)

    with pytest.raises(TypeError):
        run_f6_restart_replay(  # type: ignore[call-arg]
            spec,
            input_digest=_d("input"),
            runtime=runtime,
        )

    assert runtime.started is False
    assert not Path(spec.report_path).exists()


@pytest.mark.parametrize("unsafe_kind", ["mode", "symlink"])
def test_input_reader_rejects_wrong_mode_and_symlink(
    tmp_path: Path,
    unsafe_kind: str,
) -> None:
    path, digest, identity = _persisted_input(tmp_path)
    selected = path
    if unsafe_kind == "mode":
        path.chmod(0o600)
    else:
        selected = tmp_path / "f6-input-link.json"
        selected.symlink_to(path)

    with pytest.raises(F6RestartReplayError):
        _read_input(
            os.fspath(selected),
            expected_sha256=digest,
            expected_identity=identity,
        )


def test_input_reader_rejects_same_content_inode_replacement(
    tmp_path: Path,
) -> None:
    path, digest, identity = _persisted_input(tmp_path)
    raw = path.read_bytes()
    path.unlink()
    path.write_bytes(raw)
    path.chmod(0o400)

    with pytest.raises(
        F6RestartReplayError,
        match="identity mismatch",
    ):
        _read_input(
            os.fspath(path),
            expected_sha256=digest,
            expected_identity=identity,
        )


def test_input_reader_rejects_mutation_during_read(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path, digest, identity = _persisted_input(tmp_path)
    original_read = os.read
    mutated = False

    def mutating_read(descriptor: int, size: int) -> bytes:
        nonlocal mutated
        chunk = original_read(descriptor, size)
        if chunk and not mutated:
            mutated = True
            observed = path.stat(follow_symlinks=False)
            os.utime(
                path,
                ns=(observed.st_atime_ns, observed.st_mtime_ns + 1),
                follow_symlinks=False,
            )
        return chunk

    monkeypatch.setattr(f6_runner_module.os, "read", mutating_read)
    with pytest.raises(F6RestartReplayError, match="changed"):
        _read_input(
            os.fspath(path),
            expected_sha256=digest,
            expected_identity=identity,
        )


def test_input_reader_rejects_wrong_expected_digest(
    tmp_path: Path,
) -> None:
    path, _, identity = _persisted_input(tmp_path)

    with pytest.raises(F6RestartReplayError, match="digest"):
        _read_input(
            os.fspath(path),
            expected_sha256=_d("wrong-input"),
            expected_identity=identity,
        )


def test_report_partial_write_removes_only_owned_inode(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    spec, report = _validated_report(tmp_path)
    original_write = os.write
    calls = 0

    def partial_then_fail(descriptor: int, payload: Any) -> int:
        nonlocal calls
        calls += 1
        if calls == 1:
            return original_write(
                descriptor,
                payload[: max(1, len(payload) // 2)],
            )
        raise OSError("injected report write failure")

    monkeypatch.setattr(f6_runner_module.os, "write", partial_then_fail)
    with pytest.raises(OSError, match="injected"):
        _publish_report(spec.report_path, report)

    assert not Path(spec.report_path).exists()


def test_report_failure_preserves_foreign_replacement_race(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    spec, report = _validated_report(tmp_path)
    report_path = Path(spec.report_path)
    original_write = os.write
    original_open = os.open
    calls = 0

    def replace_then_fail(descriptor: int, payload: Any) -> int:
        nonlocal calls
        calls += 1
        if calls == 1:
            written = original_write(descriptor, payload[:1])
            report_path.unlink()
            foreign = original_open(
                report_path,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL,
                0o440,
            )
            try:
                original_write(foreign, b"foreign-winner")
            finally:
                os.close(foreign)
            return written
        raise OSError("injected post-race failure")

    monkeypatch.setattr(f6_runner_module.os, "write", replace_then_fail)
    with pytest.raises(OSError, match="post-race"):
        _publish_report(spec.report_path, report)

    assert report_path.read_bytes() == b"foreign-winner"


def test_report_file_fsync_failure_removes_owned_output(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    spec, report = _validated_report(tmp_path)
    original_fsync = os.fsync
    failed = False

    def failing_file_fsync(descriptor: int) -> None:
        nonlocal failed
        if not failed and stat.S_ISREG(os.fstat(descriptor).st_mode):
            failed = True
            raise OSError("injected file fsync failure")
        original_fsync(descriptor)

    monkeypatch.setattr(
        f6_runner_module.os,
        "fsync",
        failing_file_fsync,
    )
    with pytest.raises(OSError, match="file fsync"):
        _publish_report(spec.report_path, report)

    assert not Path(spec.report_path).exists()


def test_report_publication_fsyncs_parent_directory(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    spec, report = _validated_report(tmp_path)
    original_fsync = os.fsync
    directory_fsyncs = 0

    def observing_fsync(descriptor: int) -> None:
        nonlocal directory_fsyncs
        if stat.S_ISDIR(os.fstat(descriptor).st_mode):
            directory_fsyncs += 1
        original_fsync(descriptor)

    monkeypatch.setattr(f6_runner_module.os, "fsync", observing_fsync)
    _publish_report(spec.report_path, report)

    assert directory_fsyncs == 1
    assert canonical_json_loads(
        Path(spec.report_path).read_bytes()
    ) == report.model_dump(mode="json")


def test_report_directory_fsync_failure_removes_owned_output(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    spec, report = _validated_report(tmp_path)
    original_fsync = os.fsync

    def failing_directory_fsync(descriptor: int) -> None:
        if stat.S_ISDIR(os.fstat(descriptor).st_mode):
            raise OSError("injected directory fsync failure")
        original_fsync(descriptor)

    monkeypatch.setattr(
        f6_runner_module.os,
        "fsync",
        failing_directory_fsync,
    )
    with pytest.raises(OSError, match="directory fsync"):
        _publish_report(spec.report_path, report)

    assert not Path(spec.report_path).exists()


def test_store_probe_allows_content_mutation_but_rejects_directory_replacement(
    tmp_path: Path,
) -> None:
    store = tmp_path / "locator-store"
    store.mkdir(mode=0o700)
    descriptor = os.open(
        store,
        os.O_RDONLY | getattr(os, "O_DIRECTORY", 0),
    )
    expected = os.fstat(descriptor)
    runtime = BreadBoardF6Runtime.__new__(BreadBoardF6Runtime)
    runtime._store_probes = [
        ("locator", os.fspath(store), descriptor, expected)
    ]
    try:
        (store / "episode-locator.json").write_bytes(b"durable-locator")
        assert runtime._probe_store_authorities() == set()

        displaced = tmp_path / "displaced-locator-store"
        store.rename(displaced)
        store.mkdir(mode=0o700)
        assert runtime._probe_store_authorities() == {
            "store:locator:identity-drift"
        }
    finally:
        os.close(descriptor)
