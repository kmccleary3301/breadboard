from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

from scripts.interface_inventory import (
    EXCLUDED_PARTS,
    _iter_files,
    _sdk_imported_exports,
    canonical_bytes,
    inventory,
)


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
        {
            "registry_id": "kernel_event_kinds",
            "entries": [
                {"id": "error", "status": "active"},
                {"id": "completion", "status": "active"},
                {"id": "tool_result", "status": "active"},
                {"id": "tool_call", "status": "active"},
                {"id": "warning", "status": "active"},
            ],
        },
    )
    _json(
        registry_root / "kernel_families.v1.json",
        {"registry_id": "kernel_families", "entries": [{"id": "capability_registry", "status": "active"}]},
    )
    _json(
        registry_root / "schema_lifecycle.v1.json",
        {
            "registry_id": "schema_lifecycle",
            "entries": [
                {
                    "schema_id": "bb.context_resource_pack.v1",
                    "family": "context_resource_pack",
                    "lifecycle": "active_production",
                    "default_for_generation": True,
                    "superseded_by": None,
                },
                {
                    "schema_id": "bb.keep.v1",
                    "family": "keep",
                    "lifecycle": "validate_only",
                    "default_for_generation": False,
                    "superseded_by": None,
                },
                {
                    "schema_id": "bb.remove.v1",
                    "family": "remove",
                    "lifecycle": "validate_only",
                    "default_for_generation": False,
                    "superseded_by": None,
                },
            ],
        },
    )
    _json(
        registry_root / "config_surface_fields.v1.json",
        {"registry_id": "config_surface_fields", "entries": [{"id": "providers", "status": "active"}]},
    )
    _json(
        registry_root / "contract_tiers.v1.json",
        {
            "registry_id": "contract_tiers",
            "entries": [
                {
                    "schema_id": "bb.checkpoint_metadata.v1",
                    "tier": "host_protocol",
                    "disposition": "keep",
                    "consumers": [],
                },
                {
                    "schema_id": "bb.keep.v1",
                    "tier": "host_protocol",
                    "disposition": "keep",
                    "consumers": [],
                },
                {
                    "schema_id": "bb.remove.v1",
                    "tier": "host_protocol",
                    "disposition": "freeze",
                    "consumers": [{"kind": "loader", "path": "breadboard_engine/loader.py"}],
                },
            ],
        },
    )
    schema = engine / "contracts" / "kernel" / "schemas" / "bb.context_resource_pack.v1.schema.json"
    _json(schema, {"$id": "bb.context_resource_pack.v1", "type": "object", "properties": {"items": {"type": "array"}}, "required": ["items"]})
    keep_schema = engine / "contracts" / "kernel" / "schemas" / "bb.keep.v1.schema.json"
    _json(keep_schema, {"$id": "bb.keep.v1", "type": "object", "properties": {"value": {"type": "string"}}})
    remove_schema = engine / "contracts" / "kernel" / "schemas" / "bb.remove.v1.schema.json"
    _json(remove_schema, {"$id": "bb.remove.v1", "type": "object", "properties": {"value": {"type": "string"}}})
    manifest_schema = (
        engine
        / "contracts"
        / "kernel"
        / "manifests"
        / "bb.engine_manifest.v1.schema.json"
    )
    _json(
        manifest_schema,
        {
            "$id": "bb.engine_manifest.v1",
            "type": "object",
            "properties": {"engine": {"type": "string"}},
            "required": ["engine"],
        },
    )
    _json(
        engine / "contracts" / "public" / "frozen_public_surface.v1.json",
        {
            "operation_count": 2,
            "canonical_operations": {
                "session": ["session.start"],
                "artifact": ["artifact.list"],
            },
        },
    )
    (engine / "breadboard_engine" / "api" / "cli_bridge").mkdir(parents=True)
    (engine / "breadboard_engine" / "api" / "cli_bridge" / "events.py").write_text("class EventType:\n    TOOL_RESULT = 'tool_result'\n", encoding="utf-8")
    (engine / "breadboard_sdk").mkdir(parents=True)
    _json(
        engine
        / "breadboard_sdk"
        / "generated"
        / "public_surface_manifest.v1.json",
        {"operations": []},
    )
    (engine / "breadboard_sdk" / "__init__.py").write_text("__all__ = ['SessionEvent']\n", encoding="utf-8")
    (engine / "sdk" / "ts" / "src").mkdir(parents=True)
    (engine / "sdk" / "ts" / "src" / "index.ts").write_text(
        "export { ApiError } from './public-client.js'\n"
        "export type { SessionEvent } from './types.js'\n",
        encoding="utf-8",
    )
    tui_source = tui / "packages" / "coding-agent" / "src" / "consumer.ts"
    tui_source.parent.mkdir(parents=True)
    tui_source.write_text(
        "import { ApiError, SessionSummary } from '@breadboard/sdk'\n"
        "export const eventKind = 'tool_result';\n"
        "function render(event: { type: string }) {\n"
        "  switch (event.type) {\n"
        + ("    // ignored braces '{}' and case 'error'\n" * 40)
        + "    case 'error': return event.type;\n"
        + "    case 'warning': return event.type;\n"
        + "    default: return 'ok';\n"
        + "  }\n"
        + "}\n"
        + "void ApiError;\nvoid SessionSummary;\n",
        encoding="utf-8",
    )
    unrelated_switch = tui_source.parent / "unrelated-switch.ts"
    unrelated_switch.write_text(
        "export const eventKind = 'tool_result';\n"
        "function render(other: { type: string }) {\n"
        "  switch (other.type) {\n"
        + ("    // ignored braces '{}' and case 'error'\n" * 40)
        + "    case 'error': return other.type;\n"
        + "    default: return 'ok';\n"
        + "  }\n"
        + "}\n",
        encoding="utf-8",
    )
    (tui_source.parent / "not-a-consumer.ts").write_text(
        "const child = { once: (_event: string, _callback: () => void) => undefined };\n"
        "child.once('error', () => {});\n",
        encoding="utf-8",
    )
    (tui_source.parent / "sdk-tools.ts").write_text(
        "import { ApiError } from '@breadboard/sdk-tools'\nvoid ApiError;\n",
        encoding="utf-8",
    )
    alias_switch = tui_source.parent / "alias-switch.ts"
    alias_switch.write_text(
        "function render(event: { type: string }) {\n"
        "  const eventType = String(event.type);\n"
        "  switch (eventType) {\n"
        "    case 'error': return event.type;\n"
        "    case 'warning': return event.type;\n"
        "    default: return 'ok';\n"
        "  }\n"
        "}\n",
        encoding="utf-8",
    )
    (tui_source.parent / "unrelated-kind.ts").write_text(
        "const result = { kind: 'error' };\n"
        "if (result.kind === 'error') throw new Error('failed');\n",
        encoding="utf-8",
    )
    (tui_source.parent / "unrelated-collisions.ts").write_text(
        "const wait = { for: 'completion' };\n"
        "function render(activityState: string) {\n"
        "  switch (activityState) {\n"
        "    case 'tool_call': return wait.for;\n"
        "    default: return 'idle';\n"
        "  }\n"
        "}\n",
        encoding="utf-8",
    )
    (tui_source.parent / "commented-comparison.ts").write_text(
        "// if (event.type === 'error') return true;\n"
        "// const schema = 'bb.context_resource_pack.v1';\n"
        "const schemaExample = `bb.context_resource_pack.v1`;\n"
        "const example = \"if (event.type === 'warning') return true;\";\n",
        encoding="utf-8",
    )
    (tui_source.parent / "reassigned-alias.ts").write_text(
        "function render(event: { type: string }, mode: string) {\n"
        "  let eventType = String(event.type);\n"
        "  eventType = mode;\n"
        "  switch (eventType) {\n"
        "    case 'error': return false;\n"
        "    default: return true;\n"
        "  }\n"
        "}\n",
        encoding="utf-8",
    )
    (tui_source.parent / "applied-event-literal.ts").write_text(
        "controller.applyEvent({ type: 'completion', data: {} });\n",
        encoding="utf-8",
    )
    (tui_source.parent / "unapplied-event-literal.ts").write_text(
        "const example = { type: 'completion', data: {} };\n",
        encoding="utf-8",
    )
    (tui_source.parent / "typed-event-literal.ts").write_text(
        "const events: NormalizedEvent[] = [{ type: 'warning', data: {} }];\n"
        "buildTranscriptFromEvents(events);\n",
        encoding="utf-8",
    )
    (tui_source.parent / "called-event-literal.ts").write_text(
        "reduceWorkGraphEvent(state, { eventType: 'tool_call', data: {} });\n",
        encoding="utf-8",
    )
    (tui_source.parent / "called-event-fallback.ts").write_text(
        "reduceWorkGraphEvent(state, { eventType: options?.eventType ?? 'tool_call', data: {} });\n",
        encoding="utf-8",
    )
    (tui_source.parent / "typed-event-fallback.ts").write_text(
        "const value: NormalizedEvent = { eventType: options?.eventType ?? 'tool_call', data: {} };\n",
        encoding="utf-8",
    )
    (tui_source.parent / "optional-called-event-fallback.ts").write_text(
        "this.enqueueWorkGraphEvent?.(payload, { eventType: options?.eventType ?? 'tool_call' });\n",
        encoding="utf-8",
    )
    (tui_source.parent / "method-event-fallback.ts").write_text(
        "class Controller { enqueueWorkGraphEvent() { const value = { eventType: options?.eventType ?? 'tool_call' }; consume(value); } }\n",
        encoding="utf-8",
    )
    (tui_source.parent / "unrelated-event-variable.ts").write_text(
        "const unrelatedEvent: Result = { kind: options?.kind ?? 'error' };\n",
        encoding="utf-8",
    )
    (tui_source.parent / "callback-unrelated-event-literal.ts").write_text(
        "it('keeps local results', () => {\n"
        "  const result = { kind: 'error' };\n"
        "  consume(result);\n"
        "});\n",
        encoding="utf-8",
    )
    (tui_source.parent / "called-unrelated-event-literal.ts").write_text(
        "renderResult({ kind: 'error' });\n",
        encoding="utf-8",
    )
    docs_tmp_source = engine / "docs_tmp" / "legacy-example.py"
    docs_tmp_source.parent.mkdir(parents=True)
    docs_tmp_source.write_text("legacy compatibility example\n", encoding="utf-8")
    generated_source = tui_source.parent / "generated" / "generated.ts"
    generated_source.parent.mkdir(parents=True)
    generated_source.write_text(
        "export const generatedEvent = 'tool_result';\n",
        encoding="utf-8",
    )
    virtual_source = (
        engine
        / ".venv"
        / "lib"
        / "python3.13"
        / "site-packages"
        / "legacy_dependency.py"
    )
    virtual_source.parent.mkdir(parents=True)
    virtual_source.write_text("legacy compatibility shim\n", encoding="utf-8")
    test_source = engine / "tests" / "test_reconfigure.py"
    test_source.parent.mkdir(parents=True)
    test_source.write_text("durable_reconfigure = None\n", encoding="utf-8")
    return engine, tui
