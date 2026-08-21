from __future__ import annotations

import hashlib
import importlib
import json
from pathlib import Path
from typing import Any, Mapping

from conformance.comparators.stored_report import compare
from scripts.validate_e4_c4_chain import _diff_comparator_reports

ROOT = Path(__file__).resolve().parents[2]
INVENTORY_PATH = ROOT / "docs" / "conformance" / "e4_lane_inventory.json"
REGISTRY_PATH = ROOT / "conformance" / "comparators" / "registry.json"


def _load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return "sha256:" + digest.hexdigest()


def _accepted_lanes() -> list[dict[str, Any]]:
    inventory = _load_json(INVENTORY_PATH)
    return [lane for lane in inventory["lanes"] if lane["status"] == "accepted"]


def _registry_entries() -> list[dict[str, Any]]:
    registry = _load_json(REGISTRY_PATH)
    return registry["comparators"]


def _comparator_fixture(tmp_path: Path) -> tuple[Path, Path, dict[str, Any]]:
    replay_path = tmp_path / "bb_replay_result.json"
    _write_json(replay_path, {"status": "passed", "value": 1})
    comparator_path = tmp_path / "comparator_report.json"
    report = {
        "schema_version": "bb.e4.comparator_report.v1",
        "lane_id": "fixture_lane",
        "config_id": "fixture_config",
        "passed": 1,
        "failed": 0,
        "warned": 0,
        "details": [],
        "input_hashes": {str(replay_path): _sha256(replay_path)},
        "assertions": [
            {
                "name": "replay_exact",
                "status": "passed",
                "observed": {"status": "passed", "value": 1},
                "expected": {"status": "passed", "value": 1},
            }
        ],
    }
    _write_json(comparator_path, report)
    return replay_path, comparator_path, report


def test_tampered_replay_rerun_fails_naming_diverging_assertion_ids(tmp_path: Path) -> None:
    replay_path, comparator_path, stored = _comparator_fixture(tmp_path)
    _write_json(replay_path, {"status": "passed", "value": 2})

    fresh = compare(
        {
            "capture": {},
            "replay": {},
            "scope": {},
            "artifacts": {"comparator_ref": comparator_path},
        }
    )
    errors: list[str] = []
    diff = _diff_comparator_reports(
        stored=stored,
        fresh=fresh,
        comparator_class="deterministic_replay",
        errors=errors,
    )

    assert diff["ok"] is False
    assert diff["status_mismatch_ids"] == ["replay_exact"]
    assert any("replay_exact" in error for error in errors)


def test_registry_has_exactly_one_comparator_for_every_accepted_inventory_lane() -> None:
    entries = _registry_entries()
    for lane in _accepted_lanes():
        matches = [entry for entry in entries if lane["lane_id"] in entry.get("lane_ids", [])]
        assert len(matches) == 1, lane["lane_id"]
        assert matches[0]["comparator_id"] == lane["comparator_id"]


def test_each_registered_comparator_entrypoint_conforms_to_protocol(tmp_path: Path) -> None:
    _, comparator_path, _ = _comparator_fixture(tmp_path)
    for entry in _registry_entries():
        entrypoint = entry["entrypoint"]
        module = importlib.import_module(entrypoint["module"])
        comparator = getattr(module, entrypoint["callable"])
        report = comparator(
            {
                "capture": {},
                "replay": {},
                "scope": {},
                "artifacts": {"comparator_ref": comparator_path},
            }
        )
        assert isinstance(report, dict)
        assert report["schema_version"] == entry["report_schema_version"]
        assert isinstance(report["assertions"], list) and report["assertions"]
        assert {"assertion_id", "status", "observed", "expected"} <= set(report["assertions"][0])


def test_stored_report_comparator_is_deterministic_for_identical_inputs(tmp_path: Path) -> None:
    _, comparator_path, _ = _comparator_fixture(tmp_path)
    inp = {
        "capture": {},
        "replay": {},
        "scope": {},
        "artifacts": {"comparator_ref": comparator_path},
    }

    first = compare(inp)
    second = compare(inp)

    assert json.dumps(first, sort_keys=True, separators=(",", ":")) == json.dumps(second, sort_keys=True, separators=(",", ":"))
