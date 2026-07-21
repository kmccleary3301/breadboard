from __future__ import annotations

import hashlib
import json

from pathlib import Path
from typing import Any

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
from agentic_coder_prototype.conformance.catalog_binding import (
    catalog_segments,
    catalog_segments_hash,
    stable_entries_hash,
)


ACCEPTED_CLAIM_ID = "oh_my_pi_p6_0_l4_mcp_browser_resource_v1_c4_support_claim"
ACCEPTED_LANE_ID = "oh_my_pi_p6_0_l4_mcp_browser_resource"
COMPARATOR_SCHEMA = "bb.e4.comparator_report.v1"




def _write_json(path: Path, payload: object) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


def _stable_hash(payload: object) -> str:
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


@pytest.fixture()
def e4_fixture(tmp_path: Path) -> dict[str, object]:
    repo = tmp_path / "repo"
    inventory_path = repo / "docs/conformance/e4_lane_inventory.json"
    catalog_path = repo / "docs/conformance/e4_artifact_catalog.json"
    claims_dir = repo / "docs/conformance/support_claims"
    schemas_dir = repo / "contracts/kernel/schemas"
    ledger_path = tmp_path / "workspace/phase_15/atomic_feature_ledger.json"
    coverage_dir = tmp_path / "workspace/phase_16/coverage"
    runtime_records_dir = tmp_path / "runtime_records"

    lane_ids = [ACCEPTED_LANE_ID, *(f"fixture_lane_{index}" for index in range(1, 6))]
    lanes = [
        {
            "lane_id": lane_id,
            "phase": "fixture",
            "kind": "target_support",
            "status": "accepted",
            "target_family": "oh_my_pi" if index < 3 else "fixture",
            "artifact_roles": {"comparator": f"{lane_id}:comparator"},
        }
        for index, lane_id in enumerate(lane_ids)
    ]
    _write_json(inventory_path, {"revision": 1, "lanes": lanes})

    entries: list[dict[str, object]] = []
    for index, lane_id in enumerate(lane_ids):
        record_path = repo / f"evidence/{lane_id}/comparator_{index}.json"
        digest = _write_json(
            record_path,
            {"schema_version": COMPARATOR_SCHEMA, "lane_id": lane_id, "passed": 1},
        )
        entries.append(
            {
                "role_id": f"{lane_id}:comparator",
                "lane_id": lane_id,
                "path": record_path.relative_to(repo).as_posix(),
                "sha256": digest,
                "bytes": record_path.stat().st_size,
                "exists": True,
                "artifact_kind": "comparator",
            }
        )
    entries.append(
        {
            "role_id": "e4_static:config/e4_lane_inventory",
            "path": inventory_path.relative_to(repo).as_posix(),
            "sha256": "sha256:" + hashlib.sha256(inventory_path.read_bytes()).hexdigest(),
            "bytes": inventory_path.stat().st_size,
            "exists": True,
            "artifact_kind": "inventory",
        }
    )
    catalog = {
        "schema_version": "bb.e4.artifact_catalog.v2",
        "revision": 1,
        "generated_at_utc": "2026-07-13T00:00:00Z",
        "entries": entries,
        "segments": catalog_segments(entries),
        "integrity": {
            "entry_count": len(entries),
            "entries_hash": _stable_hash(entries),
            "segments_hash": catalog_segments_hash(entries),
            "stable_entries_hash": stable_entries_hash(entries),
        },
    }
    _write_json(catalog_path, catalog)

    ledger_rows = [
        {
            "feature_id": "feat_478375f6517dcc00" if index == 0 else f"feat_fixture_{index}",
            "lane_id": lane_id,
            "status": "accepted",
        }
        for index, lane_id in enumerate(lane_ids)
    ]
    _write_json(ledger_path, {"schema_version": "bb.atomic_feature_ledger.v1", "rows": ledger_rows})
    ledger_row = ledger_rows[0]
    ledger_row_hash = _stable_hash({"row_id": ledger_row["feature_id"], "row": ledger_row})

    node_gate_path = repo / "artifacts/conformance/node_gate/fixture_c4_chain.json"
    node_gate_hash = _write_json(
        node_gate_path,
        {"schema_version": "bb.e4.validation_report.v1", "ok": True, "errors": []},
    )
    evidence_manifest_path = claims_dir / f"{ACCEPTED_CLAIM_ID}_evidence_manifest.json"
    evidence_manifest = {
        "schema_version": "bb.e4.evidence_manifest.v1",
        "claim_id": ACCEPTED_CLAIM_ID,
        "lane_id": ACCEPTED_LANE_ID,
        "artifacts": [
            {
                "role": "node_gate",
                "path": node_gate_path.relative_to(repo).as_posix(),
                "sha256": node_gate_hash,
            },
            {
                "role": "atomic_feature_ledger",
                "path": ledger_path.as_posix(),
                "sha256": "sha256:" + hashlib.sha256(ledger_path.read_bytes()).hexdigest(),
            },
        ],
    }
    _write_json(evidence_manifest_path, evidence_manifest)

    shared_segment_hash = next(
        segment["stable_entries_hash"] for segment in catalog["segments"] if segment["segment_id"] == "shared"
    )
    segment_hashes = {
        segment["segment_id"]: segment["stable_entries_hash"] for segment in catalog["segments"]
    }
    for index, lane_id in enumerate(lane_ids):
        claim_id = ACCEPTED_CLAIM_ID if index == 0 else f"fixture_claim_{index}"
        claim = {
            "schema_version": "bb.e4.support_claim.v4",
            "claim_id": claim_id,
            "lane_id": lane_id,
            "kind": "target_support",
            "accepted": True,
            "scope": {"config_id": f"fixture_config_{index}", "lane_id": lane_id, "target_family": "oh_my_pi"},
            "evidence_manifest_ref": evidence_manifest_path.relative_to(repo).as_posix(),
            "validation_refs": [f"{node_gate_path.relative_to(repo).as_posix()}#{node_gate_hash}"],
            "ledger_row_refs": [
                f"{ledger_path.as_posix()}#{ledger_row['feature_id']}#{ledger_row_hash}"
            ],
            "catalog_binding": {
                "segment_id": lane_id,
                "segment_hash": segment_hashes[lane_id],
                "shared_segment_hash": shared_segment_hash,
            },
        }
        _write_json(claims_dir / f"{claim_id}_support_claim.json", claim)

    _write_json(
        coverage_dir / "oh_my_pi.json",
        {"schema_version": "bb.e4.coverage_matrix.v1", "target_family": "oh_my_pi", "covered": 1},
    )
    schema_packs: dict[str, list[str]] = {"legacy_frozen": [], "e4": [], "kernel": []}
    for schema_id, pack in (
        ("bb.e4.lane_inventory.v1", "legacy_frozen"),
        ("bb.e4.support_claim.v1", "legacy_frozen"),
        ("bb.e4.support_claim.v4", "e4"),
        ("bb.kernel_event.v2", "kernel"),
    ):
        filename = f"{schema_id}.schema.json"
        _write_json(
            schemas_dir / filename,
            {
                "$id": f"https://example.invalid/{schema_id}.schema.json",
                "properties": {"schema_version": {"const": schema_id}},
            },
        )
        schema_packs[pack].append(filename)
    _write_json(
        repo / "contracts/kernel/packs.v1.json",
        {
            "entries": [
                {"id": pack, "metadata": {"schemas": filenames}}
                for pack, filenames in schema_packs.items()
            ]
        },
    )
    for registry_id in ("target_families", "kernel_event_kinds", "kernel_families"):
        entries = [{"id": "oh_my_pi"}] if registry_id == "target_families" else [{"id": "fixture"}]
        _write_json(
            repo / "contracts/kernel/registries" / f"{registry_id}.json",
            {"registry_id": registry_id, "schema_version": "bb.registry.v1", "entries": entries},
        )

    return {
        "repo": repo,
        "inventory_path": inventory_path,
        "catalog_path": catalog_path,
        "claims_dir": claims_dir,
        "schemas_dir": schemas_dir,
        "ledger_path": ledger_path,
        "coverage_dir": coverage_dir,
        "runtime_records_dir": runtime_records_dir,
        "node_gate_path": node_gate_path,
        "catalog": catalog,
        "lane_ids": lane_ids,
    }


