from __future__ import annotations

import asyncio
import threading
import gc
import weakref
from pathlib import Path
from typing import Any, Mapping

import pytest

from breadboard.rl.harness import (
    AtomicEpisodeRunRequest,
    BreadBoardEpisodeService,
    EpisodeCreateRequest,
    EpisodeRunRequest,
    HarnessProfileRegistry,
    HarnessTask,
    PolicyRoute,
)
from breadboard.rl.harness.service import (
    EpisodeCancelled,
    EpisodeConflict,
    EpisodeNotFound,
    HarnessEpisodeError,
)
from breadboard.rl.state.cas import FilesystemCAS, InMemoryCAS


IMAGE = "sha256:" + "f" * 64


def _profiles(*, setup_commands: tuple[str, ...] = ()) -> HarnessProfileRegistry:
    return HarnessProfileRegistry.from_mapping(
        {
            "swe": {
                "sandbox_driver": "docker",
                "network": "none",
                "max_turns": 2,
                "action_timeout_seconds": 11,
                "verifier_timeout_seconds": 13,
                "max_observation_chars": 10_000,
                "max_artifact_bytes": 10_000,
                "default_image_digest": IMAGE,
                "allowed_image_digests": [IMAGE],
                "default_verifier_ref": "tests",
                "verifier_commands": {"tests": "verify-workspace"},
                "setup_commands": list(setup_commands),
            }
        }
    )


def _create_request(episode_id: str) -> EpisodeCreateRequest:
    return EpisodeCreateRequest(
        episode_id=episode_id,
        profile="swe",
        task=HarnessTask(
            task_id=f"task-{episode_id}",
            sandbox_image_digest=IMAGE,
            verifier_ref="tests",
        ),
    )


def _atomic_request(episode_id: str) -> AtomicEpisodeRunRequest:
    return AtomicEpisodeRunRequest(
        **_create_request(episode_id).model_dump(),
        responses_create_params={"input": f"run {episode_id}", "temperature": 0.25},
        policy=PolicyRoute(base_url="http://policy.test"),
    )


def _run_request(episode_id: str, *, temperature: float = 0.25) -> EpisodeRunRequest:
    return EpisodeRunRequest(
        responses_create_params={
            "input": f"run {episode_id}",
            "temperature": temperature,
        },
        policy=PolicyRoute(base_url="http://policy.test"),
    )


class ReclaimableLease:
    def __init__(self, lease_id: str, workspace: Path) -> None:
        self.lease_id = lease_id
        self.workspace = workspace
        self.workspace.mkdir(parents=True)
        self.closed = False

    def attestation(self) -> dict[str, Any]:
        return {
            "lease_id": self.lease_id,
            "workspace_id": self.workspace.name,
            "cleanup_state": "closed" if self.closed else "active",
        }

    async def run_shell(self, command: str, *, timeout: int) -> dict[str, Any]:
        return {"exit": 0, "stdout": "verified", "stderr": ""}

    async def stat(self, path: str) -> dict[str, Any]:
        return {"path": path, "exists": False}

    async def close(self) -> dict[str, Any]:
        if not self.closed:
            self.closed = True
            self.workspace.rmdir()
        return self.attestation()


class ReclaimableLeaseManager:
    def __init__(self, workspace_root: Path) -> None:
        self.workspace_root = workspace_root
        self.open_count = 0
        self.lease_refs: list[weakref.ReferenceType[ReclaimableLease]] = []
        self.close_calls = 0

    async def open(
        self,
        *,
        episode_id: str,
        driver: str,
        image_digest: str,
        network: str,
        copy_from: Path | None = None,
    ) -> ReclaimableLease:
        lease = ReclaimableLease(
            lease_id=f"lease-{self.open_count}",
            workspace=self.workspace_root / f"workspace-{self.open_count}",
        )
        self.open_count += 1
        self.lease_refs.append(weakref.ref(lease))
        return lease

    async def close(self) -> None:
        self.close_calls += 1


class BlockingCloseLease(ReclaimableLease):
    def __init__(self, lease_id: str, workspace: Path) -> None:
        super().__init__(lease_id, workspace)
        self.close_entered = asyncio.Event()
        self.release_close = asyncio.Event()

    async def close(self) -> dict[str, Any]:
        self.close_entered.set()
        await self.release_close.wait()
        return await super().close()


