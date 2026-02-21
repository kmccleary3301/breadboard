from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Mapping

from agentic_coder_prototype.longrun.controller import LongRunController


class _FakeLogger:
    def __init__(self) -> None:
        self.writes: List[tuple[str, Dict[str, Any]]] = []

    def write_json(self, rel_path: str, data: Dict[str, Any]) -> str:
        self.writes.append((rel_path, dict(data)))
        return rel_path


class _DiskLogger:
    def __init__(self, run_dir: Path) -> None:
        self.run_dir = str(run_dir)
        self.writes: List[tuple[str, Dict[str, Any]]] = []

    def write_json(self, rel_path: str, data: Dict[str, Any]) -> str:
        self.writes.append((rel_path, dict(data)))
        path = Path(self.run_dir) / rel_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(dict(data), indent=2), encoding="utf-8")
        return rel_path


class _FakeQueue:
    def __init__(self, items: List[Dict[str, Any]] | None = None) -> None:
        self.items = list(items or [])
        self.claimed: List[tuple[str, int]] = []
        self.completed: List[str] = []
        self.deferred: List[tuple[str, str]] = []

    def peek_next(self, state=None):
        for item in self.items:
            if item.get("status") in {"todo", "in_progress", "blocked"}:
                return item
        return None

    def claim(self, item_id: str, episode_id: int):
        self.claimed.append((item_id, episode_id))
        for item in self.items:
            if item.get("id") == item_id and item.get("status") == "todo":
                item["status"] = "in_progress"
        return self.snapshot()

    def complete(self, item_id: str, outcome=None):
        self.completed.append(item_id)
        for item in self.items:
            if item.get("id") == item_id:
                item["status"] = "done"
        return self.snapshot()

    def defer(self, item_id: str, reason: str):
        self.deferred.append((item_id, reason))
        for item in self.items:
            if item.get("id") == item_id:
                item["status"] = "blocked"
        return self.snapshot()

    def snapshot(self):
        return {"backend": "fake", "items": [dict(item) for item in self.items]}


def test_longrun_controller_stops_on_max_episodes() -> None:
    logger = _FakeLogger()
    config = {"long_running": {"enabled": True, "budgets": {"max_episodes": 2}}}
    controller = LongRunController(config, logger_v2=logger)

    calls: List[int] = []

    def episode_runner(episode_index: int) -> Dict[str, Any]:
        calls.append(episode_index)
        return {"completed": False, "completion_reason": "needs_more_work"}

    out = controller.run(episode_runner)
    summary = out["macro_summary"]
    assert calls == [0, 1]
    assert summary["episodes_run"] == 2
    assert summary["stop_reason"] == "max_episodes_reached"
    assert summary["status"] == "stopped"
    paths = [path for path, _payload in logger.writes]
    assert "meta/longrun_state.json" in paths
    assert "meta/longrun_summary.json" in paths


def test_longrun_controller_stops_when_episode_completes() -> None:
    logger = _FakeLogger()
    config = {"long_running": {"enabled": True, "budgets": {"max_episodes": 5}}}
    controller = LongRunController(config, logger_v2=logger)

    out = controller.run(
        lambda _episode_index: {
            "completed": True,
            "completion_reason": "done",
            "completion_summary": {"completed": True, "reason": "done"},
        }
    )
    summary = out["macro_summary"]
    assert summary["episodes_run"] == 1
    assert summary["stop_reason"] == "episode_completed"
    assert summary["status"] == "completed"
    assert summary["completed"] is True


def test_longrun_controller_hard_stops_on_wall_clock_budget() -> None:
    now = {"t": 100.0}

    def _time_fn() -> float:
        return now["t"]

    config = {
        "long_running": {
            "enabled": True,
            "budgets": {"max_episodes": 5, "max_wall_clock_seconds": 1.0},
        }
    }
    controller = LongRunController(config, time_fn=_time_fn)

    def episode_runner(_episode_index: int) -> Dict[str, Any]:
        now["t"] += 1.1
        return {"completed": False, "completion_reason": "still_running"}

    out = controller.run(episode_runner)
    summary = out["macro_summary"]
    assert summary["episodes_run"] == 1
    assert summary["stop_reason"] == "max_wall_clock_seconds_reached"


