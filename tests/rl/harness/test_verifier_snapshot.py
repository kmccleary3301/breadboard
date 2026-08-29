from __future__ import annotations

import asyncio
import json
import os
import stat
import threading
from dataclasses import replace
from pathlib import Path
from types import MappingProxyType
from typing import Any, Mapping

import pytest

from breadboard.rl.harness import contracts as c
from breadboard.rl.harness import materialization as materialization_module
from breadboard.rl.harness import sandbox as sandbox_module
from breadboard.rl.harness.materialization import (
    CleanupState,
    CleanupStepReceipt,
    IsolationDisposition,
    SandboxCleanupReceipt,
    WorkspaceLeaseState,
)
from breadboard.rl.harness.sandbox import (
    SandboxAttestationError,
    SandboxFault,
    SandboxRuntimeManager,
    SandboxWorkspaceLease,
    VerifierExecutionError,
    VerifierSnapshotError,
    WorkspaceStateError,
)
from tests.rl.harness.test_sandbox_runtime import (
    RecordingBackend,
    RecordingHandle,
    RuntimeHarness,
)
from tests.rl.harness.wp7_fixtures import (
    DeterministicRandom,
    _registry_snapshot,
    digest,
    independent_digest,
    make_runtime_fixture,
)


def _snapshot_entries(root: Path) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    for current_root, dirs, files in os.walk(root, topdown=True, followlinks=False):
        dirs.sort()
        files.sort()
        relative_root = Path(current_root).relative_to(root)
        for name in dirs + files:
            path = Path(current_root) / name
            relative = (relative_root / name).as_posix()
            metadata = path.lstat()
            if stat.S_ISDIR(metadata.st_mode):
                entries.append(
                    {
                        "path": relative,
                        "kind": "directory",
                        "mode": stat.S_IMODE(metadata.st_mode),
                        "size": 0,
                        "digest": None,
                    }
                )
            else:
                content = path.read_bytes()
                from tests.rl.harness.wp7_fixtures import digest

                entries.append(
                    {
                        "path": relative,
                        "kind": "file",
                        "mode": stat.S_IMODE(metadata.st_mode),
                        "size": len(content),
                        "digest": digest(content),
                    }
                )
    return sorted(entries, key=lambda entry: entry["path"])


def _valid_result(primary: Any, snapshot: Any) -> dict[str, Any]:
    return {
        "episode_id": primary.plan.episode_id,
        "task_digest": snapshot.task_digest,
        "effective_plan_digest": primary.plan.effective_plan_digest,
        "snapshot_digest": snapshot.root_digest,
        "verifier_digest": primary.plan.verifier.grant.implementation_digest,
        "score": 1,
    }


async def _opened_snapshot(tmp_path: Path) -> tuple[RuntimeHarness, Any, Any]:
    fixture = make_runtime_fixture(with_writable_mount=True)
    harness = RuntimeHarness(tmp_path, fixture)
    primary = await harness.manager.open(fixture.request)
    await primary.runner_workspace.write_text("work/candidate.txt", "candidate")
    snapshot = await primary.seal_for_verifier()
    return harness, primary, snapshot

def _isolated_verifier_fixture(runtime_class: c.RuntimeClass) -> Any:
    fixture = make_runtime_fixture(
        runtime_class=runtime_class,
        with_writable_mount=True,
    )
    verifier_authority = fixture.authorities.verifiers[0]
    verifier_runtime = next(
        runtime
        for runtime in fixture.authorities.runtimes
        if runtime.runtime_id == verifier_authority.runtime_id
    )
    runtime_updates: dict[str, Any] = {
        "runtime_class": runtime_class,
        "oci_runtime_name": (
            "runsc" if runtime_class is c.RuntimeClass.HARDENED_GVISOR else "runc"
        ),
        "oci_runtime_binary_path": "/bin/sh",
        "oci_runtime_binary_digest": verifier_runtime.measured_binary_digest,
    }
    if runtime_class is c.RuntimeClass.HARDENED_GVISOR:
        runtime_updates.update(
            {
                "runsc_binary_path": "/bin/sh",
                "runsc_binary_digest": verifier_runtime.measured_binary_digest,
            }
        )
    isolated_runtime = replace(verifier_runtime, **runtime_updates)
    isolated_verifier = replace(
        verifier_authority,
        runtime_class=runtime_class,
    )
    authorities = replace(
        fixture.authorities,
        runtimes=tuple(
            sorted(
                (
                    isolated_runtime
                    if runtime.runtime_id == verifier_runtime.runtime_id
                    else runtime
                    for runtime in fixture.authorities.runtimes
                ),
                key=lambda runtime: runtime.runtime_id,
            )
        ),
        verifiers=(isolated_verifier,),
    )
    sandbox_runtimes = tuple(
        type(record)(
            binding=type(record.binding).model_validate(
                {
                    **record.binding.model_dump(mode="python"),
                    "runtime_class": runtime_class,
                }
            )
        )
        if record.binding.runtime_id == verifier_runtime.runtime_id
        else record
        for record in fixture.registries.sandbox_runtimes
    )
    verifier_records = tuple(
        type(record).model_validate(
            {
                **record.model_dump(mode="python"),
                "runtime_class": runtime_class,
            }
        )
        for record in fixture.registries.verifiers
    )
    registry_names = (
        "runners",
        "tools",
        "setups",
        "routes",
        "secret_handles",
        "sandbox_runtimes",
        "images",
        "repository_bindings",
        "task_datasets",
        "models",
        "verifiers",
        "evidence_policies",
        "retention_policies",
        "policy_capability_attestations",
    )
    registries = _registry_snapshot(
        **{
            name: (
                sandbox_runtimes
                if name == "sandbox_runtimes"
                else verifier_records
                if name == "verifiers"
                else getattr(fixture.registries, name)
            )
            for name in registry_names
        }
    )
    return replace(fixture, authorities=authorities, registries=registries)