class BlockingPrimaryLeaseManager:
    def __init__(self, workspace_root: Path) -> None:
        self.workspace_root = workspace_root
        self.primary: BlockingCloseLease | None = None
        self.open_count = 0

    async def open(
        self, *, episode_id: str, copy_from: Path | None = None, **_: Any
    ) -> ReclaimableLease:
        workspace = self.workspace_root / f"workspace-{self.open_count}"
        self.open_count += 1
        if copy_from is None:
            self.primary = BlockingCloseLease("primary", workspace)
            return self.primary
        return ReclaimableLease("verifier", workspace)

    async def close(self) -> None:
        return None


class CompletingPolicy:
    async def generate(self, request_payload: Mapping[str, Any]) -> dict[str, Any]:
        return {
            "id": "persisted-policy-response",
            "output": [{"type": "message", "role": "assistant", "content": "complete"}],
            "token_ids": [7, 8, 9],
        }


def _unexpected_policy(base_url: str) -> CompletingPolicy:
    raise AssertionError(f"persisted result must not rerun policy at {base_url}")


async def test_completed_and_closed_episode_results_reload_across_service_restart_without_new_leases(
    tmp_path: Path,
) -> None:
    artifact_root = tmp_path / "artifacts"
    request = _create_request("restart-episode")
    run_request = _run_request("restart-episode")
    first_manager = ReclaimableLeaseManager(tmp_path / "service-a")
    service_a = BreadBoardEpisodeService(
        profiles=_profiles(),
        lease_manager=first_manager,
        artifact_store=FilesystemCAS(artifact_root),
        policy_factory=lambda base_url: CompletingPolicy(),
    )
    await service_a.create_episode(request)

    completed_result = await service_a.run_episode("restart-episode", run_request)
    completed_snapshot = completed_result.model_dump()

    recovery_manager = ReclaimableLeaseManager(tmp_path / "service-b")
    service_b = BreadBoardEpisodeService(
        profiles=_profiles(),
        lease_manager=recovery_manager,
        artifact_store=FilesystemCAS(artifact_root),
        policy_factory=_unexpected_policy,
    )
    recovered_result = await service_b.run_episode("restart-episode", run_request)
    assert recovered_result.model_dump() == completed_snapshot
    recovered_status = await service_b.status("restart-episode")
    assert recovered_status.state == "completed"
    assert recovered_status.reason == "sandbox cleanup is not confirmed"

    conflicting_create = _create_request("restart-episode").model_copy(
        update={
            "task": HarnessTask(
                task_id="different-task",
                sandbox_image_digest=IMAGE,
                verifier_ref="tests",
            )
        }
    )
    with pytest.raises(EpisodeConflict, match="different specification"):
        await service_b.create_episode(conflicting_create)
    with pytest.raises(EpisodeConflict, match="different run request"):
        await service_b.run_episode(
            "restart-episode", _run_request("restart-episode", temperature=0.9)
        )
    assert recovery_manager.open_count == 0

    closed = await service_a.close_episode("restart-episode")
    assert closed.state == "closed"
    closed_snapshot = completed_result.model_dump()

    closed_manager = ReclaimableLeaseManager(tmp_path / "service-c")
    service_c = BreadBoardEpisodeService(
        profiles=_profiles(),
        lease_manager=closed_manager,
        artifact_store=FilesystemCAS(artifact_root),
        policy_factory=_unexpected_policy,
    )
    recreated = await service_c.create_episode(request)
    reloaded_after_close = await service_c.run_episode("restart-episode", run_request)
    assert recreated.state == "closed"
    assert reloaded_after_close.model_dump() == closed_snapshot
    assert closed_manager.open_count == 0


async def test_close_only_reports_closed_after_workspace_cleanup_finishes(
    tmp_path: Path,
) -> None:
    manager = BlockingPrimaryLeaseManager(tmp_path / "leases")
    artifact_store = InMemoryCAS()
    service = BreadBoardEpisodeService(
        profiles=_profiles(),
        lease_manager=manager,
        artifact_store=artifact_store,
        policy_factory=lambda base_url: CompletingPolicy(),
    )
    request = _create_request("cleanup-gated-close")
    await service.create_episode(request)
    await service.run_episode(
        "cleanup-gated-close", _run_request("cleanup-gated-close")
    )
    assert manager.primary is not None

    close_task = asyncio.create_task(service.close_episode("cleanup-gated-close"))
    await manager.primary.close_entered.wait()
    await asyncio.sleep(0)

    assert close_task.done() is False
    assert (await service.status("cleanup-gated-close")).state == "completed"
    manager.primary.release_close.set()
    closed = await close_task

    assert closed.state == "closed"
    restarted = BreadBoardEpisodeService(
        profiles=_profiles(),
        lease_manager=ReclaimableLeaseManager(tmp_path / "restarted"),
        artifact_store=artifact_store,
        policy_factory=_unexpected_policy,
    )
    assert (await restarted.create_episode(request)).state == "closed"


