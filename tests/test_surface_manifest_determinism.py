from __future__ import annotations

from agentic_coder_prototype.surface_manifest import build_surface_manifest


def _find_surface(manifest: dict, name: str) -> dict:
    for entry in manifest.get("surfaces", []):
        if entry.get("name") == name:
            return entry
    return {}


def test_surface_manifest_mcp_external_propagates() -> None:
    snapshot = {
        "tool_schema_snapshots": [{"turn": 1, "schema": {"tools": []}}],
        "tool_catalog": {"tools": []},
        "mcp_snapshot": {"servers": [{"transport": "http", "name": "ext"}]},
    }
    manifest = build_surface_manifest(surface_snapshot=snapshot)
    assert manifest is not None
    mcp_entry = _find_surface(manifest, "mcp_snapshot")
    tool_schema_entry = _find_surface(manifest, "tool_schema_latest")
    tool_catalog_entry = _find_surface(manifest, "tool_catalog")
    assert mcp_entry.get("determinism") == "external"
    assert tool_schema_entry.get("determinism") == "external"
    assert tool_catalog_entry.get("determinism") == "external"


def test_surface_manifest_mcp_replayable_propagates() -> None:
    snapshot = {
        "tool_schema_snapshots": [{"turn": 1, "schema": {"tools": []}}],
        "tool_catalog": {"tools": []},
        "mcp_snapshot": {"servers": [{"transport": "stdio", "name": "local"}]},
    }
    manifest = build_surface_manifest(surface_snapshot=snapshot)
    assert manifest is not None
    mcp_entry = _find_surface(manifest, "mcp_snapshot")
    tool_schema_entry = _find_surface(manifest, "tool_schema_latest")
    assert mcp_entry.get("determinism") == "replayable"
    assert tool_schema_entry.get("determinism") == "replayable"
