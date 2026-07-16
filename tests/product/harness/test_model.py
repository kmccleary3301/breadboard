from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
import yaml

from breadboard.product.harness.model import HarnessDefinition
from breadboard.product.harness.validate import (
    HarnessDefinitionValidationError,
    ValidationFinding,
)

ROOT = Path(__file__).parents[3]
CONFIGS = (
    ROOT / "agent_configs/templates/minimal_harness.v2.yaml",
    ROOT / "agent_configs/v2/codex_0-107-0_e4_3-6-2026.yaml",
)


def _load(path: Path) -> dict[str, Any]:
    document = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert isinstance(document, dict)
    return document


def _typed(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: _typed(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_typed(item) for item in value]
    return type(value), value


@pytest.mark.parametrize("path", CONFIGS, ids=("minimal-v2", "complex-v2"))
def test_actual_v2_configs_round_trip_exactly_without_coercion(path: Path) -> None:
    source = _load(path)
    expected = _load(path)
    definition = HarnessDefinition.from_mapping(source)
    source["workspace"]["root"] = "mutated"
    assert _typed(definition.as_dict()) == _typed(expected)
    assert json.loads(definition.canonical_json()) == expected

def test_construction_validates_the_detached_snapshot() -> None:
    class ClearingMapping(dict[str, Any]):
        def items(self):  # type: ignore[override]
            entries = dict(self).items()
            self.clear()
            return entries

    source = ClearingMapping(_load(CONFIGS[0]))
    definition = HarnessDefinition.from_mapping(source)
    assert not source
    assert definition.as_dict() == _load(CONFIGS[0])


def test_definition_is_recursively_immutable_and_copies_are_detached() -> None:
    source = _load(CONFIGS[1])
    source["dossier"] = {"nested": [{"value": "original"}]}
    definition = HarnessDefinition.from_mapping(source)
    source["dossier"]["nested"][0]["value"] = "source"
    first, second = definition.as_dict(), definition.as_dict()
    with pytest.raises(TypeError):
        definition["workspace"]["root"] = "mutated"  # type: ignore[index]
    with pytest.raises(TypeError):
        definition["modes"][0]["name"] = "mutated"  # type: ignore[index]
    with pytest.raises(AttributeError):
        definition["modes"].append({"name": "mutated"})  # type: ignore[union-attr]
    first["workspace"]["root"] = "changed"
    first["modes"][0]["tools_enabled"].append("extra")
    first["dossier"]["nested"][0]["value"] = "copy"
    assert second == json.loads(definition.canonical_json()) == definition.as_dict()
    assert second["dossier"]["nested"][0]["value"] == "original"


def test_canonical_json_is_stable_compact_utf8_safe_and_has_no_newline() -> None:
    source = _load(CONFIGS[1])
    source["modes"][0]["prompt"] = "Plan — then execute"
    reordered = dict(reversed(tuple(source.items())))
    canonical = HarnessDefinition.from_mapping(source).canonical_json()
    assert canonical == HarnessDefinition.from_mapping(reordered).canonical_json()
    assert canonical == json.dumps(
        source, allow_nan=False, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    assert "—" in canonical and not canonical.endswith("\n")


def test_from_mapping_delegates_rejection_to_public_validation_contract() -> None:
    invalid = _load(CONFIGS[0])
    invalid["dossier"] = {"bad": {1, 2}}
    expected = ValidationFinding(
        "/dossier/bad", "json_type", "Value is not a JSON-domain value"
    )
    with pytest.raises(HarnessDefinitionValidationError) as raised:
        HarnessDefinition.from_mapping(invalid)
    assert raised.value.findings == (expected,)
