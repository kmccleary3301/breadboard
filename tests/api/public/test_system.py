from __future__ import annotations

import json
from pathlib import Path

from fastapi.testclient import TestClient

from agentic_coder_prototype.api.cli_bridge.app import create_app
from breadboard.product.cli import system as system_operations


def _client(monkeypatch, workspace: Path) -> TestClient:
    monkeypatch.setenv("BREADBOARD_PUBLIC_WORKSPACE", str(workspace))
    monkeypatch.setenv("BREADBOARD_ENABLE_E4_API", "0")
    monkeypatch.setenv("RAY_SCE_LOCAL_MODE", "1")
    return TestClient(create_app(include_atp_routes=False))


def test_candidate_family_routes_are_mounted_exactly_once(monkeypatch, tmp_path: Path) -> None:
    app = _client(monkeypatch, tmp_path).app
    document = json.loads((Path(__file__).resolve().parents[3] / "contracts/public/operations.v1.json").read_text())
    families = {"artifact", "harness", "harness_lock", "integration", "session", "system"}
    expected = {
        operation["operation_id"]
        for operation in document["operations"]
        if operation["operation_id"].split(".", 1)[0] in families
    }
    observed = [
        operation["operationId"]
        for methods in app.openapi()["paths"].values()
        for operation in methods.values()
        if isinstance(operation, dict) and operation.get("operationId") in expected
    ]
    assert len(observed) == len(set(observed)) == 26
    assert set(observed) == expected


def test_system_describe_matches_cli_result(monkeypatch, tmp_path: Path) -> None:
    client = _client(monkeypatch, tmp_path)
    response = client.get("/v1/system")
    assert response.status_code == 200
    assert response.json() == system_operations.describe(["system", "describe"], tmp_path).as_dict()


def test_public_auth_failure_is_stable_and_secret_free(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("BREADBOARD_API_TOKEN", "never-echo-this-token")
    response = _client(monkeypatch, tmp_path).get("/v1/system")
    assert response.status_code == 401
    assert response.json() == {"error": "unauthorized", "detail": "unauthorized", "path": None}
    assert "never-echo-this-token" not in response.text
