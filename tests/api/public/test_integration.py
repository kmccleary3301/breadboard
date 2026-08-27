from __future__ import annotations
from pathlib import Path
from types import SimpleNamespace
from fastapi.testclient import TestClient
from breadboard_engine.api.cli_bridge.app import create_app
from breadboard_engine.api.public import integration as api_integration
from breadboard.product.cli import integration as cli_integration
from breadboard.product.operations.integration import (
    ProbeIntegrationRequest,
    probe_integration,
)
from breadboard.product.operations.model import OperationContext
from breadboard.product.integrations.catalog import (
    IntegrationCatalog,
    IntegrationDescriptor,
    ProbeReport,
)


class _Adapter:
    descriptor = IntegrationDescriptor(
        schema_version="bb.integration_descriptor.v1",
        integration_id="fixture.provider",
        kind="provider_adapter",
        contract_version="v1",
        implementation_id="fixture:provider",
        capabilities=("chat",),
        secret_reference_names=("FIXTURE_TOKEN",),
    )

    probe_calls = 0

    def probe(self) -> ProbeReport:
        type(self).probe_calls += 1
        return ProbeReport(
            schema_version="bb.capability_probe_report.v1",
            report_id="probe:fixture.provider",
            integration_id="fixture.provider",
            kind="provider_adapter",
            implementation_id="fixture:provider",
            status="available",
            capabilities=("chat",),
            checked_at_utc="2026-07-24T00:00:00Z",
        )


def _client(monkeypatch, workspace: Path) -> TestClient:
    monkeypatch.setenv("BREADBOARD_PUBLIC_WORKSPACE", str(workspace))
    monkeypatch.setenv("BREADBOARD_ENABLE_E4_API", "0")
    monkeypatch.setenv("BREADBOARD_ENABLE_PUBLIC_API", "1")
    monkeypatch.setenv("RAY_SCE_LOCAL_MODE", "1")
    monkeypatch.setenv("FIXTURE_TOKEN", "token-value-must-not-leak")
    monkeypatch.setattr(
        api_integration.integration_operations,
        "_catalog",
        lambda: IntegrationCatalog((_Adapter(),)),
    )
    return TestClient(create_app(include_atp_routes=False))


def _success_result(command: list[str], data: dict) -> dict:
    stage = ".".join(command)
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


