from __future__ import annotations

import copy
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

from scripts.e4_parity import generate_ct_rows as ct_generator
from scripts.e4_parity.lane_definitions import load_lane_defs


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "e4_parity" / "generate_ct_rows.py"
LIVE_INVENTORY = ROOT / "docs" / "conformance" / "e4_lane_inventory.json"
LIVE_MANIFEST = ROOT / "docs" / "conformance" / "ct_scenarios_v1.json"


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _json_out_target(command: list[str]) -> str:
    try:
        return command[command.index("--json-out") + 1]
    except (ValueError, IndexError) as exc:
        raise AssertionError(f"command is missing a --json-out target: {command!r}") from exc


def _accepted_ct_lanes(inventory: dict[str, Any]) -> list[dict[str, Any]]:
    return [lane for lane in inventory["lanes"] if lane.get("ct") is not None and lane.get("status") == "accepted"]


def _check_cli(manifest: Path, out: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--inventory",
            str(LIVE_INVENTORY),
            "--manifest",
            str(manifest),
            "--out",
            str(out),
            "--check",
        ],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )


def test_generate_inventory_scenarios_normalizes_inventory_ct_rows() -> None:
    """Accepted inventory CT rows become sorted manifest rows with phase support gates and JSON assertion targets."""
    inventory = {
        "schema_version": "bb.e4.lane_inventory.v1",
        "inventory_id": "unit-fixture",
        "generated_at_utc": "2026-07-03T00:00:00Z",
        "revision": 1,
        "lanes": [
            {
                "lane_id": "z_lane",
                "config_id": "z_lane_v1",
                "phase": "P5",
                "status": "accepted",
                "provider_model": "no-provider",
                "sandbox_mode": "read-only-no-secret",
                "ct": {
                    "test_id": "CT-ZZZ-C4",
                    "gate_level": "C4",
                    "description": "Z lane support chain validation",
                    "timeout_seconds": 240,
                    "command": {
                        "argv": [
                            ".venv/bin/python",
                            "scripts/validate_e4_c4_chain.py",
                            "--config-id",
                            "z_lane_v1",
                            "--json-out",
                            "artifacts/conformance/node_gate/ct_zzz_c4.json",
                        ],
                        "cwd": ".",
                    },
                },
            },
            {
                "lane_id": "uncovered_lane",
                "config_id": "uncovered_lane_v1",
                "phase": "P4",
                "status": "accepted",
                "provider_model": "no-provider",
                "sandbox_mode": "read-only",
                "ct": None,
            },
            {
                "lane_id": "scaffolded_lane",
                "config_id": "scaffolded_lane_v1",
                "phase": "P4",
                "status": "scaffolded",
                "provider_model": "no-provider",
                "sandbox_mode": "read-only",
                "ct": {
                    "test_id": "CT-SCAFFOLD-C4",
                    "gate_level": "C4",
                    "description": "Scaffolded lane must not render",
                    "timeout_seconds": 120,
                    "command": {
                        "argv": [
                            ".venv/bin/python",
                            "scripts/validate_e4_c4_chain.py",
                            "--config-id",
                            "scaffolded_lane_v1",
                            "--json-out",
                            "artifacts/conformance/node_gate/ct_scaffold_c4.json",
                        ],
                        "cwd": ".",
                    },
                },
            },
            {
                "lane_id": "a_lane",
                "config_id": "a_lane_v1",
                "phase": "P3",
                "status": "accepted",
                "provider_model": "no-provider",
                "sandbox_mode": "read-only",
                "ct": {
                    "test_id": "CT-AAA-C4",
                    "gate_level": "C4",
                    "description": "A lane support chain validation",
                    "timeout_seconds": 180,
                    "command": {
                        "argv": [
                            ".venv/bin/python",
                            "scripts/validate_e4_c4_chain.py",
                            "--config-id",
                            "a_lane_v1",
                            "--support-claim",
                            "docs/conformance/support_claims/a_lane_v1_c4_support_claim.json",
                            "--json-out",
                            "artifacts/conformance/node_gate/ct_aaa_c4.json",
                        ],
                        "cwd": ".",
                    },
                },
            },
        ],
    }

    rows = ct_generator.generate_inventory_scenarios(inventory)

    assert [row["test_id"] for row in rows] == ["CT-AAA-C4", "CT-ZZZ-C4"]
    assert "CT-SCAFFOLD-C4" not in {row["test_id"] for row in rows}
    assert all("status" not in row for row in rows)

    by_id = {row["test_id"]: row for row in rows}
    assert by_id["CT-AAA-C4"]["gate_level"] == "P3-support"
    assert by_id["CT-ZZZ-C4"]["gate_level"] == "P5-support"
    assert all(row["gate_level"] != "C4" for row in rows)

    for row in rows:
        assert row["command"][0] == "python"
        assert row["assertions"]["json_files"][0]["path"] == _json_out_target(row["command"])


