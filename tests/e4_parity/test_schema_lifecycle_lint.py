from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any

from scripts.e4_parity import lint_schema_lifecycle as lifecycle_lint


ROOT = Path(__file__).resolve().parents[2]
LINT_SCRIPT = ROOT / "scripts" / "e4_parity" / "lint_schema_lifecycle.py"


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _write_schemas(repo_root: Path, schema_ids: list[str]) -> None:
    for schema_id in schema_ids:
        _write_json(repo_root / "contracts" / "kernel" / "schemas" / f"{schema_id}.schema.json", {"$id": schema_id})


def _family_entry(family_id: str, schema_ids: list[str]) -> dict[str, Any]:
    return {
        "id": family_id,
        "status": "active",
        "description": f"{family_id} schema generation family.",
        "metadata": {"schema_versions": schema_ids},
    }


def _lifecycle_entry(
    schema_id: str,
    family: str,
    *,
    lifecycle: str = "active_production",
    default_for_generation: bool = False,
    superseded_by: str | None = None,
    notes: str | None = None,
) -> dict[str, Any]:
    entry: dict[str, Any] = {
        "schema_id": schema_id,
        "family": family,
        "lifecycle": lifecycle,
        "default_for_generation": default_for_generation,
        "superseded_by": superseded_by,
    }
    if notes is not None:
        entry["notes"] = notes
    return entry


def _write_kernel_families(repo_root: Path, entries: list[dict[str, Any]]) -> None:
    _write_json(
        repo_root / "contracts" / "kernel" / "registries" / "kernel_families.v1.json",
        {
            "schema_version": "bb.registry.v1",
            "registry_id": "kernel_families",
            "entries": entries,
        },
    )


def _write_lifecycle(repo_root: Path, entries: list[dict[str, Any]]) -> None:
    _write_json(
        repo_root / "contracts" / "kernel" / "registries" / "schema_lifecycle.v1.json",
        {
            "schema_version": "bb.schema_lifecycle.v1",
            "registry_id": "schema_lifecycle",
            "entries": entries,
        },
    )


def _install_registry(
    repo_root: Path,
    *,
    schema_ids: list[str],
    families: list[dict[str, Any]],
    lifecycle_entries: list[dict[str, Any]],
) -> None:
    _write_schemas(repo_root, schema_ids)
    _write_kernel_families(repo_root, families)
    _write_lifecycle(repo_root, lifecycle_entries)


def _diagnostics(repo_root: Path) -> list[lifecycle_lint.LifecycleDiagnostic]:
    return lifecycle_lint.lint_schema_lifecycle(repo_root=repo_root)


def _rules(diagnostics: list[lifecycle_lint.LifecycleDiagnostic]) -> list[str]:
    return [diagnostic.rule for diagnostic in diagnostics]


def test_minimal_registry_allows_two_latest_true_generation_families(tmp_path: Path) -> None:
    _install_registry(
        tmp_path,
        schema_ids=["bb.work_item.v2", "bb.tool_spec.v2"],
        families=[
            _family_entry("work_item", ["bb.work_item.v2"]),
            _family_entry("tool_spec", ["bb.tool_spec.v2"]),
        ],
        lifecycle_entries=[
            _lifecycle_entry("bb.work_item.v2", "work_item", default_for_generation=True),
            _lifecycle_entry("bb.tool_spec.v2", "tool_spec", default_for_generation=True),
        ],
    )

    assert _diagnostics(tmp_path) == []


def test_missing_lifecycle_row_reports_the_uncovered_schema(tmp_path: Path) -> None:
    _install_registry(
        tmp_path,
        schema_ids=["bb.work_item.v2", "bb.tool_spec.v2"],
        families=[
            _family_entry("work_item", ["bb.work_item.v2"]),
            _family_entry("tool_spec", ["bb.tool_spec.v2"]),
        ],
        lifecycle_entries=[
            _lifecycle_entry("bb.work_item.v2", "work_item", default_for_generation=True),
        ],
    )

    diagnostics = _diagnostics(tmp_path)

    assert _rules(diagnostics) == ["totality"]
    assert diagnostics[0].message == "schema_lifecycle missing schema(s): bb.tool_spec.v2"
    assert diagnostics[0].pointer == "$/entries"


def test_duplicate_lifecycle_row_reports_the_repeated_schema_id(tmp_path: Path) -> None:
    _install_registry(
        tmp_path,
        schema_ids=["bb.work_item.v2", "bb.tool_spec.v2"],
        families=[
            _family_entry("work_item", ["bb.work_item.v2"]),
            _family_entry("tool_spec", ["bb.tool_spec.v2"]),
        ],
        lifecycle_entries=[
            _lifecycle_entry("bb.work_item.v2", "work_item", default_for_generation=True),
            _lifecycle_entry("bb.work_item.v2", "work_item"),
            _lifecycle_entry("bb.tool_spec.v2", "tool_spec", default_for_generation=True),
        ],
    )

    diagnostics = _diagnostics(tmp_path)

    assert _rules(diagnostics) == ["totality"]
    assert diagnostics[0].message == "bb.work_item.v2 appears 2 times in schema_lifecycle"