async def test_cancel_completed_episode_finalizes_closed_recovery_without_reopening_lease(
    tmp_path: Path,
) -> None:
    artifact_store = InMemoryCAS()
    manager = ReclaimableLeaseManager(tmp_path / "live")
    service = BreadBoardEpisodeService(
        profiles=_profiles(),
        lease_manager=manager,
        artifact_store=artifact_store,
        policy_factory=lambda base_url: CompletingPolicy(),
    )
    request = _create_request("cancel-completed")
    run_request = _run_request("cancel-completed")
    await service.create_episode(request)
    completed_result = await service.run_episode("cancel-completed", run_request)
    opens_before_cancel = manager.open_count

    cancelled = await service.cancel_episode("cancel-completed", "operator cleanup")

    assert cancelled.state == "closed"
    assert cancelled.reason == "operator cleanup"
    assert (await service.status("cancel-completed")).state == "closed"
    assert (await service.create_episode(request)).state == "closed"
    reloaded_result = await service.run_episode("cancel-completed", run_request)
    assert reloaded_result.model_dump() == completed_result.model_dump()
    assert manager.open_count == opens_before_cancel

    restarted_manager = ReclaimableLeaseManager(tmp_path / "restarted-cancel")
    restarted = BreadBoardEpisodeService(
        profiles=_profiles(),
        lease_manager=restarted_manager,
        artifact_store=artifact_store,
        policy_factory=_unexpected_policy,
    )
    assert (await restarted.status("cancel-completed")).state == "closed"
    assert restarted_manager.open_count == 0


class EventLoopRejectingCAS(InMemoryCAS):
    def __init__(self) -> None:
        super().__init__()
        self.event_loop_thread = threading.get_ident()

    def _assert_background_thread(self, operation: str) -> None:
        if threading.get_ident() == self.event_loop_thread:
            raise AssertionError(f"{operation} blocked the async service thread")

    def put_bytes(self, data: bytes, **kwargs: Any):
        self._assert_background_thread("put_bytes")
        return super().put_bytes(data, **kwargs)

    def get_ref(self, artifact_id: str):
        self._assert_background_thread("get_ref")
        return super().get_ref(artifact_id)

    def get_bytes(self, artifact_ref: Any) -> bytes:
        self._assert_background_thread("get_bytes")
        return super().get_bytes(artifact_ref)

    def has(self, artifact_ref: Any) -> bool:
        self._assert_background_thread("has")
        return super().has(artifact_ref)


async def test_async_episode_lifecycle_offloads_durable_cas_operations(
    tmp_path: Path,
) -> None:
    artifact_store = EventLoopRejectingCAS()
    service = BreadBoardEpisodeService(
        profiles=_profiles(),
        lease_manager=ReclaimableLeaseManager(tmp_path / "writer"),
        artifact_store=artifact_store,
        policy_factory=lambda base_url: CompletingPolicy(),
    )
    request = _atomic_request("offloaded-cas")

    written_result = await service.run_atomic(request)

    restarted = BreadBoardEpisodeService(
        profiles=_profiles(),
        lease_manager=ReclaimableLeaseManager(tmp_path / "reader"),
        artifact_store=artifact_store,
        policy_factory=_unexpected_policy,
    )
    loaded_result = await restarted.run_episode(
        "offloaded-cas", _run_request("offloaded-cas")
    )
    assert loaded_result.model_dump() == written_result.model_dump()


class SlowLookupCAS(InMemoryCAS):
    def __init__(self) -> None:
        super().__init__()
        self.lookup_started = threading.Event()
        self.release_lookup = threading.Event()

    def has(self, artifact_ref: Any) -> bool:
        artifact_id = (
            artifact_ref.artifact_id
            if hasattr(artifact_ref, "artifact_id")
            else str(artifact_ref)
        )
        if artifact_id.startswith("slow-missing:"):
            self.lookup_started.set()
            self.release_lookup.wait()
        return super().has(artifact_ref)


