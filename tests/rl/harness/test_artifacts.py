from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from breadboard.rl.harness import BreadBoardEpisodeService, HarnessProfileRegistry
from breadboard.rl.harness.api import create_app
from breadboard.rl.state.cas import (
    ArtifactConflictError,
    ArtifactIntegrityError,
    FilesystemCAS,
)


IMAGE = "sha256:" + "e" * 64


def _profiles() -> HarnessProfileRegistry:
    return HarnessProfileRegistry.from_mapping(
        {
            "swe": {
                "sandbox_driver": "docker",
                "network": "none",
                "default_image_digest": IMAGE,
                "allowed_image_digests": [IMAGE],
                "default_verifier_ref": "tests",
                "verifier_commands": {"tests": "run-project-verifier"},
            }
        }
    )


class ClosedOnlyLeaseManager:
    def __init__(self) -> None:
        self.close_calls = 0

    async def open(self, **_: Any) -> None:
        raise AssertionError("artifact retrieval must not allocate a sandbox")

    async def close(self) -> None:
        self.close_calls += 1


def _artifact_service(
    root: Path,
) -> tuple[BreadBoardEpisodeService, ClosedOnlyLeaseManager]:
    manager = ClosedOnlyLeaseManager()
    service = BreadBoardEpisodeService(
        profiles=_profiles(),
        lease_manager=manager,
        artifact_store=FilesystemCAS(root),
    )
    return service, manager


def test_filesystem_cas_survives_reopen_and_rejects_mutating_an_artifact_id(
    tmp_path: Path,
) -> None:
    root = tmp_path / "cas"
    first_store = FilesystemCAS(root)
    first_ref = first_store.put_bytes(
        b"stable artifact bytes",
        artifact_id="episode-7:trajectory",
        media_type="application/json",
        metadata={"kind": "trajectory", "episode_id": "episode-7"},
    )

    reopened_store = FilesystemCAS(root)
    reopened_ref = reopened_store.get_ref("episode-7:trajectory")

    assert reopened_ref == first_ref
    assert reopened_store.get_bytes(reopened_ref) == b"stable artifact bytes"
    with pytest.raises(ArtifactConflictError, match="CAS artifact overwrite rejected"):
        reopened_store.put_bytes(b"different bytes", artifact_id="episode-7:trajectory")


def test_filesystem_cas_detects_corrupted_persisted_blob_bytes(tmp_path: Path) -> None:
    root = tmp_path / "cas"
    store = FilesystemCAS(root)
    ref = store.put_bytes(b"verified payload", artifact_id="artifact-with-integrity")
    blob_path = root / "blobs" / ref.sha256.removeprefix("sha256:")
    blob_path.write_bytes(b"tampered payload")

    with pytest.raises(
        ArtifactIntegrityError, match="CAS artifact integrity check failed"
    ):
        FilesystemCAS(root).get_bytes("artifact-with-integrity")


def test_harness_app_requires_explicit_unauthenticated_loopback_opt_in(
    tmp_path: Path,
) -> None:
    service, _ = _artifact_service(tmp_path / "cas")

    with pytest.raises(RuntimeError, match="explicitly enabled"):
        create_app(service=service, auth_token="")


def test_authenticated_artifact_get_reads_reopened_store_at_result_retrieval_path(
    tmp_path: Path,
) -> None:
    root = tmp_path / "cas"
    writer = FilesystemCAS(root)
    ref = writer.put_bytes(
        b'{"termination_reason":"submitted"}',
        artifact_id="episode-7:trajectory",
        media_type="application/json",
        metadata={"kind": "breadboard_harness_trajectory"},
    )
    service, manager = _artifact_service(root)
    app = create_app(service=service, auth_token="artifact-secret")
    retrieval_path = "/v1/artifacts/episode-7%3Atrajectory"

    with TestClient(app) as client:
        unauthorized = client.get(retrieval_path)
        authorized = client.get(
            retrieval_path,
            headers={"Authorization": "Bearer artifact-secret"},
        )

    assert unauthorized.status_code == 401
    assert authorized.status_code == 200
    assert authorized.content == b'{"termination_reason":"submitted"}'
    assert authorized.headers["content-type"] == "application/json"
    assert authorized.headers["etag"] == f'"{ref.sha256}"'
    assert authorized.headers["x-breadboard-artifact-sha256"] == ref.sha256
    assert manager.close_calls == 1


def test_artifact_integrity_corruption_maps_to_server_storage_failure(
    tmp_path: Path,
) -> None:
    root = tmp_path / "cas"
    writer = FilesystemCAS(root)
    ref = writer.put_bytes(b"trusted bytes", artifact_id="corrupt-artifact")
    (root / "blobs" / ref.sha256.removeprefix("sha256:")).write_bytes(
        b"corrupted bytes"
    )
    service, manager = _artifact_service(root)
    app = create_app(service=service, auth_token="artifact-secret")

    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.get(
            "/v1/artifacts/corrupt-artifact",
            headers={"Authorization": "Bearer artifact-secret"},
        )

    assert response.status_code == 502
    assert "integrity" in response.json()["detail"].lower()
    assert manager.close_calls == 1
