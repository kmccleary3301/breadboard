from __future__ import annotations
import json
import os
from pathlib import Path
from fastapi.testclient import TestClient
import pytest
from jsonschema.exceptions import SchemaError
from breadboard_engine.api.cli_bridge.app import create_app
from breadboard.product.cli import system as system_operations
from breadboard.product.cli import harness as harness_operations
from breadboard_engine.api.public import models as public_models
@pytest.fixture(autouse=True)
def _clear_default_profile_cache():
    harness_operations.resolve_default_profile.cache_clear()
    yield
    harness_operations.resolve_default_profile.cache_clear()

def _client(monkeypatch, workspace: Path, *, e4_flag: str = "0") -> TestClient:
    monkeypatch.delenv("BREADBOARD_LEGACY_ROUTES", raising=False)
    monkeypatch.setenv("BREADBOARD_PUBLIC_WORKSPACE", str(workspace))
    monkeypatch.setenv("BREADBOARD_ENABLE_E4_API", e4_flag)
    monkeypatch.setenv("BREADBOARD_ENABLE_PUBLIC_API", "1")
    monkeypatch.setenv("RAY_SCE_LOCAL_MODE", "1")
    return TestClient(create_app(include_atp_routes=False))
def test_candidate_family_routes_are_mounted_exactly_once(monkeypatch, tmp_path: Path) -> None:
    app = _client(monkeypatch, tmp_path).app
    document = json.loads((Path(__file__).resolve().parents[3] / "contracts/public/operations.v2.json").read_text())
    expected = {operation["operation_id"] for operation in document["operations"]}
    observed = [
        operation["operationId"]
        for methods in app.openapi()["paths"].values()
        for operation in methods.values()
        if isinstance(operation, dict) and "operationId" in operation
    ]
    product_operations = [operation_id for operation_id in observed if operation_id in expected]
    assert len(product_operations) == len(set(product_operations)) == len(expected)
    assert set(product_operations) == expected
def test_product_routes_are_enabled_by_default_and_can_be_disabled(monkeypatch) -> None:
    monkeypatch.setenv("RAY_SCE_LOCAL_MODE", "1")
    monkeypatch.delenv("BREADBOARD_ENABLE_PUBLIC_API", raising=False)
    assert TestClient(create_app(include_atp_routes=False)).get("/v1/system").status_code == 200
    monkeypatch.setenv("BREADBOARD_ENABLE_PUBLIC_API", "0")
    assert TestClient(create_app(include_atp_routes=False)).get("/v1/system").status_code == 404
def test_system_describe_matches_cli_result(monkeypatch, tmp_path: Path) -> None:
    client = _client(monkeypatch, tmp_path)
    response = client.get("/v1/system")
    assert response.status_code == 200
    assert response.json() == system_operations.describe(["system", "describe"], tmp_path).as_dict()
    assert response.json()["data"]["operation_count"] == 26
    assert response.json()["data"]["internal_extensions"] == []
    assert response.json()["hashes"]["profile"] == (
        "sha256:165d34c5ed177005fa289544da0b451294c89bb51b0d289f2372c4bd081eff43"
    )
    assert response.json()["data"]["default_profile"] == {
        "profile_id": "daily_driver.v1",
        "definition_ref": "agent_configs/templates/daily_driver.v1.yaml",
        "schema_version": "bb.harness_definition.v1",
        "source_sha256": (
            "sha256:155e9db1dabee3975739a221324215993002438dc33dd73402959dc4649709f5"
        ),
        "effective_lock_schema_version": "bb.effective_config_graph.v1",
        "effective_lock_hash": (
            "sha256:165d34c5ed177005fa289544da0b451294c89bb51b0d289f2372c4bd081eff43"
        ),
        "resources": [
            {
                "ref": "agent_configs/templates/daily_driver_roles.v1.json",
                "sha256": (
                    "sha256:4094accaa44d06ba1484141c1b0cd01dfc13cf225141db0230b37aec86a75f61"
                ),
            },
            {
                "ref": (
                    "agent_configs/templates/prompts/"
                    "daily_driver_system.md"
                ),
                "sha256": (
                    "sha256:1b3f1543403a6bf3c1d8c8a3e95d44412f44876265a32b5e9557567afdacf695"
                ),
            },
        ],
    }