def _enable_test_quota(
    harness: RuntimeHarness, fixture: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    def measure(backing: Path) -> Mapping[str, Any]:
        metadata = backing.stat()
        quota_bytes = (
            min(
                fixture.plan.effective_capabilities.resources.storage_bytes,
                fixture.plan.effective_capabilities.limits.artifact_bytes_total,
            )
            if backing.name.startswith("verifier-workspace-")
            else fixture.plan.effective_capabilities.resources.storage_bytes
        )
        return {
            "authority_id": "test-quota-authority",
            "quota_enforced": True,
            "quota_bytes": quota_bytes,
            "owner_uid": metadata.st_uid,
            "owner_gid": metadata.st_gid,
        }

    monkeypatch.setattr(harness.store.storage_backend, "measure", measure)


class WeakRewardVerifierBackend(RecordingBackend):
    async def launch(self, *args: Any, **kwargs: Any) -> tuple[Any, Any]:
        handle, measurement = await super().launch(*args, **kwargs)
        context = kwargs["context"]
        if context.role == "verifier":
            measurement = replace(
                measurement,
                isolation_disposition=IsolationDisposition.TRUSTED_PROCESS,
                isolated=False,
                reward_eligible=False,
            )
        return handle, measurement


async def test_snapshot_digest_independently_binds_every_path_byte_and_mode(
    tmp_path: Path,
) -> None:
    harness, primary, snapshot = await _opened_snapshot(tmp_path)
    object_root = harness.cache_root / "objects" / snapshot.root_digest.removeprefix(
        "sha256:"
    )
    entries = _snapshot_entries(object_root)

    assert snapshot.manifest_digest == independent_digest(entries)
    assert snapshot.root_digest == independent_digest(
        {"schema_version": "bb.rl.verifier-snapshot.v1", "entries": entries}
    )
    assert snapshot.file_count == sum(entry["kind"] == "file" for entry in entries)
    assert snapshot.inode_count == len(entries)
    assert snapshot.byte_count == sum(entry["size"] for entry in entries)
    assert (object_root / "work" / "candidate.txt").read_bytes() == b"candidate"
    assert all(
        stat.S_IMODE(path.stat().st_mode) & 0o222 == 0
        for path in object_root.rglob("*")
        if path.is_file()
    )

    with pytest.raises(WorkspaceStateError) as captured:
        await primary.runner_workspace.write_text("work/late.txt", "late")
    assert captured.value.code == "lease_not_active"
    assert not (object_root / "work" / "late.txt").exists()
    await primary.close()


@pytest.mark.parametrize("attack", ["symlink", "hardlink", "fifo", "depth", "files", "bytes"])
async def test_snapshot_rejects_links_special_files_and_budget_bombs_before_verifier(
    tmp_path: Path, attack: str
) -> None:
    fixture = make_runtime_fixture(with_writable_mount=True)
    harness = RuntimeHarness(tmp_path, fixture)
    primary = await harness.manager.open(fixture.request)
    port = primary.runner_workspace
    work = primary._materialized.workspace_path / "work"
    if attack == "symlink":
        target = work / "escape"
        target.symlink_to("/etc/passwd")
        assert target.is_symlink()
    elif attack == "hardlink":
        target = work / "alias"
        os.link(work / "seed.txt", target)
        assert target.stat().st_ino == (work / "seed.txt").stat().st_ino
    elif attack == "fifo":
        target = work / "pipe"
        os.mkfifo(target)
        assert stat.S_ISFIFO(target.stat().st_mode)
    elif attack == "depth":
        await port.write_text("work/a/b/c/d/e/f/g/h/i/deep.txt", "x")
    elif attack == "files":
        for index in range(65):
            await port.write_text(f"work/file-{index:02d}.txt", "x")
    elif attack == "bytes":
        for index in range(3):
            await port.write_text(f"work/large-{index}.txt", "x" * 7_000)

    with pytest.raises(VerifierSnapshotError) as captured:
        await primary.seal_for_verifier()

    assert captured.value.code == "snapshot_tampered"
    assert primary.state is WorkspaceLeaseState.QUARANTINED
    assert primary._verifier_children == []
    assert len(list(harness.workspace_root.iterdir())) == 1
    receipt = await primary.close()
    assert receipt.state is CleanupState.RELEASED
    assert list(harness.workspace_root.iterdir()) == []


async def test_snapshot_requires_positive_runtime_quiescence_and_starts_no_verifier(
    tmp_path: Path,
) -> None:
    fixture = make_runtime_fixture(with_writable_mount=True)
    harness = RuntimeHarness(tmp_path, fixture)
    primary = await harness.manager.open(fixture.request)
    handle = harness.backend.handles[0]
    handle.termination_states = [CleanupState.FAILED, CleanupState.RELEASED]

    with pytest.raises(VerifierSnapshotError) as captured:
        await primary.seal_for_verifier()

    assert captured.value.code == "snapshot_not_quiescent"
    assert primary.state is WorkspaceLeaseState.QUARANTINED
    assert len(harness.backend.launches) == 1
    assert list((harness.cache_root / "objects").glob("*/work/candidate.txt")) == []
    assert (await primary.close()).state is CleanupState.RELEASED


async def test_verifier_uses_distinct_runtime_workspace_and_read_only_snapshot(
    tmp_path: Path,
) -> None:
    harness, primary, snapshot = await _opened_snapshot(tmp_path)

    verifier = await harness.manager.open_verifier(primary, snapshot)

    assert verifier.lease_id != primary.lease_id
    assert verifier.workspace != primary._materialized.workspace_path
    assert verifier.measurement.workspace_id != primary.measurement.workspace_id
    assert verifier.plan.runtime.runtime_id == "verifier-runtime"
    assert verifier.plan.image.image_digest == primary.plan.verifier.grant.image_digest
    assert verifier.plan.security_policy.policy_digest == primary.plan.verifier.security_policy_digest
    assert verifier.plan.network_policy.policy_digest == primary.plan.verifier.grant.network_policy_digest
    assert (verifier.workspace / "snapshot" / "work" / "candidate.txt").read_bytes() == b"candidate"
    assert stat.S_IMODE(
        (verifier.workspace / "snapshot" / "work" / "candidate.txt").stat().st_mode
    ) == 0o400
    assert stat.S_IMODE((verifier.workspace / "result").stat().st_mode) == 0o700
    assert len(harness.backend.launches) == 2
    assert primary.measurement.reward_eligible is False
    assert verifier.measurement.isolated is False
    assert verifier.measurement.reward_eligible is False

    payload = _valid_result(primary, snapshot)
    result_path = verifier.workspace / "result" / verifier.plan.verifier.result_relative_path
    result_path.write_text(json.dumps(payload), encoding="utf-8")
    result = await verifier.execute()
    assert isinstance(result, MappingProxyType)
    assert dict(result) == payload
    verifier_executable = next(
        runtime.executable_path
        for runtime in harness.fixture.authorities.runtimes
        if runtime.runtime_id == verifier.plan.runtime.runtime_id
    )
    assert harness.backend.handles[1].argv_actions == [
        (
            (verifier_executable, "-c", "printf verifier"),
            primary.plan.limits.verifier_timeout_ms,
            primary.plan.limits.observation_bytes,
        )
    ]

    verifier_workspace = verifier.workspace
    child_receipt = await verifier.close()
    assert child_receipt.state is CleanupState.RELEASED
    assert not verifier_workspace.exists()
    assert not (
        harness.cache_root
        / "objects"
        / snapshot.root_digest.removeprefix("sha256:")
    ).exists()
    assert await verifier.close() == child_receipt
    primary_receipt = await primary.close()
    assert primary_receipt.state is CleanupState.RELEASED
    assert list(harness.workspace_root.iterdir()) == []


@pytest.mark.parametrize(
    "runtime_class",
    [c.RuntimeClass.HARDENED_DOCKER, c.RuntimeClass.HARDENED_GVISOR],
)
async def test_reward_eligible_primary_rejects_trusted_verifier_before_effects(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    runtime_class: c.RuntimeClass,
) -> None:
    fixture = make_runtime_fixture(
        runtime_class=runtime_class,
        with_writable_mount=True,
    )
    harness = RuntimeHarness(tmp_path, fixture)
    _enable_test_quota(harness, fixture, monkeypatch)
    primary = await harness.manager.open(fixture.request)
    await primary.runner_workspace.write_text("work/candidate.txt", "candidate")
    snapshot = await primary.seal_for_verifier()
    before_records = {
        path.name: path.read_bytes() for path in harness.lease_root.iterdir()
    }

    with pytest.raises(VerifierExecutionError) as captured:
        await harness.manager.open_verifier(primary, snapshot)

    assert captured.value.code == "verifier_authority_mismatch"
    assert primary.measurement.isolated is True
    assert primary.measurement.reward_eligible is True
    assert len(harness.backend.launches) == 1
    assert len(harness.backend.handles) == 1
    assert primary._verifier_children == []
    assert {
        path.name: path.read_bytes() for path in harness.lease_root.iterdir()
    } == before_records
    assert len(list(harness.workspace_root.iterdir())) == 1
    assert (await primary.close()).state is CleanupState.RELEASED
    assert list(harness.workspace_root.iterdir()) == []
    assert list(harness.lease_root.iterdir()) == []


@pytest.mark.parametrize(
    ("runtime_class", "weak_measurement"),
    [
        (c.RuntimeClass.HARDENED_DOCKER, False),
        (c.RuntimeClass.HARDENED_GVISOR, False),
        (c.RuntimeClass.HARDENED_DOCKER, True),
        (c.RuntimeClass.HARDENED_GVISOR, True),
    ],
)
async def test_reward_eligible_primary_requires_equally_isolated_verifier_measurement(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    runtime_class: c.RuntimeClass,
    weak_measurement: bool,
) -> None:
    fixture = _isolated_verifier_fixture(runtime_class)
    backend = WeakRewardVerifierBackend() if weak_measurement else RecordingBackend()
    harness = RuntimeHarness(tmp_path, fixture, backend=backend)
    _enable_test_quota(harness, fixture, monkeypatch)
    primary = await harness.manager.open(fixture.request)
    await primary.runner_workspace.write_text("work/candidate.txt", "candidate")
    snapshot = await primary.seal_for_verifier()

    if weak_measurement:
        with pytest.raises(SandboxAttestationError) as captured:
            await harness.manager.open_verifier(primary, snapshot)
        assert captured.value.code == "runtime_measurement_mismatch"
        assert len(backend.launches) == 2
        assert len(backend.handles) == 2
        assert backend.handles[1].terminate_calls == 1
        assert primary._verifier_children == []
        assert len(list(harness.workspace_root.iterdir())) == 1
        assert len(list(harness.lease_root.iterdir())) == 2
    else:
        verifier = await harness.manager.open_verifier(primary, snapshot)
        assert primary.measurement.isolation_disposition is IsolationDisposition.ISOLATED
        assert primary.measurement.isolated is True
        assert primary.measurement.reward_eligible is True
        assert verifier.measurement.isolation_disposition is IsolationDisposition.ISOLATED
        assert verifier.measurement.isolated is True
        assert verifier.measurement.reward_eligible is True
        assert (await verifier.close()).state is CleanupState.RELEASED

    assert (await primary.close()).state is CleanupState.RELEASED
    assert list(harness.workspace_root.iterdir()) == []
    assert list(harness.lease_root.iterdir()) == []


@pytest.mark.parametrize(
    ("case", "expected_code"),
    [
        ("nonzero", "verifier_result_malformed"),
        ("missing", "verifier_result_malformed"),
        ("malformed", "verifier_result_malformed"),
        ("oversized", "verifier_result_malformed"),
        ("symlink", "verifier_result_malformed"),
        ("fifo", "verifier_result_malformed"),
        ("device", "verifier_result_malformed"),
        ("sparse", "verifier_result_malformed"),
        ("multibyte", "verifier_result_malformed"),
        ("episode", "verifier_result_identity_mismatch"),
        ("task", "verifier_result_identity_mismatch"),
        ("effective-plan", "verifier_result_identity_mismatch"),
        ("snapshot", "verifier_result_identity_mismatch"),
        ("verifier", "verifier_result_identity_mismatch"),
    ],
)
async def test_malicious_verifier_results_are_typed_and_cleanup_is_authoritative(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    case: str,
    expected_code: str,
) -> None:
    harness, primary, snapshot = await _opened_snapshot(tmp_path)
    verifier = await harness.manager.open_verifier(primary, snapshot)
    handle = harness.backend.handles[1]
    result_path = verifier.workspace / "result" / verifier.plan.verifier.result_relative_path
    payload = _valid_result(primary, snapshot)
    device_fd: int | None = None
    if case == "nonzero":
        handle.result = {"returncode": 7, "stdout": "", "stderr": "failed"}
        result_path.write_text(json.dumps(payload), encoding="utf-8")
    elif case == "missing":
        pass
    elif case == "malformed":
        result_path.write_bytes(b"{")
    elif case == "oversized":
        payload["padding"] = "x" * 9_000
        result_path.write_text(json.dumps(payload), encoding="utf-8")
    elif case == "symlink":
        outside = tmp_path / "outside-result.json"
        outside.write_text(json.dumps(payload), encoding="utf-8")
        result_path.symlink_to(outside)
    elif case == "fifo":
        os.mkfifo(result_path)
    elif case == "device":
        device_fd = os.open("/dev/null", os.O_RDONLY)
        original_open = os.open

        def substitute_device(
            path: Any, flags: int, *args: Any, **kwargs: Any
        ) -> int:
            if path == result_path.name and kwargs.get("dir_fd") is not None:
                return os.dup(device_fd)
            return original_open(path, flags, *args, **kwargs)

        monkeypatch.setattr(sandbox_module.os, "open", substitute_device)
    elif case == "sparse":
        ceiling = min(
            verifier.plan.limits.artifact_bytes_each,
            verifier.plan.limits.artifact_bytes_total,
        )
        result_path.touch(mode=0o600)
        with result_path.open("r+b") as sparse_result:
            sparse_result.seek(ceiling)
            sparse_result.write(b"\0")
        assert result_path.stat().st_size == ceiling + 1
    elif case == "multibyte":
        ceiling = min(
            verifier.plan.limits.artifact_bytes_each,
            verifier.plan.limits.artifact_bytes_total,
        )
        payload["padding"] = ""
        baseline = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        payload["padding"] = "é" * ((ceiling - len(baseline)) // 2 + 1)
        encoded = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        assert len(payload["padding"]) < ceiling < len(encoded)
        result_path.write_bytes(encoded)
    else:
        field = {
            "episode": "episode_id",
            "task": "task_digest",
            "effective-plan": "effective_plan_digest",
            "snapshot": "snapshot_digest",
            "verifier": "verifier_digest",
        }[case]
        payload[field] = "sha256:" + "0" * 64
        result_path.write_text(json.dumps(payload), encoding="utf-8")

    try:
        with pytest.raises(VerifierExecutionError) as captured:
            await asyncio.wait_for(verifier.execute(), 1)
    finally:
        if device_fd is not None:
            os.close(device_fd)

    assert captured.value.code == expected_code
    assert len(harness.backend.launches) == 2
    assert len(handle.argv_actions) == 1
    verifier_workspace = verifier.workspace
    verifier_receipt = await verifier.close()
    assert verifier_receipt.steps == (
        CleanupStepReceipt("runtime", CleanupState.RELEASED),
        CleanupStepReceipt("workspace", CleanupState.RELEASED),
        CleanupStepReceipt("snapshot", CleanupState.RELEASED),
        CleanupStepReceipt("lease_record", CleanupState.RELEASED),
    )
    assert not verifier_workspace.exists()
    primary_receipt = await primary.close()
    assert primary_receipt.state is CleanupState.RELEASED
    assert await harness.manager.close() == ()
    assert list(harness.workspace_root.iterdir()) == []
    assert list(harness.lease_root.iterdir()) == []


@pytest.mark.parametrize(
    "field",
    [
        "snapshot_id",
        "source_workspace_id",
        "source_lease_id",
        "effective_plan_digest",
        "task_digest",
        "verifier_digest",
        "manifest_digest",
        "root_digest",
        "file_count",
        "inode_count",
        "byte_count",
        "immutable_storage_object_id",
    ],
)
async def test_forged_snapshot_identity_starts_no_verifier_and_preserves_primary(
    tmp_path: Path, field: str
) -> None:
    harness, primary, snapshot = await _opened_snapshot(tmp_path)
    if field in {"file_count", "inode_count", "byte_count"}:
        forged_value: Any = getattr(snapshot, field) + 1
    elif field in {
        "effective_plan_digest",
        "task_digest",
        "verifier_digest",
        "manifest_digest",
        "root_digest",
    }:
        forged_value = digest(f"forged-{field}")
    else:
        forged_value = f"forged-{field}"
    forged = replace(snapshot, **{field: forged_value})

    with pytest.raises(VerifierSnapshotError) as captured:
        await harness.manager.open_verifier(primary, forged)

    assert captured.value.code == "snapshot_tampered"
    assert len(harness.backend.launches) == 1
    assert len(harness.backend.handles) == 1
    assert primary.state is WorkspaceLeaseState.QUIESCING
    assert primary._verifier_children == []
    assert len(list(harness.workspace_root.iterdir())) == 1
    receipt = await primary.close()
    assert receipt.state is CleanupState.RELEASED
    assert receipt.steps[0] == CleanupStepReceipt(
        "child_verifier",
        CleanupState.ALREADY_RELEASED,
    )
    assert await harness.manager.close() == ()
    assert list(harness.workspace_root.iterdir()) == []
    assert list(harness.lease_root.iterdir()) == []


async def test_snapshot_seal_cancellation_releases_completed_snapshot_worker(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixture = make_runtime_fixture(with_writable_mount=True)
    harness = RuntimeHarness(tmp_path, fixture)
    primary = await harness.manager.open(fixture.request)
    entered = threading.Event()
    release = threading.Event()
    original_seal = harness.store.seal_snapshot

    def blocked_seal(*args: Any, **kwargs: Any) -> Any:
        entered.set()
        if not release.wait(timeout=2):
            raise AssertionError("snapshot worker was not released")
        return original_seal(*args, **kwargs)

    monkeypatch.setattr(harness.store, "seal_snapshot", blocked_seal)
    sealing = asyncio.create_task(primary.seal_for_verifier())
    assert await asyncio.to_thread(entered.wait, 1)

    sealing.cancel()
    await asyncio.sleep(0)
    assert not sealing.done()
    release.set()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(sealing, 1)

    assert harness.manager._snapshots == {}
    assert (await primary.close()).state is CleanupState.RELEASED
    assert await harness.manager.close() == ()
    assert list(harness.workspace_root.iterdir()) == []
    assert list(harness.lease_root.iterdir()) == []

async def test_identical_snapshot_seal_holds_reference_during_release(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixture = make_runtime_fixture(with_writable_mount=True)
    harness = RuntimeHarness(tmp_path, fixture)
    first = await harness.manager.open(fixture.request)
    await first.runner_workspace.write_text("work/candidate.txt", "candidate")
    first_snapshot = await first.seal_for_verifier()
    second = await harness.manager.open(fixture.request)
    await second.runner_workspace.write_text("work/candidate.txt", "candidate")
    entered = threading.Event()
    continue_verify = threading.Event()
    original_verify = harness.store.verify_snapshot

    def blocked_verify(*args: Any, **kwargs: Any) -> Any:
        entered.set()
        if not continue_verify.wait(timeout=2):
            raise AssertionError("snapshot verification was not released")
        return original_verify(*args, **kwargs)

    monkeypatch.setattr(harness.store, "verify_snapshot", blocked_verify)
    sealing = asyncio.create_task(second.seal_for_verifier())
    assert await asyncio.to_thread(entered.wait, 1)
    releasing = asyncio.create_task(
        harness.manager._release_snapshot(first_snapshot.snapshot_id)
    )
    await asyncio.sleep(0)
    assert not releasing.done()

    continue_verify.set()
    second_snapshot = await asyncio.wait_for(sealing, 1)
    assert await asyncio.wait_for(releasing, 1) == CleanupStepReceipt(
        "snapshot",
        CleanupState.RELEASED,
    )
    object_path = (
        harness.cache_root
        / "objects"
        / second_snapshot.root_digest.removeprefix("sha256:")
    )
    assert object_path.is_dir()
    assert await harness.manager._release_snapshot(
        second_snapshot.snapshot_id
    ) == CleanupStepReceipt("snapshot", CleanupState.RELEASED)
    assert not object_path.exists()
    assert (await first.close()).state is CleanupState.QUARANTINED
    assert (await second.close()).state is CleanupState.RELEASED
    await harness.manager.close()


async def test_snapshot_copy_cancellation_waits_before_verifier_workspace_release(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    harness, primary, snapshot = await _opened_snapshot(tmp_path)
    entered = threading.Event()
    release = threading.Event()
    original_copy = harness.store.copy_snapshot

    def blocked_copy(*args: Any, **kwargs: Any) -> Any:
        entered.set()
        if not release.wait(timeout=2):
            raise AssertionError("snapshot copy worker was not released")
        return original_copy(*args, **kwargs)

    monkeypatch.setattr(harness.store, "copy_snapshot", blocked_copy)
    opening = asyncio.create_task(
        harness.manager.open_verifier(primary, snapshot)
    )
    assert await asyncio.to_thread(entered.wait, 1)

    opening.cancel()
    await asyncio.sleep(0)
    assert not opening.done()
    release.set()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(opening, 1)

    assert not any(
        path.name.startswith("verifier-workspace-")
        for path in harness.workspace_root.iterdir()
    )
    assert not any(
        path.name.startswith("verifier-lease-")
        for path in harness.lease_root.iterdir()
    )
    assert (await primary.close()).state is CleanupState.RELEASED
    assert harness.manager._snapshots == {}
    assert await harness.manager.close() == ()
    assert list(harness.workspace_root.iterdir()) == []
    assert list(harness.lease_root.iterdir()) == []


async def test_post_seal_snapshot_mutation_starts_no_verifier_and_cleans_primary(
    tmp_path: Path,
) -> None:
    harness, primary, snapshot = await _opened_snapshot(tmp_path)
    object_root = harness.cache_root / "objects" / snapshot.root_digest.removeprefix(
        "sha256:"
    )
    candidate = object_root / "work" / "candidate.txt"
    candidate.chmod(0o600)
    candidate.write_bytes(b"tampered")

    with pytest.raises(VerifierSnapshotError) as captured:
        await harness.manager.open_verifier(primary, snapshot)

    assert captured.value.code == "snapshot_tampered"
    assert len(harness.backend.launches) == 1
    assert len(harness.backend.handles) == 1
    assert primary.state is WorkspaceLeaseState.QUIESCING
    assert primary._verifier_children == []
    assert list((harness.cache_root / "staging").iterdir()) == []
    receipt = await primary.close()
    assert receipt.state is CleanupState.RELEASED
    assert receipt.steps[0] == CleanupStepReceipt(
        "child_verifier",
        CleanupState.ALREADY_RELEASED,
    )
    assert await harness.manager.close() == ()
    assert list(harness.workspace_root.iterdir()) == []
    assert list(harness.lease_root.iterdir()) == []


async def test_open_verifier_reservation_precedes_parent_close_and_child_cleanup(
    tmp_path: Path,
) -> None:
    harness, primary, snapshot = await _opened_snapshot(tmp_path)
    backend = harness.backend
    backend.launch_entered = asyncio.Event()
    backend.release_launch = asyncio.Event()
    opening = asyncio.create_task(
        harness.manager.open_verifier(primary, snapshot)
    )

    await asyncio.wait_for(backend.launch_entered.wait(), 1)
    assert len(backend.launches) == 2
    closing = asyncio.create_task(primary.close())
    await asyncio.sleep(0)
    assert not closing.done()
    backend.release_launch.set()
    verifier = await asyncio.wait_for(opening, 1)
    parent_receipt = await asyncio.wait_for(closing, 1)

    assert parent_receipt.steps == (
        CleanupStepReceipt("child_verifier", CleanupState.RELEASED),
        CleanupStepReceipt("runtime", CleanupState.RELEASED),
        CleanupStepReceipt("workspace", CleanupState.RELEASED),
        CleanupStepReceipt("cache_holder", CleanupState.RELEASED),
        CleanupStepReceipt("lease_record", CleanupState.RELEASED),
    )
    assert verifier._closed is True
    assert primary._verifier_children == []
    assert primary.state is WorkspaceLeaseState.RELEASED
    assert len(backend.handles) == 2
    assert backend.handles[0].terminate_calls == 2
    assert backend.handles[1].terminate_calls == 1
    assert await harness.manager.close() == ()
    assert list(harness.workspace_root.iterdir()) == []
    assert list(harness.lease_root.iterdir()) == []


async def test_primary_close_aggregates_multiple_verifier_children_once(
    tmp_path: Path,
) -> None:
    harness, primary, snapshot = await _opened_snapshot(tmp_path)
    first = await harness.manager.open_verifier(primary, snapshot)
    second = await harness.manager.open_verifier(primary, snapshot)

    receipt = await primary.close()

    assert [step.resource for step in receipt.steps].count("child_verifier") == 1
    assert receipt.steps[0] == CleanupStepReceipt(
        "child_verifier",
        CleanupState.RELEASED,
    )
    assert first._closed is True
    assert second._closed is True
    assert await harness.manager.close() == ()


async def test_primary_close_aggregates_failed_child_without_duplicate_resource(
    tmp_path: Path,
) -> None:
    harness, primary, _ = await _opened_snapshot(tmp_path)

    class Child:
        def __init__(self, lease_id: str, state: CleanupState) -> None:
            self.lease_id = lease_id
            self.state = state
            self._closed = False

        async def close(self) -> SandboxCleanupReceipt:
            self._closed = True
            return SandboxCleanupReceipt.from_steps(
                self.lease_id,
                (CleanupStepReceipt("runtime", self.state),),
            )

    primary._verifier_children.extend(
        (
            Child("verifier-released", CleanupState.RELEASED),
            Child("verifier-failed", CleanupState.FAILED),
        )
    )

    receipt = await primary.close()

    child_steps = tuple(
        step for step in receipt.steps if step.resource == "child_verifier"
    )
    assert child_steps == (
        CleanupStepReceipt(
            "child_verifier",
            CleanupState.FAILED,
            "verifier-failed",
        ),
    )
    assert receipt.state is CleanupState.QUARANTINED
    reconciled = await harness.manager.close()
    assert len(reconciled) == 1
    assert reconciled[0].state is CleanupState.RELEASED
@pytest.mark.parametrize(
    "primary_case",
    ["fabricated", "subclass", "foreign-manager", "stale", "non-live"],
)
async def test_open_verifier_rejects_noncanonical_primary_before_effects(
    tmp_path: Path, primary_case: str
) -> None:
    harness, primary, snapshot = await _opened_snapshot(tmp_path)
    foreign_harness: RuntimeHarness | None = None
    candidate = primary
    manager = harness.manager
    removed_live = False

    if primary_case in {"fabricated", "subclass"}:
        if primary_case == "subclass":
            class DerivedPrimary(SandboxWorkspaceLease):
                pass

            lease_type: type[SandboxWorkspaceLease] = DerivedPrimary
        else:
            lease_type = SandboxWorkspaceLease
        candidate = lease_type(
            manager=harness.manager,
            lease_id=primary.lease_id,
            plan=primary.plan,
            materialized=primary._materialized,
            runtime=primary._runtime,
            measurement=primary.measurement,
            owner_token=primary._owner_token,
            epoch=primary._epoch,
        )
        candidate._state = WorkspaceLeaseState.QUIESCING
    elif primary_case == "foreign-manager":
        foreign_root = tmp_path / "foreign"
        foreign_root.mkdir()
        foreign_harness = RuntimeHarness(foreign_root, harness.fixture)
        manager = foreign_harness.manager
    elif primary_case == "stale":
        removed_live = True
        harness.manager._leases.pop(primary.lease_id)
    else:
        receipt = await primary.close()
        assert receipt.state is CleanupState.RELEASED

    before = (
        len(harness.backend.launches),
        tuple(sorted(path.name for path in harness.workspace_root.iterdir())),
        {
            path.name: path.read_bytes()
            for path in sorted(harness.lease_root.iterdir())
        },
        dict(harness.manager._snapshots),
    )
    foreign_before = (
        None
        if foreign_harness is None
        else (
            len(foreign_harness.backend.launches),
            tuple(foreign_harness.workspace_root.iterdir()),
            tuple(foreign_harness.lease_root.iterdir()),
        )
    )

    with pytest.raises(VerifierSnapshotError) as captured:
        await manager.open_verifier(candidate, snapshot)

    assert captured.value.code == "snapshot_not_quiescent"
    assert (
        len(harness.backend.launches),
        tuple(sorted(path.name for path in harness.workspace_root.iterdir())),
        {
            path.name: path.read_bytes()
            for path in sorted(harness.lease_root.iterdir())
        },
        dict(harness.manager._snapshots),
    ) == before
    if foreign_harness is not None:
        assert (
            len(foreign_harness.backend.launches),
            tuple(foreign_harness.workspace_root.iterdir()),
            tuple(foreign_harness.lease_root.iterdir()),
        ) == foreign_before
        assert await foreign_harness.manager.close() == ()
    if removed_live:
        harness.manager._leases[primary.lease_id] = primary
    if primary_case != "non-live":
        assert (await primary.close()).state is CleanupState.RELEASED


@pytest.mark.parametrize("authority_case", ["argv", "digest", "object"])
async def test_open_verifier_rejects_caller_substituted_authority_before_effects(
    tmp_path: Path, authority_case: str
) -> None:
    harness, primary, snapshot = await _opened_snapshot(tmp_path)
    canonical_plan = primary.plan
    canonical = canonical_plan.verifier
    if authority_case == "argv":
        forged = replace(
            canonical,
            argv=("/bin/sh", "-c", "printf substituted"),
        )
    elif authority_case == "digest":
        grant_payload = canonical.grant.model_dump(mode="python")
        grant_payload["executable_digest"] = digest("foreign-verifier-executable")
        foreign_grant = type(canonical.grant).model_validate(grant_payload)
        forged = replace(
            canonical,
            grant=foreign_grant,
            executable_digest=foreign_grant.executable_digest,
        )
    else:
        class DerivedVerifier(type(canonical)):
            pass

        forged = DerivedVerifier(
            grant=canonical.grant,
            runtime_id=canonical.runtime_id,
            runtime_class=canonical.runtime_class,
            security_policy_digest=canonical.security_policy_digest,
            argv=canonical.argv,
            result_relative_path=canonical.result_relative_path,
            executable_digest=canonical.executable_digest,
            code_digest=canonical.code_digest,
            input_schema_digest=canonical.input_schema_digest,
            result_schema_digest=canonical.result_schema_digest,
        )
    primary.plan = replace(canonical_plan, verifier=forged)
    before = (
        len(harness.backend.launches),
        tuple(sorted(path.name for path in harness.workspace_root.iterdir())),
        {
            path.name: path.read_bytes()
            for path in sorted(harness.lease_root.iterdir())
        },
        dict(harness.manager._snapshots),
    )

    try:
        with pytest.raises(VerifierExecutionError) as captured:
            await harness.manager.open_verifier(primary, snapshot)
    finally:
        primary.plan = canonical_plan

    assert captured.value.code == "verifier_authority_mismatch"
    assert (
        len(harness.backend.launches),
        tuple(sorted(path.name for path in harness.workspace_root.iterdir())),
        {
            path.name: path.read_bytes()
            for path in sorted(harness.lease_root.iterdir())
        },
        dict(harness.manager._snapshots),
    ) == before
    assert (await primary.close()).state is CleanupState.RELEASED




class VerifierRestartBackend(RecordingBackend):
    def __init__(self) -> None:
        super().__init__()
        self.reconciled: list[Mapping[str, Any]] = []

    async def reconcile(
        self, record: Mapping[str, Any]
    ) -> tuple[CleanupStepReceipt, ...]:
        self.reconciled.append(record)
        assert record["owner_token"]
        assert record["epoch"] == 1
        runtime_state = (
            CleanupState.RELEASED
            if "runtime_resource_id" in record
            else CleanupState.ALREADY_RELEASED
        )
        return (CleanupStepReceipt("runtime", runtime_state),)


def _restart_manager(
    harness: RuntimeHarness, backend: VerifierRestartBackend
) -> SandboxRuntimeManager:
    return SandboxRuntimeManager(
        registries=harness.fixture.registries,
        installed_authorities=harness.fixture.authorities,
        materialization_store=harness.store,
        lease_root=harness.lease_root,
        process_backend=backend,
        docker_backend=None,
        random_bytes=DeterministicRandom(40_000),
    )


@pytest.mark.parametrize("phase", ["allocating", "active"])
async def test_restart_reconciles_durable_verifier_child_before_parent(
    tmp_path: Path, phase: str
) -> None:
    harness, primary, snapshot = await _opened_snapshot(tmp_path)
    opening: asyncio.Task[Any] | None = None
    if phase == "allocating":
        harness.backend.launch_entered = asyncio.Event()
        harness.backend.release_launch = asyncio.Event()
        opening = asyncio.create_task(
            harness.manager.open_verifier(primary, snapshot)
        )
        await asyncio.wait_for(harness.backend.launch_entered.wait(), 1)
        verifier_lease_id = harness.backend.launches[-1][2].lease_id
    else:
        verifier = await harness.manager.open_verifier(primary, snapshot)
        verifier_lease_id = verifier.lease_id

    verifier_record_path = harness.lease_root / f"{verifier_lease_id}.json"
    verifier_record = harness.manager._read_lease_record(verifier_record_path)
    assert verifier_record["role"] == "verifier"
    assert verifier_record["parent_lease_id"] == primary.lease_id
    assert verifier_record["state"] == phase
    if phase == "allocating":
        assert "runtime_resource_id" not in verifier_record
    else:
        assert verifier_record["runtime_resource_id"] == "runtime-resource-test"

    recovery_backend = VerifierRestartBackend()
    recovery = _restart_manager(harness, recovery_backend)
    harness.clock.advance(minutes=5)
    harness.manager._release_lease_owner_lock(
        verifier_lease_id,
        unlink=False,
    )
    harness.manager._release_lease_owner_lock(
        primary.lease_id,
        unlink=False,
    )
    receipts = await asyncio.wait_for(recovery.reconcile_stale(), 1)

    if opening is not None:
        opening.cancel()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(opening, 1)
    assert [record["role"] for record in recovery_backend.reconciled] == [
        "verifier",
        "primary",
    ]
    assert [record["lease_id"] for record in recovery_backend.reconciled] == [
        verifier_lease_id,
        primary.lease_id,
    ]
    verifier_receipt, primary_receipt = receipts
    expected_runtime_state = (
        CleanupState.ALREADY_RELEASED
        if phase == "allocating"
        else CleanupState.RELEASED
    )
    assert verifier_receipt.steps == (
        CleanupStepReceipt("runtime", expected_runtime_state),
        CleanupStepReceipt("workspace", CleanupState.RELEASED),
        CleanupStepReceipt("cache_holder", CleanupState.ALREADY_RELEASED),
        CleanupStepReceipt("lease_record", CleanupState.RELEASED),
    )
    assert primary_receipt.steps == (
        CleanupStepReceipt(
            "child_verifier",
            CleanupState.ALREADY_RELEASED,
        ),
        CleanupStepReceipt(
            "snapshot",
            CleanupState.RELEASED,
            snapshot.snapshot_id,
        ),
        CleanupStepReceipt("runtime", CleanupState.RELEASED),
        CleanupStepReceipt("workspace", CleanupState.RELEASED),
        CleanupStepReceipt("cache_holder", CleanupState.RELEASED),
        CleanupStepReceipt("lease_record", CleanupState.RELEASED),
    )
    assert not verifier_record_path.exists()
    assert not (
        harness.lease_root / f"{primary.lease_id}.json"
    ).exists()
    assert list(harness.workspace_root.iterdir()) == []


async def test_same_manager_reconcile_skips_live_primary_and_verifier_leases(
    tmp_path: Path,
) -> None:
    harness, primary, snapshot = await _opened_snapshot(tmp_path)
    verifier = await harness.manager.open_verifier(primary, snapshot)
    primary_record = harness.lease_root / f"{primary.lease_id}.json"
    verifier_record = harness.lease_root / f"{verifier.lease_id}.json"
    harness.clock.advance(minutes=5)

    receipts = await asyncio.wait_for(harness.manager.reconcile_stale(), 1)

    assert receipts == ()
    assert primary_record.exists()
    assert verifier_record.exists()
    assert primary.state is WorkspaceLeaseState.QUIESCING
    assert verifier._closed is False
    assert harness.backend.handles[0].terminate_calls == 1
    assert harness.backend.handles[1].terminate_calls == 0

    assert (await verifier.close()).state is CleanupState.RELEASED
    assert (await primary.close()).state is CleanupState.RELEASED
    assert list(harness.workspace_root.iterdir()) == []
    assert list(harness.lease_root.iterdir()) == []



@pytest.mark.parametrize(
    ("runtime_steps", "cleanup_complete"),
    [
        (
            (
                CleanupStepReceipt("runtime_stop", CleanupState.RELEASED),
                CleanupStepReceipt("runtime_remove", CleanupState.RELEASED),
                CleanupStepReceipt("runtime_absence", CleanupState.RELEASED),
            ),
            True,
        ),
        (
            (
                CleanupStepReceipt("runtime_stop", CleanupState.RELEASED),
                CleanupStepReceipt("runtime_remove", CleanupState.FAILED),
                CleanupStepReceipt("runtime_absence", CleanupState.QUARANTINED),
            ),
            False,
        ),
        (
            (
                CleanupStepReceipt("runtime_stop", CleanupState.RELEASED),
                CleanupStepReceipt(
                    "runtime_remove",
                    CleanupState.QUARANTINED,
                    "cleanup detail incomplete",
                ),
            ),
            False,
        ),
    ],
)
async def test_verifier_open_record_failure_obeys_detailed_runtime_cleanup_proof(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    runtime_steps: tuple[CleanupStepReceipt, ...],
    cleanup_complete: bool,
) -> None:
    harness, primary, snapshot = await _opened_snapshot(tmp_path)
    harness.backend.handle_termination_receipts = runtime_steps
    primary_workspaces = set(harness.workspace_root.iterdir())
    primary_records = set(harness.lease_root.iterdir())
    write_record = harness.manager._write_lease_record
    calls = 0

    def fail_active_record(lease_id: str, payload: Mapping[str, Any]) -> None:
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError("verifier active record durability fault")
        write_record(lease_id, payload)

    monkeypatch.setattr(harness.manager, "_write_lease_record", fail_active_record)

    if cleanup_complete:
        with pytest.raises(OSError, match="verifier active record durability fault"):
            await harness.manager.open_verifier(primary, snapshot)
    else:
        with pytest.raises(SandboxFault) as captured:
            await harness.manager.open_verifier(primary, snapshot)
        assert captured.value.primary.args == (
            "verifier active record durability fault",
        )
        assert captured.value.cleanup_receipt.steps[: len(runtime_steps)] == runtime_steps

    assert calls == 2
    assert harness.backend.handles[1].terminate_calls == 1
    if cleanup_complete:
        assert set(harness.workspace_root.iterdir()) == primary_workspaces
        assert set(harness.lease_root.iterdir()) == primary_records
    else:
        assert len(set(harness.workspace_root.iterdir()) - primary_workspaces) == 1
        assert len(set(harness.lease_root.iterdir()) - primary_records) == 2

@pytest.mark.parametrize(
    "runtime_state",
    [CleanupState.FAILED, CleanupState.QUARANTINED],
)
async def test_verifier_close_retains_dependents_until_runtime_absence_is_proven(
    tmp_path: Path, runtime_state: CleanupState
) -> None:
    harness, primary, snapshot = await _opened_snapshot(tmp_path)
    verifier = await harness.manager.open_verifier(primary, snapshot)
    handle = harness.backend.handles[1]
    handle.termination_states = [runtime_state, CleanupState.RELEASED]
    result_path = (
        verifier.workspace
        / "result"
        / verifier.plan.verifier.result_relative_path
    )
    result_path.write_text("retained", encoding="utf-8")
    record_path = harness.lease_root / f"{verifier.lease_id}.json"

    first = await verifier.close()

    assert first.steps == (
        CleanupStepReceipt("runtime", runtime_state),
        CleanupStepReceipt(
            "workspace",
            CleanupState.QUARANTINED,
            "dependent runtime cleanup incomplete",
        ),
        CleanupStepReceipt(
            "snapshot",
            CleanupState.QUARANTINED,
            "dependent runtime cleanup incomplete",
        ),
        CleanupStepReceipt(
            "lease_record",
            CleanupState.QUARANTINED,
            "dependent cleanup incomplete",
        ),
    )
    assert first.state is CleanupState.QUARANTINED
    assert verifier._closed is False
    assert verifier.workspace.exists()
    assert result_path.read_text(encoding="utf-8") == "retained"
    assert record_path.exists()
    assert handle.terminate_calls == 1

    with pytest.raises(WorkspaceStateError) as captured:
        await verifier.execute()

    assert captured.value.code == "lease_not_active"
    assert handle.terminate_calls == 1

    second = await verifier.close()
    assert second.steps == (
        CleanupStepReceipt("runtime", CleanupState.RELEASED),
        CleanupStepReceipt("workspace", CleanupState.RELEASED),
        CleanupStepReceipt("snapshot", CleanupState.RELEASED),
        CleanupStepReceipt("lease_record", CleanupState.RELEASED),
    )
    assert verifier._closed is True
    assert not verifier.workspace.exists()
    assert not record_path.exists()
    assert handle.terminate_calls == 2
    assert await verifier.close() == second
    assert handle.terminate_calls == 2
    assert (await primary.close()).state is CleanupState.RELEASED
    assert list(harness.workspace_root.iterdir()) == []
    assert list(harness.lease_root.iterdir()) == []




class BlockingVerifierHandle(RecordingHandle):
    def __init__(self) -> None:
        super().__init__()
        self.entered = asyncio.Event()
        self.cancelled = False

    async def run_argv(
        self, argv: tuple[str, ...], *, timeout_ms: int, output_limit: int
    ) -> Mapping[str, Any]:
        self.argv_actions.append((tuple(argv), timeout_ms, output_limit))
        self.entered.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            self.cancelled = True
            raise
        raise AssertionError("unreachable")


class BlockingVerifierBackend(RecordingBackend):
    async def launch(self, *args: Any, **kwargs: Any) -> tuple[Any, Any]:
        handle, measurement = await super().launch(*args, **kwargs)
        if len(self.handles) == 2:
            blocking = BlockingVerifierHandle()
            self.handles[-1] = blocking
            return blocking, measurement
        return handle, measurement


async def test_verifier_cancellation_then_close_leaves_no_child_or_primary_resources(
    tmp_path: Path,
) -> None:
    fixture = make_runtime_fixture(with_writable_mount=True)
    backend = BlockingVerifierBackend()
    harness = RuntimeHarness(tmp_path, fixture, backend=backend)
    primary = await harness.manager.open(fixture.request)
    snapshot = await primary.seal_for_verifier()
    verifier = await harness.manager.open_verifier(primary, snapshot)
    handle = backend.handles[1]

    execution = asyncio.create_task(verifier.execute())
    await asyncio.wait_for(handle.entered.wait(), 1)
    execution.cancel()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(execution, 1)
    assert handle.cancelled is True

    child = await verifier.close()
    assert child.state is CleanupState.RELEASED
    parent = await primary.close()
    assert parent.state is CleanupState.RELEASED
    assert list(harness.workspace_root.iterdir()) == []


async def test_verifier_close_preempts_active_execution_and_cleans_resources(
    tmp_path: Path,
) -> None:
    fixture = make_runtime_fixture(with_writable_mount=True)
    backend = BlockingVerifierBackend()
    harness = RuntimeHarness(tmp_path, fixture, backend=backend)
    primary = await harness.manager.open(fixture.request)
    snapshot = await primary.seal_for_verifier()
    verifier = await harness.manager.open_verifier(primary, snapshot)
    handle = backend.handles[1]

    execution = asyncio.create_task(verifier.execute())
    await asyncio.wait_for(handle.entered.wait(), 1)
    child = await asyncio.wait_for(verifier.close(), 1)

    with pytest.raises(asyncio.CancelledError):
        await execution
    assert handle.cancelled is True
    assert child.state is CleanupState.RELEASED
    assert (await primary.close()).state is CleanupState.RELEASED
    assert list(harness.workspace_root.iterdir()) == []


async def test_verifier_cleanup_survives_close_caller_cancellation(
    tmp_path: Path,
) -> None:
    harness, primary, snapshot = await _opened_snapshot(tmp_path)
    verifier = await harness.manager.open_verifier(primary, snapshot)
    handle = harness.backend.handles[1]
    handle.terminate_entered = asyncio.Event()
    handle.release_terminate = asyncio.Event()

    first = asyncio.create_task(verifier.close())
    await asyncio.wait_for(handle.terminate_entered.wait(), 1)
    first.cancel()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(first, 1)

    follower = asyncio.create_task(verifier.close())
    await asyncio.sleep(0)
    assert follower.done() is False
    handle.release_terminate.set()
    receipt = await asyncio.wait_for(follower, 1)

    assert receipt.state is CleanupState.RELEASED
    assert handle.terminate_calls == 1
    assert verifier._closed is True
    assert (await primary.close()).state is CleanupState.RELEASED
    assert list(harness.workspace_root.iterdir()) == []


async def test_snapshot_rejects_inode_change_during_read_and_publishes_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fixture = make_runtime_fixture(with_writable_mount=True)
    harness = RuntimeHarness(tmp_path, fixture)
    primary = await harness.manager.open(fixture.request)
    target = primary._materialized.workspace_path / "work" / "seed.txt"
    target_metadata = target.stat()
    target_identity = (target_metadata.st_dev, target_metadata.st_ino)
    original_read = os.read
    changed = False

    def changing_read(descriptor: int, size: int) -> bytes:
        nonlocal changed
        content = original_read(descriptor, size)
        metadata = os.fstat(descriptor)
        if (metadata.st_dev, metadata.st_ino) == target_identity and not changed:
            changed = True
            with target.open("ab") as handle:
                handle.write(b"-changed")
                handle.flush()
                os.fsync(handle.fileno())
        return content

    monkeypatch.setattr(materialization_module.os, "read", changing_read)

    with pytest.raises(VerifierSnapshotError) as captured:
        await asyncio.wait_for(primary.seal_for_verifier(), 1)

    assert captured.value.code == "snapshot_race"
    assert changed is True
    assert primary.state is WorkspaceLeaseState.QUARANTINED
    assert primary._verifier_children == []
    assert list((harness.cache_root / "staging").iterdir()) == []
    assert (await primary.close()).state is CleanupState.RELEASED
