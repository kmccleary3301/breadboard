from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from fastapi import APIRouter, Body, Query
from fastapi.responses import JSONResponse

from agentic_coder_prototype.conformance.c4_chain import validate_c4_chain
from agentic_coder_prototype.conformance.catalog_binding import catalog_segments, catalog_segments_hash, stable_entries_hash

from .models import (
    E4ApiError,
    E4CatalogBindingSegment,
    E4CatalogBinding,
    E4CatalogPage,
    E4ClaimDetail,
    E4ClaimList,
    E4CoverageMatrix,
    E4Error,
    E4Health,
    E4LaneDetail,
    E4LaneList,
    E4LedgerRows,
    E4RecordEnvelope,
    E4RecordList,
    E4ResolvedArtifact,
    E4ReverifyRequest,
    E4ReverifyResult,
    E4SchemaInfo,
    E4SchemaList,
    E4SupportClaim,
    RegistryInfo,
    RegistryList,
)

_JSON_CACHE: dict[tuple[Path, int], Any] = {}
_SHA256_CACHE: dict[tuple[Path, int], str] = {}


def _mtime_key(path: Path) -> tuple[Path, int]:
    resolved = path.resolve()
    return (resolved, resolved.stat().st_mtime_ns)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return "sha256:" + digest.hexdigest()


def _sha256_cached(path: Path) -> str:
    key = _mtime_key(path)
    cached = _SHA256_CACHE.get(key)
    if cached is None:
        cached = _sha256(path)
        _SHA256_CACHE[key] = cached
    return cached


def _load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _load_json_cached(path: Path) -> Any:
    key = _mtime_key(path)
    if key not in _JSON_CACHE:
        _JSON_CACHE[key] = _load_json(path)
    return _JSON_CACHE[key]


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _display_path(repo_root: Path, path: Path) -> str:
    resolved = path.resolve()
    workspace = repo_root.parent.resolve()
    try:
        return resolved.relative_to(workspace).as_posix()
    except ValueError:
        try:
            return resolved.relative_to(repo_root.resolve()).as_posix()
        except ValueError:
            return resolved.as_posix()


def _resolve_path(repo_root: Path, value: str) -> Path:
    raw = value.split("#", 1)[0]
    path = Path(raw)
    if path.is_absolute():
        return path.resolve()
    if raw.startswith("docs_tmp/") or raw.startswith(repo_root.name + "/"):
        return (repo_root.parent / raw).resolve()
    return (repo_root / raw).resolve()


def _schema_version(schema: dict[str, Any]) -> str | None:
    props = schema.get("properties")
    if isinstance(props, dict):
        schema_version = props.get("schema_version") or props.get("schemaVersion")
        if isinstance(schema_version, dict):
            const = schema_version.get("const")
            return const if isinstance(const, str) else None
    return None


def _schema_id(path: Path, schema: dict[str, Any]) -> str:
    raw_id = schema.get("$id")
    if isinstance(raw_id, str) and raw_id:
        return raw_id.rsplit("/", 1)[-1].removesuffix(".schema.json")
    return path.name.removesuffix(".schema.json")

def _schema_pack_by_filename(packs_path: Path) -> dict[str, str]:
    manifest = _load_json_cached(packs_path)
    result: dict[str, str] = {}
    entries = manifest.get("entries") if isinstance(manifest, dict) else None
    if not isinstance(entries, list):
        return result
    for entry in entries:
        if not isinstance(entry, dict) or not isinstance(entry.get("id"), str):
            continue
        schemas = entry.get("metadata", {}).get("schemas") if isinstance(entry.get("metadata"), dict) else None
        if not isinstance(schemas, list):
            continue
        for schema_name in schemas:
            if isinstance(schema_name, str):
                result[schema_name] = entry["id"]
    return result


def _catalog_entries(catalog: dict[str, Any]) -> list[dict[str, Any]]:
    entries = catalog.get("entries")
    return [dict(entry) for entry in entries] if isinstance(entries, list) else []


