from __future__ import annotations
import json
import os
import sysconfig
from pathlib import Path
from fastapi.testclient import TestClient
import pytest
from jsonschema.exceptions import SchemaError
from breadboard_engine.api.cli_bridge.app import create_app
import breadboard_engine.api.cli_bridge.app as app_module
from breadboard.product.cli.main import main as cli_main
from breadboard.product.cli import system as system_operations
from breadboard.product.harness import (
    default_profile,
    resolution as harness_resolution,
    templates as harness_templates,
)
from breadboard.product.operations.model import OperationContext, OperationResult
from breadboard.product.operations.system import (
    DescribeSystemRequest,
    describe_system,
)
from breadboard_engine.api.public import artifact as public_artifact
from breadboard_engine.api.public import models as public_models


@pytest.fixture(autouse=True)
def _clear_default_profile_cache():
    default_profile.resolve_default_profile.cache_clear()
    yield
    default_profile.resolve_default_profile.cache_clear()


def _expected_system_describe() -> dict:
    fixture = Path(__file__).with_name("fixtures") / "system_describe.json"
    return json.loads(fixture.read_text(encoding="utf-8"))


_PUBLIC_SCHEMAS = [
    "bb.artifact_manifest.v1.schema.json",
    "bb.capability_probe_report.v1.schema.json",
    "bb.claim_reverification_report.v1.schema.json",
    "bb.cli.result.v1.schema.json",
    "bb.comparison_report.v1.schema.json",
    "bb.effective_harness_lock.v1.schema.json",
    "bb.harness_definition.v1.schema.json",
    "bb.harness_explanation_report.v1.schema.json",
    "bb.harness_validation_report.v1.schema.json",
    "bb.integration_descriptor.v1.schema.json",
    "bb.lane_execution_report.v1.schema.json",
    "bb.operator_interaction.v1.schema.json",
    "bb.page.v1.schema.json",
    "bb.problem.v1.schema.json",
    "bb.provider_exchange.v2.schema.json",
    "bb.public_axis_smoke_manifest.v1.schema.json",
    "bb.public_operation_catalog.v1.schema.json",
    "bb.public_operation_catalog.v2.schema.json",
    "bb.public_record_surface.v1.schema.json",
    "bb.public_surface_inventory.v1.schema.json",
    "bb.replay_artifact_manifest.v1.schema.json",
    "bb.replay_execution.v1.schema.json",
    "bb.replay_plan.v1.schema.json",
    "bb.session.v1.schema.json",
    "bb.stage_report.v1.schema.json",
]


def _expected_read_result(
    command: list[str],
    stage: str,
    data: dict,
) -> dict:
    return {
        "schema_version": "bb.cli.result.v1",
        "ok": True,
        "status": "ok",
        "command": command,
        "record_refs": [],
        "hashes": {},
        "stage_outcomes": [
            {
                "stage": stage,
                "status": "passed",
                "report_ref": None,
                "next_action": None,
            }
        ],
        "warnings": [],
        "next_actions": [],
        "error": None,
        "exit_code": 0,
        "data": data,
    }


def _client(monkeypatch, workspace: Path, *, e4_flag: str = "0") -> TestClient:
    monkeypatch.delenv("BREADBOARD_LEGACY_ROUTES", raising=False)
    monkeypatch.setenv("BREADBOARD_PUBLIC_WORKSPACE", str(workspace))
    monkeypatch.setenv("BREADBOARD_ENABLE_E4_API", e4_flag)
    monkeypatch.setenv("BREADBOARD_ENABLE_PUBLIC_API", "1")
    monkeypatch.setenv("RAY_SCE_LOCAL_MODE", "1")
    return TestClient(create_app(include_atp_routes=False))


