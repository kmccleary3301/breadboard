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
                    "done": {
                        "require_deliverable_refs": True,
                        "require_all_required_refs": True,
                    },
                    "review": {
                        "explicit_verdicts": True,
                        "allowed_blocked_actions": ["retry", "checkpoint", "escalate", "human_required"],
                    },
                    "merge": {
                        "reducer_result_contract": "shard_aggregate_v1",
                    },
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
    assert team.coordination.done.require_deliverable_refs is True
    assert team.coordination.done.require_all_required_refs is True
    assert team.coordination.review.explicit_verdicts is True
    assert team.coordination.review.allowed_blocked_actions == [
        "retry",
        "checkpoint",
        "escalate",
        "human_required",
    ]
    assert team.coordination.merge.reducer_result_contract == "shard_aggregate_v1"


def test_team_config_coordination_defaults_stay_narrow() -> None:
    team = TeamConfig.from_dict({"team": {"id": "coord-defaults"}})

    assert team.coordination.mission_owner_role == "supervisor"
    assert team.coordination.legacy_completion_sources == [
        "text_sentinel",
        "tool_call",
        "provider_finish",
    ]
    assert team.coordination.preserve_legacy_wake_conditions is True
    assert team.coordination.done.require_deliverable_refs is False
    assert team.coordination.done.require_all_required_refs is True
    assert team.coordination.review.explicit_verdicts is True
    assert team.coordination.review.allowed_blocked_actions == [
        "retry",
        "checkpoint",
        "escalate",
        "human_required",
    ]
    assert team.coordination.merge.reducer_result_contract is None
