from __future__ import annotations

from agentic_coder_prototype.longrun.recovery import RecoveryPolicy


def test_recovery_policy_rollback_then_stop() -> None:
    policy = RecoveryPolicy(
        max_retries_per_item=1,
        rollback_enabled=True,
        checkpoint_every_episodes=1,
    )

    action = policy.on_episode_fail(
        {"retry_streak": 1, "rollback_used": False, "last_checkpoint_episode": 2},
        {"completed": False},
    )
    assert action["action"] == "rollback_and_retry"
    assert action["stop"] is False

    action = policy.on_episode_fail(
        {"retry_streak": 1, "rollback_used": True, "last_checkpoint_episode": 2},
        {"completed": False},
    )
    assert action["action"] == "stop"
    assert action["stop"] is True
    assert action["reason"] == "retry_budget_reached"


def test_recovery_policy_continue_when_retries_disabled() -> None:
    policy = RecoveryPolicy(
        max_retries_per_item=0,
        rollback_enabled=False,
        checkpoint_every_episodes=0,
    )
    action = policy.on_episode_fail({"retry_streak": 99}, {"completed": False})
    assert action["action"] == "continue"
    assert action["stop"] is False


def test_recovery_policy_provider_error_retry_with_backoff() -> None:
    policy = RecoveryPolicy(
        max_retries_per_item=2,
        rollback_enabled=False,
        checkpoint_every_episodes=0,
        backoff_base_seconds=0.25,
        backoff_max_seconds=1.0,
        backoff_disable_jitter=True,
    )
    action = policy.on_provider_error({"retry_streak": 0}, {"message": "boom"})
    assert action["action"] == "retry_with_backoff"
    assert action["stop"] is False
    assert action["backoff_seconds"] == 0.25

    action2 = policy.on_provider_error({"retry_streak": 2}, {"message": "boom"})
    assert action2["action"] == "stop"
    assert action2["stop"] is True
    assert action2["reason"] == "provider_error_retry_exhausted"