def _stable_json_hash(payload: Any) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def _catalog_generated_at(catalog: dict[str, Any]) -> str | None:
    value = catalog.get("generated_at_utc") or catalog.get("generated_at")
    return str(value) if value is not None else None


def _claim_summary_text(claim: dict[str, Any]) -> str | None:
    summary = claim.get("summary") or claim.get("claim_summary") or claim.get("description")
    return str(summary) if summary is not None else None


def _claim_model(claim: dict[str, Any], lane_by_id: dict[str, dict[str, Any]]) -> E4SupportClaim:
    lane_id = str(claim.get("lane_id") or claim.get("scope", {}).get("lane_id") or "")
    lane = lane_by_id.get(lane_id, {})
    payload = dict(claim)
    payload.update(
        {
            "claim_id": str(claim.get("claim_id") or ""),
            "config_id": claim.get("config_id"),
            "lane_id": lane_id,
            "kind": lane.get("kind"),
            "accepted": bool(claim.get("accepted")),
            "schema_version": claim.get("schema_version"),
            "summary": _claim_summary_text(claim),
            "scope": claim.get("scope") if isinstance(claim.get("scope"), dict) else None,
            "evidence_manifest_ref": claim.get("evidence_manifest_ref"),
            "target_family": claim.get("scope", {}).get("target_family") or lane.get("target_family"),
            "target_version": claim.get("scope", {}).get("target_version") or lane.get("target_version"),
            "run_id": claim.get("scope", {}).get("run_id") or lane.get("run_id"),
        }
    )
    return E4SupportClaim.model_validate(payload)


def _claim_target_family(claim: E4SupportClaim) -> str | None:
    if isinstance(claim.scope, dict) and isinstance(claim.scope.get("target_family"), str):
        return claim.scope["target_family"]
    value = getattr(claim, "target_family", None)
    return value if isinstance(value, str) else None


def _iter_json_files(paths: Iterable[Path]) -> Iterable[Path]:
    for root in paths:
        if root.exists():
            yield from sorted(root.glob("*.json"))