@pytest.fixture()
def client(e4_fixture: dict[str, object]) -> TestClient:
    repo = Path(e4_fixture["repo"])
    app = FastAPI()

    @app.exception_handler(E4ApiError)
    async def _e4_api_error_handler(_request: Request, exc: E4ApiError) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status_code,
            content={"error": exc.error, "detail": exc.detail_text, "path": exc.path},
        )

    app.include_router(
        create_e4_router(
            repo_root=repo,
            inventory_path=Path(e4_fixture["inventory_path"]),
            catalog_path=Path(e4_fixture["catalog_path"]),
            claims_dir=Path(e4_fixture["claims_dir"]),
            schemas_dir=Path(e4_fixture["schemas_dir"]),
            ledger_path=Path(e4_fixture["ledger_path"]),
            coverage_dir=Path(e4_fixture["coverage_dir"]),
            runtime_records_dir=Path(e4_fixture["runtime_records_dir"]),
        ),
        prefix="/v1/e4",
    )
    return TestClient(app)


def _e4_client_for_catalog(repo_root: Path, catalog_path: Path, runtime_records_dir: Path) -> TestClient:
    app = FastAPI()

    @app.exception_handler(E4ApiError)
    async def _e4_api_error_handler(_request: Request, exc: E4ApiError) -> JSONResponse:
        return JSONResponse(status_code=exc.status_code, content={"error": exc.error, "detail": exc.detail_text, "path": exc.path})

    app.include_router(
        create_e4_router(
            repo_root=repo_root,
            inventory_path=repo_root / "docs" / "conformance" / "e4_lane_inventory.json",
            catalog_path=catalog_path,
            claims_dir=repo_root / "docs" / "conformance" / "support_claims",
            schemas_dir=repo_root / "contracts" / "kernel" / "schemas",
            ledger_path=repo_root / ".test_state" / "atomic_feature_ledger.json",
            coverage_dir=repo_root / ".test_state" / "coverage",
            runtime_records_dir=runtime_records_dir,
        ),
        prefix="/v1/e4",
    )
    return TestClient(app)

