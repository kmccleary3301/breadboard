from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from scripts.interface_inventory import canonical_bytes, inventory


SAMPLE = {
    ("kernel_event_kinds", "tool_result"),
    ("kernel_families", "capability_registry"),
    ("schema_lifecycle", "bb.context_resource_pack.v1"),
    ("config_surface_fields", "providers"),
    ("contract_tiers", "bb.checkpoint_metadata.v1"),
}


def _json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def _fixture_roots(tmp_path: Path) -> tuple[Path, Path]:
    engine = tmp_path / "engine"
    tui = tmp_path / "tui"
    registry_root = engine / "contracts" / "kernel" / "registries"
    _json(
        registry_root / "kernel_event_kinds.v1.json",
        {"registry_id": "kernel_event_kinds", "entries": [{"id": "tool_result", "status": "active"}]},
    )
    _json(
        registry_root / "kernel_families.v1.json",
        {"registry_id": "kernel_families", "entries": [{"id": "capability_registry", "status": "active"}]},
    )
    _json(
        registry_root / "schema_lifecycle.v1.json",
        {
            "registry_id": "schema_lifecycle",
            "entries": [{"schema_id": "bb.context_resource_pack.v1", "family": "context_resource_pack", "lifecycle": "active_production", "default_for_generation": True, "superseded_by": None}],
        },
    )
    _json(
        registry_root / "config_surface_fields.v1.json",
        {"registry_id": "config_surface_fields", "entries": [{"id": "providers", "status": "active"}]},
    )
    _json(
        registry_root / "contract_tiers.v1.json",
        {"registry_id": "contract_tiers", "entries": [{"schema_id": "bb.checkpoint_metadata.v1", "tier": "host_protocol", "disposition": "keep", "consumers": []}]},
    )
    schema = engine / "contracts" / "kernel" / "schemas" / "bb.context_resource_pack.v1.schema.json"
    _json(schema, {"$id": "bb.context_resource_pack.v1", "type": "object", "properties": {"items": {"type": "array"}}, "required": ["items"]})
    (engine / "breadboard_engine" / "api" / "cli_bridge").mkdir(parents=True)
    (engine / "breadboard_engine" / "api" / "cli_bridge" / "events.py").write_text("class EventType:\n    TOOL_RESULT = 'tool_result'\n", encoding="utf-8")
    (engine / "breadboard_sdk").mkdir(parents=True)
    (engine / "breadboard_sdk" / "__init__.py").write_text("__all__ = ['SessionEvent']\n", encoding="utf-8")
    (engine / "sdk" / "ts" / "src").mkdir(parents=True)
    (engine / "sdk" / "ts" / "src" / "index.ts").write_text("export type { SessionEvent } from './types.js'\n", encoding="utf-8")
    tui_source = tui / "packages" / "coding-agent" / "src" / "consumer.ts"
    tui_source.parent.mkdir(parents=True)
    tui_source.write_text("export const eventKind = 'tool_result';\n", encoding="utf-8")
    return engine, tui


def test_inventory_interface_is_deterministic_and_recalls_sample(tmp_path: Path) -> None:
    engine, tui = _fixture_roots(tmp_path)
    first = inventory(engine, tui)
    second = inventory(engine, tui)

    assert canonical_bytes(first) == canonical_bytes(second)
    assert {key for key in first} >= {
        "owners",
        "schemas",
        "event_kinds",
        "projections",
        "sdk_exports",
        "tui_consumers",
        "compatibility_surfaces",
    }
    found = {(row["registry_id"], row["entry_id"]) for row in first["authoritative_registry_entries"]}
    assert SAMPLE <= found
    assert str(engine) not in canonical_bytes(first).decode()
    assert first["tui_consumers"]["consumer_count"] == 1


def test_inventory_cli_writes_and_checks_fixed_point(tmp_path: Path) -> None:
    engine, tui = _fixture_roots(tmp_path)
    output = tmp_path / "inventory.json"
    script = Path(__file__).resolve().parents[1] / "scripts" / "interface_inventory.py"
    command = [sys.executable, str(script), "--engine-root", str(engine), "--tui-root", str(tui), "--output", str(output)]
    written = subprocess.run(command, check=False, capture_output=True, text=True)
    assert written.returncode == 0, written.stdout + written.stderr
    checked = subprocess.run([*command, "--check"], check=False, capture_output=True, text=True)
    assert checked.returncode == 0, checked.stdout + checked.stderr
