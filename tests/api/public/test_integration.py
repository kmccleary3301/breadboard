from __future__ import annotations
from pathlib import Path
from fastapi.testclient import TestClient
from agentic_coder_prototype.api.cli_bridge.app import create_app
from agentic_coder_prototype.api.public import integration as api_integration
from breadboard.product.integrations.catalog import IntegrationCatalog, IntegrationDescriptor, ProbeReport
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
    def probe(self) -> ProbeReport:
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
    monkeypatch.setenv("RAY_SCE_LOCAL_MODE", "1")
    monkeypatch.setenv("FIXTURE_TOKEN", "token-value-must-not-leak")
    monkeypatch.setattr(api_integration.operations, "_catalog", lambda: IntegrationCatalog((_Adapter(),)))
    return TestClient(create_app(include_atp_routes=False))
def test_integration_list_get_and_async_probe(monkeypatch, tmp_path: Path) -> None:
    client = _client(monkeypatch, tmp_path)
    listing = client.get("/v1/integrations")
    assert listing.status_code == 200
    assert listing.json()["data"]["integrations"][0]["integration_id"] == "fixture.provider"
    fetched = client.get("/v1/integrations/fixture.provider")
    assert fetched.status_code == 200
    assert fetched.json()["data"]["integration"] == _Adapter.descriptor.to_record()
    probed = client.post("/v1/integrations/fixture.provider/probe", headers={"Idempotency-Key": "probe-fixture"})
    assert probed.status_code == 202
    assert probed.json()["data"]["probe"]["schema_version"] == "bb.capability_probe_report.v1"
    assert "FIXTURE_TOKEN" in fetched.text
    assert "token-value-must-not-leak" not in fetched.text + probed.text
    missing = client.get("/v1/integrations/missing")
    assert missing.status_code == 409 and missing.json()["error"]["error_code"] == "integration_not_found"
