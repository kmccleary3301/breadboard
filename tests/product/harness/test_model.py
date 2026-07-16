from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

import pytest
import yaml

from breadboard.product.harness.model import HarnessDefinition
from breadboard.product.harness.validate import HarnessDefinitionValidationError

ROOT = Path(__file__).parents[3]
CONFIGS = (
    ROOT / "agent_configs/templates/minimal_harness.v2.yaml",
    ROOT / "agent_configs/v2/codex_0-107-0_e4_3-6-2026.yaml",
)


def _load(path: Path) -> dict[str, Any]:
    document = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert isinstance(document, dict)
    return document


def _assert_exact(actual: Any, expected: Any) -> None:
    assert type(actual) is type(expected)
    if isinstance(expected, dict):
        assert actual.keys() == expected.keys()
        for key in expected:
            _assert_exact(actual[key], expected[key])
    elif isinstance(expected, list):
        assert len(actual) == len(expected)
        for actual_item, expected_item in zip(actual, expected):
            _assert_exact(actual_item, expected_item)
    else:
        assert actual == expected


@pytest.mark.parametrize("path", CONFIGS, ids=("minimal-v2", "complex-v2"))
def test_actual_v2_configs_round_trip_exactly_without_coercion(path: Path) -> None:
    source = _load(path)
    expected = copy.deepcopy(source)
    definition = HarnessDefinition.from_mapping(source)
    source["workspace"]["root"] = "mutated"

    _assert_exact(definition.as_dict(), expected)


def test_definition_is_recursively_immutable_and_copies_are_detached() -> None:
    source = _load(CONFIGS[1])
    definition = HarnessDefinition.from_mapping(source)
    first, second = definition.as_dict(), definition.as_dict()

    with pytest.raises(TypeError):
        definition["workspace"]["root"] = "mutated"  # type: ignore[index]
    with pytest.raises(TypeError):
        definition["modes"][0]["name"] = "mutated"  # type: ignore[index]
    with pytest.raises(AttributeError):
        definition["modes"].append({"name": "mutated"})  # type: ignore[union-attr]

    first["workspace"]["root"] = "changed"
    first["modes"][0]["tools_enabled"].append("extra")
    assert second == source == definition.as_dict()
    assert first["workspace"] is not second["workspace"]
    assert first["modes"] is not second["modes"]


def test_canonical_json_is_stable_compact_utf8_safe_and_has_no_newline() -> None:
    source = _load(CONFIGS[1])
    source["modes"][0]["prompt"] = "Plan — then execute"
    reordered = dict(reversed(tuple(source.items())))

    canonical = HarnessDefinition.from_mapping(source).canonical_json()
    assert canonical == HarnessDefinition.from_mapping(reordered).canonical_json()
    assert canonical == json.dumps(
        source,
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    assert "—" in canonical and not canonical.endswith("\n")


def test_from_mapping_delegates_rejection_to_public_validation_contract() -> None:
    invalid = _load(CONFIGS[0])
    invalid.update(schema_version="bb.unknown.v9", version=9)

    with pytest.raises(HarnessDefinitionValidationError):
        HarnessDefinition.from_mapping(invalid)
