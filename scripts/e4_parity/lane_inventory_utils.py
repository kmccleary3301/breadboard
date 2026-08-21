from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_INVENTORY_PATH = ROOT / "docs" / "conformance" / "e4_lane_inventory.json"


def load_inventory(path: Path = DEFAULT_INVENTORY_PATH) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or not isinstance(payload.get("lanes"), list):
        raise ValueError(f"lane inventory must be an object with lanes list: {path}")
    return payload


def ct_output(lane: Mapping[str, Any]) -> str:
    ct = lane.get("ct")
    command = ct.get("command") if isinstance(ct, Mapping) else None
    argv = command.get("argv") if isinstance(command, Mapping) else None
    if not isinstance(argv, list):
        raise ValueError(f"inventory lane {lane.get('lane_id')!r} missing ct command argv")
    for index, value in enumerate(argv[:-1]):
        if value == "--json-out" and isinstance(argv[index + 1], str):
            return argv[index + 1]
    raise ValueError(f"inventory lane {lane.get('lane_id')!r} missing --json-out")


def primary_primitive(lane: Mapping[str, Any]) -> str:
    primitives = lane.get("primitives")
    if not isinstance(primitives, list) or len(primitives) != 1 or not isinstance(primitives[0], str):
        raise ValueError(f"inventory lane {lane.get('lane_id')!r} must declare one primitive")
    return primitives[0]


def ledger_feature_id(lane: Mapping[str, Any]) -> str:
    feature_ids = lane.get("ledger_feature_ids")
    if not isinstance(feature_ids, list) or len(feature_ids) != 1 or not isinstance(feature_ids[0], str):
        raise ValueError(f"inventory lane {lane.get('lane_id')!r} must declare one ledger feature id")
    return feature_ids[0]


def lane_for_builder(script_path: Path, *, inventory_path: Path = DEFAULT_INVENTORY_PATH) -> dict[str, Any]:
    script_display = script_path.resolve().relative_to(ROOT).as_posix()
    matches: list[dict[str, Any]] = []
    for lane in load_inventory(inventory_path)["lanes"]:
        if not isinstance(lane, dict):
            continue
        builder = lane.get("builder")
        argv = builder.get("argv") if isinstance(builder, Mapping) else None
        if isinstance(argv, list) and any(isinstance(item, str) and item == script_display for item in argv):
            matches.append(lane)
    if len(matches) != 1:
        raise ValueError(f"expected one inventory lane for builder {script_display}, got {len(matches)}")
    return matches[0]


def claim_id(lane: Mapping[str, Any]) -> str:
    value = lane.get("claim_id")
    if not isinstance(value, str) or not value:
        raise ValueError(f"inventory lane {lane.get('lane_id')!r} missing claim_id")
    return value


def ct_id(lane: Mapping[str, Any]) -> str:
    ct = lane.get("ct")
    value = ct.get("test_id") if isinstance(ct, Mapping) else None
    if not isinstance(value, str) or not value:
        raise ValueError(f"inventory lane {lane.get('lane_id')!r} missing ct.test_id")
    return value