def test_candidate_family_routes_are_mounted_exactly_once(
    monkeypatch, tmp_path: Path
) -> None:
    app = _client(monkeypatch, tmp_path).app
    document = json.loads(
        (
            Path(__file__).resolve().parents[3] / "contracts/public/operations.v2.json"
        ).read_text()
    )
    expected = {operation["operation_id"] for operation in document["operations"]}
    observed = [
        operation["operationId"]
        for methods in app.openapi()["paths"].values()
        for operation in methods.values()
        if isinstance(operation, dict) and "operationId" in operation
    ]
    product_operations = [
        operation_id for operation_id in observed if operation_id in expected
    ]
    assert len(product_operations) == len(set(product_operations)) == len(expected)
    assert set(product_operations) == expected


def test_product_routes_are_enabled_by_default_and_can_be_disabled(monkeypatch) -> None:
    monkeypatch.setenv("RAY_SCE_LOCAL_MODE", "1")
    monkeypatch.delenv("BREADBOARD_ENABLE_PUBLIC_API", raising=False)
    assert (
        TestClient(create_app(include_atp_routes=False)).get("/v1/system").status_code
        == 200
    )
    monkeypatch.setenv("BREADBOARD_ENABLE_PUBLIC_API", "0")
    assert (
        TestClient(create_app(include_atp_routes=False)).get("/v1/system").status_code
        == 404
    )


def test_system_health_and_schemas_are_fixed_nonmutating_reads(
    monkeypatch,
    tmp_path: Path,
) -> None:
    with _client(monkeypatch, tmp_path) as client:
        before = {
            path.relative_to(tmp_path).as_posix(): path.read_bytes()
            for path in tmp_path.rglob("*")
            if path.is_file() and not path.is_symlink()
        }
        described = client.get("/v1/system")
        health = client.get("/v1/health")
        schemas = client.get("/v1/schemas")
        after = {
            path.relative_to(tmp_path).as_posix(): path.read_bytes()
            for path in tmp_path.rglob("*")
            if path.is_file() and not path.is_symlink()
        }

    assert described.status_code == 200
    assert described.json() == _expected_system_describe()
    assert health.status_code == 200
    assert health.json() == _expected_read_result(
        ["system", "health"],
        "system.health",
        {
            "workspace": ".",
            "workspace_exists": True,
            "metadata_dir": ".breadboard",
            "metadata_exists": False,
            "python": sysconfig.get_platform(),
        },
    )
    assert schemas.status_code == 200
    assert schemas.json() == _expected_read_result(
        ["system", "schemas"],
        "system.schemas",
        {
            "schema_count": len(_PUBLIC_SCHEMAS),
            "schemas": _PUBLIC_SCHEMAS,
        },
    )
    assert system_operations.health(["system", "health"], tmp_path).as_dict() == (
        health.json()
    )
    assert system_operations.schemas(["system", "schemas"], tmp_path).as_dict() == (
        schemas.json()
    )
    assert after == before


def test_system_describe_matches_fixed_operation_contract(
    monkeypatch,
    tmp_path: Path,
) -> None:
    client = _client(monkeypatch, tmp_path)
    expected = _expected_system_describe()
    direct = describe_system(
        DescribeSystemRequest(),
        OperationContext(workspace=tmp_path),
    )
    cli = system_operations.describe(tmp_path)
    response = client.get("/v1/system")

    assert direct.as_dict() == expected
    assert cli.as_dict() == expected
    assert response.status_code == 200
    assert response.json() == expected


def test_system_describe_cli_json_matches_fixed_operation_contract(
    monkeypatch,
    tmp_path: Path,
    capsys,
) -> None:
    monkeypatch.setenv("BREADBOARD_ENABLE_E4_API", "0")
    exit_code = cli_main(
        [
            "--json",
            "system",
            "--workspace",
            str(tmp_path),
            "describe",
        ]
    )
    captured = capsys.readouterr()

    assert exit_code == 0
    assert captured.err == ""
    assert json.loads(captured.out) == _expected_system_describe()


