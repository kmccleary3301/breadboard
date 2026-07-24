from __future__ import annotations
import json
import os
from pathlib import Path
from fastapi.testclient import TestClient
import pytest
from agentic_coder_prototype.api.cli_bridge.app import create_app
from breadboard.product.cli import system as system_operations
from agentic_coder_prototype.api.public import models as public_models
def _client(monkeypatch, workspace: Path) -> TestClient:
    monkeypatch.setenv("BREADBOARD_PUBLIC_WORKSPACE", str(workspace))
    monkeypatch.setenv("BREADBOARD_ENABLE_E4_API", "0")
    monkeypatch.setenv("BREADBOARD_ENABLE_PUBLIC_API", "1")
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
def test_candidate_routes_wait_for_atomic_activation(monkeypatch) -> None:
    monkeypatch.setenv("RAY_SCE_LOCAL_MODE", "1")
    monkeypatch.delenv("BREADBOARD_ENABLE_PUBLIC_API", raising=False)
    assert TestClient(create_app(include_atp_routes=False)).get("/v1/system").status_code == 404
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
def test_default_legacy_http_errors_keep_error_envelope(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.delenv("BREADBOARD_ENABLE_PUBLIC_API", raising=False)
    monkeypatch.setenv("RAY_SCE_LOCAL_MODE", "1")
    response = TestClient(create_app(include_atp_routes=False)).get("/v1/registries/missing")
    assert response.status_code == 404
    assert response.json()["error"] == "registry_not_found"
    assert "detail" in response.json() and "path" in response.json()
def test_idempotency_record_write_rejects_planted_temp_symlink(monkeypatch, tmp_path: Path) -> None:
    record = tmp_path / "record.json"
    outside = tmp_path / "outside.json"
    outside.write_text("owner content")
    monkeypatch.setattr(os, "urandom", lambda _size: b"\0" * 8)
    record.with_name(f".{record.name}.{'00' * 8}.tmp").symlink_to(outside)
    with pytest.raises(FileExistsError):
        public_models._write_idempotency_record(record, b"cached result")
    assert outside.read_text() == "owner content"
def test_problem_response_preserves_status_exit_semantics() -> None:
    response = public_models.problem_response("system.describe", 404, "not_found", "not found")
    assert response.status_code == 404
    assert json.loads(response.body)["exit_code"] == 3
    send_input = json.loads(public_models.problem_response("session.send_input", 422, "invalid_request", "bad").body)
    assert send_input["command"] == ["session", "send-input"]
    assert send_input["stage_outcomes"][0]["stage"] == "session.send-input"
