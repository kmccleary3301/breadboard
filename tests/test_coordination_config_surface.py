from __future__ import annotations

from agentic_coder_prototype.orchestration.schema import TeamConfig


def test_team_config_parses_minimal_coordination_surface() -> None:
    team = TeamConfig.from_dict(
        {
            "team": {
                "id": "coord-config",
                "coordination": {
                    "mission_owner_role": "supervisor",
                    "legacy_completion_sources": [
                        "text_sentinel",
                        "tool_call",
                        "provider_finish",
                    ],
                    "preserve_legacy_wake_conditions": True,
                },
            }
        }
    )

    assert team.coordination.mission_owner_role == "supervisor"
    assert team.coordination.legacy_completion_sources == [
        "text_sentinel",
        "tool_call",
        "provider_finish",
    ]
    assert team.coordination.preserve_legacy_wake_conditions is True


def test_team_config_coordination_defaults_stay_narrow() -> None:
    team = TeamConfig.from_dict({"team": {"id": "coord-defaults"}})

    assert team.coordination.mission_owner_role == "supervisor"
    assert team.coordination.legacy_completion_sources == [
        "text_sentinel",
        "tool_call",
        "provider_finish",
    ]
    assert team.coordination.preserve_legacy_wake_conditions is True
