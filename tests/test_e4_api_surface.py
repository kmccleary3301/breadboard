from __future__ import annotations
import json

from pathlib import Path

import pytest
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

from agentic_coder_prototype.api.cli_bridge import runtime_emission
from agentic_coder_prototype.api.cli_bridge.app import create_app
from agentic_coder_prototype.api.cli_bridge.runtime_emission import emit_session_start_records
from agentic_coder_prototype.api.cli_bridge.models import SessionCreateRequest, SessionStatus
from agentic_coder_prototype.api.cli_bridge.registry import SessionRecord
from agentic_coder_prototype.api.cli_bridge.service import SessionService
from agentic_coder_prototype.api.e4 import create_e4_router
from agentic_coder_prototype.api.e4.models import E4ApiError


ACCEPTED_CLAIM_ID = "oh_my_pi_p6_0_l4_mcp_browser_resource_v1_c4_support_claim"
ACCEPTED_LANE_ID = "oh_my_pi_p6_0_l4_mcp_browser_resource"
COMPARATOR_SCHEMA = "bb.e4.comparator_report.v1"




@pytest.fixture()
def client() -> TestClient:
    return TestClient(create_app())


@pytest.mark.parametrize("flag_value", ["0", "false", "no"])
def test_e4_mount_gate_excludes_versioned_routes_when_disabled(
    monkeypatch: pytest.MonkeyPatch, flag_value: str
) -> None:
    monkeypatch.setenv("BREADBOARD_ENABLE_E4_API", flag_value)

    response = TestClient(create_app()).get("/v1/e4/health")

    assert response.status_code == 404


@pytest.mark.parametrize("flag_value", [None, "1", "true", "yes", "on"])
def test_e4_mount_gate_serves_versioned_routes_by_default_or_when_enabled(
    monkeypatch: pytest.MonkeyPatch, flag_value: str | None
) -> None:
    if flag_value is None:
        monkeypatch.delenv("BREADBOARD_ENABLE_E4_API", raising=False)
    else:
        monkeypatch.setenv("BREADBOARD_ENABLE_E4_API", flag_value)

    response = TestClient(create_app()).get("/v1/e4/health")

    assert response.status_code == 200
    assert response.json()["ok"] is True


@pytest.mark.parametrize("flag_value", ["1", "true", "yes", "on"])
def test_legacy_routes_can_be_explicitly_enabled(
    monkeypatch: pytest.MonkeyPatch, flag_value: str
) -> None:
    monkeypatch.setenv("BREADBOARD_LEGACY_ROUTES", flag_value)

    local_client = TestClient(create_app())

    assert local_client.get("/status").status_code == 200
    assert local_client.get("/v1/status").status_code == 200
    assert local_client.get("/sessions").status_code == 200
    assert local_client.get("/v1/sessions").status_code == 200
    assert local_client.get("/rl/runs/probe").status_code == 400
    assert local_client.get("/v1/rl/runs/probe").status_code == 400