def test_non_exempt_schema_family_must_match_kernel_families_owner(tmp_path: Path) -> None:
    _install_registry(
        tmp_path,
        schema_ids=["bb.work_item.v2"],
        families=[_family_entry("work_item", ["bb.work_item.v2"])],
        lifecycle_entries=[
            _lifecycle_entry("bb.work_item.v2", "tool_spec", default_for_generation=True),
        ],
    )

    diagnostics = _diagnostics(tmp_path)

    assert _rules(diagnostics) == ["family_census"]
    assert diagnostics[0].message == "bb.work_item.v2 lifecycle family 'tool_spec' does not match kernel_families owner 'work_item'"
    assert diagnostics[0].pointer == "$/entries/0/family"


def test_duplicate_kernel_families_id_is_rejected_even_when_versions_are_disjoint(tmp_path: Path) -> None:
    _install_registry(
        tmp_path,
        schema_ids=["bb.work_item.v1", "bb.work_item.v2"],
        families=[
            _family_entry("work_item", ["bb.work_item.v1"]),
            _family_entry("work_item", ["bb.work_item.v2"]),
        ],
        lifecycle_entries=[
            _lifecycle_entry(
                "bb.work_item.v1",
                "work_item",
                lifecycle="validate_only",
                superseded_by="bb.work_item.v2",
            ),
            _lifecycle_entry("bb.work_item.v2", "work_item", default_for_generation=True),
        ],
    )

    diagnostics = _diagnostics(tmp_path)

    assert _rules(diagnostics) == ["kernel_families_unique_id"]
    assert diagnostics[0].message == "kernel_families id 'work_item' appears 2 times"
    assert diagnostics[0].pointer == "$/entries"


def test_family_with_multiple_generation_defaults_is_rejected(tmp_path: Path) -> None:
    _install_registry(
        tmp_path,
        schema_ids=["bb.work_item.v1", "bb.work_item.v2"],
        families=[_family_entry("work_item", ["bb.work_item.v1", "bb.work_item.v2"])],
        lifecycle_entries=[
            _lifecycle_entry("bb.work_item.v1", "work_item", default_for_generation=True),
            _lifecycle_entry("bb.work_item.v2", "work_item", default_for_generation=True),
        ],
    )

    diagnostics = _diagnostics(tmp_path)

    assert _rules(diagnostics) == ["default_per_family"]
    assert diagnostics[0].message == "family work_item has 2 defaults; expected exactly one"


def test_non_active_non_newest_entry_must_name_its_family_successor(tmp_path: Path) -> None:
    _install_registry(
        tmp_path,
        schema_ids=["bb.work_item.v1", "bb.work_item.v2"],
        families=[_family_entry("work_item", ["bb.work_item.v1", "bb.work_item.v2"])],
        lifecycle_entries=[
            _lifecycle_entry("bb.work_item.v1", "work_item", lifecycle="validate_only"),
            _lifecycle_entry("bb.work_item.v2", "work_item", default_for_generation=True),
        ],
    )

    diagnostics = _diagnostics(tmp_path)

    assert _rules(diagnostics) == ["supersession"]
    assert diagnostics[0].message == "bb.work_item.v1 must supersede to an existing schema in family work_item"
    assert diagnostics[0].pointer == "$/entries/0/superseded_by"


def test_superseded_by_must_stay_inside_the_lifecycle_family(tmp_path: Path) -> None:
    _install_registry(
        tmp_path,
        schema_ids=["bb.work_item.v1", "bb.work_item.v2", "bb.tool_spec.v1"],
        families=[
            _family_entry("work_item", ["bb.work_item.v1", "bb.work_item.v2"]),
            _family_entry("tool_spec", ["bb.tool_spec.v1"]),
        ],
        lifecycle_entries=[
            _lifecycle_entry(
                "bb.work_item.v1",
                "work_item",
                lifecycle="validate_only",
                superseded_by="bb.tool_spec.v1",
            ),
            _lifecycle_entry("bb.work_item.v2", "work_item", default_for_generation=True),
            _lifecycle_entry("bb.tool_spec.v1", "tool_spec", default_for_generation=True),
        ],
    )

    diagnostics = _diagnostics(tmp_path)

    assert _rules(diagnostics) == ["supersession"]
    assert diagnostics[0].message == "bb.work_item.v1 must supersede to an existing schema in family work_item"
    assert diagnostics[0].pointer == "$/entries/0/superseded_by"


def test_newest_non_active_schema_requires_superseded_by_even_with_adr_notes(tmp_path: Path) -> None:
    schema_ids = ["bb.work_item.v1", "bb.work_item.v2"]
    families = [_family_entry("work_item", schema_ids)]
    _install_registry(
        tmp_path,
        schema_ids=schema_ids,
        families=families,
        lifecycle_entries=[
            _lifecycle_entry("bb.work_item.v1", "work_item", default_for_generation=True),
            _lifecycle_entry("bb.work_item.v2", "work_item", lifecycle="frozen_accepted_evidence"),
        ],
    )

    diagnostics = _diagnostics(tmp_path)

    assert _rules(diagnostics) == ["supersession"]
    assert diagnostics[0].message == "bb.work_item.v2 must supersede to an existing schema in family work_item"

    _write_lifecycle(
        tmp_path,
        [
            _lifecycle_entry("bb.work_item.v1", "work_item", default_for_generation=True),
            _lifecycle_entry(
                "bb.work_item.v2",
                "work_item",
                lifecycle="frozen_accepted_evidence",
                notes="Generation paused by ADR-SCHEMA-LIFECYCLE until the v2 producer is accepted.",
            ),
        ],
    )

    assert _diagnostics(tmp_path) == diagnostics

