from __future__ import annotations

from agentic_coder_prototype.ctrees.live_grounding import (
    first_verify_step_from_artifacts,
    first_write_step_from_artifacts,
)


def test_live_grounding_normalizes_turn_indices_to_ordinals() -> None:
    artifacts = {
        "tool_usage": {
            "turns": {
                "1": {"tools": [{"name": "update_plan"}]},
                "7": {"tools": [{"name": "update_plan"}]},
                "25": {"tools": [{"name": "shell_command"}]},
                "39": {"tools": [{"name": "apply_patch"}]},
            }
        }
    }

    assert first_verify_step_from_artifacts(artifacts) == 3
    assert first_write_step_from_artifacts(artifacts) == 4