def test_longrun_controller_stops_on_no_progress_threshold() -> None:
    config = {
        "long_running": {
            "enabled": True,
            "budgets": {"max_episodes": 10, "max_retries_per_item": 0},
            "recovery": {"no_progress_max_episodes": 2},
        }
    }
    controller = LongRunController(config)

    out = controller.run(lambda _idx: {"completed": False, "completion_reason": "not_done"})
    summary = out["macro_summary"]
    assert summary["episodes_run"] == 2
    assert summary["stop_reason"] == "no_progress_threshold_reached"


def test_longrun_controller_stops_on_retry_budget() -> None:
    config = {
        "long_running": {
            "enabled": True,
            "budgets": {"max_episodes": 10, "max_retries_per_item": 2},
        }
    }
    controller = LongRunController(config)

    out = controller.run(lambda _idx: {"completed": False, "completion_reason": "not_done"})
    summary = out["macro_summary"]
    assert summary["episodes_run"] == 2
    assert summary["stop_reason"] == "retry_budget_reached"


def test_longrun_controller_uses_single_rollback_then_stops() -> None:
    logger = _FakeLogger()
    config = {
        "long_running": {
            "enabled": True,
            "budgets": {"max_episodes": 5, "max_retries_per_item": 1},
            "recovery": {
                "rollback_enabled": True,
                "checkpoint_every_episodes": 1,
            },
        }
    }
    controller = LongRunController(config, logger_v2=logger)
    out = controller.run(lambda _idx: {"completed": False, "completion_reason": "not_done"})
    state = out["macro_state"]
    summary = out["macro_summary"]

    assert summary["episodes_run"] == 2
    assert summary["stop_reason"] == "retry_budget_reached"
    assert state["rollback_used"] is True
    assert state["last_checkpoint_episode"] == 1
    assert state["last_recovery_action"]["action"] == "rollback_and_retry"

    paths = [path for path, _payload in logger.writes]
    assert "meta/checkpoints/longrun_state_ep_0_start.json" in paths
    assert "meta/checkpoints/longrun_state_ep_0_end.json" in paths


def test_longrun_controller_stops_when_queue_empty() -> None:
    queue = _FakeQueue(items=[])
    config = {"long_running": {"enabled": True, "budgets": {"max_episodes": 3}}}
    controller = LongRunController(config, work_queue=queue)
    out = controller.run(lambda _idx: {"completed": False, "completion_reason": "not_done"})
    summary = out["macro_summary"]
    assert summary["episodes_run"] == 0
    assert summary["stop_reason"] == "queue_empty"


def test_longrun_controller_queue_claim_and_complete() -> None:
    queue = _FakeQueue(items=[{"id": "q1", "title": "Task 1", "status": "todo"}])
    config = {"long_running": {"enabled": True, "budgets": {"max_episodes": 3}}}
    controller = LongRunController(config, work_queue=queue)

    out = controller.run(lambda _idx: {"completed": True, "completion_reason": "done"})
    summary = out["macro_summary"]
    assert summary["stop_reason"] == "episode_completed"
    assert queue.claimed == [("q1", 0)]
    assert queue.completed == ["q1"]
    assert queue.deferred == []
    assert isinstance(summary.get("queue_snapshot"), dict)


