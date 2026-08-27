from __future__ import annotations

import os
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from breadboard.product.cli import artifact as cli_artifact
from breadboard.product.runtime.artifacts import ArtifactStore
from breadboard_engine.api.cli_bridge.app import create_app


def _client(monkeypatch, workspace: Path) -> TestClient:
    monkeypatch.setenv("BREADBOARD_PUBLIC_WORKSPACE", str(workspace))
    monkeypatch.setenv("BREADBOARD_ENABLE_E4_API", "0")
    monkeypatch.setenv("BREADBOARD_ENABLE_PUBLIC_API", "1")
    monkeypatch.setenv("RAY_SCE_LOCAL_MODE", "1")
    return TestClient(create_app(include_atp_routes=False))


def test_artifact_family_reads_and_verifies_immutable_store(
    monkeypatch,
    tmp_path: Path,
) -> None:
    content = b"public artifact"
    reference = ArtifactStore(tmp_path / ".breadboard/artifacts").put(
        content,
        media_type="text/plain",
    )
    artifact_path = (
        tmp_path
        / ".breadboard"
        / "artifacts"
        / "sha256"
        / reference.digest.removeprefix("sha256:")[:2]
        / reference.digest.removeprefix("sha256:")
    )
    expected_artifact = {
        "digest": reference.digest,
        "size_bytes": len(content),
        "media_type": "application/octet-stream",
    }
    client = _client(monkeypatch, tmp_path)

    listing = client.get("/v1/artifacts")
    assert listing.status_code == 200
    assert listing.json() == {
        "schema_version": "bb.cli.result.v1",
        "ok": True,
        "status": "ok",
        "command": ["artifact", "list"],
        "record_refs": [".breadboard/artifacts/sha256"],
        "hashes": {},
        "stage_outcomes": [
            {
                "stage": "artifact.list",
                "status": "passed",
                "report_ref": None,
                "next_action": None,
            }
        ],
        "warnings": [],
        "next_actions": [],
        "error": None,
        "exit_code": 0,
        "data": {"artifacts": [expected_artifact], "count": 1},
    }
    cli_arguments = SimpleNamespace(workspace=tmp_path, REF=reference.digest)
    assert cli_artifact.list_artifacts(cli_arguments).as_dict() == listing.json()
    records_before_reads = listing.json()["data"]["artifacts"]
    content_before_reads = artifact_path.read_bytes()

    fetched = client.get(f"/v1/artifacts/{reference.digest}")
    assert fetched.status_code == 200
    assert fetched.json()["data"] == {
        "artifact": expected_artifact,
        "bytes": len(content),
    }
    assert cli_artifact.get(cli_arguments).as_dict() == fetched.json()

    verified = client.post(f"/v1/artifacts/{reference.digest}/verify")
    assert verified.status_code == 200
    assert verified.json() == {
        "schema_version": "bb.cli.result.v1",
        "ok": True,
        "status": "ok",
        "command": ["artifact", "verify"],
        "record_refs": [],
        "hashes": {"artifact": reference.digest},
        "stage_outcomes": [
            {
                "stage": "artifact.verify",
                "status": "passed",
                "report_ref": None,
                "next_action": None,
            }
        ],
        "warnings": [],
        "next_actions": [],
        "error": None,
        "exit_code": 0,
        "data": {"artifact": expected_artifact, "verified": True},
    }
    assert cli_artifact.verify(cli_arguments).as_dict() == verified.json()

    records_after_reads = client.get("/v1/artifacts").json()["data"]["artifacts"]
    assert records_after_reads == records_before_reads
    assert artifact_path.read_bytes() == content_before_reads


def test_invalid_artifact_reference_uses_stable_problem(
    monkeypatch,
    tmp_path: Path,
) -> None:
    response = _client(monkeypatch, tmp_path).get("/v1/artifacts/not-a-digest")
    assert response.status_code == 422
    assert response.json()["error"]["schema_version"] == "bb.problem.v1"
    assert response.json()["error"]["error_code"] == "invalid_state"


@pytest.mark.skipif(
    os.name == "nt",
    reason="symlink creation is not reliably available on Windows CI",
)
def test_artifact_operations_reject_digest_directory_symlinks(
    monkeypatch,
    tmp_path: Path,
) -> None:
    outside = tmp_path.parent / f"{tmp_path.name}-outside-artifacts"
    outside.mkdir()
    digest = "a" * 64
    (outside / digest).write_bytes(b"outside store content")
    sha = tmp_path / ".breadboard/artifacts/sha256"
    sha.mkdir(parents=True)
    (sha / "aa").symlink_to(outside, target_is_directory=True)
    client = _client(monkeypatch, tmp_path)

    listing = client.get("/v1/artifacts")
    assert listing.status_code == 200
    assert listing.json()["data"]["artifacts"] == []

    fetched = client.get(f"/v1/artifacts/sha256:{digest}")
    verified = client.post(f"/v1/artifacts/sha256:{digest}/verify")
    assert fetched.status_code == 404
    assert fetched.json()["error"]["error_code"] == "path_unavailable"
    assert verified.status_code == 404
    assert verified.json()["error"]["error_code"] == "path_unavailable"
