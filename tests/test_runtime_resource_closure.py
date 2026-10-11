"""Consumer-visible compaction advertisement agrees with runtime configuration."""
from __future__ import annotations

import json

from breadboard_engine.e4_targets import load_e4_target

def test_openclaw_new_revision_advertises_actual_compaction() -> None:
    package = load_e4_target("openclaw-r2@2026.9.4")
    settings = {item["name"]: item["value"] for item in package.descriptor["overlay"]["settings"]}
    assert settings["model.compaction"] is True
    native = json.loads(package.read_asset_text("native-config.json"))
    assert native["agent_exec"]["compaction"] is True
    baseline = load_e4_target("openclaw@2026.9.4")
    baseline_settings = {item["name"]: item["value"] for item in baseline.descriptor["overlay"]["settings"]}
    assert baseline_settings["model.compaction"] is False