async def test_slow_tombstone_lookup_does_not_block_unrelated_cached_status(
    tmp_path: Path,
) -> None:
    artifact_store = SlowLookupCAS()
    service = BreadBoardEpisodeService(
        profiles=_profiles(),
        lease_manager=ReclaimableLeaseManager(tmp_path),
        artifact_store=artifact_store,
        policy_factory=lambda base_url: CompletingPolicy(),
    )
    await service.run_atomic(_atomic_request("cached-episode"))
    slow_status = asyncio.create_task(service.status("slow-missing"))
    await asyncio.wait_for(
        asyncio.to_thread(artifact_store.lookup_started.wait), timeout=1
    )

    try:
        cached_status = asyncio.create_task(service.status("cached-episode"))
        await asyncio.sleep(0)
        assert cached_status.done() is True
        assert (await cached_status).state == "closed"
    finally:
        artifact_store.release_lookup.set()

    with pytest.raises(EpisodeNotFound):
        await slow_status


class RecordingInMemoryCAS(InMemoryCAS):
    def __init__(self) -> None:
        super().__init__()
        self.get_bytes_calls: list[str] = []

    def get_bytes(self, artifact_ref: Any) -> bytes:
        artifact_id = (
            artifact_ref.artifact_id
            if hasattr(artifact_ref, "artifact_id")
            else str(artifact_ref)
        )
        self.get_bytes_calls.append(artifact_id)
        return super().get_bytes(artifact_ref)


async def test_closed_results_reload_idempotently_with_bounded_tombstones_and_no_lease_retention(
    tmp_path: Path,
) -> None:
    lease_manager = ReclaimableLeaseManager(tmp_path)
    artifact_store = RecordingInMemoryCAS()
    service = BreadBoardEpisodeService(
        profiles=_profiles(),
        lease_manager=lease_manager,
        artifact_store=artifact_store,
        policy_factory=lambda base_url: CompletingPolicy(),
        tombstone_limit=2,
    )
    first_request = _atomic_request("episode-1")

    first_result = await service.run_atomic(first_request)
    opens_after_first_run = lease_manager.open_count
    first_result.response["id"] = "caller-mutated-value"
    reloaded_result = await service.run_atomic(first_request)

    assert reloaded_result.response["id"] == "persisted-policy-response"
    assert reloaded_result.response["token_ids"] == [7, 8, 9]
    assert lease_manager.open_count == opens_after_first_run

    await service.run_atomic(_atomic_request("episode-2"))
    await service.run_atomic(_atomic_request("episode-3"))

    artifact_store.get_bytes_calls.clear()
    assert (await service.status("episode-1")).state == "closed"
    assert "episode-1:episode-tombstone" in artifact_store.get_bytes_calls
    assert (await service.status("episode-2")).state == "closed"
    assert (await service.status("episode-3")).state == "closed"

    del first_result, reloaded_result
    await asyncio.sleep(0)
    gc.collect()
    retained_lease_ids = [
        lease.lease_id
        for reference in lease_manager.lease_refs
        if (lease := reference())
    ]
    assert retained_lease_ids == []


class CreateLookupCAS(InMemoryCAS):
    def __init__(self, episode_id: str) -> None:
        super().__init__()
        self.closed_tombstone_id = f"{episode_id}:episode-tombstone"
        self.closed_lookup_count = 0
        self._count_lock = threading.Lock()

    def has(self, artifact_ref: Any) -> bool:
        artifact_id = (
            artifact_ref.artifact_id
            if hasattr(artifact_ref, "artifact_id")
            else str(artifact_ref)
        )
        result = super().has(artifact_ref)
        if artifact_id == self.closed_tombstone_id:
            with self._count_lock:
                self.closed_lookup_count += 1
        return result


class SetupLease:
    def __init__(
        self,
        workspace: Path,
        setup_entered: asyncio.Event,
        release_setup: asyncio.Event,
        setup_result: dict[str, Any],
    ) -> None:
        self.workspace = workspace
        self.closed = False
        self.cleanup_count = 0
        self.setup_entered = setup_entered
        self.release_setup = release_setup
        self.setup_result = setup_result

    def attestation(self) -> dict[str, Any]:
        return {
            "workspace_id": self.workspace.name,
            "cleanup_state": "closed" if self.closed else "active",
        }

    async def run_shell(self, command: str, *, timeout: int) -> dict[str, Any]:
        self.setup_entered.set()
        await self.release_setup.wait()
        return dict(self.setup_result)

    async def close(self) -> dict[str, Any]:
        if not self.closed:
            self.closed = True
            self.cleanup_count += 1
        return self.attestation()


