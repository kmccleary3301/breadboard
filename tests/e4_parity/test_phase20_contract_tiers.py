from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from jsonschema import Draft202012Validator

from scripts import check_contract_tiers


ROOT = Path(__file__).resolve().parents[2]
SCHEMA_DIR = ROOT / "contracts" / "kernel" / "schemas"
TIER_SCHEMA_PATH = SCHEMA_DIR / "bb.contract_tiers.v1.schema.json"
TIER_REGISTRY_PATH = ROOT / "contracts" / "kernel" / "registries" / "contract_tiers.v1.json"
PACKS_PATH = ROOT / "contracts" / "kernel" / "packs.v1.json"
README_PATH = SCHEMA_DIR / "README.md"


def _load_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    assert isinstance(value, dict)
    return value


def _entries_by_schema_id() -> dict[str, dict[str, Any]]:
    registry = _load_json(TIER_REGISTRY_PATH)
    return {entry["schema_id"]: entry for entry in registry["entries"]}


def test_contract_tier_schema_and_product_spine_consumers_validate() -> None:
    """Kept runtime/config contracts name consumers that are present in the tracked snapshot."""
    schema = _load_json(TIER_SCHEMA_PATH)
    Draft202012Validator.check_schema(schema)
    kept_product_spine_tiers = {
        entry["tier"]
        for entry in _entries_by_schema_id().values()
        if entry["disposition"] == "keep"
        and entry["tier"] in {"runtime_protocol", "config_algebra"}
    }
    assert kept_product_spine_tiers == {"runtime_protocol", "config_algebra"}

    assert check_contract_tiers.validate_contract_tiers(
        registry_path=TIER_REGISTRY_PATH,
        schema_path=TIER_SCHEMA_PATH,
        packs_path=PACKS_PATH,
    ) == []


@pytest.mark.parametrize(
    ("schema_id", "tier", "consumers"),
    [
        (
            "bb.run_context.v1",
            "host_protocol",
            [
                {
                    "kind": "runtime_emission",
                    "path": "sdk/ts-kernel-core/src/contracts.ts",
                }
            ],
        ),
        (
            "bb.run_request.v1",
            "host_protocol",
            [
                {
                    "kind": "runtime_emission",
                    "path": "sdk/ts-host-bridges/src/index.ts",
                }
            ],
        ),
        (
            "bb.tool_binding.v1",
            "config_algebra",
            [
                {
                    "kind": "sdk",
                    "path": "sdk/ts-kernel-core/src/tool-surfaces.ts",
                }
            ],
        ),
        (
            "bb.work_item.v2",
            "runtime_protocol",
            [
                {
                    "kind": "runtime_emission",
                    "path": "agentic_coder_prototype/api/cli_bridge/runtime_emission.py",
                }
            ],
        ),
        (
            "bb.contract_tiers.v1",
            "evidence",
            [
                {
                    "kind": "evidence_machinery",
                    "path": "scripts/check_contract_tiers.py",
                },
                {
                    "kind": "evidence_machinery",
                    "path": "scripts/check_phase20_freeze.py",
                },
            ],
        ),
    ],
)
def test_audited_contracts_name_their_actual_tier_and_consumer(
    schema_id: str,
    tier: str,
    consumers: list[dict[str, str]],
) -> None:
    """Audited contracts remain classified by their actual runtime or evidence use."""
    entry = _entries_by_schema_id()[schema_id]

    assert entry["tier"] == tier
    assert entry["disposition"] == "keep"
    assert {
        (consumer["kind"], consumer["path"]) for consumer in entry["consumers"]
    } == {(consumer["kind"], consumer["path"]) for consumer in consumers}


@pytest.mark.parametrize(
    ("schema_id", "tier"),
    [
        ("bb.config_mutation_record.v1", "config_algebra"),
        ("bb.context_resource_pack.v1", "config_algebra"),
        ("bb.e4.lane_lock.v1", "config_algebra"),
        ("bb.environment_selector.v2", "config_algebra"),
        ("bb.registry.v1", "config_algebra"),
        ("bb.tool_spec.v2", "config_algebra"),
        ("bb.task.v1", "frozen_legacy"),
        ("bb.work_item.v1", "frozen_legacy"),
    ],
)
def test_descriptive_tier_without_a_product_consumer_remains_frozen(
    schema_id: str,
    tier: str,
) -> None:
    """A descriptive tier does not imply an unearned keep disposition."""
    entry = _entries_by_schema_id()[schema_id]

    assert entry["tier"] == tier
    assert entry["disposition"] == "freeze"
    assert entry["consumers"] == []


def test_frozen_legacy_contracts_are_never_marked_for_continued_use() -> None:
    """A frozen-legacy classification always carries the freeze disposition."""
    frozen_legacy = [
        entry
        for entry in _entries_by_schema_id().values()
        if entry["tier"] == "frozen_legacy"
    ]

    assert frozen_legacy
    assert {entry["disposition"] for entry in frozen_legacy} == {"freeze"}




def test_kernel_pack_describes_minimality_as_a_tier_qualified_claim() -> None:
    """Pack documentation limits “minimal” to runtime_protocol and points to its registry."""
    packs = _load_json(PACKS_PATH)
    kernel = next(entry for entry in packs["entries"] if entry["id"] == "kernel")
    description = kernel["description"]

    assert "runtime_protocol" in description
    assert "contracts/kernel/registries/contract_tiers.v1.json" in description
    assert "minimal expressive " + "harness primitive language" not in description.lower()


def test_generated_readme_lists_every_registry_entry_with_its_tier() -> None:
    """Generated schema documentation preserves the registry's complete tier assignment."""
    readme = README_PATH.read_text(encoding="utf-8")
    data_rows = [line for line in readme.splitlines() if line.startswith("| `bb.")]
    documented_tiers: dict[str, str] = {}
    for row in data_rows:
        cells = row.split(" | ")
        schema_id = cells[0].removeprefix("| `").removesuffix("`")
        tier = cells[-1].removeprefix("`").removesuffix("` |")
        documented_tiers[schema_id] = tier

    registry_tiers = {
        schema_id: entry["tier"]
        for schema_id, entry in _entries_by_schema_id().items()
    }
    assert len(data_rows) == len(registry_tiers)
    assert documented_tiers == registry_tiers
    assert "minimal expressive " + "harness primitive language" not in readme.lower()


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


def test_contract_tier_checker_rejects_existing_consumer_absent_from_tracked_snapshot(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A consumer must belong to the freeze snapshot; filesystem existence alone is insufficient."""
    registry = _load_json(TIER_REGISTRY_PATH)
    keep_entry = next(entry for entry in registry["entries"] if entry["disposition"] == "keep")
    consumer_path = Path("existing-but-untracked.py")
    keep_entry["consumers"][0]["path"] = consumer_path.as_posix()
    (tmp_path / consumer_path).write_text("# intentionally untracked\n", encoding="utf-8")
    mutated_path = tmp_path / "contract_tiers.v1.json"
    mutated_path.write_text(json.dumps(registry), encoding="utf-8")
    monkeypatch.setattr(check_contract_tiers, "ROOT", tmp_path)

    errors = check_contract_tiers.validate_contract_tiers(
        registry_path=mutated_path,
        schema_path=TIER_SCHEMA_PATH,
        packs_path=PACKS_PATH,
        tracked_files=frozenset({tmp_path / "tracked-control.py"}),
    )

    assert any(
        keep_entry["schema_id"] in error
        and consumer_path.as_posix() in error
        and "not tracked" in error
        for error in errors
    )