def test_non_active_schema_producer_requires_narrow_allowlist(tmp_path: Path) -> None:
    _install_registry(
        tmp_path,
        schema_ids=["bb.work_item.v1", "bb.work_item.v2"],
        families=[_family_entry("work_item", ["bb.work_item.v1", "bb.work_item.v2"])],
        lifecycle_entries=[
            _lifecycle_entry("bb.work_item.v1", "work_item", lifecycle="validate_only", superseded_by="bb.work_item.v2"),
            _lifecycle_entry("bb.work_item.v2", "work_item", default_for_generation=True),
        ],
    )
    producer = tmp_path / "scripts" / "emit_work_item.py"
    producer.parent.mkdir(parents=True, exist_ok=True)
    producer.write_text(
        "def emit() -> dict[str, str]:\n"
        "    return {'schema_version': 'bb.work_item.v1'}\n",
        encoding="utf-8",
    )

    diagnostics = _diagnostics(tmp_path)

    assert _rules(diagnostics) == ["producer_conformance"]
    assert diagnostics[0].path == "scripts/emit_work_item.py"
    assert diagnostics[0].message == "bb.work_item.v1 is non-active at 'emit'; active producers must emit bb.work_item.v2"


def test_non_active_schema_allowlist_is_schema_specific(tmp_path: Path, monkeypatch: Any) -> None:
    _install_registry(
        tmp_path,
        schema_ids=["bb.work_item.v1", "bb.work_item.v2", "bb.tool_spec.v1", "bb.tool_spec.v2"],
        families=[
            _family_entry("work_item", ["bb.work_item.v1", "bb.work_item.v2"]),
            _family_entry("tool_spec", ["bb.tool_spec.v1", "bb.tool_spec.v2"]),
        ],
        lifecycle_entries=[
            _lifecycle_entry("bb.work_item.v1", "work_item", lifecycle="validate_only", superseded_by="bb.work_item.v2"),
            _lifecycle_entry("bb.work_item.v2", "work_item", default_for_generation=True),
            _lifecycle_entry("bb.tool_spec.v1", "tool_spec", lifecycle="validate_only", superseded_by="bb.tool_spec.v2"),
            _lifecycle_entry("bb.tool_spec.v2", "tool_spec", default_for_generation=True),
        ],
    )
    producer = tmp_path / "scripts" / "emit_contracts.py"
    producer.parent.mkdir(parents=True, exist_ok=True)
    producer.write_text(
        "def emit() -> list[dict[str, str]]:\n"
        "    return [\n"
        "        {'schema_version': 'bb.work_item.v1'},\n"
        "        {'schema_version': 'bb.tool_spec.v1'},\n"
        "    ]\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(
        lifecycle_lint,
        "LEGACY_PRODUCER_ALLOWLIST",
        {"scripts/emit_contracts.py:emit": "ADR-AV-999"},
    )
    monkeypatch.setattr(
        lifecycle_lint,
        "LEGACY_PRODUCER_SCHEMAS",
        {"scripts/emit_contracts.py:emit": "bb.work_item.v1"},
    )
    monkeypatch.setattr(lifecycle_lint, "ACCEPTED_LEGACY_PRODUCER_ADRS", frozenset({"ADR-AV-999"}))

    diagnostics = _diagnostics(tmp_path)

    assert _rules(diagnostics) == ["producer_conformance"]
    assert diagnostics[0].message.startswith("bb.tool_spec.v1 is non-active at 'emit'")


def test_cli_json_returns_nonzero_and_structured_diagnostics_on_errors(tmp_path: Path) -> None:
    _install_registry(
        tmp_path,
        schema_ids=["bb.work_item.v2", "bb.tool_spec.v2"],
        families=[
            _family_entry("work_item", ["bb.work_item.v2"]),
            _family_entry("tool_spec", ["bb.tool_spec.v2"]),
        ],
        lifecycle_entries=[
            _lifecycle_entry("bb.work_item.v2", "work_item", default_for_generation=True),
        ],
    )

    result = subprocess.run(
        [sys.executable, str(LINT_SCRIPT), "--repo-root", str(tmp_path), "--json"],
        check=False,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )

    assert result.returncode == 1
    assert result.stderr == ""
    payload = json.loads(result.stdout)
    assert payload == {
        "diagnostic_count": 1,
        "diagnostics": [
            {
                "message": "schema_lifecycle missing schema(s): bb.tool_spec.v2",
                "path": "contracts/kernel/registries/schema_lifecycle.v1.json",
                "pointer": "$/entries",
                "rule": "totality",
            }
        ],
        "ok": False,
    }
