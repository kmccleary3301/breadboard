from __future__ import annotations

import hashlib
import importlib
import json
from pathlib import Path
import shutil
from typing import Any, Mapping
from conformance.comparators.stored_report import compare
from conformance.comparators.openhands_sdk import (
    compare as compare_openhands,
    project_supplier_case,
)
from conformance.comparators.openclaw_2026_9_4 import (
    project_supplier_case as project_openclaw_supplier_case,
)
from scripts.validate_e4_c4_chain import _diff_comparator_reports

ROOT = Path(__file__).resolve().parents[2]
INVENTORY_PATH = ROOT / "docs" / "conformance" / "e4_lane_inventory.json"
REGISTRY_PATH = ROOT / "conformance" / "comparators" / "registry.json"
OPENHANDS_CASE = ROOT / "tests" / "e4_parity" / "fixtures" / "openhands_sdk" / "OH-01-normal-file-effect"
OPENCLAW_CASE = ROOT / "tests" / "e4_parity" / "fixtures" / "openclaw_packet_640"

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


def _openhands_measured_bb_trace() -> dict[str, Any]:
    supplier = project_supplier_case(OPENHANDS_CASE)
    workspace = OPENHANDS_CASE / "workspace"
    effects: dict[str, dict[str, Any]] = {}
    for relative, digest in supplier["file_effects"].items():
        if digest is None:
            effects[relative] = {"exists": False}
            continue
        path = workspace / relative
        content = path.read_bytes()
        assert _sha256(path) == digest
        effects[relative] = {
            "exists": True,
            "bytes": len(content),
            "sha256": digest,
            "content_utf8": content.decode("utf-8", "replace"),
        }
    replay = dict(supplier)
    replay["file_effects"] = effects
    return replay


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