def test_system_describe_separates_explicit_internal_extension(
    monkeypatch,
    tmp_path: Path,
) -> None:
    client = _client(monkeypatch, tmp_path, e4_flag="true")
    expected = _expected_system_describe()
    expected["data"]["internal_extensions"] = [
        {
            "extension_id": "e4",
            "catalog_id": "bb.internal_evidence_operation_catalog.v1",
            "operation_count": 19,
        }
    ]
    direct = describe_system(
        DescribeSystemRequest(),
        OperationContext(
            workspace=tmp_path,
            enabled_extensions=frozenset({"e4"}),
        ),
    )
    cli = system_operations.describe(tmp_path)
    response = client.get("/v1/system")

    assert direct.as_dict() == expected
    assert cli.as_dict() == expected
    assert response.status_code == 200
    assert response.json() == expected


def _copy_default_profile(tmp_path: Path) -> Path:
    bundle = tmp_path / "package" / "agent_configs" / "templates"
    bundle.mkdir(parents=True)
    profile = bundle / "daily_driver.v1.yaml"
    profile.write_text(
        harness_templates.daily_driver_template_path().read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    roles = bundle / "daily_driver_roles.v1.json"
    roles.write_bytes(harness_templates.daily_driver_model_roles_path().read_bytes())
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
        tmp_path / "package" / "agent_configs" / "templates" / "daily_driver.v1.yaml"
    )
    monkeypatch.setattr(default_profile, "daily_driver_template_path", lambda: missing)

    response = _client(monkeypatch, tmp_path).get("/v1/system")

    assert response.status_code == 404
    assert response.json()["error"]["error_code"] == ("default_profile_unavailable")
    assert "Reinstall BreadBoard" in response.json()["error"]["hint"]
    assert str(tmp_path) not in response.text


def test_system_describe_fails_typed_when_profile_prompt_is_missing(
    monkeypatch,
    tmp_path: Path,
) -> None:
    profile = _copy_default_profile(tmp_path)
    monkeypatch.setattr(default_profile, "daily_driver_template_path", lambda: profile)

    response = _client(monkeypatch, tmp_path).get("/v1/system")

    assert response.status_code == 404
    assert response.json()["error"]["error_code"] == ("default_profile_unavailable")


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
        harness_templates.daily_driver_prompt_path().read_bytes()
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
            harness_templates.daily_driver_model_roles_path().read_bytes()
        )
        roles.symlink_to(external)
    monkeypatch.setattr(default_profile, "daily_driver_template_path", lambda: profile)

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
        harness_templates.daily_driver_prompt_path().read_bytes()
    )

    def raise_schema_error(_path: Path) -> dict:
        raise SchemaError("malformed packaged model-role schema")

    monkeypatch.setattr(default_profile, "daily_driver_template_path", lambda: profile)
    monkeypatch.setattr(
        harness_resolution,
        "load_daily_driver_model_roles",
        raise_schema_error,
    )

    response = _client(monkeypatch, tmp_path).get("/v1/system")

    assert response.status_code == 422
    assert response.json()["error"]["error_code"] == ("default_profile_invalid")
    assert "Reinstall BreadBoard" in response.json()["error"]["hint"]
    assert str(tmp_path) not in response.text