def test_longrun_controller_provider_error_retry_then_success() -> None:
    config = {
        "long_running": {
            "enabled": True,
            "budgets": {"max_episodes": 3, "max_retries_per_item": 2},
            "recovery": {
                "backoff_base_seconds": 0.01,
                "backoff_max_seconds": 0.01,
                "backoff_disable_jitter": True,
            },
        }
    }
    calls = {"n": 0}

    def runner(_idx: int) -> Dict[str, Any]:
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("provider timeout")
        return {"completed": True, "completion_reason": "done_after_retry"}

    sleeps: List[float] = []
    controller = LongRunController(config, sleep_fn=lambda s: sleeps.append(s))
    out = controller.run(runner)
    summary = out["macro_summary"]
    assert summary["stop_reason"] == "episode_completed"
    assert summary["recovery_metrics"]["provider_error_count"] == 1
    assert len(summary["recovery_metrics"]["backoff_seconds"]) == 1
    assert sleeps == [0.01]


def test_longrun_controller_provider_error_retry_exhausted() -> None:
    config = {
        "long_running": {
            "enabled": True,
            "budgets": {"max_episodes": 3, "max_retries_per_item": 1},
            "recovery": {
                "backoff_base_seconds": 0.01,
                "backoff_max_seconds": 0.01,
                "backoff_disable_jitter": True,
            },
        }
    }

    def runner(_idx: int) -> Dict[str, Any]:
        raise RuntimeError("provider down")

    controller = LongRunController(config, sleep_fn=lambda _s: None)
    out = controller.run(runner)
    summary = out["macro_summary"]
    assert summary["stop_reason"] == "provider_error_retry_exhausted"


def test_longrun_controller_macro_events_emit_order_when_enabled() -> None:
    events: List[tuple[str, Dict[str, Any]]] = []
    cfg = {
        "long_running": {
            "enabled": True,
            "observability": {"emit_macro_events": True},
            "budgets": {"max_episodes": 2},
        }
    }
    controller = LongRunController(
        cfg,
        macro_event_emitter=lambda event_type, payload: events.append((event_type, dict(payload))),
    )
    out = controller.run(lambda _idx: {"completed": True, "completion_reason": "done"})
    assert out["macro_summary"]["stop_reason"] == "episode_completed"
    assert [name for name, _payload in events] == [
        "loop.episode.start",
        "loop.episode.end",
        "loop.stop",
    ]


def test_longrun_controller_macro_events_disabled_by_default() -> None:
    events: List[tuple[str, Dict[str, Any]]] = []
    cfg = {"long_running": {"enabled": True, "budgets": {"max_episodes": 1}}}
    controller = LongRunController(
        cfg,
        macro_event_emitter=lambda event_type, payload: events.append((event_type, dict(payload))),
    )
    controller.run(lambda _idx: {"completed": True, "completion_reason": "done"})
    assert events == []


def test_longrun_controller_verification_hard_fail_marks_episode_failed() -> None:
    cfg = {
        "long_running": {
            "enabled": True,
            "budgets": {"max_episodes": 1},
            "verification": {
                "tiers": [
                    {
                        "name": "smoke",
                        "commands": ["pytest -q smoke"],
                        "timeout_seconds": 10,
                        "hard_fail": True,
                    }
                ]
            },
        }
    }

    def verifier(command: str, timeout_seconds: float, context: Mapping[str, Any]) -> Dict[str, Any]:
        assert "smoke" in command
        return {"status": "fail", "duration_ms": 1234, "signal": "hard"}

    controller = LongRunController(cfg, verification_executor=verifier)
    out = controller.run(lambda _idx: {"completed": True, "completion_reason": "done"})
    assert out["result"]["completed"] is False
    assert out["result"]["completion_reason"] == "verification_failed_hard"
    assert out["result"]["verification_summary"]["overall_status"] == "fail"
    assert out["macro_summary"]["episodes"][0]["verification_summary"]["overall_status"] == "fail"