def _registered_comparator_input(
    comparator_id: str,
    comparator_path: Path,
    tmp_path: Path,
) -> dict[str, Any]:
    if comparator_id == "pi_coding_agent_0_73_1_trace_v1":
        return {
            "capture": {"role": "supplier", "requests": []},
            "replay": {
                "role": "replay",
                "messages": [],
                "requests": [],
                "runtime_inputs": {
                    "cwd": "/workspace",
                    "home": "/home",
                    "current_date": "2027-04-05",
                    "package_dir": "/srv/pi",
                },
            },
            "scope": {},
            "artifacts": {"comparator_ref": comparator_path},
        }
    if comparator_id == "mini_swe_agent_trace_v1":
        trace_fields = {
            "requests": [],
            "history": [],
            "exit": {},
            "effects": {},
            "counters": {},
            "normalizations": [],
        }
        supplier_trace = {
            "schema_version": "bb.e4.mini-trace.v1",
            "role": "supplier",
            "case_id": "fixture-case",
            "scenario_sha256": "sha256:" + "0" * 64,
            **trace_fields,
        }
        replay_trace = {**supplier_trace, "role": "breadboard"}
        supplier_trace_path = tmp_path / "mini_supplier_trace.json"
        replay_trace_path = tmp_path / "mini_replay_trace.json"
        supplier_manifest_path = tmp_path / "mini_supplier_manifest.json"
        replay_manifest_path = tmp_path / "mini_replay_manifest.json"
        _write_json(supplier_trace_path, supplier_trace)
        _write_json(replay_trace_path, replay_trace)
        _write_json(
            supplier_manifest_path,
            {
                "schema_version": "bb.e4.mini-trace-manifest.v1",
                "role": "supplier",
                "cases": {
                    "fixture-case": {
                        "path": supplier_trace_path.name,
                        "sha256": _sha256(supplier_trace_path),
                    }
                },
            },
        )
        _write_json(
            replay_manifest_path,
            {
                "schema_version": "bb.e4.mini-trace-manifest.v1",
                "role": "breadboard",
                "cases": {
                    "fixture-case": {
                        "path": replay_trace_path.name,
                        "sha256": _sha256(replay_trace_path),
                    }
                },
            },
        )
        return {
            "capture": _load_json(supplier_manifest_path),
            "replay": _load_json(replay_manifest_path),
            "scope": {},
            "artifacts": {
                "comparator_ref": comparator_path,
                "capture_ref": supplier_manifest_path,
                "replay_ref": replay_manifest_path,
            },
        }
    if comparator_id == "openhands_sdk_trace_v1":
        return {
            "supplier_case": str(OPENHANDS_CASE),
            "bb_trace": _openhands_measured_bb_trace(),
        }
    if comparator_id == "openclaw_2026_9_4_trace_v1":
        return {
            "capture": str(OPENCLAW_CASE),
            "replay": project_openclaw_supplier_case(OPENCLAW_CASE),
            "scope": {},
        }
    if comparator_id == "semantic_replay_v1":
        return {
            "capture": {"captured_artifacts": []},
            "replay": {
                "replay_summary": {},
                "normalized_records": [],
                "input_hashes": {},
            },
            "scope": {"lane_id": "fixture_semantic_lane"},
            "artifacts": {"comparator_ref": comparator_path},
        }
    if comparator_id in {
        "oh_my_pi_stored_report_replay",
        "pi_stored_report_replay",
        "codex_stored_report_replay",
        "north_star_stored_report_replay",
    }:
        return {
            "capture": {},
            "replay": {},
            "scope": {},
            "artifacts": {"comparator_ref": comparator_path},
        }
    raise AssertionError(f"unhandled comparator ID: {comparator_id}")


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
        comparator_input = _registered_comparator_input(
            entry["comparator_id"], comparator_path, tmp_path
        )
        report = comparator(comparator_input)
        assert isinstance(report, dict)
        report_schema_version = report.get("schema_version", report.get("report_schema_version"))
        assert report_schema_version == entry["report_schema_version"]
        assert isinstance(report["assertions"], list) and report["assertions"]
        assert {"assertion_id", "status", "observed", "expected"} <= set(report["assertions"][0])
        if entry["comparator_id"] == "openclaw_2026_9_4_trace_v1":
            assert report["ok"] is True
            # Positive mutation: replay carrying envelope/classification keys compares equal with gap recorded
            positive_replay = json.loads(json.dumps(comparator_input["replay"]))
            positive_replay["classification"] = {"verdict": "success"}
            positive_replay["envelope"] = {"turn_count": 3, "status": "completed"}
            positive_replay["final_envelope"] = {"turn_count": 3, "status": "completed"}
            positive_report = comparator({**comparator_input, "replay": positive_replay})
            assert positive_report["ok"] is True
            assert any(
                assertion["assertion_id"] == "episode_equal"
                and assertion["status"] == "passed"
                for assertion in positive_report["assertions"]
            )
            assert any(
                gap.get("gap_id") == "openclaw-supplier-envelope-unrecorded"
                for gap in positive_report.get("declared_gaps", [])
            )
            # Negative mutation 1: tampered effects fail episode equality
            tampered = json.loads(json.dumps(comparator_input["replay"]))
            tampered["effects"]["marker.txt"] = None
            rejected = comparator({**comparator_input, "replay": tampered})
            assert rejected["ok"] is False
            assert any(
                assertion["assertion_id"] == "effects_equal"
                and assertion["status"] == "failed"
                for assertion in rejected["assertions"]
            )
            # Negative mutation 2: supplier receipt carrying classification fails closed
            mutated_supplier = tmp_path / "mutated_openclaw_supplier"
            shutil.copytree(comparator_input["capture"], mutated_supplier)
            receipt_path = mutated_supplier / "case-receipt.json"
            receipt_data = json.loads(receipt_path.read_text(encoding="utf-8"))
            receipt_data["classification"] = {"unexpected": True}
            receipt_path.write_text(json.dumps(receipt_data), encoding="utf-8")
            closed_report = comparator({**comparator_input, "capture": str(mutated_supplier)})
            assert closed_report["ok"] is False
            assert any("classification" in err for err in closed_report.get("errors", []))

def test_rejected_openhands_bb_trace_reports_error() -> None:
    replay = _openhands_measured_bb_trace()
    replay["file_effects"]["marker.txt"] = "sha256:" + ("a" * 64)
    report = compare_openhands(
        {
            "supplier_case": str(OPENHANDS_CASE),
            "bb_trace": replay,
        }
    )
    assert report["ok"] is False
    assert report["errors"]


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
