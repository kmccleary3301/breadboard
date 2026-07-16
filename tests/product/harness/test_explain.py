from __future__ import annotations

import pytest

from breadboard.product.harness.compile import compile_harness_definition

def test_explanation_accounts_for_winners_effects_and_blockers() -> None:
    compiled = compile_harness_definition(
        {"capabilities": {"read": True}, "limit": 1, "dossier": {"note": "hidden"}},
        source_ref="root.yaml",
        defaults={"capabilities": {"read": False}, "limit": 0},
        overlays=({"capabilities": {"write": False}, "limit": 2},),
    )
    explanation = compiled.explanation
    assert explanation["schema_version"] == "bb.config_explanation.v1"
    assert explanation["ok"] is True
    assert not any(row["severity"] == "error" for row in explanation["diagnostics"])
    messages = {row["message"] for row in explanation["diagnostics"]}
    assert {
        "effect=defaulted; source=harness-default:0000",
        "effect=overridden; source=harness-default:0000; winner=agent-config:0000:root.yaml",
        "effect=capability_enabled; source=agent-config:0000:root.yaml",
        "blocker=capability_disabled; source=harness-overlay:0000",
    } <= messages
    winners = {row["path"]: row["source_layer"] for row in explanation["fields"]}
    locked = {row["path"]: row["source_layer_id"]
              for row in compiled.lock["effective_values"]}
    assert winners == locked
    assert "dossier.note" not in winners

def test_prompt_summary_includes_only_concrete_pack_files() -> None:
    summary = compile_harness_definition({
        "prompts": {
            "environment": {"format": "xml"},
            "injection": {"system_order": ["environment", "pack"]},
            "packs": {"base": {"system": "prompts/system.md",
                               "builder": "prompts/builder.md",
                               "compact": "prompts/system.md"}},
            "tool_prompt_mode": "none",
        },
    }, source_ref="root").explanation["resolved_summary"]
    assert summary["prompt_files"] == ("prompts/builder.md", "prompts/system.md")

def test_explanation_is_frozen_and_detached() -> None:
    explanation = compile_harness_definition({"value": 1}, source_ref="root").explanation
    with pytest.raises(TypeError):
        explanation["resolved_summary"]["tool_count"] = 3  # type: ignore[index]
    detached = explanation.as_dict()
    detached["fields"].clear()
    assert explanation["fields"]