def create_e4_router(
    *,
    repo_root: Path,
    inventory_path: Path,
    catalog_path: Path,
    claims_dir: Path,
    schemas_dir: Path,
    ledger_path: Path,
    coverage_dir: Path,
    runtime_records_dir: Path,
) -> APIRouter:
    repo_root = repo_root.resolve()
    inventory_path = inventory_path.resolve()
    catalog_path = catalog_path.resolve()
    claims_dir = claims_dir.resolve()
    schemas_dir = schemas_dir.resolve()
    ledger_path = ledger_path.resolve()
    coverage_dir = coverage_dir.resolve()
    runtime_records_dir = runtime_records_dir.resolve()
    freeze_manifest = repo_root / "config" / "e4_target_freeze_manifest.yaml"
    registries_dir = repo_root / "contracts" / "kernel" / "registries"
    comparator_registry = repo_root / "conformance" / "comparators" / "registry.json"
    packs_manifest = repo_root / "contracts" / "kernel" / "packs.v1.json"
    router = APIRouter()

    def inventory() -> dict[str, Any]:
        return _load_json_cached(inventory_path)

    def catalog() -> dict[str, Any]:
        return _load_json_cached(catalog_path)

    def lane_by_id() -> dict[str, dict[str, Any]]:
        lanes = inventory().get("lanes", [])
        return {str(lane.get("lane_id")): dict(lane) for lane in lanes if isinstance(lane, dict)}

    def claim_by_id() -> dict[str, tuple[dict[str, Any], Path]]:
        result: dict[str, tuple[dict[str, Any], Path]] = {}
        for path in sorted(claims_dir.glob("*_support_claim.json")):
            claim = _load_json_cached(path)
            if not isinstance(claim, dict) or not isinstance(claim.get("claim_id"), str):
                continue
            claim_id = claim["claim_id"]
            existing = result.get(claim_id)
            if existing is not None:
                raise E4ApiError(
                    status_code=409,
                    error="duplicate_claim_id",
                    detail={
                        "claim_id": claim_id,
                        "paths": [
                            _display_path(repo_root, existing[1]),
                            _display_path(repo_root, path),
                        ],
                    },
                    path=_display_path(repo_root, claims_dir),
                )
            result[claim_id] = (claim, path)
        return result

    @router.get("/health", response_model=E4Health, responses={503: {"model": E4Error}})
    def health() -> E4Health:
        try:
            inv = inventory()
            cat = catalog()
        except Exception as exc:
            raise E4ApiError(status_code=503, error="unreadable_e4_state", detail=str(exc), path=None) from exc
        return E4Health(ok=True, repo_root=_display_path(repo_root, repo_root), inventory_revision=int(inv.get("revision", 0)), catalog_revision=int(cat.get("revision", 0)))

    @router.get("/schemas", response_model=E4SchemaList)
    def list_schemas() -> E4SchemaList:
        schemas: list[E4SchemaInfo] = []
        pack_by_filename = _schema_pack_by_filename(packs_manifest)
        for path in sorted(schemas_dir.glob("*.json")):
            schema = _load_json_cached(path)
            if isinstance(schema, dict):
                pack = pack_by_filename.get(path.name)
                if pack is None:
                    raise E4ApiError(status_code=503, error="schema_pack_not_found", detail=path.name, path=_display_path(repo_root, packs_manifest))
                schemas.append(E4SchemaInfo(schema_id=_schema_id(path, schema), schema_version=_schema_version(schema), pack=pack, path=_display_path(repo_root, path), sha256=_sha256_cached(path)))
        return E4SchemaList(schemas=schemas, total=len(schemas))

    @router.get("/schemas/{schema_id}")
    def get_schema(schema_id: str) -> JSONResponse:
        for path in sorted(schemas_dir.glob("*.json")):
            schema = _load_json_cached(path)
            if isinstance(schema, dict) and schema_id in {_schema_id(path, schema), path.name, path.stem}:
                return JSONResponse(schema)
        raise E4ApiError(status_code=404, error="schema_not_found", detail=schema_id, path=None)

    @router.get("/lanes", response_model=E4LaneList)
    def list_lanes(
        phase: str | None = None,
        kind: str | None = None,
        target_family: str | None = None,
        status: str | None = None,
        offset: int = Query(default=0, ge=0),
        limit: int = Query(default=200, ge=1, le=1000),
    ) -> E4LaneList:
        lanes = [dict(lane) for lane in inventory().get("lanes", []) if isinstance(lane, dict)]
        filters = {"phase": phase, "kind": kind, "target_family": target_family, "status": status}
        for key, value in filters.items():
            if value is not None:
                lanes = [lane for lane in lanes if lane.get(key) == value]
        lanes.sort(key=lambda lane: str(lane.get("lane_id", "")))
        total = len(lanes)
        return E4LaneList(lanes=lanes[offset : offset + limit], total=total)

    @router.get("/lanes/{lane_id}", response_model=E4LaneDetail)
    def get_lane(lane_id: str) -> E4LaneDetail:
        lane = lane_by_id().get(lane_id)
        if lane is None:
            raise E4ApiError(status_code=404, error="lane_not_found", detail=lane_id, path=None)
        entries_by_role = {str(entry.get("role_id")): entry for entry in _catalog_entries(catalog())}
        artifacts: list[E4ResolvedArtifact] = []
        for role, role_id in sorted((lane.get("artifact_roles") or {}).items()):
            entry = entries_by_role.get(str(role_id), {})
            path = str(entry.get("path") or "")
            artifacts.append(E4ResolvedArtifact(role=str(role), role_id=str(role_id), path=path, sha256=entry.get("sha256"), bytes=entry.get("bytes"), exists=bool(entry.get("exists", False))))
        return E4LaneDetail(lane=lane, resolved_artifacts=artifacts)

    @router.get("/claims", response_model=E4ClaimList)
    def list_claims(
        accepted: bool | None = None,
        target_family: str | None = None,
        kind: str | None = None,
        offset: int = Query(default=0, ge=0),
        limit: int = Query(default=200, ge=1, le=1000),
    ) -> E4ClaimList:
        lanes = lane_by_id()
        claims = [_claim_model(claim, lanes) for claim, _path in claim_by_id().values()]
        if accepted is not None:
            claims = [claim for claim in claims if claim.accepted is accepted]
        if target_family is not None:
            claims = [claim for claim in claims if _claim_target_family(claim) == target_family]
        if kind is not None:
            claims = [claim for claim in claims if claim.kind == kind]
        claims.sort(key=lambda claim: claim.claim_id)
        total = len(claims)
        return E4ClaimList(claims=claims[offset : offset + limit], total=total)

    @router.get("/claims/{claim_id}", response_model=E4ClaimDetail)
    def get_claim(claim_id: str) -> E4ClaimDetail:
        found = claim_by_id().get(claim_id)
        if found is None:
            raise E4ApiError(status_code=404, error="claim_not_found", detail=claim_id, path=None)
        claim, _source_path = found
        manifest_ref = claim.get("evidence_manifest_ref")
        if not isinstance(manifest_ref, str):
            evidence_manifest = {}
        else:
            try:
                evidence_manifest = _load_json(_resolve_path(repo_root, manifest_ref))
            except (OSError, ValueError) as exc:
                raise E4ApiError(status_code=409, error="evidence_manifest_unavailable", detail=str(exc), path=manifest_ref) from exc
        return E4ClaimDetail(claim=_claim_model(claim, lane_by_id()), evidence_manifest=evidence_manifest if isinstance(evidence_manifest, dict) else {})

    @router.get("/catalog", response_model=E4CatalogPage)
    def get_catalog(lane_id: str | None = None, artifact_kind: str | None = None, offset: int = Query(default=0, ge=0), limit: int = Query(default=200, ge=1, le=1000)) -> E4CatalogPage:
        cat = catalog()
        entries = _catalog_entries(cat)
        if lane_id is not None:
            entries = [entry for entry in entries if entry.get("lane_id") == lane_id]
        if artifact_kind is not None:
            entries = [entry for entry in entries if entry.get("artifact_kind") == artifact_kind]
        entries.sort(key=lambda entry: (str(entry.get("lane_id", "")), str(entry.get("role_id", ""))))
        return E4CatalogPage(revision=int(cat.get("revision", 0)), total=len(entries), entries=entries[offset : offset + limit])

    @router.get("/catalog/binding", response_model=E4CatalogBinding)
    def get_catalog_binding() -> E4CatalogBinding:
        cat = catalog()
        schema_version = cat.get("schema_version")
        if schema_version != "bb.e4.artifact_catalog.v2":
            raise E4ApiError(
                status_code=409,
                error="catalog_binding_requires_v2",
                detail=str(schema_version),
                path=_display_path(repo_root, catalog_path),
            )
        stored_segments = cat.get("segments")
        integrity = cat.get("integrity")
        segments_valid = isinstance(stored_segments, list) and all(
            isinstance(segment, dict)
            and isinstance(segment.get("segment_id"), str)
            and isinstance(segment.get("entry_count"), int)
            and not isinstance(segment.get("entry_count"), bool)
            and isinstance(segment.get("entries_hash"), str)
            and isinstance(segment.get("stable_entries_hash"), str)
            for segment in stored_segments
        )
        integrity_valid = (
            isinstance(integrity, dict)
            and isinstance(integrity.get("entry_count"), int)
            and not isinstance(integrity.get("entry_count"), bool)
            and isinstance(integrity.get("entries_hash"), str)
            and isinstance(integrity.get("segments_hash"), str)
            and isinstance(integrity.get("stable_entries_hash"), str)
        )
        if not segments_valid or not integrity_valid:
            raise E4ApiError(
                status_code=409,
                error="catalog_binding_missing",
                detail="v2 catalog has missing or malformed segments/integrity",
                path=_display_path(repo_root, catalog_path),
            )
        entries = _catalog_entries(cat)
        if (
            catalog_segments(entries) != stored_segments
            or catalog_segments_hash(entries) != integrity["segments_hash"]
            or len(entries) != integrity["entry_count"]
            or _stable_json_hash(entries) != integrity["entries_hash"]
            or stable_entries_hash(entries) != integrity["stable_entries_hash"]
        ):
            raise E4ApiError(
                status_code=409,
                error="catalog_binding_drift",
                detail="stored segments/integrity disagree with entries recomputation",
                path=_display_path(repo_root, catalog_path),
            )
        return E4CatalogBinding(
            catalog_path=catalog_path.relative_to(repo_root).as_posix(),
            schema_version=str(schema_version),
            segments=[
                E4CatalogBindingSegment(
                    segment_id=segment["segment_id"],
                    stable_entries_hash=segment["stable_entries_hash"],
                )
                for segment in stored_segments
            ],
            segments_hash=str(integrity["segments_hash"]),
            generated_at_utc=_catalog_generated_at(cat),
            stable_entries_hash=str(integrity["stable_entries_hash"]),
        )

    @router.get("/registries", response_model=RegistryList)
    def list_registries() -> RegistryList:
        registries: list[RegistryInfo] = []
        for path in sorted(registries_dir.glob("*.json")):
            payload = _load_json_cached(path)
            if isinstance(payload, dict) and isinstance(payload.get("registry_id"), str):
                entries = payload.get("entries")
                registries.append(
                    RegistryInfo(
                        registry_id=str(payload["registry_id"]),
                        schema_version=payload.get("schema_version") if isinstance(payload.get("schema_version"), str) else None,
                        path=_display_path(repo_root, path),
                        entries=len(entries) if isinstance(entries, list) else 0,
                    )
                )
        return RegistryList(registries=registries, total=len(registries))

    @router.get("/registries/{registry_id}")
    def get_registry(registry_id: str) -> JSONResponse:
        for path in sorted(registries_dir.glob("*.json")):
            payload = _load_json_cached(path)
            if isinstance(payload, dict) and registry_id in {str(payload.get("registry_id")), path.name, path.stem}:
                return JSONResponse(payload)
        raise E4ApiError(status_code=404, error="registry_not_found", detail=registry_id, path=_display_path(repo_root, registries_dir))

    @router.get("/ledger/rows", response_model=E4LedgerRows)
    def get_ledger_rows(
        feature_id: str | None = None,
        lane_id: str | None = None,
        offset: int = Query(default=0, ge=0),
        limit: int = Query(default=200, ge=1, le=1000),
    ) -> E4LedgerRows:
        rows = [dict(row) for row in _load_json(ledger_path).get("rows", []) if isinstance(row, dict)]
        if feature_id is not None:
            rows = [row for row in rows if row.get("feature_id") == feature_id]
        if lane_id is not None:
            rows = [row for row in rows if row.get("lane_id") == lane_id]
        rows.sort(key=lambda row: str(row.get("feature_id", "")))
        total = len(rows)
        return E4LedgerRows(rows=rows[offset : offset + limit], total=total)

    @router.get("/records", response_model=E4RecordList)
    def get_records(
        schema_version: str = Query(...),
        lane_id: str | None = None,
        source: str = Query(default="evidence", pattern="^(evidence|runtime)$"),
        offset: int = Query(default=0, ge=0),
        limit: int = Query(default=200, ge=1, le=1000),
    ) -> E4RecordList:
        records: list[E4RecordEnvelope] = []
        if source == "runtime":
            if lane_id is not None:
                raise E4ApiError(status_code=400, error="unsupported_filter", detail="lane_id is not supported for source=runtime", path=None)
            for path in sorted(runtime_records_dir.glob("*/*.json")):
                payload = _load_json(path)
                if isinstance(payload, dict) and payload.get("schema_version") == schema_version:
                    records.append(E4RecordEnvelope(schema_version=schema_version, source=source, path=_display_path(repo_root, path), record=payload))
            for path in sorted(runtime_records_dir.glob("*/records/*.jsonl")):
                try:
                    lines = path.read_text(encoding="utf-8").splitlines()
                except OSError:
                    continue
                for index, line in enumerate(lines, start=1):
                    if not line.strip():
                        continue
                    try:
                        payload = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if isinstance(payload, dict) and payload.get("stream") == "config_plane":
                        continue
                    if isinstance(payload, dict) and payload.get("schema_version") == schema_version:
                        record_payload = payload.get("record") if isinstance(payload.get("record"), dict) else payload
                        records.append(
                            E4RecordEnvelope(
                                schema_version=schema_version,
                                source=source,
                                path=f"{_display_path(repo_root, path)}:{index}",
                                record=record_payload,
                            )
                        )
        else:
            for entry in _catalog_entries(catalog()):
                if lane_id is not None and entry.get("lane_id") != lane_id:
                    continue
                path_value = entry.get("path")
                if not isinstance(path_value, str) or not path_value.endswith(".json"):
                    continue
                path = _resolve_path(repo_root, path_value)
                if not path.exists():
                    continue
                payload = _load_json(path)
                if isinstance(payload, dict) and payload.get("schema_version") == schema_version:
                    records.append(E4RecordEnvelope(schema_version=schema_version, source=source, path=_display_path(repo_root, path), record=payload))
        records.sort(key=lambda item: item.path)
        total = len(records)
        return E4RecordList(records=records[offset : offset + limit], total=total)

    @router.post("/claims/{claim_id}/reverify", response_model=E4ReverifyResult, responses={404: {"model": E4Error}, 409: {"model": E4Error}})
    def reverify_claim(claim_id: str, payload: E4ReverifyRequest = Body(default_factory=E4ReverifyRequest)) -> E4ReverifyResult:
        found = claim_by_id().get(claim_id)
        if found is None:
            raise E4ApiError(status_code=404, error="claim_not_found", detail=claim_id, path=None)
        claim, source_path = found
        no_rerun_reason = None
        if payload.rerun_comparators is False:
            no_rerun_reason = (
                "API request set rerun_comparators=false"
                if "rerun_comparators" in payload.model_fields_set
                else "API default: check-only reverification"
            )
        try:
            report = validate_c4_chain(
                repo_root=repo_root,
                freeze_manifest_path=freeze_manifest,
                config_id=str(claim.get("config_id")),
                support_claim_path=source_path.resolve(),
                evidence_manifest_path=_resolve_path(repo_root, str(claim.get("evidence_manifest_ref"))),
                rerun_comparators=payload.rerun_comparators,
                comparator_registry_path=comparator_registry,
                no_rerun_reason=no_rerun_reason,
            )
        except Exception as exc:
            raise E4ApiError(status_code=409, error="reverify_failed", detail=str(exc), path=None) from exc
        return E4ReverifyResult(ok=bool(report.get("ok")), errors=list(report.get("errors", [])), claim_id=claim_id, comparator_rerun=payload.rerun_comparators, checked_at=_utc_now())

    @router.get("/coverage/{target_family}", response_model=E4CoverageMatrix)
    def get_coverage(target_family: str) -> E4CoverageMatrix:
        for path in _iter_json_files([coverage_dir]):
            payload = _load_json(path)
            if isinstance(payload, dict) and payload.get("target_family") == target_family:
                return E4CoverageMatrix(root=payload)
        raise E4ApiError(status_code=404, error="coverage_not_found", detail=target_family, path=_display_path(repo_root, coverage_dir))

    return router