def test_sdk_import_extraction_ignores_comments_and_string_bodies() -> None:
    text = """
// import { Commented } from '@breadboard/sdk'
const example = `export { Templated } from "@breadboard/sdk"`;
import type { LiveType as Alias } from '@breadboard/sdk';
import * as SDK from '@breadboard/sdk';
void SDK.LiveMember;
"""
    assert _sdk_imported_exports(text) == {"LiveMember", "LiveType"}




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
    assert first["tui_consumers"]["consumer_count"] == 10
    assert set(first["inputs"]) == {"engine_root", "tui_root"}
    for root_identity in first["inputs"].values():
        assert root_identity["content_digest"].startswith("sha256:")
        assert len(root_identity["content_digest"]) == len("sha256:") + 64
        assert root_identity["file_count"] > 0
    consumer_rows = first["tui_consumers"]["files"]
    consumer = next(row for row in consumer_rows if row["path"].endswith("consumer.ts"))
    assert {"ApiError", "SessionSummary", "tool_result", "error", "warning"} <= set(
        consumer["matched_tokens"]
    )
    consumer_text = (tui / "packages" / "coding-agent" / "src" / "consumer.ts").read_text()
    assert len(consumer_text.split("case 'error':", 1)[0]) > 320
    unrelated = next(row for row in consumer_rows if row["path"].endswith("unrelated-switch.ts"))
    assert unrelated["matched_tokens"] == ["tool_result"]
    alias = next(row for row in consumer_rows if row["path"].endswith("alias-switch.ts"))
    assert alias["matched_tokens"] == ["error", "warning"]
    applied = next(
        row
        for row in consumer_rows
        if row["path"].endswith("applied-event-literal.ts")
    )
    assert applied["matched_tokens"] == ["completion"]
    typed = next(
        row
        for row in consumer_rows
        if row["path"].endswith("typed-event-literal.ts")
    )
    assert typed["matched_tokens"] == ["warning"]
    called = next(
        row
        for row in consumer_rows
        if row["path"].endswith("called-event-literal.ts")
    )
    assert called["matched_tokens"] == ["tool_call"]
    fallback = next(
        row
        for row in consumer_rows
        if row["path"].endswith("called-event-fallback.ts")
    )
    assert fallback["matched_tokens"] == ["tool_call"]
    typed_fallback = next(
        row
        for row in consumer_rows
        if row["path"].endswith("typed-event-fallback.ts")
    )
    assert typed_fallback["matched_tokens"] == ["tool_call"]
    optional_fallback = next(
        row
        for row in consumer_rows
        if row["path"].endswith("optional-called-event-fallback.ts")
    )
    assert optional_fallback["matched_tokens"] == ["tool_call"]
    method_fallback = next(
        row
        for row in consumer_rows
        if row["path"].endswith("method-event-fallback.ts")
    )
    assert method_fallback["matched_tokens"] == ["tool_call"]
    assert not any(
        row["path"].endswith("unrelated-event-variable.ts")
        for row in consumer_rows
    )
    assert not any(
        row["path"].endswith("callback-unrelated-event-literal.ts")
        for row in consumer_rows
    )
    assert not any(
        row["path"].endswith("called-unrelated-event-literal.ts")
        for row in consumer_rows
    )
    assert not any(row["path"].endswith("sdk-tools.ts") for row in consumer_rows)
    assert not any(row["path"].endswith("unrelated-kind.ts") for row in consumer_rows)
    assert not any(
        row["path"].endswith("unrelated-collisions.ts") for row in consumer_rows
    )
    assert not any(
        row["path"].endswith("commented-comparison.ts") for row in consumer_rows
    )
    assert not any(
        row["path"].startswith("engine_root/docs_tmp/")
        for row in first["compatibility_surfaces"]["source_signals"]
    )
    assert not any(
        row["path"].endswith("reassigned-alias.ts") for row in consumer_rows
    )
    assert not any(
        row["path"].endswith("unapplied-event-literal.ts")
        for row in consumer_rows
    )
    assert not any("generated/" in row["path"] for row in consumer_rows)
    assert not any(row["path"].startswith("engine_root/tests/") for row in first["compatibility_surfaces"]["reconfiguration"])
    assert not any(
        ".venv/" in row["path"]
        for row in first["compatibility_surfaces"]["source_signals"]
    )
    frozen = next(
        row
        for row in first["compatibility_surfaces"]["public_catalogs"]
        if row["path"].endswith("frozen_public_surface.v1.json")
    )
    manifest = next(
        row for row in first["schemas"] if row["id"] == "bb.engine_manifest.v1"
    )
    assert manifest["domain"] == "kernel_manifest"
    assert manifest["fields"] == ["engine"]
    assert frozen["operation_ids"] == ["artifact.list", "session.start"]
    assert first["method"]["excluded_path_parts"] == sorted(EXCLUDED_PARTS)
    deletion_ids = {
        row.get("schema_id") or row.get("entry_id")
        for row in first["compatibility_surfaces"]["deletion_candidates"]
    }
    assert "bb.keep.v1" not in deletion_ids
    remove_rows = [
        row
        for row in first["compatibility_surfaces"]["deletion_candidates"]
        if row.get("schema_id") == "bb.remove.v1"
    ]
    assert len(remove_rows) == 1
    assert remove_rows[0]["authorities"] == ["contract_tiers", "schema_lifecycle"]
    assert remove_rows[0]["contract_tier"]["disposition"] == "freeze"
    assert remove_rows[0]["schema_lifecycle"]["lifecycle"] == "validate_only"
    lifecycle_keep = next(
        row
        for row in first["compatibility_surfaces"]["schema_lifecycle"]["entries"]
        if row.get("schema_id") == "bb.keep.v1"
    )
    assert lifecycle_keep["tier_disposition"] == "keep"


