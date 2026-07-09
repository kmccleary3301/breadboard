from __future__ import annotations

from pathlib import Path

import pytest
import ray

from breadboard.rl.harness import sandbox as sandbox_module
from breadboard.rl.harness.sandbox import (
    SandboxCleanupError,
    SandboxLease,
    SandboxLeaseManager,
)


IMAGE = "sha256:" + "9" * 64


async def test_actor_kill_failure_is_reported_as_failed_cleanup_not_a_closed_attestation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    actor = object()
    lease = SandboxLease(
        lease_id="lease-kill-failure",
        actor=actor,
        workspace=workspace,
        driver="process",
        image_digest=IMAGE,
        network="host",
    )

    def fail_kill(actor_to_kill: object, *, no_restart: bool) -> None:
        assert actor_to_kill is actor
        assert no_restart is True
        raise RuntimeError("actor control plane unavailable")

    monkeypatch.setattr(ray, "kill", fail_kill)

    with pytest.raises(
        SandboxCleanupError, match="actor termination failed.*control plane unavailable"
    ):
        await lease.close()

    attestation = lease.attestation()
    assert lease.closed is False
    assert lease.actor is actor
    assert attestation["cleanup_state"] == "failed"
    assert "actor termination failed" in attestation["cleanup_error"]
    assert not workspace.exists()


async def test_verifier_workspace_copy_failure_propagates_and_removes_partial_workspace(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace_root = tmp_path / "workspaces"
    source = tmp_path / "primary"
    source.mkdir()
    (source / "task.txt").write_text("primary state", encoding="utf-8")
    manager = SandboxLeaseManager(workspace_root)
    sandbox_started = False

    def fail_copy(
        source_path: Path, destination_path: Path, *, dirs_exist_ok: bool
    ) -> None:
        assert source_path == source
        assert destination_path.parent == workspace_root
        assert dirs_exist_ok is True
        raise OSError("snapshot copy denied")

    def record_sandbox_start(launch_spec: object) -> None:
        nonlocal sandbox_started
        sandbox_started = True

    monkeypatch.setattr(ray, "is_initialized", lambda: True)
    monkeypatch.setattr(sandbox_module.shutil, "copytree", fail_copy)
    monkeypatch.setattr(sandbox_module, "create_sandbox", record_sandbox_start)

    with pytest.raises(OSError, match="snapshot copy denied"):
        await manager.open(
            episode_id="episode-verifier",
            driver="process",
            image_digest=IMAGE,
            network="host",
            copy_from=source,
        )

    assert sandbox_started is False
    assert list(workspace_root.iterdir()) == []
    await manager.close()
