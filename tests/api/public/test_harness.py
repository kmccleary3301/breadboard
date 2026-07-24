from __future__ import annotations
from pathlib import Path
from types import SimpleNamespace
from fastapi.testclient import TestClient
from agentic_coder_prototype.api.cli_bridge.app import create_app
from breadboard.product.cli import harness as harness_operations
def _client(monkeypatch, workspace: Path) -> TestClient:
    monkeypatch.setenv("BREADBOARD_PUBLIC_WORKSPACE", str(workspace))
    monkeypatch.setenv("BREADBOARD_ENABLE_E4_API", "0")
    monkeypatch.setenv("RAY_SCE_LOCAL_MODE", "1")
    return TestClient(create_app(include_atp_routes=False))
def test_harness_family_delegates_to_product_operations(monkeypatch, tmp_path: Path) -> None:
    client = _client(monkeypatch, tmp_path)
    created = client.post("/v1/harnesses", json={})
    assert created.status_code == 200
    assert created.json()["data"]["path"] == "minimal_harness.v2.yaml"
    fetched = client.get("/v1/harnesses/minimal_harness.v2.yaml")
    direct = harness_operations.get(SimpleNamespace(workspace=tmp_path, PATH=tmp_path / "minimal_harness.v2.yaml"))
    assert fetched.json() == direct.as_dict()
    assert client.post("/v1/harnesses/minimal_harness.v2.yaml/validate").json()["ok"] is True
    assert client.put("/v1/harnesses/minimal_harness.v2.yaml").json()["ok"] is True
    assert client.post("/v1/harnesses/minimal_harness.v2.yaml/explain").json()["ok"] is True
    locked = client.post("/v1/harnesses/minimal_harness.v2.yaml/lock")
    assert locked.status_code == 200
    lock_id = locked.json()["data"]["path"]
    assert client.get(f"/v1/harness-locks/{lock_id}").json()["ok"] is True
    assert client.get("/v1/harnesses").json()["data"]["harnesses"] == ["minimal_harness.v2.yaml"]
def test_public_api_cannot_write_maintainer_evidence_trees(monkeypatch, tmp_path: Path) -> None:
    maintainer_tree = Path(__file__).resolve().parents[4] / "docs_tmp"
    marker = maintainer_tree / "minimal_harness.v2.yaml"
    existed = marker.exists()
    response = _client(monkeypatch, maintainer_tree).post("/v1/harnesses", json={})
    assert response.status_code == 404
    assert response.json()["error"]["error_code"] == "path_unavailable"
    assert marker.exists() is existed
    workspace = tmp_path / "workspace"; workspace.mkdir()
    client = _client(monkeypatch, workspace)
    assert client.post("/v1/harnesses", json={}).status_code == 200
    outside = tmp_path / "outside-lock.json"
    (workspace / "minimal_harness.v2.lock.json").symlink_to(outside)
    blocked = client.post("/v1/harnesses/minimal_harness.v2.yaml/lock"); assert blocked.status_code == 422 and not outside.exists()
