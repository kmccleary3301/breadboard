from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator

from scripts import check_contract_tiers


ROOT = Path(__file__).resolve().parents[2]
SCHEMA_DIR = ROOT / "contracts" / "kernel" / "schemas"
TIER_SCHEMA_PATH = SCHEMA_DIR / "bb.contract_tiers.v1.schema.json"
TIER_REGISTRY_PATH = ROOT / "contracts" / "kernel" / "registries" / "contract_tiers.v1.json"
PACKS_PATH = ROOT / "contracts" / "kernel" / "packs.v1.json"


def _load_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    assert isinstance(value, dict)
    return value


def test_contract_tier_schema_and_checked_in_registry_validate() -> None:
    """The checked-in tier registry conforms to a valid Draft 2020-12 registry schema."""
    schema = _load_json(TIER_SCHEMA_PATH)
    Draft202012Validator.check_schema(schema)

    assert check_contract_tiers.validate_contract_tiers(
        registry_path=TIER_REGISTRY_PATH,
        schema_path=TIER_SCHEMA_PATH,
        packs_path=PACKS_PATH,
    ) == []


def test_contract_tier_registry_exactly_matches_generated_schema_census() -> None:
    """Every packed or Phase 20 schema is classified once, with no missing or stale registry rows."""
    registry = _load_json(TIER_REGISTRY_PATH)
    registered_ids = {entry["schema_id"] for entry in registry["entries"]}

    assert registered_ids == check_contract_tiers.generated_schema_ids(PACKS_PATH)


def test_contract_tier_checker_rejects_keep_entry_without_a_consumer(tmp_path: Path) -> None:
    """A schema classified for continued use must name at least one real consumer."""
    registry = _load_json(TIER_REGISTRY_PATH)
    keep_entry = next(entry for entry in registry["entries"] if entry["disposition"] == "keep")
    keep_entry["consumers"] = []
    mutated_path = tmp_path / "contract_tiers.v1.json"
    mutated_path.write_text(json.dumps(registry), encoding="utf-8")

    errors = check_contract_tiers.validate_contract_tiers(
        registry_path=mutated_path,
        schema_path=TIER_SCHEMA_PATH,
        packs_path=PACKS_PATH,
    )

    assert any(keep_entry["schema_id"] in error and "consumer" in error for error in errors)