def test_system_describe_separates_explicit_internal_extension(monkeypatch, tmp_path: Path) -> None:
    response = _client(monkeypatch,tmp_path,e4_flag="true").get("/v1/system")
    assert response.status_code == 200
    assert response.json()["data"]["internal_extensions"] == [
        {"extension_id":"e4","catalog_id":"bb.internal_evidence_operation_catalog.v1","operation_count":19}
    ]
    assert response.json()["data"]["default_profile"]["profile_id"] == (
        "daily_driver.v1"
    )


def _copy_default_profile(tmp_path: Path) -> Path:
    bundle = tmp_path / "package" / "agent_configs" / "templates"
    bundle.mkdir(parents=True)
    profile = bundle / "daily_driver.v1.yaml"
    profile.write_text(
        harness_operations.daily_driver_template_path().read_text(
            encoding="utf-8"
        ),
        encoding="utf-8",
    )
    roles = bundle / "daily_driver_roles.v1.json"
    roles.write_bytes(
        harness_operations.daily_driver_model_roles_path().read_bytes()
    )
    return profile
def _profile_prompt_path(profile: Path) -> Path:
    prompt = profile.parent / "prompts" / "daily_driver_system.md"
    prompt.parent.mkdir()
    return prompt
def _profile_model_roles_path(profile: Path) -> Path:
    return profile.parent / "daily_driver_roles.v1.json"



def test_system_describe_fails_typed_when_default_profile_is_missing(
    monkeypatch,
    tmp_path: Path,
) -> None:
    missing = (
        tmp_path / "package" / "agent_configs" / "templates"
        / "daily_driver.v1.yaml"
    )
    monkeypatch.setattr(
        harness_operations,
        "daily_driver_template_path",
        lambda: missing,
    )

    response = _client(monkeypatch, tmp_path).get("/v1/system")

    assert response.status_code == 404
    assert response.json()["error"]["error_code"] == (
        "default_profile_unavailable"
    )
    assert "Reinstall BreadBoard" in response.json()["error"]["hint"]
    assert str(tmp_path) not in response.text


def test_system_describe_fails_typed_when_profile_prompt_is_missing(
    monkeypatch,
    tmp_path: Path,
) -> None:
    profile = _copy_default_profile(tmp_path)
    monkeypatch.setattr(
        harness_operations,
        "daily_driver_template_path",
        lambda: profile,
    )

    response = _client(monkeypatch, tmp_path).get("/v1/system")

    assert response.status_code == 404
    assert response.json()["error"]["error_code"] == (
        "default_profile_unavailable"
    )

@pytest.mark.parametrize(
    ("resource_kind", "status_code", "error_code"),
    [
        ("missing", 404, "default_profile_unavailable"),
        ("directory", 422, "default_profile_invalid"),
        ("non_utf8", 422, "default_profile_invalid"),
        ("invalid_schema", 422, "default_profile_invalid"),
        ("symlink", 422, "default_profile_invalid"),
    ],
)
def test_system_describe_classifies_model_role_resource_failures(
    monkeypatch,
    tmp_path: Path,
    resource_kind: str,
    status_code: int,
    error_code: str,
) -> None:
    profile = _copy_default_profile(tmp_path)
    _profile_prompt_path(profile).write_bytes(
        harness_operations.daily_driver_prompt_path().read_bytes()
    )
    roles = _profile_model_roles_path(profile)
    roles.unlink()
    if resource_kind == "directory":
        roles.mkdir()
    elif resource_kind == "non_utf8":
        roles.write_bytes(b"\xff")
    elif resource_kind == "invalid_schema":
        roles.write_text('{"schema_version":"bb.model_roles.v1"}\n')
    elif resource_kind == "symlink":
        external = tmp_path / "external-model-roles.json"
        external.write_bytes(
            harness_operations.daily_driver_model_roles_path().read_bytes()
        )
        roles.symlink_to(external)
    monkeypatch.setattr(
        harness_operations,
        "daily_driver_template_path",
        lambda: profile,
    )

    response = _client(monkeypatch, tmp_path).get("/v1/system")

    assert response.status_code == status_code
    assert response.json()["error"]["error_code"] == error_code
    assert str(tmp_path) not in response.text



