from __future__ import annotations
from pathlib import Path
from types import SimpleNamespace
from fastapi.testclient import TestClient
from agentic_coder_prototype.api.cli_bridge.app import create_app
from breadboard.product.cli import artifact as artifact_operations
from breadboard.product.runtime.artifacts import ArtifactStore
def _client(monkeypatch, workspace: Path) -> TestClient:
    monkeypatch.setenv("BREADBOARD_PUBLIC_WORKSPACE", str(workspace))
    monkeypatch.setenv("BREADBOARD_ENABLE_E4_API", "0")
    monkeypatch.setenv("BREADBOARD_ENABLE_PUBLIC_API", "1")
    monkeypatch.setenv("RAY_SCE_LOCAL_MODE", "1")
    return TestClient(create_app(include_atp_routes=False))
def test_artifact_family_reads_and_verifies_immutable_store(monkeypatch, tmp_path: Path) -> None:
    reference = ArtifactStore(tmp_path / ".breadboard/artifacts").put(b"public artifact", media_type="text/plain")
    client = _client(monkeypatch, tmp_path)
    listing = client.get("/v1/artifacts")
    assert listing.status_code == 200
    assert [row["digest"] for row in listing.json()["data"]["artifacts"]] == [reference.digest]
    fetched = client.get(f"/v1/artifacts/{reference.digest}")
    assert fetched.status_code == 200
    assert fetched.json()["data"]["bytes"] == len(b"public artifact")
    verified = client.post(f"/v1/artifacts/{reference.digest}/verify")
    direct = artifact_operations.verify(
        SimpleNamespace(workspace=tmp_path, REF=reference.digest, size=None, media_type=None)
    )
    assert verified.status_code == 200
    assert verified.json() == direct.as_dict()
    assert verified.json()["data"]["verified"] is True
def test_invalid_artifact_reference_uses_stable_problem(monkeypatch, tmp_path: Path) -> None:
    response = _client(monkeypatch, tmp_path).get("/v1/artifacts/not-a-digest")
    assert response.status_code == 422
    assert response.json()["error"]["schema_version"] == "bb.problem.v1"
    assert response.json()["error"]["error_code"] == "invalid_state"