def test_system_describe_classifies_missing_extended_profile_as_unavailable(
    monkeypatch,
    tmp_path: Path,
) -> None:
    profile = _copy_default_profile(tmp_path)
    profile.write_text(
        profile.read_text(encoding="utf-8") + "\nextends: missing.yaml\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(default_profile, "daily_driver_template_path", lambda: profile)

    response = _client(monkeypatch, tmp_path).get("/v1/system")

    assert response.status_code == 404
    assert response.json()["error"]["error_code"] == ("default_profile_unavailable")


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
    monkeypatch.setattr(default_profile, "daily_driver_template_path", lambda: profile)

    response = _client(monkeypatch, tmp_path).get("/v1/system")

    assert response.status_code == 422
    assert response.json()["error"]["error_code"] == ("default_profile_invalid")
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
    monkeypatch.setattr(default_profile, "daily_driver_template_path", lambda: profile)

    response = _client(monkeypatch, tmp_path).get("/v1/system")

    assert response.status_code == 422
    assert response.json()["error"]["error_code"] == ("default_profile_invalid")


def test_system_describe_rejects_symlinked_profile_source(
    monkeypatch,
    tmp_path: Path,
) -> None:
    profile = (
        tmp_path / "package" / "agent_configs" / "templates" / "daily_driver.v1.yaml"
    )
    profile.parent.mkdir(parents=True)
    profile.symlink_to(harness_templates.daily_driver_template_path())
    monkeypatch.setattr(default_profile, "daily_driver_template_path", lambda: profile)

    response = _client(monkeypatch, tmp_path).get("/v1/system")

    assert response.status_code == 422
    assert response.json()["error"]["error_code"] == ("default_profile_invalid")


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
        harness_templates.daily_driver_template_path().read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    prompt = templates / "prompts" / "daily_driver_system.md"
    prompt.parent.mkdir()
    prompt.write_bytes(harness_templates.daily_driver_prompt_path().read_bytes())
    package.mkdir()
    (package / "agent_configs").symlink_to(
        external,
        target_is_directory=True,
    )
    linked_profile = package / "agent_configs" / "templates" / "daily_driver.v1.yaml"
    monkeypatch.setattr(
        default_profile, "daily_driver_template_path", lambda: linked_profile
    )

    response = _client(monkeypatch, tmp_path).get("/v1/system")

    assert response.status_code == 422
    assert response.json()["error"]["error_code"] == ("default_profile_invalid")


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
    monkeypatch.setattr(default_profile, "daily_driver_template_path", lambda: profile)

    response = _client(monkeypatch, tmp_path).get("/v1/system")

    assert response.status_code == 422
    assert response.json()["error"]["error_code"] == ("default_profile_invalid")


def test_system_describe_fails_typed_when_default_profile_is_corrupt(
    monkeypatch,
    tmp_path: Path,
) -> None:
    corrupt = _copy_default_profile(tmp_path)
    corrupt.write_text("{}\n", encoding="utf-8")
    monkeypatch.setattr(default_profile, "daily_driver_template_path", lambda: corrupt)

    response = _client(monkeypatch, tmp_path).get("/v1/system")

    assert response.status_code == 422
    assert response.json()["error"]["error_code"] == ("default_profile_invalid")
    assert response.json()["error"]["message"] == (
        "bundled daily-driver profile is corrupt"
    )


def test_public_auth_failure_is_stable_and_secret_free(
    monkeypatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("BREADBOARD_API_TOKEN", "never-echo-this-token")
    response = _client(monkeypatch, tmp_path).get("/v1/system")
    assert response.status_code == 401
    assert response.json() == {
        "error": "unauthorized",
        "detail": "unauthorized",
        "path": None,
    }
    assert "never-echo-this-token" not in response.text


def test_default_legacy_http_errors_keep_error_envelope(
    monkeypatch, tmp_path: Path
) -> None:
    monkeypatch.delenv("BREADBOARD_ENABLE_PUBLIC_API", raising=False)
    monkeypatch.setenv("BREADBOARD_LEGACY_ROUTES", "1")
    monkeypatch.setenv("RAY_SCE_LOCAL_MODE", "1")
    response = TestClient(create_app(include_atp_routes=False)).get(
        "/v1/registries/missing"
    )
    assert response.status_code == 404
    assert response.json()["error"] == "registry_not_found"
    assert "detail" in response.json() and "path" in response.json()


def test_idempotency_record_write_rejects_planted_temp_symlink(
    monkeypatch, tmp_path: Path
) -> None:
    record = tmp_path / "record.json"
    outside = tmp_path / "outside.json"
    outside.write_text("owner content")
    monkeypatch.setattr(os, "urandom", lambda _size: b"\0" * 8)
    record.with_name(f".{record.name}.{'00' * 8}.tmp").symlink_to(outside)
    with pytest.raises(FileExistsError):
        public_models._write_idempotency_record(record, b"cached result")
    assert outside.read_text() == "owner content"


def test_problem_response_preserves_status_exit_semantics() -> None:
    response = public_models.problem_response(
        "system.describe", 404, "not_found", "not found"
    )
    assert response.status_code == 404
    assert json.loads(response.body)["exit_code"] == 3
    send_input = json.loads(
        public_models.problem_response(
            "session.send_input", 422, "invalid_request", "bad"
        ).body
    )
    assert send_input["command"] == ["session", "send-input"]
    assert send_input["stage_outcomes"][0]["stage"] == "session.send-input"


def test_generated_capability_policy_gates_dispatch_before_callbacks(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("BREADBOARD_PUBLIC_WORKSPACE", str(tmp_path))
    assert public_models.public_operation_context(tmp_path).capabilities == frozenset()
    calls: list[frozenset[str]] = []

    def callback(workspace: Path) -> OperationResult:
        calls.append(public_models.public_operation_context(workspace).capabilities)
        return OperationResult.success(["artifact", "list"], stage="artifact.list")

    denied = public_models.invoke(
        "artifact.list",
        callback,
        capabilities=frozenset(),
    )
    assert denied.status_code == 403
    denied_payload = json.loads(denied.body)
    assert denied_payload["schema_version"] == "bb.cli.result.v1"
    assert denied_payload["error"]["error_code"] == "capability_required"

    unknown = public_models.invoke("unknown.operation", callback)
    assert unknown.status_code == 404
    assert json.loads(unknown.body)["error"]["error_code"] == "unknown_operation"

    wrong_dispatch = public_models.invoke("session.start", callback)
    assert wrong_dispatch.status_code == 422
    assert (
        json.loads(wrong_dispatch.body)["error"]["error_code"]
        == "idempotency_mode_mismatch"
    )

    keyed_denied = public_models.invoke_idempotent(
        "session.start",
        "start-key",
        {},
        callback,
        capabilities=frozenset(),
    )
    assert keyed_denied.status_code == 403
    assert calls == []

    allowed = public_models.invoke(
        "artifact.list",
        callback,
        capabilities=public_models.PUBLIC_CAPABILITIES,
    )
    assert allowed.status_code == 200
    assert calls == [public_models._ALL_PUBLIC_CAPABILITIES]


def test_http_capability_grant_gates_effect_before_public_callback(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    calls: list[frozenset[str]] = []

    def list_effect(_request, context):
        calls.append(context.capabilities)
        return OperationResult.success(["artifact", "list"], stage="artifact.list")

    monkeypatch.setattr(public_artifact, "run_list_artifacts", list_effect)
    allowed = _client(monkeypatch, tmp_path).get("/v1/artifacts")
    assert allowed.status_code == 200
    assert calls == [public_models.PUBLIC_CAPABILITIES]

    monkeypatch.setattr(
        app_module,
        "_public_request_principal",
        lambda _request, _required_token: public_models.PublicPrincipal("anonymous"),
    )
    denied = _client(monkeypatch, tmp_path).get("/v1/artifacts")
    assert denied.status_code == 403
    assert denied.json()["error"]["error_code"] == "capability_required"
    assert calls == [public_models.PUBLIC_CAPABILITIES]
    assert "public.artifact.read" in calls[0]


@pytest.mark.asyncio
async def test_generated_capability_policy_gates_async_dispatch_before_callbacks() -> (
    None
):
    calls = 0

    async def callback(_workspace: Path) -> OperationResult:
        nonlocal calls
        calls += 1
        return OperationResult.success(["artifact", "list"], stage="artifact.list")

    denied = await public_models.invoke_async(
        "artifact.list",
        callback,
        capabilities=frozenset(),
    )
    keyed_denied = await public_models.invoke_idempotent_async(
        "session.start",
        "start-key",
        {},
        callback,
        capabilities=frozenset(),
    )

    assert denied.status_code == 403
    assert keyed_denied.status_code == 403
    assert calls == 0