def test_system_describe_classifies_malformed_model_role_schema(
    monkeypatch,
    tmp_path: Path,
) -> None:
    profile = _copy_default_profile(tmp_path)
    _profile_prompt_path(profile).write_bytes(
        harness_operations.daily_driver_prompt_path().read_bytes()
    )

    def raise_schema_error(_path: Path) -> dict:
        raise SchemaError("malformed packaged model-role schema")

    monkeypatch.setattr(
        harness_operations,
        "daily_driver_template_path",
        lambda: profile,
    )
    monkeypatch.setattr(
        harness_operations,
        "load_daily_driver_model_roles",
        raise_schema_error,
    )

    response = _client(monkeypatch, tmp_path).get("/v1/system")

    assert response.status_code == 422
    assert response.json()["error"]["error_code"] == (
        "default_profile_invalid"
    )
    assert "Reinstall BreadBoard" in response.json()["error"]["hint"]
    assert str(tmp_path) not in response.text


def test_system_describe_classifies_missing_extended_profile_as_unavailable(
    monkeypatch,
    tmp_path: Path,
) -> None:
    profile = _copy_default_profile(tmp_path)
    profile.write_text(
        profile.read_text(encoding="utf-8")
        + "\nextends: missing.yaml\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(
        harness_operations,
        "daily_driver_template_path",
        lambda: profile,
    )

    response = _client(monkeypatch, tmp_path).get("/v1/system")

    assert response.status_code == 404
    assert response.json()["error"]["error_code"] == (
        "default_profile_unavailable"
    )


