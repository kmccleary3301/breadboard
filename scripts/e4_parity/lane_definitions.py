from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any, Mapping

import yaml
from jsonschema import Draft202012Validator, RefResolver

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_LANE_DEF_DIR = ROOT / "config" / "e4_lanes"
SCHEMA_VERSION_V1 = "bb.e4.lane_def.v1"
SCHEMA_VERSION_V2 = "bb.e4.lane_def.v2"
SUPPORTED_SCHEMA_VERSIONS = (SCHEMA_VERSION_V1, SCHEMA_VERSION_V2)
SCHEMA_DIR = ROOT / "contracts" / "kernel" / "schemas"
COMMON_SCHEMA_PATH = SCHEMA_DIR / "bb.kernel.common.v1.schema.json"
E4_COMMON_SCHEMA_PATH = SCHEMA_DIR / "bb.e4.common.v1.schema.json"


class LaneDefValidationError(ValueError):
    pass


def _load_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise LaneDefValidationError(f"{path} must contain a JSON object")
    return payload


def _load_yaml(path: Path) -> dict[str, Any]:
    payload = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise LaneDefValidationError(f"{path} must contain a YAML object")
    return payload


def _schema_path(schema_version: str) -> Path:
    return SCHEMA_DIR / f"{schema_version}.schema.json"


@lru_cache(maxsize=None)
def _validator(schema_version: str) -> Draft202012Validator:
    schema_path = _schema_path(schema_version)
    schema = _load_json(schema_path)
    common = _load_json(COMMON_SCHEMA_PATH)
    e4_common = _load_json(E4_COMMON_SCHEMA_PATH)
    store = {
        schema.get("$id", schema_path.as_uri()): schema,
        schema_path.name: schema,
        common.get("$id", COMMON_SCHEMA_PATH.as_uri()): common,
        COMMON_SCHEMA_PATH.name: common,
        e4_common.get("$id", E4_COMMON_SCHEMA_PATH.as_uri()): e4_common,
        E4_COMMON_SCHEMA_PATH.name: e4_common,
    }
    return Draft202012Validator(
        schema,
        resolver=RefResolver(base_uri=schema_path.as_uri(), referrer=schema, store=store),
    )


def _format_error(error: Any) -> str:
    pointer = "/" + "/".join(str(part) for part in error.absolute_path) if error.absolute_path else ""
    return f"{pointer or '<root>'}: {error.message}"


def _schema_version(payload: Mapping[str, Any], *, source: Path | None = None) -> str:
    value = payload.get("schema_version")
    if value not in SUPPORTED_SCHEMA_VERSIONS:
        prefix = f"{source}: " if source is not None else ""
        supported = ", ".join(SUPPORTED_SCHEMA_VERSIONS)
        raise LaneDefValidationError(f"{prefix}unknown schema_version {value!r}; expected one of: {supported}")
    return str(value)


def _normalize_v1_lane(lane_def: dict[str, Any]) -> dict[str, Any]:
    normalized = dict(lane_def)
    normalized["_lane_def_version"] = 1
    metadata = normalized.get("metadata") or {}
    if not isinstance(metadata, Mapping):
        metadata = {}
    acceptance_packet = metadata.get("acceptance_packet") or {}
    if not isinstance(acceptance_packet, Mapping):
        acceptance_packet = {}

    normalized.setdefault(
        "run",
        {
            "run_id": metadata.get("run_id"),
            "provider_model": metadata.get("provider_model"),
            "sandbox_mode": metadata.get("sandbox_mode"),
        },
    )
    if normalized["run"] == {"run_id": None, "provider_model": None, "sandbox_mode": None}:
        normalized["run"] = None

    if normalized.get("provenance") is None:
        if normalized.get("target_family") == "breadboard" and not acceptance_packet:
            normalized["provenance"] = None
        else:
            normalized["provenance"] = {
                "upstream_repo": acceptance_packet.get("upstream_repo"),
                "upstream_commit": acceptance_packet.get("upstream_commit"),
                "upstream_commit_date": acceptance_packet.get("upstream_commit_date"),
                "upstream_release_label": acceptance_packet.get("upstream_release_label"),
                "source_paths": list(acceptance_packet.get("source_paths") or []),
            }

    if normalized.get("acceptance") is None:
        normalized["acceptance"] = {
            "behavior_family": acceptance_packet.get("behavior_family"),
            "semantic_key": acceptance_packet.get("semantic_key"),
            "target": acceptance_packet.get("target"),
            "assertions": list(acceptance_packet.get("assertions") or []),
        }

    ct = normalized.get("ct")
    if isinstance(ct, Mapping):
        ct = dict(ct)
        ct.setdefault("test_id", metadata.get("legacy_inventory_ct_test_id"))
        normalized["ct"] = ct

    return normalized


def _normalize_v2_lane(lane_def: dict[str, Any]) -> dict[str, Any]:
    normalized = dict(lane_def)
    normalized["_lane_def_version"] = 2
    return normalized


def validate_lane_def(payload: Mapping[str, Any], *, source: Path | None = None) -> dict[str, Any]:
    schema_version = _schema_version(payload, source=source)
    lane_def = dict(payload)
    errors = sorted((_format_error(error) for error in _validator(schema_version).iter_errors(lane_def)))
    if errors:
        prefix = f"{source}: " if source is not None else ""
        raise LaneDefValidationError(prefix + "; ".join(errors))
    if schema_version == SCHEMA_VERSION_V1:
        return _normalize_v1_lane(lane_def)
    return _normalize_v2_lane(lane_def)


def load_lane_def(path: Path) -> dict[str, Any]:
    return validate_lane_def(_load_yaml(path), source=path)


def load_lane_defs(directory: Path = DEFAULT_LANE_DEF_DIR) -> dict[str, dict[str, Any]]:
    if not directory.exists():
        return {}
    lane_defs: dict[str, dict[str, Any]] = {}
    for path in sorted(directory.glob("*.yaml")):
        lane_def = load_lane_def(path)
        lane_id = str(lane_def["lane_id"])
        if lane_id in lane_defs:
            raise LaneDefValidationError(f"duplicate lane_id {lane_id!r} in {directory}")
        lane_defs[lane_id] = lane_def
    return lane_defs