def test_legacy_routes_default_off_removes_unversioned_aliases(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("BREADBOARD_LEGACY_ROUTES", raising=False)

    local_client = TestClient(create_app())

    assert local_client.get("/status").status_code == 404
    assert local_client.get("/features").status_code == 404
    assert local_client.get("/models", params={"config_path": "agent_configs/test.yaml"}).status_code == 404
    assert local_client.get("/sessions").status_code == 404
    assert local_client.get("/rl/runs/probe").status_code == 404
    assert local_client.get("/health").status_code == 200
    assert local_client.get("/v1/status").status_code == 200
    assert local_client.get("/v1/features").status_code == 200
    assert local_client.get("/v1/sessions").status_code == 200
    assert local_client.get("/v1/rl/runs/probe").status_code == 400

@pytest.mark.parametrize("flag_value", ["0", "false", "no", "off"])
def test_legacy_routes_flag_off_removes_unversioned_aliases(
    monkeypatch: pytest.MonkeyPatch, flag_value: str
) -> None:
    monkeypatch.setenv("BREADBOARD_LEGACY_ROUTES", flag_value)

    local_client = TestClient(create_app())

    assert local_client.get("/status").status_code == 404
    assert local_client.get("/features").status_code == 404
    assert local_client.get("/models", params={"config_path": "agent_configs/test.yaml"}).status_code == 404
    assert local_client.get("/sessions").status_code == 404
    assert local_client.get("/rl/runs/probe").status_code == 404
    assert local_client.get("/health").status_code == 200
    assert local_client.get("/v1/status").status_code == 200
    assert local_client.get("/v1/features").status_code == 200
    assert local_client.get("/v1/sessions").status_code == 200
    assert local_client.get("/v1/rl/runs/probe").status_code == 400


def test_validate_e4_c4_chain_shim_reexports_public_conformance_functions() -> None:
    from agentic_coder_prototype.conformance import c4_chain
    from scripts.validate_e4_c4_chain import _diff_comparator_reports, validate_c4_chain

    assert validate_c4_chain is c4_chain.validate_c4_chain
    assert _diff_comparator_reports is c4_chain.diff_comparator_reports


def test_e4_openapi_exposes_versioned_read_surface(client: TestClient) -> None:
    schema = client.get("/openapi.json").json()
    assert {path for path in schema["paths"] if path.startswith("/v1/e4/")} == {
        "/v1/e4/catalog",
        "/v1/e4/catalog/binding",
        "/v1/e4/claims",
        "/v1/e4/claims/{claim_id}",
        "/v1/e4/claims/{claim_id}/reverify",
        "/v1/e4/coverage/{target_family}",
        "/v1/e4/health",
        "/v1/e4/lanes",
        "/v1/e4/lanes/{lane_id}",
        "/v1/e4/ledger/rows",
        "/v1/e4/records",
        "/v1/e4/registries",
        "/v1/e4/registries/{registry_id}",
        "/v1/e4/schemas",
        "/v1/e4/schemas/{schema_id}",
    }


def test_e4_health_schemas_and_lane_filters_read_accepted_inventory(client: TestClient) -> None:
    health = client.get("/v1/e4/health")
    assert health.status_code == 200
    assert health.json()["ok"] is True
    assert health.json()["inventory_revision"] >= 1

    schemas = client.get("/v1/e4/schemas")
    assert schemas.status_code == 200
    schema_rows = {schema["schema_id"]: schema for schema in schemas.json()["schemas"]}
    assert "bb.e4.lane_inventory.v1" in schema_rows
    assert schema_rows["bb.e4.lane_inventory.v1"]["pack"] == "legacy_frozen"
    assert "bb.e4.support_claim.v1" in schema_rows
    assert schema_rows["bb.e4.support_claim.v1"]["pack"] == "legacy_frozen"
    assert schema_rows["bb.e4.support_claim.v4"]["pack"] == "e4"
    assert schema_rows["bb.kernel_event.v2"]["pack"] == "kernel"
    lanes = client.get("/v1/e4/lanes", params={"status": "accepted"})
    assert lanes.status_code == 200
    payload = lanes.json()
    assert len(payload["lanes"]) >= 21
    assert {lane["status"] for lane in payload["lanes"]} == {"accepted"}

    oh_my_pi_lanes = client.get("/v1/e4/lanes", params={"status": "accepted", "target_family": "oh_my_pi"})
    assert oh_my_pi_lanes.status_code == 200
    assert {lane["target_family"] for lane in oh_my_pi_lanes.json()["lanes"]} == {"oh_my_pi"}


def test_e4_lane_claim_catalog_ledger_and_records_resolve_real_artifacts(client: TestClient) -> None:
    lane = client.get(f"/v1/e4/lanes/{ACCEPTED_LANE_ID}")
    assert lane.status_code == 200
    lane_payload = lane.json()
    assert lane_payload["lane"]["lane_id"] == ACCEPTED_LANE_ID
    assert lane_payload["resolved_artifacts"]
    assert all(artifact["exists"] for artifact in lane_payload["resolved_artifacts"])

    claims = client.get("/v1/e4/claims", params={"accepted": "true", "kind": "target_support"})
    assert claims.status_code == 200
    claim_ids = {claim["claim_id"] for claim in claims.json()["claims"]}
    assert ACCEPTED_CLAIM_ID in claim_ids

    claim = client.get(f"/v1/e4/claims/{ACCEPTED_CLAIM_ID}")
    assert claim.status_code == 200
    claim_payload = claim.json()
    assert claim_payload["claim"]["claim_id"] == ACCEPTED_CLAIM_ID
    assert claim_payload["claim"]["accepted"] is True
    assert claim_payload["evidence_manifest"]["schema_version"] == "bb.e4.evidence_manifest.v1"

    catalog = client.get("/v1/e4/catalog", params={"lane_id": ACCEPTED_LANE_ID, "limit": 5})
    assert catalog.status_code == 200
    catalog_payload = catalog.json()
    assert catalog_payload["total"] >= len(catalog_payload["entries"]) > 0
    assert {entry["lane_id"] for entry in catalog_payload["entries"]} == {ACCEPTED_LANE_ID}
    ledger = client.get("/v1/e4/ledger/rows", params={"feature_id": "feat_478375f6517dcc00"})
    assert ledger.status_code == 200
    ledger_rows = ledger.json()["rows"]
    assert ledger_rows
    assert {row["feature_id"] for row in ledger_rows} == {"feat_478375f6517dcc00"}

    records = client.get("/v1/e4/records", params={"schema_version": COMPARATOR_SCHEMA, "lane_id": ACCEPTED_LANE_ID})
    assert records.status_code == 200
    record_payload = records.json()
    assert record_payload["records"]
    assert {record["schema_version"] for record in record_payload["records"]} == {COMPARATOR_SCHEMA}


def test_e4_catalog_binding_exposes_current_catalog_hash(client: TestClient) -> None:
    response = client.get("/v1/e4/catalog/binding")

    assert response.status_code == 200
    payload = response.json()
    assert payload["catalog_path"] == "docs/conformance/e4_artifact_catalog.json"
    assert payload["schema_version"] == "bb.e4.artifact_catalog.v2"
    assert payload["segments"][0]["segment_id"] in {"global", "shared"}
    assert payload["segments"][0]["stable_entries_hash"].startswith("sha256:")
    assert payload["segments_hash"].startswith("sha256:")


def test_registries_endpoints_expose_contract_registry_files(client: TestClient) -> None:
    list_response = client.get("/v1/registries")

    assert list_response.status_code == 200
    registries = list_response.json()["registries"]
    registry_ids = {registry["registry_id"] for registry in registries}
    assert {"target_families", "kernel_event_kinds", "kernel_families"}.issubset(registry_ids)

    registry_response = client.get("/v1/registries/target_families")
    assert registry_response.status_code == 200
    payload = registry_response.json()
    assert payload["registry_id"] == "target_families"
    assert payload["schema_version"] == "bb.registry.v1"
    assert any(entry["id"] == "oh_my_pi" for entry in payload["entries"])

    missing = client.get("/v1/registries/not_a_registry")
    assert missing.status_code == 404
    assert missing.json()["error"] == "registry_not_found"


def test_split_v1_session_file_endpoints_list_and_read_content(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "alpha.txt").write_text("alpha\n", encoding="utf-8")
    (workspace / "subdir").mkdir()

    class FakeRunner:
        def get_workspace_dir(self) -> Path:
            return workspace

    service = SessionService()
    record = SessionRecord(session_id="file-session", status=SessionStatus.RUNNING)
    record.runner = FakeRunner()
    service.registry._records[record.session_id] = record
    local_client = TestClient(create_app(service=service))

    listing = local_client.get("/v1/sessions/file-session/files")
    assert listing.status_code == 200
    assert listing.json() == [
        {"path": "subdir", "type": "directory", "size": None, "updated_at": None},
        {"path": "alpha.txt", "type": "file", "size": 6, "updated_at": None},
    ]

    content = local_client.get("/v1/sessions/file-session/files/content", params={"path": "alpha.txt"})
    assert content.status_code == 200
    assert content.json() == {"path": "alpha.txt", "content": "alpha\n", "truncated": False, "total_bytes": 6}


def test_session_records_endpoint_serves_runtime_jsonl_with_schema_filter(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    runtime_root = tmp_path / "runtime_records"
    monkeypatch.setenv("BREADBOARD_RUNTIME_RECORD_ROOT", str(runtime_root))
    emit_session_start_records(
        session_id="records-session",
        request=SessionCreateRequest(
            config_path="agent_configs/atp_hilbert_like_gpt54_v1.yaml",
            task="runtime records endpoint probe",
        ),
        generated_at="2026-07-04T00:00:00Z",
    )
    service = SessionService()
    record = SessionRecord(
        session_id="records-session",
        status=SessionStatus.RUNNING,
        metadata={"runtime_record_dir": str(runtime_root / "records-session")},
    )
    service.registry._records[record.session_id] = record
    local_client = TestClient(create_app(service=service))

    response = local_client.get(
        "/v1/sessions/records-session/records",
        params={"schema_version": "bb.work_item.v1", "offset": 0, "limit": 1},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["session_id"] == "records-session"
    assert payload["total"] == 2
    assert payload["offset"] == 0
    assert payload["limit"] == 1
    assert len(payload["records"]) == 1
    assert payload["records"][0]["schema_version"] == "bb.work_item.v1"
    assert payload["records"][0]["record"]["state"]["status"] in {"queued", "running"}


def test_api_error_envelope_covers_auth_and_validation(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BREADBOARD_API_TOKEN", "secret")
    auth_response = TestClient(create_app()).get("/v1/sessions")
    assert auth_response.status_code == 401
    assert auth_response.json() == {"error": "unauthorized", "detail": "unauthorized", "path": None}

    validation_response = TestClient(create_app()).post("/v1/sessions", json={}, headers={"Authorization": "Bearer secret"})
    assert validation_response.status_code == 422
    assert validation_response.json()["error"] == "invalid_request"
    assert "errors" in validation_response.json()["detail"]


def test_e4_paginated_resources_report_pre_slice_total_and_return_requested_window(
    client: TestClient,
) -> None:
    cases = [
        ("/v1/e4/lanes", {}, "lanes", 2, 3),
        ("/v1/e4/claims", {}, "claims", 1, 4),
        ("/v1/e4/ledger/rows", {}, "rows", 3, 2),
        ("/v1/e4/records", {"schema_version": COMPARATOR_SCHEMA}, "records", 1, 2),
    ]

    for path, params, item_key, offset, limit in cases:
        full_response = client.get(path, params={**params, "offset": 0, "limit": 1000})
        assert full_response.status_code == 200
        full_payload = full_response.json()
        full_items = full_payload[item_key]
        assert full_payload["total"] == len(full_items)
        assert full_payload["total"] > offset

        page_response = client.get(path, params={**params, "offset": offset, "limit": limit})
        assert page_response.status_code == 200
        page_payload = page_response.json()

        assert page_payload["total"] == full_payload["total"]
        assert page_payload[item_key] == full_items[offset : offset + limit]
        assert len(page_payload[item_key]) == min(limit, full_payload["total"] - offset)


def test_e4_claim_reverify_runs_check_only_without_writing_probe_artifacts(client: TestClient) -> None:
    json_out = Path("artifacts/conformance/node_gate/ct_p6_oh_my_pi_l4_c4_chain.json")
    before_bytes = json_out.read_bytes()
    before_mtime_ns = json_out.stat().st_mtime_ns

    response = client.post(f"/v1/e4/claims/{ACCEPTED_CLAIM_ID}/reverify", json={"rerun_comparators": True})
    assert response.status_code == 200
    payload = response.json()
    assert payload["claim_id"] == ACCEPTED_CLAIM_ID
    assert payload["mode"] == "check_only"
    assert payload["comparator_rerun"] is True
    assert payload["ok"] is True
    assert payload["errors"] == []
    assert json_out.read_bytes() == before_bytes
    assert json_out.stat().st_mtime_ns == before_mtime_ns


def test_e4_claim_reverify_defaults_to_check_only_without_comparator_rerun(client: TestClient) -> None:

    response = client.post(f"/v1/e4/claims/{ACCEPTED_CLAIM_ID}/reverify", json={})
    assert response.status_code == 200

    payload = response.json()
    assert payload["ok"] is True
    assert payload["comparator_rerun"] is False
    assert payload["mode"] == "check_only"
    assert payload["errors"] == []


def test_e4_missing_resources_return_flat_error_envelope(client: TestClient) -> None:
    missing_lane = client.get("/v1/e4/lanes/not_a_lane")
    assert missing_lane.status_code == 404
    assert missing_lane.json() == {"error": "lane_not_found", "detail": "not_a_lane", "path": None}

    assert client.get("/v1/e4/claims/not_a_claim").status_code == 404
    assert client.get("/v1/e4/schemas/not_a_schema").status_code == 404
    assert client.get("/v1/e4/coverage/not_a_target_family").status_code == 404

def test_e4_claim_detail_missing_evidence_manifest_returns_flat_error_envelope(tmp_path: Path) -> None:
    repo_root = Path(__file__).resolve().parents[1]
    claims_dir = tmp_path / "support_claims"
    claims_dir.mkdir()
    broken_claim_id = "broken_missing_manifest_claim"
    claim = json.loads((repo_root / "docs" / "conformance" / "support_claims" / f"{ACCEPTED_CLAIM_ID}.json").read_text(encoding="utf-8"))
    claim["claim_id"] = broken_claim_id
    claim["evidence_manifest_ref"] = "docs/conformance/support_claims/missing_evidence_manifest.json"
    (claims_dir / f"{broken_claim_id}_support_claim.json").write_text(json.dumps(claim, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    app = FastAPI()

    @app.exception_handler(E4ApiError)
    async def _e4_api_error_handler(_request: Request, exc: E4ApiError) -> JSONResponse:
        return JSONResponse(status_code=exc.status_code, content={"error": exc.error, "detail": exc.detail_text, "path": exc.path})

    app.include_router(
        create_e4_router(
            repo_root=repo_root,
            inventory_path=repo_root / "docs" / "conformance" / "e4_lane_inventory.json",
            catalog_path=repo_root / "docs" / "conformance" / "e4_artifact_catalog.json",
            claims_dir=claims_dir,
            schemas_dir=repo_root / "contracts" / "kernel" / "schemas",
            ledger_path=repo_root.parent / "docs_tmp" / "phase_15" / "BB_E4_ATOMIC_FEATURE_LEDGER_SEED.json",
            coverage_dir=repo_root.parent / "docs_tmp" / "phase_16" / "coverage",
            runtime_records_dir=tmp_path / "runtime_records",
        ),
        prefix="/v1/e4",
    )

    response = TestClient(app).get(f"/v1/e4/claims/{broken_claim_id}")

    assert response.status_code == 409
    assert response.json()["error"] == "evidence_manifest_unavailable"
    assert response.json()["path"] == "docs/conformance/support_claims/missing_evidence_manifest.json"



def test_runtime_emission_flag_off_writes_no_records(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.delenv("BREADBOARD_EMIT_PRIMITIVES", raising=False)
    monkeypatch.setenv("BREADBOARD_RUNTIME_RECORD_ROOT", str(tmp_path / "runtime_records"))
    async def _noop_start(self: object) -> None:
        return None

    monkeypatch.setattr("agentic_coder_prototype.api.cli_bridge.service.SessionRunner.start", _noop_start)

    client = TestClient(create_app())

    response = client.post(
        "/v1/sessions",
        json={"config_path": "agent_configs/atp_hilbert_like_gpt54_v1.yaml", "task": "runtime emission flag off probe"},
    )
    assert response.status_code == 200
    records = client.get(
        "/v1/e4/records",
        params={"schema_version": "bb.effective_config_graph.v1", "source": "runtime"},
    )
    assert records.json()["records"] == []
    assert not (tmp_path / "runtime_records").exists()


def test_runtime_emission_records_are_served_by_e4_api(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    runtime_root = tmp_path / "runtime_records"
    monkeypatch.setenv("BREADBOARD_RUNTIME_RECORD_ROOT", str(runtime_root))
    paths = emit_session_start_records(
        session_id="api_surface_runtime_session",
        request=SessionCreateRequest(
            config_path="agent_configs/atp_hilbert_like_gpt54_v1.yaml",
            task="runtime emission probe",
        ),
        generated_at="2026-07-04T00:00:00Z",
    )

    payloads = {name: Path(path).read_text(encoding="utf-8") for name, path in paths.items()}
    assert set(payloads) == {
        "capability_registry",
        "effective_config_graph",
        "effective_operation_policy",
        "effective_tool_surface",
        "work_item_queued",
        "work_item_running",
        "coordination_slice",
    }

    client = TestClient(create_app())
    graph_records = client.get(
        "/v1/e4/records",
        params={"schema_version": "bb.effective_config_graph.v1", "source": "runtime"},
    )
    assert graph_records.status_code == 200
    assert [record["record"]["graph_id"] for record in graph_records.json()["records"]] == [
        "api_surface_runtime_session_effective_config_graph"
    ]

    work_records = client.get(
        "/v1/e4/records",
        params={"schema_version": "bb.work_item.v1", "source": "runtime"},
    )
    assert work_records.status_code == 200
    states = {record["record"]["state"]["status"] for record in work_records.json()["records"]}
    assert states == {"queued", "running"}

def test_runtime_emission_dual_dialect_serves_coordination_pack(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    runtime_root = tmp_path / "runtime_records"
    monkeypatch.setenv("BREADBOARD_RUNTIME_RECORD_ROOT", str(runtime_root))
    monkeypatch.setenv("BREADBOARD_CONFIG_PLANE_DIALECT", "both")
    paths = emit_session_start_records(
        session_id="api_surface_dual_dialect_session",
        request=SessionCreateRequest(
            config_path="agent_configs/atp_hilbert_like_gpt54_v1.yaml",
            task="runtime emission dialect probe",
        ),
        generated_at="2026-07-04T00:00:00Z",
    )

    assert {"coordination_slice", "coordination_pack"} <= set(paths)
    pack = json.loads(Path(paths["coordination_pack"]).read_text(encoding="utf-8"))
    assert pack["schema_version"] == "bb.coordination_pack.v3"
    assert pack["records"]["slices"][0]["schema_version"] == "bb.coordination_slice.v2"

    client = TestClient(create_app())
    response = client.get(
        "/v1/e4/records",
        params={"schema_version": "bb.coordination_pack.v3", "source": "runtime"},
    )
    assert response.status_code == 200
    assert [record["record"]["pack_id"] for record in response.json()["records"]] == [
        "api_surface_dual_dialect_session_coordination_pack"
    ]


def test_runtime_emission_legacy_dialect_fallback_warns_once(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    runtime_root = tmp_path / "runtime_records"
    monkeypatch.setenv("BREADBOARD_RUNTIME_RECORD_ROOT", str(runtime_root))
    monkeypatch.delenv("BREADBOARD_CONFIG_PLANE_DIALECT", raising=False)
    monkeypatch.setenv("BREADBOARD_PRIMITIVES_DIALECT", "both")
    monkeypatch.setattr(runtime_emission, "_CONFIG_PLANE_DIALECT_FALLBACK_WARNED", False)

    def emit(session_id: str) -> dict[str, str]:
        return runtime_emission.emit_session_start_records(
            session_id=session_id,
            request=SessionCreateRequest(
                config_path="agent_configs/atp_hilbert_like_gpt54_v1.yaml",
                task="runtime emission dialect fallback probe",
            ),
            generated_at="2026-07-04T00:00:00Z",
        )

    first_paths = emit("api_surface_legacy_dialect_session_one")
    second_paths = emit("api_surface_legacy_dialect_session_two")

    assert {"coordination_slice", "coordination_pack"} <= set(first_paths)
    assert {"coordination_slice", "coordination_pack"} <= set(second_paths)
    stderr = capsys.readouterr().err
    assert stderr.count("BREADBOARD_PRIMITIVES_DIALECT is deprecated") == 1

def test_runtime_records_reject_lane_filter(client: TestClient) -> None:
    response = client.get(
        "/v1/e4/records",
        params={"schema_version": "bb.effective_config_graph.v1", "source": "runtime", "lane_id": ACCEPTED_LANE_ID},
    )

    assert response.status_code == 400
    assert response.json() == {
        "error": "unsupported_filter",
        "detail": "lane_id is not supported for source=runtime",
        "path": None,
    }