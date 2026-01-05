from __future__ import annotations

from agentic_coder_prototype.optimize.gepa_guardrails import validate_prompt_mutation


BASE = """System prompt
<!-- OPT_BLOCK_START -->
Allowed to edit here.
<!-- OPT_BLOCK_END -->
Footer."""


def test_gepa_guardrails_allow_inside_edit() -> None:
    mutated = """System prompt
<!-- OPT_BLOCK_START -->
Allowed to edit here. Added line.
<!-- OPT_BLOCK_END -->
Footer."""
    result = validate_prompt_mutation(BASE, mutated)
    assert result.ok is True


def test_gepa_guardrails_reject_outside_edit() -> None:
    mutated = """System prompt (changed)
<!-- OPT_BLOCK_START -->
Allowed to edit here.
<!-- OPT_BLOCK_END -->
Footer."""
    result = validate_prompt_mutation(BASE, mutated)
    assert result.ok is False
    assert "mutated_text_changes_outside_opt_blocks" in result.issues


def test_gepa_guardrails_requires_opt_blocks_by_default() -> None:
    base = "No opt blocks here."
    mutated = "No opt blocks here.\nExtra"
    result = validate_prompt_mutation(base, mutated)
    assert result.ok is False
    assert "no_opt_blocks_in_base_prompt" in result.issues
