from __future__ import annotations
from pathlib import Path
from types import SimpleNamespace
from fastapi.testclient import TestClient
from breadboard_engine.api.cli_bridge.app import create_app
from breadboard.product.cli import harness as harness_operations
def _client(monkeypatch, workspace: Path) -> TestClient:
    monkeypatch.setenv("BREADBOARD_PUBLIC_WORKSPACE", str(workspace))
    monkeypatch.setenv("BREADBOARD_ENABLE_E4_API", "0")
    monkeypatch.setenv("BREADBOARD_ENABLE_PUBLIC_API", "1")
    monkeypatch.setenv("RAY_SCE_LOCAL_MODE", "1")
    return TestClient(create_app(include_atp_routes=False))
def test_harness_family_delegates_to_product_operations(monkeypatch, tmp_path: Path) -> None:
    client = _client(monkeypatch, tmp_path)
    created = client.post("/v1/harnesses", json={})
    assert created.status_code == 200
    assert created.json()["data"]["path"] == "daily_driver.v1.yaml"
    assert created.json()["data"]["model_roles_path"] == "daily_driver_roles.v1.json"
    assert (tmp_path / "daily_driver_roles.v1.json").is_file()
    replayed = client.post("/v1/harnesses", json={})
    assert replayed.status_code == 200 and replayed.json()["data"] == created.json()["data"]
    fetched = client.get("/v1/harnesses/daily_driver.v1.yaml")
    direct = harness_operations.get(SimpleNamespace(workspace=tmp_path, PATH=tmp_path / "daily_driver.v1.yaml"))
    assert fetched.json() == direct.as_dict()
    assert client.post("/v1/harnesses/daily_driver.v1.yaml/validate").json()["ok"] is True
    definition = fetched.json()["data"]["definition"]
    definition["modes"][0]["name"] = "review"
    definition["loop"]["sequence"][0]["mode"] = "review"
    updated = client.put("/v1/harnesses/daily_driver.v1.yaml", json={"definition": definition})
    assert updated.status_code == 200 and updated.json()["stage_outcomes"][0]["stage"] == "harness.update"
    assert client.get("/v1/harnesses/daily_driver.v1.yaml").json()["data"]["definition"]["modes"][0]["name"] == "review"
    nested = client.post("/v1/harnesses", json={"directory": "bundles"})
    assert nested.status_code == 200
    invalid_nested_update = client.put("/v1/harnesses/bundles/daily_driver.v1.yaml", json={})
    assert invalid_nested_update.status_code == 422
    assert invalid_nested_update.json()["schema_version"] == "bb.cli.result.v1"
    assert invalid_nested_update.json()["error"]["schema_version"] == "bb.problem.v1"
    outside = tmp_path.parent / f"{tmp_path.name}-outside-prompt.md"
    outside.write_text("outside-secret-value")
    definition["prompts"]["packs"]["base"]["system"] = "prompts/daily_driver_system.md"
    definition["extends"] = f"../{outside.name}"
    escaped = client.put("/v1/harnesses/daily_driver.v1.yaml", json={"definition": definition})
    assert escaped.status_code == 422 and escaped.json()["error"]["error_code"] == "invalid_state"
    assert "outside-secret-value" not in escaped.text
    definition.pop("extends")
    definition["prompts"]["packs"]["base"]["system"] = f"../{outside.name}"
    resource_escaped = client.put(
        "/v1/harnesses/daily_driver.v1.yaml",
        json={"definition": definition},
    )
    assert resource_escaped.status_code == 404
    assert resource_escaped.json()["error"]["error_code"] == "path_unavailable"
    assert "outside-secret-value" not in resource_escaped.text
    assert client.get("/v1/harnesses/daily_driver.v1.yaml").json()["data"]["definition"]["modes"][0]["name"] == "review"
    assert client.post("/v1/harnesses/daily_driver.v1.yaml/explain").json()["ok"] is True
    locked = client.post("/v1/harnesses/daily_driver.v1.yaml/lock")
    assert locked.status_code == 200
    lock_id = locked.json()["data"]["path"]
    assert client.get(f"/v1/harness-locks/{lock_id}").json()["ok"] is True
    (tmp_path / "unrelated.yaml").write_text("name: not-a-harness\n")
    assert client.get("/v1/harnesses").json()["data"]["harnesses"] == ["bundles/daily_driver.v1.yaml", "daily_driver.v1.yaml"]
def test_public_api_cannot_write_maintainer_evidence_trees(monkeypatch, tmp_path: Path) -> None:
    maintainer_tree = Path(__file__).resolve().parents[4] / "docs_tmp"
    marker = maintainer_tree / "daily_driver.v1.yaml"
    existed = marker.exists()
    response = _client(monkeypatch, maintainer_tree).post("/v1/harnesses", json={})
    if maintainer_tree.is_symlink():
        assert response.status_code == 422
        assert response.json()["error"]["error_code"] == "invalid_state"
    else:
        assert response.status_code == 404
        assert response.json()["error"]["error_code"] == "path_unavailable"
    assert marker.exists() is existed
    workspace = tmp_path / "workspace"; workspace.mkdir()
    client = _client(monkeypatch, workspace)
    assert client.post("/v1/harnesses", json={}).status_code == 200
    outside = tmp_path / "outside-lock.json"
    (workspace / "daily_driver.v1.lock.json").symlink_to(outside)
    blocked = client.post("/v1/harnesses/daily_driver.v1.yaml/lock"); assert blocked.status_code == 422 and not outside.exists()
