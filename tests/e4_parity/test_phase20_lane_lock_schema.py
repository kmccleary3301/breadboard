from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

import pytest
from jsonschema import Draft202012Validator

from scripts import check_phase20_freeze


ROOT = Path(__file__).resolve().parents[2]
LOCK_SCHEMA_PATH = ROOT / "contracts" / "kernel" / "schemas" / "bb.e4.lane_lock.v1.schema.json"


def _load_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    assert isinstance(value, dict)
    return value


def test_lane_lock_schema_is_valid_and_contains_no_volatile_property_names() -> None:
    """The deterministic lock contract cannot expose timestamps, durations, or elapsed-time fields."""
    schema = _load_json(LOCK_SCHEMA_PATH)
    Draft202012Validator.check_schema(schema)

    assert check_phase20_freeze.validate_lane_lock_schema(schema) == []


@pytest.mark.parametrize("property_name", ["generated_at_utc", "duration_ms", "elapsed_seconds"])
def test_freeze_checker_finds_nested_volatile_lock_properties(property_name: str) -> None:
    """The freeze checker recursively reports newly introduced nondeterministic lock fields."""
    schema = _load_json(LOCK_SCHEMA_PATH)
    mutated = copy.deepcopy(schema)
    mutated["properties"]["target_freeze"]["properties"][property_name] = {"type": "string"}

    violations = check_phase20_freeze.validate_lane_lock_schema(mutated)

    assert len(violations) == 1
    assert violations[0].endswith(f"/properties/{property_name}")