def test_lane_def_data_projects_new_ct_row_without_python_lane_branch(tmp_path: Path) -> None:
    """A new accepted CT lane can source row text, timeout, and result checks from lane_def data."""
    lane_id = "synthetic_data_owned_lane"
    config_id = "synthetic_data_owned_lane_v1"
    output_path = "artifacts/conformance/node_gate/ct_synthetic_data_owned_lane.json"
    expected_checks = [
        {"equals": "lane-def-owned", "path": "source"},
        {"length_equals": 0, "path": "violations"},
    ]
    lane_defs_path = tmp_path / "e4_lanes"
    _write_json(
        lane_defs_path / f"{lane_id}.yaml",
        {
            "schema_version": "bb.e4.lane_def.v1",
            "lane_id": lane_id,
            "config_id": config_id,
            "target_family": "synthetic",
            "target_version": "synthetic-target@1.0.0",
            "package_ref": None,
            "kind": "target_support",
            "status": "accepted",
            "points": 1,
            "capture": {
                "strategy": "probe_argv",
                "argv": None,
                "inputs": ["synthetic/input.json"],
                "workspace_template": None,
            },
            "normalize": {"mode": "identity", "translator": "identity", "config": {}},
            "replay": {
                "mode": "stored",
                "artifacts": [
                    "docs/conformance/e4_target_support/synthetic_data_owned_lane/bb_replay_result.json"
                ],
                "session": None,
                "comparator_class": "semantic",
            },
            "compare": {"comparator": "synthetic_comparator", "config": {"assertions": expected_checks}},
            "claim": {"scope": {"behaviors": ["bb.synthetic.v1"], "surfaces": ["synthetic C4"]}, "exclusions": []},
            "ct": {"description": "Lane-def-owned synthetic CT row", "timeout_seconds": 77},
            "artifacts_root": "docs/conformance/e4_target_support/synthetic_data_owned_lane",
            "reverify_command": None,
            "metadata": {"legacy_inventory_ct_test_id": "CT-SYNTHETIC-DATA-LANE"},
        },
    )
    inventory = {
        "schema_version": "bb.e4.lane_inventory.v1",
        "inventory_id": "synthetic-fixture",
        "generated_at_utc": "2026-07-06T00:00:00Z",
        "revision": 1,
        "lanes": [
            {
                "lane_id": lane_id,
                "config_id": config_id,
                "phase": "P5",
                "status": "accepted",
                "provider_model": "inventory-provider-would-be-wrong",
                "sandbox_mode": "inventory-sandbox-would-be-wrong",
                "ct": {
                    "test_id": "CT-SYNTHETIC-DATA-LANE",
                    "gate_level": "C4",
                    "description": "Inventory-owned description must not win",
                    "timeout_seconds": 240,
                    "command": {
                        "argv": [
                            ".venv/bin/python",
                            "scripts/validate_e4_c4_chain.py",
                            "--config-id",
                            config_id,
                            "--json-out",
                            output_path,
                        ],
                        "cwd": ".",
                    },
                },
            }
        ],
    }

    rows = ct_generator.generate_inventory_scenarios(inventory, lane_defs=load_lane_defs(lane_defs_path))

    assert rows == [
        {
            "assertions": {"json_files": [{"checks": expected_checks, "path": output_path}]},
            "command": [
                "python",
                "scripts/validate_e4_c4_chain.py",
                "--config-id",
                config_id,
                "--json-out",
                output_path,
            ],
            "description": "Lane-def-owned synthetic CT row",
            "gate_level": "P5-support",
            "test_id": "CT-SYNTHETIC-DATA-LANE",
            "timeout_seconds": 77,
        }
    ]