class SetupLeaseManager:
    def __init__(self, tmp_path: Path, setup_result: dict[str, Any]) -> None:
        self.open_count = 0
        self.setup_entered = asyncio.Event()
        self.release_setup = asyncio.Event()
        self.lease = SetupLease(
            tmp_path / "setup-workspace",
            self.setup_entered,
            self.release_setup,
            setup_result,
        )

    async def open(self, **_: Any) -> SetupLease:
        self.open_count += 1
        return self.lease

    async def close(self) -> None:
        return None


async def _start_duplicate_create(
    service: BreadBoardEpisodeService,
    request: EpisodeCreateRequest,
    started: asyncio.Event,
):
    started.set()
    return await service.create_episode(request)


async def test_concurrent_identical_create_waits_for_setup_and_shares_one_ready_episode(
    tmp_path: Path,
) -> None:
    lease_manager = SetupLeaseManager(
        tmp_path,
        {"exit": 0, "stdout": "setup complete", "stderr": ""},
    )
    artifact_store = CreateLookupCAS("concurrent-episode")
    service = BreadBoardEpisodeService(
        profiles=_profiles(setup_commands=("prepare-workspace",)),
        lease_manager=lease_manager,
        artifact_store=artifact_store,
    )
    request = _create_request("concurrent-episode")
    first = asyncio.create_task(service.create_episode(request))
    await lease_manager.setup_entered.wait()
    duplicate_started = asyncio.Event()
    duplicate = asyncio.create_task(
        _start_duplicate_create(service, request, duplicate_started)
    )
    await duplicate_started.wait()
    await asyncio.sleep(0)

    assert duplicate.done() is False
    lease_manager.release_setup.set()
    first_result, duplicate_result = await asyncio.gather(first, duplicate)

    assert first_result.state == duplicate_result.state == "ready"
    assert first_result.sandbox_attestation == duplicate_result.sandbox_attestation
    assert lease_manager.open_count == 1
    assert artifact_store.closed_lookup_count == 1
    await service.close_episode("concurrent-episode")


async def test_setup_failure_reaches_all_concurrent_creators_and_closes_partial_workspace(
    tmp_path: Path,
) -> None:
    lease_manager = SetupLeaseManager(
        tmp_path,
        {"exit": 23, "stdout": "", "stderr": "dependency unavailable"},
    )
    artifact_store = CreateLookupCAS("failing-setup")
    service = BreadBoardEpisodeService(
        profiles=_profiles(setup_commands=("prepare-workspace",)),
        lease_manager=lease_manager,
        artifact_store=artifact_store,
    )
    request = _create_request("failing-setup")
    first = asyncio.create_task(service.create_episode(request))
    await lease_manager.setup_entered.wait()
    duplicate_started = asyncio.Event()
    duplicate = asyncio.create_task(
        _start_duplicate_create(service, request, duplicate_started)
    )
    await duplicate_started.wait()
    await asyncio.sleep(0)
    assert duplicate.done() is False

    lease_manager.release_setup.set()
    outcomes = await asyncio.gather(first, duplicate, return_exceptions=True)

    assert all(isinstance(outcome, HarnessEpisodeError) for outcome in outcomes)
    assert {str(outcome) for outcome in outcomes} == {
        "profile setup command failed with exit 23: dependency unavailable"
    }
    assert lease_manager.open_count == 1
    assert artifact_store.closed_lookup_count == 1
    assert lease_manager.lease.closed is True
    assert lease_manager.lease.cleanup_count == 1
    with pytest.raises(EpisodeNotFound):
        await service.status("failing-setup")


async def test_cancel_during_final_setup_publish_race_rejects_all_creators_and_closes_lease(
    tmp_path: Path,
) -> None:
    lease_manager = SetupLeaseManager(
        tmp_path,
        {"exit": 0, "stdout": "setup complete", "stderr": ""},
    )
    artifact_store = CreateLookupCAS("cancelled-during-setup")
    service = BreadBoardEpisodeService(
        profiles=_profiles(setup_commands=("prepare-workspace",)),
        lease_manager=lease_manager,
        artifact_store=artifact_store,
    )
    request = _create_request("cancelled-during-setup")
    first = asyncio.create_task(service.create_episode(request))
    await lease_manager.setup_entered.wait()
    duplicate_started = asyncio.Event()
    duplicate = asyncio.create_task(
        _start_duplicate_create(service, request, duplicate_started)
    )
    await duplicate_started.wait()
    await asyncio.sleep(0)
    assert duplicate.done() is False

    cancellation = await service.cancel_episode(
        "cancelled-during-setup", "operator cancelled setup"
    )
    assert cancellation.state == "cancel_requested"
    lease_manager.release_setup.set()
    outcomes = await asyncio.gather(first, duplicate, return_exceptions=True)

    assert all(isinstance(outcome, EpisodeCancelled) for outcome in outcomes)
    assert {str(outcome) for outcome in outcomes} == {"operator cancelled setup"}
    assert artifact_store.closed_lookup_count == 2
    assert lease_manager.lease.closed is True
    assert lease_manager.lease.cleanup_count == 1
    with pytest.raises(EpisodeNotFound):
        await service.status("cancelled-during-setup")