def test_integration_list_get_and_async_probe(monkeypatch, tmp_path: Path) -> None:
    client = _client(monkeypatch, tmp_path)
    descriptor = {
        "schema_version": "bb.integration_descriptor.v1",
        "integration_id": "fixture.provider",
        "kind": "provider_adapter",
        "contract_version": "v1",
        "implementation_id": "fixture:provider",
        "capabilities": ["chat"],
        "configuration_schema_id": None,
        "secret_reference_names": ["FIXTURE_TOKEN"],
        "status": "available",
        "probe_evidence_sha256": None,
    }
    expected_listing = _success_result(
        ["integration", "list"],
        {"integrations": [descriptor], "count": 1},
    )
    expected_get = _success_result(
        ["integration", "get"],
        {"integration": descriptor},
    )
    expected_probe = _success_result(
        ["integration", "probe"],
        {
            "probe": {
                "schema_version": "bb.capability_probe_report.v1",
                "report_id": "probe:fixture.provider",
                "integration_id": "fixture.provider",
                "kind": "provider_adapter",
                "implementation_id": "fixture:provider",
                "identity": {
                    "integration_id": "fixture.provider",
                    "kind": "provider_adapter",
                    "implementation_id": "fixture:provider",
                },
                "status": "available",
                "capabilities": ["chat"],
                "effects": [],
                "permissions": [],
                "error": None,
                "checked_at_utc": "2026-07-24T00:00:00Z",
            }
        },
    )
    assert (
        probe_integration(
            ProbeIntegrationRequest("fixture.provider"),
            OperationContext(workspace=tmp_path),
        ).as_dict()
        == expected_probe
    )

    listing = client.get("/v1/integrations")
    fetched = client.get("/v1/integrations/fixture.provider")
    cli_arguments = SimpleNamespace(workspace=tmp_path)

    assert listing.status_code == 200
    assert listing.json() == expected_listing
    assert fetched.status_code == 200
    assert fetched.json() == expected_get
    assert cli_integration.list_integrations(cli_arguments).as_dict() == (
        expected_listing
    )
    cli_arguments.INTEGRATION_ID = "fixture.provider"
    assert cli_integration.get(cli_arguments).as_dict() == expected_get
    assert cli_integration.probe(cli_arguments).as_dict() == expected_probe
    assert cli_integration.probe(SimpleNamespace(workspace=tmp_path)).as_dict() == (
        _success_result(
            ["integration", "probe"],
            {"probe": [expected_probe["data"]["probe"]]},
        )
    )
    before_reads = {
        path.relative_to(tmp_path).as_posix(): path.read_bytes()
        for path in tmp_path.rglob("*")
        if path.is_file() and not path.is_symlink()
    }
    assert client.get("/v1/integrations").status_code == 200
    assert client.get("/v1/integrations/fixture.provider").status_code == 200
    after_reads = {
        path.relative_to(tmp_path).as_posix(): path.read_bytes()
        for path in tmp_path.rglob("*")
        if path.is_file() and not path.is_symlink()
    }
    assert after_reads == before_reads
    probe_calls_before_http = _Adapter.probe_calls
    probed = client.post(
        "/v1/integrations/fixture.provider/probe",
        headers={"Idempotency-Key": "probe-fixture"},
    )
    replayed = client.post(
        "/v1/integrations/fixture.provider/probe",
        headers={"Idempotency-Key": "probe-fixture"},
    )
    assert probed.status_code == 202
    assert replayed.status_code == 202
    assert probed.json() == expected_probe
    assert replayed.json() == expected_probe
    assert _Adapter.probe_calls == probe_calls_before_http + 1
    assert "FIXTURE_TOKEN" in fetched.text
    assert "token-value-must-not-leak" not in fetched.text + probed.text
    missing = client.get("/v1/integrations/missing")
    assert (
        missing.status_code == 409
        and missing.json()["error"]["error_code"] == "integration_not_found"
    )
    assert missing.json()["command"] == ["integration", "get"]
    assert missing.json()["ok"] is False
    assert missing.json()["exit_code"] == 6
    assert missing.json()["error"] == {
        "schema_version": "bb.problem.v1",
        "error_code": "integration_not_found",
        "message": "integration not found: missing",
        "record_refs": [],
        "failed_stage": "integration.get",
        "hint": None,
        "next_actions": ["breadboard integration list"],
    }
    assert missing.json()["stage_outcomes"] == [
        {
            "stage": "integration.get",
            "status": "blocked",
            "report_ref": None,
            "next_action": "breadboard integration list",
        }
    ]


def test_default_catalog_exposes_provider_free_capture_adapters(
    monkeypatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("BREADBOARD_PUBLIC_WORKSPACE", str(tmp_path))
    monkeypatch.setenv("BREADBOARD_ENABLE_E4_API", "0")
    monkeypatch.setenv("BREADBOARD_ENABLE_PUBLIC_API", "1")
    monkeypatch.setenv("RAY_SCE_LOCAL_MODE", "1")
    client = TestClient(create_app(include_atp_routes=False))

    listing = client.get("/v1/integrations")
    assert listing.status_code == 200
    assert [
        row["integration_id"] for row in listing.json()["data"]["integrations"]
    ] == ["capture:json", "capture:memory"]

    fetched = client.get("/v1/integrations/capture%3Amemory")
    assert fetched.status_code == 200
    assert fetched.json()["data"]["integration"]["implementation_id"] == (
        "breadboard.memory"
    )

    probed = client.post(
        "/v1/integrations/capture%3Amemory/probe",
        headers={"Idempotency-Key": "probe-default-capture-memory"},
    )
    assert probed.status_code == 202
    report = probed.json()["data"]["probe"]
    assert report["integration_id"] == "capture:memory"
    assert report["status"] == "available"
    assert report["effects"] == ["record"]
    assert report["permissions"] == ["capture.write"]