def test_longrun_controller_verification_soft_fail_allows_completion() -> None:
    cfg = {
        "long_running": {
            "enabled": True,
            "budgets": {"max_episodes": 1},
            "verification": {
                "tiers": [
                    {
                        "name": "smoke",
                        "commands": ["pytest -q smoke"],
                        "timeout_seconds": 10,
                        "hard_fail": False,
                    }
                ]
            },
        }
    }

    def verifier(command: str, timeout_seconds: float, context: Mapping[str, Any]) -> Dict[str, Any]:
        assert "smoke" in command
        return {"status": "timeout", "duration_ms": 10000, "signal": "soft"}

    controller = LongRunController(cfg, verification_executor=verifier)
    out = controller.run(lambda _idx: {"completed": True, "completion_reason": "done"})
    assert out["result"]["completed"] is True
    assert out["result"]["verification_summary"]["overall_status"] == "soft_fail"
    assert out["macro_summary"]["stop_reason"] == "episode_completed"


def test_longrun_controller_writes_per_episode_artifact_summary() -> None:
    logger = _FakeLogger()
    cfg = {"long_running": {"enabled": True, "budgets": {"max_episodes": 1}}}
    controller = LongRunController(cfg, logger_v2=logger)
    controller.run(lambda _idx: {"completed": True, "completion_reason": "done"})
    paths = [path for path, _payload in logger.writes]
    assert "meta/episodes/longrun_episode_0.json" in paths


def test_longrun_controller_stops_on_repeated_failure_signature() -> None:
    cfg = {
        "long_running": {
            "enabled": True,
            "budgets": {"max_episodes": 10, "max_retries_per_item": 0},
            "recovery": {
                "no_progress_max_episodes": 0,
                "no_progress_signature_repeats": 2,
            },
        }
    }
    controller = LongRunController(cfg)
    out = controller.run(lambda _idx: {"completed": False, "completion_reason": "same_failure"})
    assert out["macro_summary"]["episodes_run"] == 2
    assert out["macro_summary"]["stop_reason"] == "no_progress_threshold_reached"
    assert out["macro_summary"]["budget_usage"]["failure_signature_streak"] == 2


def test_longrun_controller_repeated_failure_signature_resets_on_change() -> None:
    cfg = {
        "long_running": {
            "enabled": True,
            "budgets": {"max_episodes": 3, "max_retries_per_item": 0},
            "recovery": {
                "no_progress_max_episodes": 0,
                "no_progress_signature_repeats": 3,
            },
        }
    }
    reasons = iter(["r1", "r2", "r2"])
    controller = LongRunController(cfg)
    out = controller.run(lambda _idx: {"completed": False, "completion_reason": next(reasons, "r2")})
    assert out["macro_summary"]["episodes_run"] == 3
    assert out["macro_summary"]["stop_reason"] == "max_episodes_reached"


def test_longrun_controller_reviewer_hook_gated_and_persisted(monkeypatch) -> None:
    monkeypatch.setenv("BREADBOARD_LONGRUN_REVIEWER_ENABLE", "1")
    logger = _FakeLogger()
    cfg = {
        "long_running": {
            "enabled": True,
            "budgets": {"max_episodes": 1},
            "reviewer": {"enabled": True, "mode": "read_only"},
        }
    }
    seen_context: Dict[str, Any] = {}

    def reviewer(ctx: Mapping[str, Any]) -> Mapping[str, Any]:
        seen_context.update(dict(ctx))
        return {"status": "ok", "notes": "looks good"}

    controller = LongRunController(cfg, logger_v2=logger, reviewer_hook=reviewer)
    out = controller.run(lambda _idx: {"completed": True, "completion_reason": "done"})
    episode = out["macro_summary"]["episodes"][0]
    reviewer_summary = episode["reviewer_summary"]
    assert reviewer_summary["status"] == "ok"
    assert reviewer_summary["mode"] == "read_only"
    assert seen_context["mode"] == "read_only"
    paths = [path for path, _payload in logger.writes]
    assert "meta/reviewer/reviewer_ep_0.json" in paths