def test_system_describe_rejects_absolute_profile_resource_without_leak(
    monkeypatch,
    tmp_path: Path,
) -> None:
    profile = _copy_default_profile(tmp_path)
    prompt = _profile_prompt_path(profile)
    prompt.write_text("prompt\n", encoding="utf-8")
    profile.write_text(
        profile.read_text(encoding="utf-8").replace(
            "prompts/daily_driver_system.md",
            str(prompt),
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        harness_operations,
        "daily_driver_template_path",
        lambda: profile,
    )

    response = _client(monkeypatch, tmp_path).get("/v1/system")

    assert response.status_code == 422
    assert response.json()["error"]["error_code"] == (
        "default_profile_invalid"
    )
    assert str(prompt) not in response.text


@pytest.mark.parametrize("resource_kind", ["directory", "non_utf8"])
def test_system_describe_rejects_corrupt_prompt_resource(
    monkeypatch,
    tmp_path: Path,
    resource_kind: str,
) -> None:
    profile = _copy_default_profile(tmp_path)
    prompt = _profile_prompt_path(profile)
    if resource_kind == "directory":
        prompt.mkdir()
    else:
        prompt.write_bytes(b"\xff")
    monkeypatch.setattr(
        harness_operations,
        "daily_driver_template_path",
        lambda: profile,
    )

    response = _client(monkeypatch, tmp_path).get("/v1/system")

    assert response.status_code == 422
    assert response.json()["error"]["error_code"] == (
        "default_profile_invalid"
    )


def test_system_describe_rejects_symlinked_profile_source(
    monkeypatch,
    tmp_path: Path,
) -> None:
    profile = (
        tmp_path / "package" / "agent_configs" / "templates"
        / "daily_driver.v1.yaml"
    )
    profile.parent.mkdir(parents=True)
    profile.symlink_to(harness_operations.daily_driver_template_path())
    monkeypatch.setattr(
        harness_operations,
        "daily_driver_template_path",
        lambda: profile,
    )

    response = _client(monkeypatch, tmp_path).get("/v1/system")

    assert response.status_code == 422
    assert response.json()["error"]["error_code"] == (
        "default_profile_invalid"
    )


def test_system_describe_rejects_symlinked_package_resource_parent(
    monkeypatch,
    tmp_path: Path,
) -> None:
    package = tmp_path / "package"
    external = tmp_path / "external-agent-configs"
    templates = external / "templates"
    templates.mkdir(parents=True)
    profile = templates / "daily_driver.v1.yaml"
    profile.write_text(
        harness_operations.daily_driver_template_path().read_text(
            encoding="utf-8"
        ),
        encoding="utf-8",
    )
    prompt = templates / "prompts" / "daily_driver_system.md"
    prompt.parent.mkdir()
    prompt.write_bytes(
        harness_operations.daily_driver_prompt_path().read_bytes()
    )
    package.mkdir()
    (package / "agent_configs").symlink_to(
        external,
        target_is_directory=True,
    )
    linked_profile = (
        package / "agent_configs" / "templates"
        / "daily_driver.v1.yaml"
    )
    monkeypatch.setattr(
        harness_operations,
        "daily_driver_template_path",
        lambda: linked_profile,
    )

    response = _client(monkeypatch, tmp_path).get("/v1/system")

    assert response.status_code == 422
    assert response.json()["error"]["error_code"] == (
        "default_profile_invalid"
    )


def test_system_describe_rejects_escaping_profile_resource(
    monkeypatch,
    tmp_path: Path,
) -> None:
    profile = _copy_default_profile(tmp_path)
    profile.write_text(
        profile.read_text(encoding="utf-8").replace(
            "system: prompts/daily_driver_system.md",
            "system: ../escape.md",
        ),
        encoding="utf-8",
    )
    escaped = profile.parent.parent / "escape.md"
    escaped.write_text("outside template root\n", encoding="utf-8")
    monkeypatch.setattr(
        harness_operations,
        "daily_driver_template_path",
        lambda: profile,
    )

    response = _client(monkeypatch, tmp_path).get("/v1/system")

    assert response.status_code == 422
    assert response.json()["error"]["error_code"] == (
        "default_profile_invalid"
    )


def test_system_describe_fails_typed_when_default_profile_is_corrupt(
    monkeypatch,
    tmp_path: Path,
) -> None:
    corrupt = _copy_default_profile(tmp_path)
    corrupt.write_text("{}\n", encoding="utf-8")
    monkeypatch.setattr(
        harness_operations,
        "daily_driver_template_path",
        lambda: corrupt,
    )

    response = _client(monkeypatch, tmp_path).get("/v1/system")

    assert response.status_code == 422
    assert response.json()["error"]["error_code"] == (
        "default_profile_invalid"
    )
    assert response.json()["error"]["message"] == (
        "bundled daily-driver profile is corrupt"
    )
def test_public_auth_failure_is_stable_and_secret_free(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("BREADBOARD_API_TOKEN", "never-echo-this-token")
    response = _client(monkeypatch, tmp_path).get("/v1/system")
    assert response.status_code == 401
    assert response.json() == {"error": "unauthorized", "detail": "unauthorized", "path": None}
    assert "never-echo-this-token" not in response.text
def test_default_legacy_http_errors_keep_error_envelope(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.delenv("BREADBOARD_ENABLE_PUBLIC_API", raising=False)
    monkeypatch.setenv("BREADBOARD_LEGACY_ROUTES", "1")
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
