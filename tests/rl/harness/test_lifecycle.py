from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import ray
from fastapi.testclient import TestClient

from breadboard.rl.harness import (
    BreadBoardEpisodeService,
    EpisodeCreateRequest,
    HarnessProfileRegistry,
    HarnessTask,
)
from breadboard.rl.harness.api import create_app
from breadboard.rl.harness import sandbox as sandbox_module
from breadboard.rl.harness.sandbox import SandboxLeaseManager
from breadboard.rl.state.cas import InMemoryCAS


IMAGE = "sha256:" + "d" * 64


def _profiles() -> HarnessProfileRegistry:
    return HarnessProfileRegistry.from_mapping(
        {
            "swe": {
                "sandbox_driver": "docker",
                "default_image_digest": IMAGE,
                "allowed_image_digests": [IMAGE],
                "default_verifier_ref": "tests",
                "verifier_commands": {"tests": "run-project-verifier"},
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


class LifecycleLease:
    def __init__(self, lease_id: str, workspace: Path) -> None:
        self.lease_id = lease_id
        self.workspace = workspace
        self.closed = False

    def attestation(self) -> dict[str, Any]:
        return {
            "lease_id": self.lease_id,
            "workspace_id": self.workspace.name,
            "cleanup_state": "closed" if self.closed else "active",
        }

    async def close(self) -> dict[str, Any]:
        self.closed = True
        return self.attestation()


class LifecycleLeaseManager:
    def __init__(self, workspace_root: Path) -> None:
        self.workspace_root = workspace_root
        self.leases: list[LifecycleLease] = []
        self.close_calls = 0
        self.closed_lease_ids_at_close: set[str] = set()

    async def open(
        self,
        *,
        episode_id: str,
        driver: str,
        image_digest: str,
        network: str,
        copy_from: Path | None = None,
    ) -> LifecycleLease:
        lease = LifecycleLease(episode_id, self.workspace_root / episode_id)
        self.leases.append(lease)
        return lease

    async def close(self) -> None:
        self.close_calls += 1
        self.closed_lease_ids_at_close = {
            lease.lease_id for lease in self.leases if lease.closed
        }


async def test_manager_close_shuts_manager_owned_local_ray_exactly_once(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    if ray.is_initialized():
        pytest.skip("requires a process without an existing Ray connection")

    original_shutdown = ray.shutdown
    shutdown_calls = 0

    def recording_shutdown() -> None:
        nonlocal shutdown_calls
        shutdown_calls += 1
        original_shutdown()

    monkeypatch.delenv("BREADBOARD_HARNESS_RAY_ADDRESS", raising=False)
    monkeypatch.setattr(sandbox_module, "create_sandbox", lambda launch_spec: None)
    monkeypatch.setattr(ray, "shutdown", recording_shutdown)
    manager = SandboxLeaseManager(tmp_path)
    lease = None

    try:
        lease = await manager.open(
            episode_id="owned-ray",
            driver="process",
            image_digest=IMAGE,
            network="host",
        )
        assert ray.is_initialized()
        await lease.close()

        await manager.close()
        await manager.close()

        assert shutdown_calls == 1
        assert not ray.is_initialized()
    finally:
        if lease is not None and not lease.closed:
            await lease.close()
        await manager.close()
        if ray.is_initialized():
            original_shutdown()


async def test_manager_close_preserves_externally_initialized_ray(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    if ray.is_initialized():
        pytest.skip("requires a process without an existing Ray connection")

    monkeypatch.delenv("BREADBOARD_HARNESS_RAY_ADDRESS", raising=False)
    monkeypatch.setattr(sandbox_module, "create_sandbox", lambda launch_spec: None)
    ray.init(address=None, include_dashboard=False)
    manager = SandboxLeaseManager(tmp_path)
    lease = None

    try:
        lease = await manager.open(
            episode_id="external-ray",
            driver="process",
            image_digest=IMAGE,
            network="host",
        )
        await lease.close()

        await manager.close()

        assert ray.is_initialized()
    finally:
        if lease is not None and not lease.closed:
            await lease.close()
        await manager.close()
        if ray.is_initialized():
            ray.shutdown()


async def test_service_close_cancels_active_episodes_closes_leases_then_manager(
    tmp_path: Path,
) -> None:
    lease_manager = LifecycleLeaseManager(tmp_path)
    service = BreadBoardEpisodeService(
        profiles=_profiles(), lease_manager=lease_manager, artifact_store=InMemoryCAS()
    )
    await service.create_episode(_create_request("episode-1"))
    await service.create_episode(_create_request("episode-2"))

    await service.close()

    first_status = await service.status("episode-1")
    second_status = await service.status("episode-2")
    assert (first_status.state, first_status.reason) == (
        "cancelled",
        "BreadBoard episode service is shutting down",
    )
    assert (second_status.state, second_status.reason) == (
        "cancelled",
        "BreadBoard episode service is shutting down",
    )
    assert all(lease.closed for lease in lease_manager.leases)
    assert lease_manager.closed_lease_ids_at_close == {"episode-1", "episode-2"}
    assert lease_manager.close_calls == 1


def test_fastapi_lifespan_closes_episode_service_manager(tmp_path: Path) -> None:
    lease_manager = LifecycleLeaseManager(tmp_path)
    service = BreadBoardEpisodeService(
        profiles=_profiles(), lease_manager=lease_manager, artifact_store=InMemoryCAS()
    )
    app = create_app(
        service=service, auth_token="", allow_unauthenticated_loopback=True
    )

    with TestClient(app) as client:
        response = client.get("/healthz")
        assert response.status_code == 200
        assert lease_manager.close_calls == 0

    assert lease_manager.close_calls == 1