def test_engine_identity_tracks_scanned_generated_manifests(tmp_path: Path) -> None:
    engine, tui = _fixture_roots(tmp_path)
    before = inventory(engine, tui)
    manifest = (
        engine
        / "breadboard_sdk"
        / "generated"
        / "public_surface_manifest.v1.json"
    )
    _json(manifest, {"operations": [{"operation_id": "session.start"}]})

    after = inventory(engine, tui)

    assert (
        before["inputs"]["engine_root"]["content_digest"]
        != after["inputs"]["engine_root"]["content_digest"]
    )
    assert before["sdk_exports"]["generated_manifests"] != after["sdk_exports"][
        "generated_manifests"
    ]


def test_inventory_cli_writes_and_checks_fixed_point(tmp_path: Path) -> None:
    engine, tui = _fixture_roots(tmp_path)
    output = (
        engine
        / "breadboard_sdk"
        / "generated"
        / "interface_inventory_manifest.json"
    )
    script = Path(__file__).resolve().parents[1] / "scripts" / "interface_inventory.py"
    command = [sys.executable, str(script), "--engine-root", str(engine), "--tui-root", str(tui), "--output", str(output)]
    written = subprocess.run(command, check=False, capture_output=True, text=True)
    assert written.returncode == 0, written.stdout + written.stderr
    checked = subprocess.run([*command, "--check"], check=False, capture_output=True, text=True)
    assert checked.returncode == 0, checked.stdout + checked.stderr


def test_iter_files_prunes_excluded_directories(monkeypatch, tmp_path) -> None:
    root = tmp_path / "root"
    included = root / "src" / "kept.py"
    excluded = root / ".git"
    included.parent.mkdir(parents=True)
    excluded.mkdir(parents=True)
    included.write_text("kept = True\n", encoding="utf-8")
    (excluded / "ignored.py").write_text("ignored = True\n", encoding="utf-8")
    real_scandir = os.scandir

    def tracked_scandir(path):  # type: ignore[no-untyped-def]
        assert Path(path) != excluded
        return real_scandir(path)

    monkeypatch.setattr(os, "scandir", tracked_scandir)

    assert _iter_files(root, {".py"}) == [included]
