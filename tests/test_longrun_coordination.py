from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List

from agentic_coder_prototype.longrun.controller import LongRunController


class _DiskLogger:
    def __init__(self, run_dir: Path) -> None:
        self.run_dir = str(run_dir)

    def write_json(self, rel_path: str, data: Dict[str, Any]) -> str:
        path = Path(self.run_dir) / rel_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(dict(data), indent=2), encoding="utf-8")
        return rel_path


def test_longrun_controller_emits_no_progress_coordination_trace() -> None:
    signals: List[Dict[str, Any]] = []
    reviews: List[Dict[str, Any]] = []
    directives: List[Dict[str, Any]] = []
    controller = LongRunController(
        {
            "long_running": {
                "enabled": True,
                "budgets": {"max_episodes": 10},
                "recovery": {"no_progress_max_episodes": 2},
            },
            "coordination": {
                "review": {
                    "no_progress_action": "checkpoint",
                }
            },
        },
        coordination_signal_emitter=lambda payload: signals.append(dict(payload)),
        coordination_review_verdict_emitter=lambda payload: reviews.append(dict(payload)),
        coordination_directive_emitter=lambda payload: directives.append(dict(payload)),
    )

    out = controller.run(lambda _idx: {"completed": False, "completion_reason": "not_done"})
    summary = out["macro_summary"]

    assert summary["stop_reason"] == "no_progress_threshold_reached"
    assert [item["code"] for item in signals] == ["no_progress"]
    assert [item["verdict_code"] for item in reviews] == ["checkpoint"]
    assert [item["directive_code"] for item in directives] == ["checkpoint"]
    assert summary["coordination"]["signals"][0]["payload"]["stall_reason"] == "no_progress_threshold_reached"
    assert len(summary["coordination"]["events"]) == 3


def test_longrun_controller_emits_retryable_failure_coordination_trace() -> None:
    signals: List[Dict[str, Any]] = []
    reviews: List[Dict[str, Any]] = []
    directives: List[Dict[str, Any]] = []
    calls = {"count": 0}
    controller = LongRunController(
        {
            "long_running": {
                "enabled": True,
                "budgets": {"max_episodes": 3, "max_retries_per_item": 2},
                "recovery": {
                    "backoff_base_seconds": 0.01,
                    "backoff_max_seconds": 0.01,
                    "backoff_disable_jitter": True,
                },
            },
            "coordination": {
                "review": {
                    "retryable_failure_action": "retry",
                }
            },
        },
        sleep_fn=lambda _seconds: None,
        coordination_signal_emitter=lambda payload: signals.append(dict(payload)),
        coordination_review_verdict_emitter=lambda payload: reviews.append(dict(payload)),
        coordination_directive_emitter=lambda payload: directives.append(dict(payload)),
    )

    def runner(_idx: int) -> Dict[str, Any]:
        calls["count"] += 1
        if calls["count"] == 1:
            raise RuntimeError("provider timeout")
        return {"completed": True, "completion_reason": "done_after_retry"}

    out = controller.run(runner)
    summary = out["macro_summary"]

    assert summary["stop_reason"] == "episode_completed"
    assert [item["code"] for item in signals] == ["retryable_failure"]
    assert [item["verdict_code"] for item in reviews] == ["retry"]
    assert [item["directive_code"] for item in directives] == ["retry"]
    assert signals[0]["payload"]["retry_basis"] == "provider_error_retry"


def test_longrun_controller_emits_reviewed_human_required_trace() -> None:
    signals: List[Dict[str, Any]] = []
    reviews: List[Dict[str, Any]] = []
    directives: List[Dict[str, Any]] = []
    controller = LongRunController(
        {"long_running": {"enabled": True, "budgets": {"max_episodes": 2}}},
        coordination_signal_emitter=lambda payload: signals.append(dict(payload)),
        coordination_review_verdict_emitter=lambda payload: reviews.append(dict(payload)),
        coordination_directive_emitter=lambda payload: directives.append(dict(payload)),
    )

    out = controller.run(
        lambda _idx: {
            "completed": False,
            "completion_reason": "human_required",
            "human_required": {
                "required_input": "operator_confirmation",
                "blocking_reason": "needs approval before continuing",
                "evidence_refs": ["artifact://meta/operator_note.json"],
            },
        }
    )

    summary = out["macro_summary"]
    assert summary["stop_reason"] == "human_required"
    assert [item["code"] for item in signals] == ["human_required"]
    assert [item["verdict_code"] for item in reviews] == ["human_required"]
    assert [item["directive_code"] for item in directives] == ["escalate"]


def test_longrun_controller_resume_restores_coordination_trace(tmp_path) -> None:
    logger = _DiskLogger(tmp_path)
    first = LongRunController(
        {
            "long_running": {
                "enabled": True,
                "resume": {"enabled": True},
                "budgets": {"max_episodes": 1},
            }
        },
        logger_v2=logger,
    )
    first.run(
        lambda _idx: {
            "completed": False,
            "completion_reason": "human_required",
            "human_required": {
                "required_input": "operator_confirmation",
                "blocking_reason": "needs approval before continuing",
                "evidence_refs": ["artifact://meta/operator_note.json"],
            },
        }
    )

    resumed = LongRunController(
        {
            "long_running": {
                "enabled": True,
                "resume": {"enabled": True},
                "budgets": {"max_episodes": 1},
            }
        },
        logger_v2=logger,
    )
    out = resumed.run(lambda _idx: {"completed": True, "completion_reason": "should_not_run"})
    summary = out["macro_summary"]

    assert summary["stop_reason"] == "max_episodes_reached"
    assert [item["code"] for item in summary["coordination"]["signals"]] == ["human_required"]
    assert [item["directive_code"] for item in summary["coordination"]["directives"]] == ["escalate"]