def test_longrun_controller_reviewer_workspace_mutation_request_blocked(monkeypatch) -> None:
    monkeypatch.setenv("BREADBOARD_LONGRUN_REVIEWER_ENABLE", "1")
    cfg = {
        "long_running": {
            "enabled": True,
            "budgets": {"max_episodes": 1},
            "reviewer": {"enabled": True, "mode": "read_only"},
        }
    }

    def reviewer(_ctx: Mapping[str, Any]) -> Mapping[str, Any]:
        return {"status": "ok", "workspace_mutation_requested": True}

    controller = LongRunController(cfg, reviewer_hook=reviewer)
    out = controller.run(lambda _idx: {"completed": True, "completion_reason": "done"})
    reviewer_summary = out["macro_summary"]["episodes"][0]["reviewer_summary"]
    assert reviewer_summary["status"] == "blocked"
    assert reviewer_summary["reason"] == "reviewer_workspace_mutation_blocked"


def test_longrun_controller_resume_enabled_loads_state(tmp_path: Path) -> None:
    run_dir = tmp_path / "run"
    state_path = run_dir / "meta" / "longrun_state.json"
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(
        json.dumps(
            {
                "started_at": 100.0,
                "episode_index": 2,
                "episodes_run": 2,
                "budgets": {
                    "episodes_used": 2,
                    "wall_clock_seconds_used": 12.0,
                    "total_tokens_used": 40,
                    "total_cost_usd_used": 0.4,
                },
                "retry_streak": 1,
                "no_progress_streak": 1,
            }
        ),
        encoding="utf-8",
    )
    logger = _DiskLogger(run_dir)
    cfg = {
        "long_running": {
            "enabled": True,
            "resume": {"enabled": True},
            "budgets": {"max_episodes": 4},
        }
    }
    controller = LongRunController(cfg, logger_v2=logger, time_fn=lambda: 110.0)
    out = controller.run(lambda _idx: {"completed": True, "completion_reason": "done"})
    assert out["macro_state"]["resumed_from_state"] is True
    assert out["macro_state"]["episodes_run"] == 3
    assert out["macro_summary"]["resumed_from_state"] is True
    assert out["macro_summary"]["episodes"][0]["episode_index"] == 2
    assert out["macro_state"]["resume_state_path"].endswith("meta/longrun_state.json")


def test_longrun_controller_resume_disabled_ignores_state(tmp_path: Path) -> None:
    run_dir = tmp_path / "run"
    state_path = run_dir / "meta" / "longrun_state.json"
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(json.dumps({"episode_index": 9, "episodes_run": 9}), encoding="utf-8")
    logger = _DiskLogger(run_dir)
    cfg = {"long_running": {"enabled": True, "resume": {"enabled": False}, "budgets": {"max_episodes": 2}}}
    controller = LongRunController(cfg, logger_v2=logger)
    out = controller.run(lambda _idx: {"completed": True, "completion_reason": "done"})
    assert out["macro_state"]["resumed_from_state"] is False
    assert out["macro_summary"]["episodes"][0]["episode_index"] == 0


def test_longrun_controller_stops_on_total_tokens_budget() -> None:
    cfg = {
        "long_running": {
            "enabled": True,
            "budgets": {"max_episodes": 5, "total_tokens": 10},
        }
    }
    controller = LongRunController(cfg)

    out = controller.run(
        lambda _idx: {
            "completed": False,
            "completion_reason": "not_done",
            "usage": {"total_tokens": 6, "cost_usd": 0.1},
        }
    )
    assert out["macro_summary"]["stop_reason"] == "total_tokens_budget_reached"