class DelayedActiveRecoveryCAS(InMemoryCAS):
    def __init__(self) -> None:
        super().__init__()
        self.active_write_started = threading.Event()
        self.release_active_write = threading.Event()

    def put_bytes(self, data: bytes, **kwargs: Any):
        artifact_id = str(kwargs.get("artifact_id") or "")
        if artifact_id.endswith(":episode-tombstone-completed"):
            self.active_write_started.set()
            self.release_active_write.wait()
        return super().put_bytes(data, **kwargs)


@pytest.mark.parametrize("terminal_action", ["cancel", "close"])
async def test_late_active_recovery_write_cannot_downgrade_closed_lifecycle(
    tmp_path: Path,
    terminal_action: str,
) -> None:
    episode_id = f"monotonic-{terminal_action}"
    artifact_store = DelayedActiveRecoveryCAS()
    service = BreadBoardEpisodeService(
        profiles=_profiles(),
        lease_manager=ReclaimableLeaseManager(tmp_path / terminal_action),
        artifact_store=artifact_store,
        policy_factory=lambda base_url: CompletingPolicy(),
    )
    request = _create_request(episode_id)
    await service.create_episode(request)
    run_task = asyncio.create_task(
        service.run_episode(episode_id, _run_request(episode_id))
    )
    await asyncio.wait_for(
        asyncio.to_thread(artifact_store.active_write_started.wait), timeout=1
    )

    try:
        if terminal_action == "cancel":
            terminal = await service.cancel_episode(episode_id, "operator finalized")
        else:
            terminal = await service.close_episode(episode_id)
        assert terminal.state == "closed"
        assert (await service.status(episode_id)).state == "closed"
    finally:
        artifact_store.release_active_write.set()

    await run_task
    assert (await service.status(episode_id)).state == "closed"
    assert (await service.create_episode(request)).state == "closed"
    assert artifact_store.has(f"{episode_id}:episode-tombstone")


class BlockingOwnerLookupCAS(InMemoryCAS):
    def __init__(self, episode_id: str) -> None:
        super().__init__()
        self.closed_tombstone_id = f"{episode_id}:episode-tombstone"
        self.block_lookup = False
        self.lookup_started = threading.Event()
        self.release_lookup = threading.Event()

    def has(self, artifact_ref: Any) -> bool:
        artifact_id = (
            artifact_ref.artifact_id
            if hasattr(artifact_ref, "artifact_id")
            else str(artifact_ref)
        )
        if self.block_lookup and artifact_id == self.closed_tombstone_id:
            self.lookup_started.set()
            self.release_lookup.wait()
        return super().has(artifact_ref)


async def test_service_close_cancels_owner_blocked_on_durable_tombstone_lookup(
    tmp_path: Path,
) -> None:
    episode_id = "shutdown-during-lookup"
    artifact_store = BlockingOwnerLookupCAS(episode_id)
    request = _atomic_request(episode_id)
    seed_service = BreadBoardEpisodeService(
        profiles=_profiles(),
        lease_manager=ReclaimableLeaseManager(tmp_path / "seed"),
        artifact_store=artifact_store,
        policy_factory=lambda base_url: CompletingPolicy(),
    )
    await seed_service.run_atomic(request)
    artifact_store.block_lookup = True

    manager = ReclaimableLeaseManager(tmp_path / "restarted")
    service = BreadBoardEpisodeService(
        profiles=_profiles(),
        lease_manager=manager,
        artifact_store=artifact_store,
        policy_factory=_unexpected_policy,
    )
    owner = asyncio.create_task(service.create_episode(_create_request(episode_id)))
    await asyncio.wait_for(
        asyncio.to_thread(artifact_store.lookup_started.wait), timeout=1
    )

    await service.close()
    artifact_store.release_lookup.set()

    with pytest.raises(EpisodeCancelled, match="shutting down"):
        await owner
    assert manager.open_count == 0
    assert manager.close_calls == 1
