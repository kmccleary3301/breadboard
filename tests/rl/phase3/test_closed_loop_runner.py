from __future__ import annotations

import json
from pathlib import Path

import pytest

from breadboard.rl.phase3.rollout_runner import Phase3ClosedLoopSpec, run_phase3_closed_loop, validate_phase3_closed_loop_report

TARGET = "20260623T000000Z-slurm-234555"


def _spec(tmp_path: Path, rows: list[dict]) -> Phase3ClosedLoopSpec:
    manifest = tmp_path / "tasks.json"
    manifest.write_text(json.dumps({"rows": rows}))
    env = tmp_path / "env.tar"; env.write_text("env")
    return Phase3ClosedLoopSpec(TARGET, env, manifest, "policy-1", "verl_ppo", tmp_path / "out", 10)


def _row(task: str, status: str = "accepted", quarantine: str = "clear", replay: str | None = None, policy: str = "policy-1") -> dict:
    return {"task_id": task, "policy_snapshot_id": policy, "admission": {"row_status": status, "quarantine_status": quarantine}, "accepted_replay_ref": replay, "input_ids": [1, 2], "prompt_ids": [1], "completion_ids": [2], "attention_mask": [1, 1], "loss_mask": [False, True], "assistant_mask": [False, True], "tool_action_mask": [False, False], "reward_mask": [False, True], "completion_logprobs": [-0.1], "completion_logprob_status": "native_available", "reward": {"scalar": 1}, "rollout_id": task, "trajectory_id": task, "episode_id": task, "projection_manifest_id": "pm", "trainable_candidate": True}


def test_accepted_rejected_replay_closure(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr("breadboard.rl.phase3.rollout_runner.build_phase3_dataproto", lambda *a, **k: object())
    report = run_phase3_closed_loop(_spec(tmp_path, [_row("a", replay="replay/a.json"), _row("b", status="rejected")]))
    assert report["accepted_replay_refs"] == ["replay/a.json"]
    assert report["rejected_count"] == 1
    assert validate_phase3_closed_loop_report(report) == []


def test_quarantined_row_exclusion(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr("breadboard.rl.phase3.rollout_runner.build_phase3_dataproto", lambda batch, **k: (_ for _ in ()).throw(AssertionError("quarantined entered")) if any(r["task_id"] == "q" for r in batch["rows"]) else object())
    report = run_phase3_closed_loop(_spec(tmp_path, [_row("a", replay="replay/a.json"), _row("q", quarantine="quarantined")]))
    assert report["quarantined_count"] == 1
    assert report["passed"] is True


def test_trainer_update_failure_propagates(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr("breadboard.rl.phase3.rollout_runner.build_phase3_dataproto", lambda *a, **k: (_ for _ in ()).throw(ValueError("bad")))
    report = run_phase3_closed_loop(_spec(tmp_path, [_row("a", replay="replay/a.json")]))
    assert report["passed"] is False
    assert any("trainer update failed" in error for error in report["errors"])


def test_policy_snapshot_continuity(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr("breadboard.rl.phase3.rollout_runner.build_phase3_dataproto", lambda *a, **k: object())
    report = run_phase3_closed_loop(_spec(tmp_path, [_row("a", replay="replay/a.json", policy="wrong")]))
    assert report["passed"] is False
    assert any("policy snapshot" in error for error in report["errors"])