def test_longrun_controller_accumulates_rlm_episode_deltas() -> None:
    cfg = {
        "long_running": {
            "enabled": True,
            "budgets": {"max_episodes": 2},
        }
    }
    controller = LongRunController(cfg)

    def _runner(idx: int) -> Dict[str, Any]:
        return {
            "completed": False,
            "completion_reason": "not_done",
            "rlm": {
                "subcall_count": 1,
                "total_tokens": 20 + idx,
                "total_cost_usd": 0.05,
                "branch_event_count": 3,
                "lane_counts": {"tool_heavy": 1},
            },
        }

    out = controller.run(_runner)
    summary = out["macro_summary"]
    assert summary["episodes_run"] == 2
    assert summary["rlm_usage"]["subcalls_used"] == 2
    assert summary["rlm_usage"]["total_tokens_used"] == 41
    assert summary["rlm_usage"]["total_cost_usd_used"] == 0.1
    assert summary["rlm_usage"]["branch_event_count"] == 6
    assert summary["rlm_usage"]["lane_counts"]["tool_heavy"] == 2
    assert summary["episodes"][0]["rlm_delta"]["subcall_count"] == 1


def test_longrun_controller_stops_on_total_cost_budget() -> None:
    cfg = {
        "long_running": {
            "enabled": True,
            "budgets": {"max_episodes": 5, "total_cost_usd": 1.0},
        }
    }
    controller = LongRunController(cfg)

    out = controller.run(
        lambda _idx: {
            "completed": False,
            "completion_reason": "not_done",
            "usage": {"total_tokens": 1, "cost_usd": 0.6},
        }
    )
    assert out["macro_summary"]["stop_reason"] == "total_cost_budget_reached"
    assert out["macro_summary"]["budget_usage"]["total_cost_usd_used"] >= 1.0


def test_longrun_controller_resume_falls_back_to_checkpoint_pointer(tmp_path: Path) -> None:
    run_dir = tmp_path / "run"
    ckpt_dir = run_dir / "meta" / "checkpoints"
    ckpt_dir.mkdir(parents=True, exist_ok=True)
    checkpoint_rel = "meta/checkpoints/longrun_state_ep_3_end.json"
    (run_dir / checkpoint_rel).write_text(
        json.dumps(
            {
                "started_at": 50.0,
                "episode_index": 4,
                "episodes_run": 4,
                "budgets": {"episodes_used": 4, "total_tokens_used": 120, "total_cost_usd_used": 2.5},
            }
        ),
        encoding="utf-8",
    )
    (ckpt_dir / "latest_checkpoint.json").write_text(
        json.dumps({"path": checkpoint_rel, "episode": 3, "phase": "end"}),
        encoding="utf-8",
    )
    logger = _DiskLogger(run_dir)
    cfg = {"long_running": {"enabled": True, "resume": {"enabled": True}, "budgets": {"max_episodes": 6}}}
    controller = LongRunController(cfg, logger_v2=logger)
    out = controller.run(lambda _idx: {"completed": True, "completion_reason": "done"})
    assert out["macro_state"]["resumed_from_state"] is True
    assert out["macro_summary"]["episodes"][0]["episode_index"] == 4
    assert str(out["macro_state"]["resume_state_path"]).endswith("meta/checkpoints/longrun_state_ep_3_end.json")


def test_longrun_controller_resume_respects_pre_episode_budget_caps(tmp_path: Path) -> None:
    run_dir = tmp_path / "run"
    state_path = run_dir / "meta" / "longrun_state.json"
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(
        json.dumps(
            {
                "started_at": 100.0,
                "episode_index": 2,
                "episodes_run": 2,
                "budgets": {"episodes_used": 2, "total_tokens_used": 1000, "total_cost_usd_used": 10.0},
            }
        ),
        encoding="utf-8",
    )
    logger = _DiskLogger(run_dir)
    cfg = {
        "long_running": {
            "enabled": True,
            "resume": {"enabled": True},
            "budgets": {"max_episodes": 6, "total_tokens": 100},
        }
    }
    called = {"n": 0}

    def runner(_idx: int) -> Dict[str, Any]:
        called["n"] += 1
        return {"completed": True, "completion_reason": "done"}

    controller = LongRunController(cfg, logger_v2=logger)
    out = controller.run(runner)
    assert called["n"] == 0
    assert out["macro_summary"]["stop_reason"] == "total_tokens_budget_reached"
