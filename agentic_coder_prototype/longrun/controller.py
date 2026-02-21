from __future__ import annotations

import json
import os
import time
from pathlib import Path
from types import MappingProxyType
from typing import Any, Callable, Dict, List, Mapping, Optional

from .checkpoint import load_latest_checkpoint_pointer, load_state_from_latest_checkpoint, write_checkpoint
from .recovery import RecoveryPolicy
from .queue import WorkQueue
from .verification import VerificationExecutor, VerificationPolicy
from .flags import resolve_longrun_policy_profile


EpisodeRunner = Callable[[int], Dict[str, Any]]
ReviewerHook = Callable[[Mapping[str, Any]], Mapping[str, Any] | None]


def _as_positive_int(value: Any, default: int) -> int:
    try:
        parsed = int(value)
    except Exception:
        return default
    return parsed if parsed > 0 else default


def _as_non_negative_float(value: Any, default: float) -> float:
    try:
        parsed = float(value)
    except Exception:
        return default
    return parsed if parsed >= 0 else default


def _as_non_negative_int(value: Any, default: int) -> int:
    try:
        parsed = int(value)
    except Exception:
        return default
    return parsed if parsed >= 0 else default


class LongRunController:
    """
    Minimal serial macro-loop wrapper over the existing episode loop.

    Phase 1 goals:
    - default-safe bounded execution
    - explicit stop reasons
    - durable macro-state / summary artifacts
    """

    def __init__(
        self,
        config: Mapping[str, Any] | None,
        *,
        logger_v2: Any = None,
        work_queue: WorkQueue | None = None,
        time_fn: Optional[Callable[[], float]] = None,
        sleep_fn: Optional[Callable[[float], None]] = None,
        macro_event_emitter: Optional[Callable[[str, Dict[str, Any]], None]] = None,
        verification_executor: VerificationExecutor | None = None,
        reviewer_hook: ReviewerHook | None = None,
    ) -> None:
        self._config = config if isinstance(config, Mapping) else {}
        self._logger_v2 = logger_v2
        self._work_queue = work_queue
        self._time_fn = time_fn or time.time
        self._sleep_fn = sleep_fn or time.sleep
        self._macro_event_emitter = macro_event_emitter
        self._verification_executor = verification_executor
        self._reviewer_hook = reviewer_hook

        lr_cfg = self._config.get("long_running")
        lr_cfg = lr_cfg if isinstance(lr_cfg, Mapping) else {}
        budgets = lr_cfg.get("budgets")
        budgets = budgets if isinstance(budgets, Mapping) else {}
        recovery = lr_cfg.get("recovery")
        recovery = recovery if isinstance(recovery, Mapping) else {}
        observability = lr_cfg.get("observability")
        observability = observability if isinstance(observability, Mapping) else {}
        reviewer = lr_cfg.get("reviewer")
        reviewer = reviewer if isinstance(reviewer, Mapping) else {}
        resume = lr_cfg.get("resume")
        resume = resume if isinstance(resume, Mapping) else {}

        # Keep enabled runs bounded even when config is sparse.
        self._max_episodes = _as_positive_int(budgets.get("max_episodes"), default=1)
        self._max_wall_clock_seconds = _as_non_negative_float(
            budgets.get("max_wall_clock_seconds"),
            default=0.0,
        )
        self._max_total_tokens = _as_non_negative_int(
            budgets.get("total_tokens", budgets.get("max_total_tokens")),
            default=0,
        )
        self._max_total_cost_usd = _as_non_negative_float(
            budgets.get("total_cost_usd", budgets.get("max_total_cost_usd")),
            default=0.0,
        )
        self._max_retries_per_item = _as_non_negative_int(budgets.get("max_retries_per_item"), default=0)
        self._no_progress_max_episodes = _as_non_negative_int(
            recovery.get("no_progress_max_episodes"),
            default=0,
        )
        self._no_progress_signature_repeats = _as_non_negative_int(
            recovery.get("no_progress_signature_repeats"),
            default=0,
        )
        self._recovery_policy = RecoveryPolicy.from_config(self._config)
        self._verification_policy = VerificationPolicy.from_config(self._config)
        self._checkpoint_every_episodes = int(self._recovery_policy.checkpoint_every_episodes)
        self._emit_macro_events = bool(observability.get("emit_macro_events", False))
        self._reviewer_enabled = bool(reviewer.get("enabled", False))
        self._reviewer_mode = str(reviewer.get("mode") or "read_only").strip().lower()
        self._reviewer_env_enabled = os.environ.get("BREADBOARD_LONGRUN_REVIEWER_ENABLE", "0") == "1"
        self._resume_enabled = bool(resume.get("enabled", False))
        self._resume_state_path = str(resume.get("state_path") or "meta/longrun_state.json")
        self._policy_profile = resolve_longrun_policy_profile(self._config, default="balanced")

    def run(self, episode_runner: EpisodeRunner) -> Dict[str, Any]:
        started_at = self._time_fn()
        state: Dict[str, Any] = {
            "status": "running",
            "episode_index": 0,
            "episodes_run": 0,
            "stop_reason": None,
            "started_at": started_at,
            "updated_at": started_at,
            "ended_at": None,
            "budgets": {
                "max_episodes": self._max_episodes,
                "max_wall_clock_seconds": self._max_wall_clock_seconds,
                "max_total_tokens": self._max_total_tokens,
                "max_total_cost_usd": self._max_total_cost_usd,
                "max_retries_per_item": self._max_retries_per_item,
                "wall_clock_seconds_used": 0.0,
                "episodes_used": 0,
                "total_tokens_used": 0,
                "total_cost_usd_used": 0.0,
            },
            "no_progress_streak": 0,
            "retry_streak": 0,
            "rollback_used": False,
            "last_checkpoint_episode": None,
            "last_recovery_action": None,
            "queue_snapshot": None,
            "current_item_id": None,
            "provider_error_count": 0,
            "backoff_seconds": [],
            "last_failure_signature": None,
            "failure_signature_streak": 0,
            "resumed_from_state": False,
            "resume_state_path": None,
            "policy_profile": self._policy_profile,
            "rlm_usage": {
                "subcalls_used": 0,
                "total_tokens_used": 0,
                "total_cost_usd_used": 0.0,
                "branch_event_count": 0,
                "lane_counts": {},
            },
        }
        started_at = self._seed_state_from_resume(state, default_started_at=started_at)

        episodes: List[Dict[str, Any]] = []
        final_result: Dict[str, Any] = {}

        while True:
            now = self._time_fn()
            elapsed = max(0.0, now - started_at)
            state["budgets"]["wall_clock_seconds_used"] = elapsed
            state["updated_at"] = now

            stop_reason = self._check_pre_episode_stop(state)
            if stop_reason is not None:
                state["status"] = "stopped"
                state["stop_reason"] = stop_reason
                break

            current_item_id = None
            if self._work_queue is not None:
                queue_item = self._work_queue.peek_next(state)
                if not isinstance(queue_item, Mapping):
                    state["status"] = "stopped"
                    state["stop_reason"] = "queue_empty"
                    break
                current_item_id = str(queue_item.get("id") or "").strip() or None
                state["current_item_id"] = current_item_id
                if current_item_id:
                    self._work_queue.claim(current_item_id, int(state["episode_index"]))
                state["queue_snapshot"] = self._safe_queue_snapshot()
                self._write_queue_snapshot(state["queue_snapshot"])
            else:
                state["current_item_id"] = None

            episode_index = int(state["episode_index"])
            self._emit_macro_event(
                "loop.episode.start",
                {
                    "episode_index": episode_index,
                    "episodes_run": int(state["episodes_run"]),
                    "current_item_id": state.get("current_item_id"),
                },
            )
            self._write_checkpoint(state, episode_index=episode_index, phase="start")
            try:
                result = episode_runner(episode_index)
            except Exception as exc:
                state["provider_error_count"] = int(state["provider_error_count"]) + 1
                state["retry_streak"] = int(state["retry_streak"]) + 1
                error_payload = {
                    "type": exc.__class__.__name__,
                    "message": str(exc),
                }
                action = self._recovery_policy.on_provider_error(state, error_payload)
                state["last_recovery_action"] = dict(action)
                if self._work_queue is not None and current_item_id:
                    self._work_queue.defer(current_item_id, "provider_error")
                    state["queue_snapshot"] = self._safe_queue_snapshot()
                    self._write_queue_snapshot(state["queue_snapshot"])
                if action.get("action") == "retry_with_backoff":
                    backoff_seconds = float(action.get("backoff_seconds") or 0.0)
                    try:
                        state["backoff_seconds"].append(backoff_seconds)
                    except Exception:
                        pass
                    if backoff_seconds > 0:
                        try:
                            self._sleep_fn(backoff_seconds)
                        except Exception:
                            pass
                    self._write_state(state)
                    continue
                state["status"] = "stopped"
                state["stop_reason"] = str(action.get("reason") or "provider_error_retry_exhausted")
                self._emit_macro_event(
                    "loop.stop",
                    {
                        "episode_index": episode_index,
                        "reason": state["stop_reason"],
                        "last_recovery_action": state.get("last_recovery_action"),
                    },
                )
                break
            if not isinstance(result, dict):
                result = {
                    "completed": False,
                    "completion_reason": "invalid_episode_result",
                }
            if self._verification_executor is not None:
                verification_summary = self._verification_policy.run_sequence(
                    self._verification_executor,
                    context={
                        "episode_index": episode_index,
                        "result": dict(result),
                    },
                )
                result["verification_summary"] = verification_summary
                overall_status = str(verification_summary.get("overall_status") or "pass").lower()
                if overall_status == "fail":
                    result["completed"] = False
                    result["completion_reason"] = "verification_failed_hard"
            final_result = dict(result)
            usage_delta = self._extract_usage_delta(result)
            rlm_delta = self._extract_rlm_delta(result)
            state["budgets"]["total_tokens_used"] = int(state["budgets"].get("total_tokens_used") or 0) + int(
                usage_delta.get("total_tokens") or 0
            )
            state["budgets"]["total_cost_usd_used"] = float(state["budgets"].get("total_cost_usd_used") or 0.0) + float(
                usage_delta.get("cost_usd") or 0.0
            )
            rlm_usage = state.get("rlm_usage")
            if not isinstance(rlm_usage, dict):
                rlm_usage = {
                    "subcalls_used": 0,
                    "total_tokens_used": 0,
                    "total_cost_usd_used": 0.0,
                    "branch_event_count": 0,
                    "lane_counts": {},
                }
                state["rlm_usage"] = rlm_usage
            rlm_usage["subcalls_used"] = int(rlm_usage.get("subcalls_used") or 0) + int(rlm_delta.get("subcall_count") or 0)
            rlm_usage["total_tokens_used"] = int(rlm_usage.get("total_tokens_used") or 0) + int(rlm_delta.get("total_tokens") or 0)
            rlm_usage["total_cost_usd_used"] = float(rlm_usage.get("total_cost_usd_used") or 0.0) + float(
                rlm_delta.get("total_cost_usd") or 0.0
            )
            rlm_usage["branch_event_count"] = int(rlm_usage.get("branch_event_count") or 0) + int(
                rlm_delta.get("branch_event_count") or 0
            )
            lane_counts = rlm_usage.get("lane_counts")
            lane_counts = dict(lane_counts) if isinstance(lane_counts, Mapping) else {}
            for lane, count in (rlm_delta.get("lane_counts") or {}).items():
                lane_text = str(lane)
                lane_counts[lane_text] = int(lane_counts.get(lane_text) or 0) + int(count or 0)
            rlm_usage["lane_counts"] = lane_counts

            episode_summary = {
                "episode_index": episode_index,
                "completed": bool(result.get("completed", False)),
                "completion_reason": str(result.get("completion_reason", "")),
                "completion_summary": result.get("completion_summary"),
                "progress": self._episode_made_progress(result),
                "verification_summary": result.get("verification_summary"),
                "usage_delta": usage_delta,
                "rlm_delta": rlm_delta,
            }
            reviewer_summary = self._run_reviewer(episode_index, result, state)
            if isinstance(reviewer_summary, Mapping):
                episode_summary["reviewer_summary"] = dict(reviewer_summary)
            episodes.append(episode_summary)
            self._write_episode_summary(episode_index, episode_summary)

            if self._work_queue is not None and current_item_id:
                if bool(result.get("completed", False)):
                    self._work_queue.complete(current_item_id, result)
                else:
                    self._work_queue.defer(
                        current_item_id,
                        str(result.get("completion_reason") or "episode_not_completed"),
                    )
                state["queue_snapshot"] = self._safe_queue_snapshot()
                self._write_queue_snapshot(state["queue_snapshot"])

            if episode_summary["progress"]:
                state["no_progress_streak"] = 0
                state["retry_streak"] = 0
                state["last_failure_signature"] = None
                state["failure_signature_streak"] = 0
            else:
                state["no_progress_streak"] = int(state["no_progress_streak"]) + 1
                state["retry_streak"] = int(state["retry_streak"]) + 1
                failure_signature = self._build_failure_signature(result, state)
                if failure_signature and failure_signature == state.get("last_failure_signature"):
                    state["failure_signature_streak"] = int(state.get("failure_signature_streak") or 0) + 1
                else:
                    state["failure_signature_streak"] = 1 if failure_signature else 0
                state["last_failure_signature"] = failure_signature

            state["episodes_run"] = int(state["episodes_run"]) + 1
            state["budgets"]["episodes_used"] = int(state["episodes_run"])
            state["episode_index"] = int(state["episodes_run"])
            state["updated_at"] = self._time_fn()

            self._write_state(state)
            self._write_checkpoint(state, episode_index=episode_index, phase="end")
            self._emit_macro_event(
                "loop.episode.end",
                {
                    "episode_index": episode_index,
                    "episodes_run": int(state["episodes_run"]),
                    "completed": bool(result.get("completed", False)),
                    "completion_reason": str(result.get("completion_reason", "")),
                },
            )

            stop_reason = self._check_post_episode_stop(result, state)
            if stop_reason is not None:
                state["status"] = "completed" if stop_reason == "episode_completed" else "stopped"
                state["stop_reason"] = stop_reason
                self._emit_macro_event(
                    "loop.stop",
                    {
                        "episode_index": episode_index,
                        "reason": state["stop_reason"],
                        "episodes_run": int(state["episodes_run"]),
                    },
                )
                break

        state["ended_at"] = self._time_fn()
        state["budgets"]["wall_clock_seconds_used"] = max(0.0, state["ended_at"] - started_at)
        self._write_state(state)

        summary = {
            "status": state["status"],
            "stop_reason": state["stop_reason"],
            "episodes_run": state["episodes_run"],
            "budget_limits": {
                "max_episodes": self._max_episodes,
                "max_wall_clock_seconds": self._max_wall_clock_seconds,
                "max_total_tokens": self._max_total_tokens,
                "max_total_cost_usd": self._max_total_cost_usd,
                "max_retries_per_item": self._max_retries_per_item,
                "no_progress_max_episodes": self._no_progress_max_episodes,
                "no_progress_signature_repeats": self._no_progress_signature_repeats,
            },
            "budget_usage": {
                "episodes_used": state["budgets"]["episodes_used"],
                "wall_clock_seconds_used": state["budgets"]["wall_clock_seconds_used"],
                "total_tokens_used": int(state["budgets"].get("total_tokens_used") or 0),
                "total_cost_usd_used": float(state["budgets"].get("total_cost_usd_used") or 0.0),
                "retry_streak": state["retry_streak"],
                "no_progress_streak": state["no_progress_streak"],
                "failure_signature_streak": int(state.get("failure_signature_streak") or 0),
            },
            "recovery_metrics": {
                "provider_error_count": int(state.get("provider_error_count") or 0),
                "retry_attempts": int(state.get("retry_streak") or 0),
                "backoff_seconds": list(state.get("backoff_seconds") or []),
                "last_recovery_action": state.get("last_recovery_action"),
            },
            "episodes": episodes,
            "completed": bool(final_result.get("completed", False)),
            "queue_snapshot": state.get("queue_snapshot"),
            "resumed_from_state": bool(state.get("resumed_from_state")),
            "resume_state_path": state.get("resume_state_path"),
            "policy_profile": state.get("policy_profile", self._policy_profile),
            "rlm_usage": state.get("rlm_usage"),
        }
        self._write_summary(summary)

        return {
            "result": final_result,
            "macro_state": state,
            "macro_summary": summary,
        }

    def _check_pre_episode_stop(self, state: Mapping[str, Any]) -> str | None:
        if int(state["episodes_run"]) >= self._max_episodes:
            return "max_episodes_reached"

        if self._max_wall_clock_seconds > 0:
            elapsed = float(state["budgets"]["wall_clock_seconds_used"])
            if elapsed >= self._max_wall_clock_seconds:
                return "max_wall_clock_seconds_reached"
        if self._max_total_tokens > 0:
            tokens_used = int((state.get("budgets") or {}).get("total_tokens_used") or 0)
            if tokens_used >= self._max_total_tokens:
                return "total_tokens_budget_reached"
        if self._max_total_cost_usd > 0:
            cost_used = float((state.get("budgets") or {}).get("total_cost_usd_used") or 0.0)
            if cost_used >= self._max_total_cost_usd:
                return "total_cost_budget_reached"

        return None

    def _check_post_episode_stop(self, result: Mapping[str, Any], state: Mapping[str, Any]) -> str | None:
        if bool(result.get("completed", False)):
            return "episode_completed"

        completion_reason = str(result.get("completion_reason") or "").strip().lower()
        if completion_reason in {
            "stopped",
            "cancelled",
            "canceled",
            "interrupted",
            "control_stop_requested",
        }:
            return completion_reason

        if self._max_retries_per_item > 0 and int(state["retry_streak"]) >= self._max_retries_per_item:
            action = self._recovery_policy.on_episode_fail(state, result)
            if action.get("action") == "rollback_and_retry":
                state["rollback_used"] = True
                state["retry_streak"] = 0
                state["no_progress_streak"] = 0
                state["last_recovery_action"] = dict(action)
                self._restore_from_latest_checkpoint(state)
                return None
            if bool(action.get("stop")):
                return str(action.get("reason") or "retry_budget_reached")

        if (
            self._no_progress_signature_repeats > 0
            and int(state.get("failure_signature_streak") or 0) >= self._no_progress_signature_repeats
        ):
            action = self._recovery_policy.on_no_progress(state)
            if bool(action.get("stop", True)):
                return str(action.get("reason") or "no_progress_signature_repeated")
            state["last_recovery_action"] = dict(action)
            return None

        if self._no_progress_max_episodes > 0 and int(state["no_progress_streak"]) >= self._no_progress_max_episodes:
            action = self._recovery_policy.on_no_progress(state)
            if bool(action.get("stop")):
                return str(action.get("reason") or "no_progress_threshold_reached")
            state["last_recovery_action"] = dict(action)
            return None

        if int(state["episodes_run"]) >= self._max_episodes:
            return "max_episodes_reached"

        if self._max_wall_clock_seconds > 0:
            elapsed = float(state["budgets"]["wall_clock_seconds_used"])
            if elapsed >= self._max_wall_clock_seconds:
                return "max_wall_clock_seconds_reached"
        if self._max_total_tokens > 0:
            tokens_used = int((state.get("budgets") or {}).get("total_tokens_used") or 0)
            if tokens_used >= self._max_total_tokens:
                return "total_tokens_budget_reached"
        if self._max_total_cost_usd > 0:
            cost_used = float((state.get("budgets") or {}).get("total_cost_usd_used") or 0.0)
            if cost_used >= self._max_total_cost_usd:
                return "total_cost_budget_reached"

        return None

    def _episode_made_progress(self, result: Mapping[str, Any]) -> bool:
        if bool(result.get("completed", False)):
            return True
        progress_metrics = result.get("progress_metrics")
        if isinstance(progress_metrics, Mapping):
            if bool(progress_metrics.get("made_progress")):
                return True
            try:
                if float(progress_metrics.get("delta", 0.0)) > 0:
                    return True
            except Exception:
                pass
        verification_summary = result.get("verification_summary")
        if isinstance(verification_summary, Mapping):
            status = str(verification_summary.get("overall_status", "")).lower()
            if status in {"pass", "soft_fail"}:
                return True
        return False

    def _write_state(self, payload: Mapping[str, Any]) -> None:
        if self._logger_v2 is None:
            return
        try:
            self._logger_v2.write_json("meta/longrun_state.json", dict(payload))
        except Exception:
            pass

    def _write_summary(self, payload: Mapping[str, Any]) -> None:
        if self._logger_v2 is None:
            return
        try:
            self._logger_v2.write_json("meta/longrun_summary.json", dict(payload))
        except Exception:
            pass

    def _write_episode_summary(self, episode_index: int, payload: Mapping[str, Any]) -> None:
        if self._logger_v2 is None:
            return
        try:
            self._logger_v2.write_json(f"meta/episodes/longrun_episode_{int(episode_index)}.json", dict(payload))
        except Exception:
            pass

    def _write_queue_snapshot(self, payload: Any) -> None:
        if self._logger_v2 is None:
            return
        try:
            self._logger_v2.write_json("meta/longrun_queue_snapshot.json", payload if isinstance(payload, Mapping) else {})
        except Exception:
            pass

    def _write_checkpoint(self, state: Mapping[str, Any], *, episode_index: int, phase: str) -> None:
        if self._logger_v2 is None:
            return
        if self._checkpoint_every_episodes <= 0:
            return
        ep = int(episode_index)
        if ep < 0:
            return
        if phase == "end":
            episodes_run = int(state.get("episodes_run") or 0)
            if episodes_run <= 0:
                return
            if (episodes_run % self._checkpoint_every_episodes) != 0:
                return
        rel = write_checkpoint(self._logger_v2, state, episode=ep, phase=phase)
        if rel and isinstance(state, dict):
            state["last_checkpoint_episode"] = ep

    def _restore_from_latest_checkpoint(self, state: Dict[str, Any]) -> None:
        logger = self._logger_v2
        run_dir = getattr(logger, "run_dir", None)
        if not run_dir:
            pass
            return
        restored = load_state_from_latest_checkpoint(run_dir)
        if not isinstance(restored, dict):
            return
        # Keep run identity fields; restore operational fields only.
        for key in (
            "episode_index",
            "episodes_run",
            "current_item_id",
            "queue_snapshot",
            "retry_streak",
            "no_progress_streak",
            "last_failure_signature",
            "failure_signature_streak",
            "budgets",
            "last_checkpoint_episode",
        ):
            if key in restored:
                state[key] = restored[key]

    def _seed_state_from_resume(self, state: Dict[str, Any], *, default_started_at: float) -> float:
        if not self._resume_enabled:
            return default_started_at
        logger = self._logger_v2
        run_dir = getattr(logger, "run_dir", None)
        if not run_dir:
            return default_started_at
        resume_path = Path(run_dir) / self._resume_state_path
        payload: Mapping[str, Any] | None = None
        loaded_from = None
        if resume_path.exists():
            try:
                loaded = json.loads(resume_path.read_text(encoding="utf-8"))
                if isinstance(loaded, Mapping):
                    payload = loaded
                    loaded_from = str(resume_path)
            except Exception:
                payload = None
        if payload is None:
            checkpoint_payload = load_state_from_latest_checkpoint(run_dir)
            if isinstance(checkpoint_payload, Mapping):
                payload = checkpoint_payload
                pointer = load_latest_checkpoint_pointer(run_dir)
                pointer_path = str((Path(run_dir) / "meta" / "checkpoints" / "latest_checkpoint.json"))
                if isinstance(pointer, Mapping) and isinstance(pointer.get("path"), str):
                    loaded_from = str(Path(run_dir) / str(pointer.get("path")))
                else:
                    loaded_from = pointer_path
        if payload is None:
            return default_started_at
        for key in (
            "episode_index",
            "episodes_run",
            "budgets",
            "no_progress_streak",
            "retry_streak",
            "rollback_used",
            "last_checkpoint_episode",
            "last_recovery_action",
            "queue_snapshot",
            "current_item_id",
            "provider_error_count",
            "backoff_seconds",
            "last_failure_signature",
            "failure_signature_streak",
        ):
            if key in payload:
                state[key] = payload[key]
        state["resumed_from_state"] = True
        state["resume_state_path"] = loaded_from or str(resume_path)
        resumed_started_at = payload.get("started_at")
        if isinstance(resumed_started_at, (int, float)):
            state["started_at"] = float(resumed_started_at)
            return float(resumed_started_at)
        return default_started_at

    def _extract_usage_delta(self, result: Mapping[str, Any]) -> Dict[str, Any]:
        usage = result.get("usage")
        if not isinstance(usage, Mapping):
            return {"total_tokens": 0, "cost_usd": 0.0}
        total_tokens = usage.get("total_tokens")
        if total_tokens is None:
            prompt_tokens = usage.get("prompt_tokens")
            completion_tokens = usage.get("completion_tokens")
            try:
                total_tokens = int(prompt_tokens or 0) + int(completion_tokens or 0)
            except Exception:
                total_tokens = 0
        try:
            total_tokens_int = max(0, int(total_tokens or 0))
        except Exception:
            total_tokens_int = 0
        cost_usd = usage.get("cost_usd", usage.get("total_cost_usd"))
        try:
            cost_usd_float = max(0.0, float(cost_usd or 0.0))
        except Exception:
            cost_usd_float = 0.0
        return {"total_tokens": total_tokens_int, "cost_usd": cost_usd_float}

    def _extract_rlm_delta(self, result: Mapping[str, Any]) -> Dict[str, Any]:
        rlm = result.get("rlm")
        if not isinstance(rlm, Mapping):
            return {
                "subcall_count": 0,
                "total_tokens": 0,
                "total_cost_usd": 0.0,
                "branch_event_count": 0,
                "lane_counts": {},
            }
        lane_counts_raw = rlm.get("lane_counts")
        lane_counts: Dict[str, int] = {}
        if isinstance(lane_counts_raw, Mapping):
            for lane, count in lane_counts_raw.items():
                lane_text = str(lane)
                try:
                    lane_counts[lane_text] = max(0, int(count or 0))
                except Exception:
                    continue
        try:
            subcall_count = max(0, int(rlm.get("subcall_count") or 0))
        except Exception:
            subcall_count = 0
        try:
            total_tokens = max(0, int(rlm.get("total_tokens") or 0))
        except Exception:
            total_tokens = 0
        try:
            total_cost_usd = max(0.0, float(rlm.get("total_cost_usd") or 0.0))
        except Exception:
            total_cost_usd = 0.0
        try:
            branch_event_count = max(0, int(rlm.get("branch_event_count") or 0))
        except Exception:
            branch_event_count = 0
        return {
            "subcall_count": subcall_count,
            "total_tokens": total_tokens,
            "total_cost_usd": total_cost_usd,
            "branch_event_count": branch_event_count,
            "lane_counts": lane_counts,
        }

    def _build_failure_signature(self, result: Mapping[str, Any], state: Mapping[str, Any]) -> str | None:
        completion_reason = str(result.get("completion_reason") or "").strip().lower()
        verification = result.get("verification_summary")
        verification_status = ""
        if isinstance(verification, Mapping):
            verification_status = str(verification.get("overall_status") or "").strip().lower()
        current_item_id = str(state.get("current_item_id") or "").strip().lower()
        parts = [completion_reason, verification_status, current_item_id]
        if not any(parts):
            return None
        return "|".join(parts)

    def _run_reviewer(
        self,
        episode_index: int,
        result: Mapping[str, Any],
        state: Mapping[str, Any],
    ) -> Dict[str, Any] | None:
        if not self._reviewer_enabled:
            return None
        if not self._reviewer_env_enabled:
            return None
        if self._reviewer_mode != "read_only":
            return {"status": "skipped", "reason": "reviewer_mode_not_read_only", "mode": self._reviewer_mode}
        if self._reviewer_hook is None:
            return {"status": "skipped", "reason": "reviewer_hook_missing", "mode": self._reviewer_mode}

        request_context = MappingProxyType(
            {
                "episode_index": int(episode_index),
                "result": dict(result),
                "current_item_id": state.get("current_item_id"),
                "mode": "read_only",
            }
        )
        try:
            output = self._reviewer_hook(request_context)
        except Exception as exc:
            output = {
                "status": "error",
                "reason": "reviewer_hook_exception",
                "error_type": exc.__class__.__name__,
                "message": str(exc),
            }

        payload = dict(output) if isinstance(output, Mapping) else {"status": "ok", "details": output}
        payload["mode"] = "read_only"
        if bool(payload.get("workspace_mutation_requested")):
            payload["status"] = "blocked"
            payload["reason"] = "reviewer_workspace_mutation_blocked"
        payload.setdefault("status", "ok")
        payload["episode_index"] = int(episode_index)

        artifact_path = None
        if self._logger_v2 is not None:
            try:
                artifact_path = self._logger_v2.write_json(
                    f"meta/reviewer/reviewer_ep_{int(episode_index)}.json",
                    payload,
                )
            except Exception:
                artifact_path = None
        if artifact_path:
            payload["artifact_path"] = str(artifact_path)
        return payload

    def _safe_queue_snapshot(self) -> Dict[str, Any] | None:
        if self._work_queue is None:
            return None
        try:
            snap = self._work_queue.snapshot()
            return dict(snap) if isinstance(snap, Mapping) else None
        except Exception:
            return None

    def _emit_macro_event(self, event_type: str, payload: Dict[str, Any]) -> None:
        if not self._emit_macro_events:
            return
        if self._macro_event_emitter is None:
            return
        try:
            self._macro_event_emitter(str(event_type), dict(payload))
        except Exception:
            pass
