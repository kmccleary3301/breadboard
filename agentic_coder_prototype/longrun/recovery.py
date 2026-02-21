from __future__ import annotations

import random
from typing import Any, Dict, Mapping


def _as_non_negative_int(value: Any, default: int) -> int:
    try:
        parsed = int(value)
    except Exception:
        return default
    return parsed if parsed >= 0 else default


class RecoveryPolicy:
    """
    Phase 1 recovery policy:
    - bounded retry decisions
    - optional single rollback-and-retry action
    - explicit stop reasons
    """

    def __init__(
        self,
        *,
        max_retries_per_item: int,
        rollback_enabled: bool,
        checkpoint_every_episodes: int,
        backoff_base_seconds: float = 0.25,
        backoff_max_seconds: float = 2.0,
        backoff_disable_jitter: bool = True,
    ) -> None:
        self.max_retries_per_item = max(0, int(max_retries_per_item))
        self.rollback_enabled = bool(rollback_enabled)
        self.checkpoint_every_episodes = max(0, int(checkpoint_every_episodes))
        self.backoff_base_seconds = max(0.0, float(backoff_base_seconds))
        self.backoff_max_seconds = max(0.0, float(backoff_max_seconds))
        self.backoff_disable_jitter = bool(backoff_disable_jitter)

    @classmethod
    def from_config(cls, config: Mapping[str, Any] | None) -> "RecoveryPolicy":
        cfg = config if isinstance(config, Mapping) else {}
        long_running = cfg.get("long_running")
        long_running = long_running if isinstance(long_running, Mapping) else {}
        budgets = long_running.get("budgets")
        budgets = budgets if isinstance(budgets, Mapping) else {}
        recovery = long_running.get("recovery")
        recovery = recovery if isinstance(recovery, Mapping) else {}

        return cls(
            max_retries_per_item=_as_non_negative_int(
                recovery.get("max_retries_per_item", budgets.get("max_retries_per_item")),
                0,
            ),
            rollback_enabled=bool(recovery.get("rollback_enabled", False)),
            checkpoint_every_episodes=_as_non_negative_int(recovery.get("checkpoint_every_episodes"), 0),
            backoff_base_seconds=float(recovery.get("backoff_base_seconds", 0.25) or 0.25),
            backoff_max_seconds=float(recovery.get("backoff_max_seconds", 2.0) or 2.0),
            backoff_disable_jitter=bool(recovery.get("backoff_disable_jitter", True)),
        )

    def on_episode_fail(self, state: Mapping[str, Any], episode_result: Mapping[str, Any]) -> Dict[str, Any]:
        retry_streak = int(state.get("retry_streak") or 0)
        if self.max_retries_per_item <= 0:
            return {"action": "continue", "stop": False}
        if retry_streak < self.max_retries_per_item:
            return {"action": "retry_episode", "stop": False}

        if self.rollback_enabled and not bool(state.get("rollback_used")) and state.get("last_checkpoint_episode") is not None:
            return {
                "action": "rollback_and_retry",
                "stop": False,
                "rollback_to_checkpoint": int(state["last_checkpoint_episode"]),
                "reason": "retry_budget_reached_with_rollback",
            }
        return {
            "action": "stop",
            "stop": True,
            "reason": "retry_budget_reached",
        }

    def on_no_progress(self, state: Mapping[str, Any]) -> Dict[str, Any]:
        return {
            "action": "stop",
            "stop": True,
            "reason": "no_progress_threshold_reached",
        }

    def on_provider_error(self, state: Mapping[str, Any], error: Mapping[str, Any]) -> Dict[str, Any]:
        retry_streak = int(state.get("retry_streak") or 0)
        if self.max_retries_per_item > 0 and retry_streak < self.max_retries_per_item:
            backoff = self.next_backoff_seconds(retry_streak + 1)
            return {
                "action": "retry_with_backoff",
                "stop": False,
                "reason": "provider_error_retry",
                "backoff_seconds": backoff,
                "details": dict(error or {}),
            }
        return {
            "action": "stop",
            "stop": True,
            "reason": "provider_error_retry_exhausted",
            "details": dict(error or {}),
        }

    def next_backoff_seconds(self, attempt: int) -> float:
        """Bounded exponential backoff (optionally jittered)."""
        n = max(1, int(attempt))
        raw = self.backoff_base_seconds * (2 ** (n - 1))
        bounded = min(self.backoff_max_seconds, raw)
        if self.backoff_disable_jitter:
            return bounded
        jitter_factor = random.uniform(0.5, 1.5)
        return min(self.backoff_max_seconds, bounded * jitter_factor)