def test_live_inventory_generated_rows_match_manifest_rows(tmp_path: Path) -> None:
    """Live accepted CT lanes must regenerate manifest rows exactly through checked-in lane_defs."""
    inventory = _read_json(LIVE_INVENTORY)
    manifest = _read_json(LIVE_MANIFEST)
    lane_defs = load_lane_defs()

    generated_rows = ct_generator.generate_inventory_scenarios(inventory, lane_defs=lane_defs)
    accepted_ct_lanes = _accepted_ct_lanes(inventory)
    manifest_by_id = {row["test_id"]: row for row in manifest["scenarios"]}

    assert {row["test_id"] for row in generated_rows} == {lane["ct"]["test_id"] for lane in accepted_ct_lanes}
    assert generated_rows
    assert all(row["test_id"] in manifest_by_id for row in generated_rows)
    assert generated_rows == [manifest_by_id[row["test_id"]] for row in generated_rows]

    manifest_path = tmp_path / "ct_scenarios_v1.json"
    inventory_path = tmp_path / "e4_lane_inventory.json"
    manifest_path.write_bytes(LIVE_MANIFEST.read_bytes())
    _write_json(inventory_path, inventory)

    rows = ct_generator.upsert_inventory_scenarios(manifest_path, inventory_path, ct_generator.DEFAULT_LANE_DEFS)
    rewritten = _read_json(manifest_path)

    assert rows == generated_rows
    assert rewritten == _read_json(LIVE_MANIFEST)





def test_upsert_inventory_scenarios_rewrites_only_inventory_rows(tmp_path: Path) -> None:
    """Builder hooks can replace all inventory-owned CT rows without disturbing unrelated scenarios."""
    inventory = _read_json(LIVE_INVENTORY)
    generated_rows = ct_generator.generate_inventory_scenarios(inventory)
    stale_row = copy.deepcopy(generated_rows[0])
    stale_row["gate_level"] = "stale"
    unrelated_row = {
        "test_id": "CT-UNRELATED-LEGACY",
        "gate_level": "legacy",
        "description": "Unrelated legacy row",
        "timeout_seconds": 1,
        "command": ["python", "legacy.py", "--json-out", "artifacts/legacy.json"],
        "assertions": {"json_files": [{"path": "artifacts/legacy.json", "checks": []}]},
    }
    manifest_path = tmp_path / "ct_scenarios_v1.json"
    inventory_path = tmp_path / "e4_lane_inventory.json"
    _write_json(inventory_path, inventory)
    _write_json(manifest_path, {"schema_version": "fixture", "scenarios": [unrelated_row, stale_row]})

    rows = ct_generator.upsert_inventory_scenarios(manifest_path, inventory_path)
    manifest = _read_json(manifest_path)

    assert rows == generated_rows
    assert manifest["scenarios"][0] == unrelated_row
    assert manifest["scenarios"][1:] == generated_rows

def test_check_cli_accepts_current_inventory_and_manifest(tmp_path: Path) -> None:
    """The check mode exits cleanly when the checked-in manifest matches generated inventory rows."""
    completed = _check_cli(LIVE_MANIFEST, tmp_path / "ct_scenarios_v1.json")

    assert completed.returncode == 0, completed.stdout + completed.stderr


def test_check_cli_rejects_tampered_corresponding_manifest_row(tmp_path: Path) -> None:
    """The check mode fails when an existing manifest row diverges from its inventory-generated row."""
    inventory = _read_json(LIVE_INVENTORY)
    generated_ids = {lane["ct"]["test_id"] for lane in _accepted_ct_lanes(inventory)}
    manifest = copy.deepcopy(_read_json(LIVE_MANIFEST))

    for row in manifest["scenarios"]:
        if row["test_id"] in generated_ids:
            row["gate_level"] = "C4"
            break
    else:
        raise AssertionError("live manifest has no row corresponding to an inventory CT block")

    tampered_manifest = tmp_path / "ct_scenarios_v1_tampered.json"
    _write_json(tampered_manifest, manifest)

    completed = _check_cli(tampered_manifest, tmp_path / "ct_scenarios_v1.json")

    assert completed.returncode != 0
