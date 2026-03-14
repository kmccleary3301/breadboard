from __future__ import annotations

import pytest

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
                        "allowed_reviewer_roles": ["supervisor", "system"],
                        "allowed_blocked_actions": ["retry", "checkpoint", "escalate", "human_required"],
                        "no_progress_action": "checkpoint",
                        "retryable_failure_action": "retry",
                        "verification_result_contract": "bb.coordination_verification_result.v1",
                    },
                    "merge": {
                        "reducer_result_contract": "shard_aggregate_v1",
                    },
                    "intervention": {
                        "host_allowed_actions": ["continue", "checkpoint", "terminate"],
                        "require_evidence_refs": True,
                        "require_supervisor_escalate": True,
                        "support_claim_limited_actions": ["checkpoint", "terminate"],
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
    assert team.coordination.review.allowed_reviewer_roles == ["supervisor", "system"]
    assert team.coordination.review.allowed_blocked_actions == [
        "retry",
        "checkpoint",
        "escalate",
        "human_required",
    ]
    assert team.coordination.review.no_progress_action == "checkpoint"
    assert team.coordination.review.retryable_failure_action == "retry"
    assert team.coordination.review.verification_result_contract == "bb.coordination_verification_result.v1"
    assert team.coordination.merge.reducer_result_contract == "shard_aggregate_v1"
    assert team.coordination.intervention.host_allowed_actions == [
        "continue",
        "checkpoint",
        "terminate",
    ]
    assert team.coordination.intervention.require_evidence_refs is True
    assert team.coordination.intervention.require_supervisor_escalate is True
    assert team.coordination.intervention.support_claim_limited_actions == ["checkpoint", "terminate"]


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
    assert team.coordination.review.allowed_reviewer_roles == ["supervisor", "system"]
    assert team.coordination.review.allowed_blocked_actions == [
        "retry",
        "checkpoint",
        "escalate",
        "human_required",
    ]
    assert team.coordination.review.no_progress_action == "checkpoint"
    assert team.coordination.review.retryable_failure_action == "retry"
    assert team.coordination.review.verification_result_contract is None
    assert team.coordination.merge.reducer_result_contract is None
    assert team.coordination.intervention.host_allowed_actions == [
        "continue",
        "checkpoint",
        "terminate",
    ]
    assert team.coordination.intervention.require_evidence_refs is False
    assert team.coordination.intervention.require_supervisor_escalate is True
    assert team.coordination.intervention.support_claim_limited_actions == ["checkpoint", "terminate"]


def test_team_config_rejects_mission_owner_role_outside_allowed_reviewer_roles() -> None:
    with pytest.raises(ValueError, match="mission_owner_role must be included"):
        TeamConfig.from_dict(
            {
                "team": {
                    "id": "coord-invalid-reviewer-role",
                    "coordination": {
                        "mission_owner_role": "host",
                        "review": {
                            "allowed_reviewer_roles": ["supervisor", "system"],
                        },
                    },
                }
            }
        )


def test_team_config_rejects_unknown_coordination_keys() -> None:
    with pytest.raises(ValueError, match="coordination contains unsupported keys"):
        TeamConfig.from_dict(
            {
                "team": {
                    "id": "coord-invalid-extra-key",
                    "coordination": {
                        "mission_owner_role": "supervisor",
                        "routing_tree": {"kind": "do-not-add"},
                    },
                }
            }
        )


def test_team_config_rejects_unknown_coordination_section_keys() -> None:
    with pytest.raises(ValueError, match="coordination.review contains unsupported keys"):
        TeamConfig.from_dict(
            {
                "team": {
                    "id": "coord-invalid-review-key",
                    "coordination": {
                        "review": {
                            "explicit_verdicts": True,
                            "approval_chain": ["host"],
                        }
                    },
                }
            }
        )


def test_team_config_rejects_unsupported_directive_and_reviewer_vocab() -> None:
    with pytest.raises(ValueError, match="allowed_reviewer_roles contains unsupported roles"):
        TeamConfig.from_dict(
            {
                "team": {
                    "id": "coord-invalid-reviewer-vocab",
                    "coordination": {
                        "mission_owner_role": "supervisor",
                        "review": {
                            "allowed_reviewer_roles": ["supervisor", "approver"],
                        },
                    },
                }
            }
        )

    with pytest.raises(ValueError, match="host_allowed_actions contains unsupported directive codes"):
        TeamConfig.from_dict(
            {
                "team": {
                    "id": "coord-invalid-host-action",
                    "coordination": {
                        "intervention": {
                            "host_allowed_actions": ["continue", "approve"],
                        }
                    },
                }
            }
        )