def _e4_client_for_claims_dir(repo_root: Path, claims_dir: Path, runtime_records_dir: Path) -> TestClient:
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
            ledger_path=repo_root / ".test_state" / "atomic_feature_ledger.json",
            coverage_dir=repo_root / ".test_state" / "coverage",
            runtime_records_dir=runtime_records_dir,
        ),
        prefix="/v1/e4",
    )
    return TestClient(app)


@pytest.fixture()
def reverify_fixture(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> dict[str, object]:
    from tests.test_e4_c4_chain_validation import _build_chain

    from agentic_coder_prototype.api.e4 import router as e4_router

    chain = _build_chain(tmp_path, checkout_local_sources=True)
    claim_path = Path(chain["support_claim"])
    claim_id = json.loads(claim_path.read_text(encoding="utf-8"))["claim_id"]
    real_validate_c4_chain = e4_router.validate_c4_chain
    validation_reports: list[dict[str, Any]] = []

    def _capture_validate_c4_chain(**kwargs: Any) -> dict[str, Any]:
        report = real_validate_c4_chain(**kwargs)
        validation_reports.append(report)
        return report

    monkeypatch.setattr(e4_router, "validate_c4_chain", _capture_validate_c4_chain)
    local_client = _e4_client_for_claims_dir(
        Path(chain["repo"]),
        claim_path.parent,
        tmp_path / "runtime_records",
    )
    return {
        "chain": chain,
        "claim_id": claim_id,
        "claim_path": claim_path,
        "client": local_client,
        "validation_reports": validation_reports,
    }



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

    assert local_client.get("/status").status_code == 200
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

    assert local_client.get("/status").status_code == 200
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
    assert len(payload["lanes"]) == 6
    assert {lane["status"] for lane in payload["lanes"]} == {"accepted"}

    oh_my_pi_lanes = client.get("/v1/e4/lanes", params={"status": "accepted", "target_family": "oh_my_pi"})
    assert oh_my_pi_lanes.status_code == 200
    assert {lane["target_family"] for lane in oh_my_pi_lanes.json()["lanes"]} == {"oh_my_pi"}

    coverage = client.get("/v1/e4/coverage/oh_my_pi")
    assert coverage.status_code == 200
    assert coverage.json()["target_family"] == "oh_my_pi"


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


def test_e4_catalog_binding_serves_stored_v2_binding_truth(
    client: TestClient,
    e4_fixture: dict[str, object],
) -> None:
    catalog = e4_fixture["catalog"]
    expected_segments = [
        {"segment_id": segment["segment_id"], "stable_entries_hash": segment["stable_entries_hash"]}
        for segment in catalog["segments"]
    ]

    response = client.get("/v1/e4/catalog/binding")

    assert response.status_code == 200
    payload = response.json()
    assert payload["catalog_path"] == "docs/conformance/e4_artifact_catalog.json"
    assert payload["schema_version"] == "bb.e4.artifact_catalog.v2"
    assert catalog["schema_version"] == "bb.e4.artifact_catalog.v2"
    assert payload["segments"] == expected_segments
    assert payload["segments_hash"] == catalog["integrity"]["segments_hash"]
    assert payload["stable_entries_hash"] == catalog["integrity"]["stable_entries_hash"]
    assert all(segment["stable_entries_hash"].startswith("sha256:") for segment in payload["segments"])


def test_e4_accepted_claim_catalog_bindings_match_response_segments(client: TestClient) -> None:
    binding_response = client.get("/v1/e4/catalog/binding")
    claims_response = client.get("/v1/e4/claims", params={"accepted": "true", "limit": 1000})

    assert binding_response.status_code == 200
    assert claims_response.status_code == 200
    segment_hash_by_id = {
        segment["segment_id"]: segment["stable_entries_hash"]
        for segment in binding_response.json()["segments"]
    }
    assert "shared" in segment_hash_by_id

    checked_segment_claims: set[str] = set()
    failures: list[str] = []
    for claim in claims_response.json()["claims"]:
        catalog_binding = claim.get("catalog_binding")
        claim_id = claim["claim_id"]
        if not isinstance(catalog_binding, dict):
            failures.append(f"{claim_id}: missing catalog_binding object")
            continue

        if {"segment_id", "segment_hash", "shared_segment_hash"} <= set(catalog_binding):
            checked_segment_claims.add(claim_id)
            segment_id = catalog_binding["segment_id"]
            if segment_id not in segment_hash_by_id:
                failures.append(f"{claim_id}: unknown segment_id {segment_id!r}")
                continue
            if catalog_binding["segment_hash"] != segment_hash_by_id[segment_id]:
                failures.append(f"{claim_id}: segment_hash mismatch")
            if catalog_binding["shared_segment_hash"] != segment_hash_by_id["shared"]:
                failures.append(f"{claim_id}: shared_segment_hash mismatch")
        elif {"catalog_path", "catalog_revision", "catalog_hash"} <= set(catalog_binding):
            if catalog_binding["catalog_hash"] != binding_response.json()["stable_entries_hash"]:
                failures.append(f"{claim_id}: legacy catalog_hash mismatch")
        else:
            failures.append(f"{claim_id}: malformed catalog_binding keys {sorted(catalog_binding)}")

    assert failures == []
    assert checked_segment_claims == {
        ACCEPTED_CLAIM_ID,
        *(f"fixture_claim_{index}" for index in range(1, 6)),
    }


def test_e4_catalog_binding_rejects_tampered_catalog_drift(
    tmp_path: Path,
    e4_fixture: dict[str, object],
) -> None:
    catalog = json.loads(json.dumps(e4_fixture["catalog"]))
    catalog["entries"][0]["sha256"] = "sha256:" + "0" * 64

    tmp_repo = tmp_path / "repo"
    tmp_catalog_path = tmp_repo / "docs" / "conformance" / "e4_artifact_catalog.json"
    tmp_catalog_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_catalog_path.write_text(json.dumps(catalog, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    local_client = _e4_client_for_catalog(tmp_repo, tmp_catalog_path, tmp_path / "runtime_records")
    response = local_client.get("/v1/e4/catalog/binding")

    assert response.status_code == 409
    assert response.json()["error"] == "catalog_binding_drift"


@pytest.mark.parametrize(
    ("malformed_field", "malformed_value"),
    [
        pytest.param("segments", [{"segment_id": "shared"}], id="segment-missing-required-fields"),
        pytest.param("integrity", [], id="integrity-is-not-an-object"),
    ],
)
def test_e4_catalog_binding_returns_structured_conflict_for_malformed_v2_shapes(
    tmp_path: Path,
    e4_fixture: dict[str, object],
    malformed_field: str,
    malformed_value: object,
) -> None:
    catalog = json.loads(json.dumps(e4_fixture["catalog"]))
    catalog[malformed_field] = malformed_value

    repo_root = tmp_path / "repo"
    catalog_path = repo_root / "docs" / "conformance" / "e4_artifact_catalog.json"
    catalog_path.parent.mkdir(parents=True, exist_ok=True)
    catalog_path.write_text(json.dumps(catalog), encoding="utf-8")

    response = _e4_client_for_catalog(repo_root, catalog_path, tmp_path / "runtime_records").get(
        "/v1/e4/catalog/binding"
    )

    assert response.status_code == 409
    assert response.json() == {
        "error": "catalog_binding_missing",
        "detail": "v2 catalog has missing or malformed segments/integrity",
        "path": "repo/docs/conformance/e4_artifact_catalog.json",
    }


def test_e4_claim_listing_rejects_duplicate_ids_with_sorted_source_paths(tmp_path: Path) -> None:
    repo_root = tmp_path / "repo"
    inventory_path = repo_root / "docs" / "conformance" / "e4_lane_inventory.json"
    inventory_path.parent.mkdir(parents=True)
    inventory_path.write_text(json.dumps({"lanes": []}), encoding="utf-8")
    claims_dir = inventory_path.parent / "support_claims"
    claims_dir.mkdir()

    duplicate_claim = {"claim_id": "duplicate_claim"}
    for filename in ("zeta_support_claim.json", "alpha_support_claim.json"):
        (claims_dir / filename).write_text(json.dumps(duplicate_claim), encoding="utf-8")

    response = _e4_client_for_claims_dir(repo_root, claims_dir, tmp_path / "runtime_records").get(
        "/v1/e4/claims"
    )

    assert response.status_code == 409
    assert response.json() == {
        "error": "duplicate_claim_id",
        "detail": {
            "claim_id": "duplicate_claim",
            "paths": [
                "repo/docs/conformance/support_claims/alpha_support_claim.json",
                "repo/docs/conformance/support_claims/zeta_support_claim.json",
            ],
        },
        "path": "repo/docs/conformance/support_claims",
    }


def test_registries_endpoints_expose_contract_registry_files(client: TestClient) -> None:
    list_response = client.get("/v1/e4/registries")

    assert list_response.status_code == 200
    registries = list_response.json()["registries"]
    registry_ids = {registry["registry_id"] for registry in registries}
    assert {"target_families", "kernel_event_kinds", "kernel_families"}.issubset(registry_ids)

    registry_response = client.get("/v1/e4/registries/target_families")
    assert registry_response.status_code == 200
    payload = registry_response.json()
    assert payload["registry_id"] == "target_families"
    assert payload["schema_version"] == "bb.registry.v1"
    assert any(entry["id"] == "oh_my_pi" for entry in payload["entries"])

    missing = client.get("/v1/e4/registries/not_a_registry")
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


def test_e4_claim_reverify_runs_check_only_without_writing_probe_artifacts(
    reverify_fixture: dict[str, object],
) -> None:
    chain = reverify_fixture["chain"]
    claim_id = str(reverify_fixture["claim_id"])
    json_out = Path(chain["validation_report"])
    before_bytes = json_out.read_bytes()
    before_mtime_ns = json_out.stat().st_mtime_ns

    response = reverify_fixture["client"].post(
        f"/v1/e4/claims/{claim_id}/reverify",
        json={"rerun_comparators": True},
    )

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["claim_id"] == claim_id
    assert payload["mode"] == "check_only"
    assert payload["comparator_rerun"] is True
    assert payload["ok"] is True, payload
    assert payload["errors"] == []
    validation_reports = reverify_fixture["validation_reports"]
    assert isinstance(validation_reports, list)
    assert len(validation_reports) == 1
    assert validation_reports[0]["comparator_rerun"] == {
        "assertion_count": 1,
        "comparator_class": "deterministic_replay",
        "missing_assertion_ids": [],
        "unexpected_assertion_ids": [],
        "status_mismatch_ids": [],
        "value_mismatch_ids": [],
        "ok": True,
        "comparator_id": "codex_stored_report_replay",
        "registry": "conformance/comparators/registry.json",
    }
    assert json_out.read_bytes() == before_bytes
    assert json_out.stat().st_mtime_ns == before_mtime_ns


def test_e4_claim_reverify_defaults_to_check_only_without_comparator_rerun(
    reverify_fixture: dict[str, object],
) -> None:
    response = reverify_fixture["client"].post(
        f"/v1/e4/claims/{reverify_fixture['claim_id']}/reverify",
        json={},
    )
    assert response.status_code == 200, response.text

    payload = response.json()
    assert payload["ok"] is True
    assert payload["comparator_rerun"] is False
    assert payload["mode"] == "check_only"
    assert payload["errors"] == []


def test_e4_claim_reverify_rejects_tampered_node_gate_reference(
    reverify_fixture: dict[str, object],
) -> None:
    claim_path = Path(reverify_fixture["claim_path"])
    claim = json.loads(claim_path.read_text(encoding="utf-8"))
    original_ref = claim["validation_refs"][0]
    expected_hash = original_ref.rsplit("#", 1)[1]
    claim["validation_refs"] = [f"../../outside_node_gate.json#{expected_hash}"]
    _write_json(claim_path, claim)

    response = reverify_fixture["client"].post(
        f"/v1/e4/claims/{reverify_fixture['claim_id']}/reverify",
        json={},
    )

    assert response.status_code == 409
    assert response.json()["error"] == "reverify_failed"
    assert "outside" in response.json()["detail"]



def test_e4_claim_reverify_uses_support_claim_source_path_when_filename_differs_from_claim_id(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    e4_fixture: dict[str, object],
) -> None:
    repo_root = Path(e4_fixture["repo"])
    claims_dir = tmp_path / "support_claims"
    claims_dir.mkdir()
    source_claim_path = Path(e4_fixture["claims_dir"]) / f"{ACCEPTED_CLAIM_ID}_support_claim.json"
    mismatched_claim_path = claims_dir / "stored_under_unrelated_name_support_claim.json"
    mismatched_claim_path.write_text(source_claim_path.read_text(encoding="utf-8"), encoding="utf-8")
    captured: dict[str, object] = {}

    def _capture_validate_c4_chain(**kwargs: object) -> dict[str, object]:
        captured.update(kwargs)
        return {"ok": True, "errors": []}

    monkeypatch.setattr("agentic_coder_prototype.api.e4.router.validate_c4_chain", _capture_validate_c4_chain)

    response = _e4_client_for_claims_dir(repo_root, claims_dir, tmp_path / "runtime_records").post(
        f"/v1/e4/claims/{ACCEPTED_CLAIM_ID}/reverify",
        json={"rerun_comparators": False},
    )

    assert response.status_code == 200
    assert response.json()["claim_id"] == ACCEPTED_CLAIM_ID
    assert Path(captured["support_claim_path"]).resolve() == mismatched_claim_path.resolve()
    assert captured["config_id"] == "fixture_config_0"

def test_e4_missing_resources_return_flat_error_envelope(client: TestClient) -> None:
    missing_lane = client.get("/v1/e4/lanes/not_a_lane")
    assert missing_lane.status_code == 404
    assert missing_lane.json() == {"error": "lane_not_found", "detail": "not_a_lane", "path": None}

    assert client.get("/v1/e4/claims/not_a_claim").status_code == 404
    assert client.get("/v1/e4/schemas/not_a_schema").status_code == 404
    assert client.get("/v1/e4/coverage/not_a_target_family").status_code == 404

def test_e4_claim_detail_missing_evidence_manifest_returns_flat_error_envelope(
    tmp_path: Path,
    e4_fixture: dict[str, object],
) -> None:
    repo_root = Path(e4_fixture["repo"])
    claims_dir = tmp_path / "support_claims"
    claims_dir.mkdir()
    broken_claim_id = "broken_missing_manifest_claim"
    claim = json.loads(
        (Path(e4_fixture["claims_dir"]) / f"{ACCEPTED_CLAIM_ID}_support_claim.json").read_text(
            encoding="utf-8"
        )
    )
    claim["claim_id"] = broken_claim_id
    claim["evidence_manifest_ref"] = "docs/conformance/support_claims/missing_evidence_manifest.json"
    _write_json(claims_dir / f"{broken_claim_id}_support_claim.json", claim)

    response = _e4_client_for_claims_dir(
        repo_root,
        claims_dir,
        tmp_path / "runtime_records",
    ).get(f"/v1/e4/claims/{broken_claim_id}")

    assert response.status_code == 409
    assert response.json()["error"] == "evidence_manifest_unavailable"
    assert response.json()["path"] == "docs/conformance/support_claims/missing_evidence_manifest.json"



def test_runtime_emission_flag_off_writes_no_records(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.delenv("BREADBOARD_EMIT_PRIMITIVES", raising=False)
    monkeypatch.setenv("BREADBOARD_RUNTIME_RECORD_ROOT", str(tmp_path / "runtime_records"))
    monkeypatch.setenv("BREADBOARD_SESSION_STATE_ROOT", str(tmp_path / "session_state"))

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