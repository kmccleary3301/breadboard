from __future__ import annotations

import json
import sys
import types
from pathlib import Path

import pytest

from breadboard.rl.phase2.bridge import build_verl_batch_from_projection_rows
from breadboard.rl.phase3.evidence import PHASE3_COMMAND_LOG_MANIFEST_SCHEMA, sha256_file
from breadboard.rl.phase3.trainer_live import Phase3TrainerRunSpec, build_phase3_dataproto, build_phase3_trainer_update_report, validate_phase3_trainer_update_report

TARGET = "20260623T000000Z-slurm-234555"


def row(task_id: str = "task", group_id: str | None = None, quarantined: bool = False) -> dict:
    payload = {
        "input_ids": [1, 2, 3],
        "prompt_ids": [1],
        "completion_ids": [2, 3],
        "attention_mask": [1, 1, 1],
        "loss_mask": [False, True, True],
        "assistant_mask": [False, True, True],
        "tool_action_mask": [False, False, False],
        "reward_mask": [False, True, True],
        "completion_logprobs": [-0.1, -0.2],
        "completion_logprob_status": "native_available",
        "policy_snapshot_id": "policy-1",
        "admission": {"quarantine_status": "quarantined" if quarantined else "clear", "row_status": "accepted", "trainable": True},
        "reward": {"scalar": 1.0},
        "rollout_id": f"roll-{task_id}",
        "trajectory_id": f"traj-{task_id}",
        "episode_id": f"ep-{task_id}",
        "task_id": task_id,
        "projection_manifest_id": "pm-1",
        "trainable_candidate": True,
    }
    if group_id:
        payload["group_id"] = group_id
    return payload


class FakeTensorDict(dict):
    def __init__(self, data, batch_size=None):
        super().__init__(data)
        self.batch_size = batch_size


class FakeDataProto:
    @classmethod
    def from_dict(cls, payload):
        obj = cls()
        obj.payload = payload
        return obj


@pytest.fixture(autouse=True)
def fake_verl_modules(monkeypatch):
    torch = types.SimpleNamespace(long="long", tensor=lambda values, **kwargs: {"values": values, "kwargs": kwargs})
    tensordict = types.ModuleType("tensordict")
    tensordict.TensorDict = FakeTensorDict
    verl = types.ModuleType("verl")
    protocol = types.ModuleType("verl.protocol")
    protocol.DataProto = FakeDataProto
    monkeypatch.setitem(sys.modules, "torch", torch)
    monkeypatch.setitem(sys.modules, "tensordict", tensordict)
    monkeypatch.setitem(sys.modules, "verl", verl)
    monkeypatch.setitem(sys.modules, "verl.protocol", protocol)


def test_dataproto_conversion_with_fake_verl() -> None:
    batch = build_verl_batch_from_projection_rows([row("a")], target_run_id=TARGET).to_dict()
    proto = build_phase3_dataproto(batch, device="cpu", require_grpo_uid=False)
    assert set(proto.payload["batch"].keys()) >= {"input_ids", "attention_mask", "responses", "response_mask", "token_level_rewards", "old_log_probs"}
    assert proto.payload["meta_info"]["target_run_id"] == TARGET


def test_grpo_single_uid_rejected() -> None:
    batch = build_verl_batch_from_projection_rows([row("a")], target_run_id=TARGET).to_dict()
    with pytest.raises(ValueError, match="GRPO uid groups"):
        build_phase3_dataproto(batch, device="cpu", require_grpo_uid=True)


def test_quarantined_row_rejected() -> None:
    with pytest.raises(ValueError, match="quarantined"):
        build_verl_batch_from_projection_rows([row("a", quarantined=True)], target_run_id=TARGET)


def _manifest(evidence_root: Path) -> dict:
    raw = evidence_root / "ZYPHRA" / "RL_PHASE_3" / "runs" / "command_logs" / "cmd.log"
    raw.parent.mkdir(parents=True)
    raw.write_text("ok")
    return {"schema_version": PHASE3_COMMAND_LOG_MANIFEST_SCHEMA, "target_run_id": TARGET, "commands": [{"command_id": "cmd", "argv": ["x"], "raw_log_path": "command_logs/cmd.log", "raw_log_sha256": sha256_file(raw), "slurm_job_id": "234555", "target_run_id": TARGET, "node": "n", "started_at": "a", "completed_at": "b", "exit_code": 0, "status": "passed"}]}


def test_checkpoint_hash_equality_rejected(tmp_path: Path) -> None:
    evidence = tmp_path / "docs_tmp"
    out = evidence / "ZYPHRA" / "RL_PHASE_3" / "runs" / "trainer"
    out.mkdir(parents=True)
    before = out / "before.pt"; after = out / "after.pt"; metrics = out / "metrics.json"
    before.write_text("same"); after.write_text("same"); metrics.write_text(json.dumps({"optimizer_step_count": 1, "device_count": 8, "weight_update_performed": True}))
    spec = Phase3TrainerRunSpec(TARGET, "verl_ppo", "model", out / "rows.json", out, 1)
    report = build_phase3_trainer_update_report(spec, command_log_manifest=_manifest(evidence), checkpoint_before=before, checkpoint_after=after, metrics_path=metrics)
    assert any("checkpoint" in error for error in validate_phase3_trainer_update_report(report))


def test_stale_command_log_hash_rejected(tmp_path: Path) -> None:
    evidence = tmp_path / "docs_tmp"
    manifest = _manifest(evidence)
    raw = evidence / "ZYPHRA" / "RL_PHASE_3" / "runs" / "command_logs" / "cmd.log"
    raw.write_text("changed")
    out = evidence / "ZYPHRA" / "RL_PHASE_3" / "runs" / "trainer"; out.mkdir(parents=True)
    before = out / "before.pt"; after = out / "after.pt"; metrics = out / "metrics.json"
    before.write_text("a"); after.write_text("b"); metrics.write_text(json.dumps({"optimizer_step_count": 1, "device_count": 8, "weight_update_performed": True}))
    report = build_phase3_trainer_update_report(Phase3TrainerRunSpec(TARGET, "verl_ppo", "model", out / "rows.json", out, 1), command_log_manifest=manifest, checkpoint_before=before, checkpoint_after=after, metrics_path=metrics)
    assert report["passed"] is False


def test_valid_ppo_grpo_reports_accept(tmp_path: Path) -> None:
    evidence = tmp_path / "docs_tmp"
    manifest = _manifest(evidence)
    out = evidence / "ZYPHRA" / "RL_PHASE_3" / "runs" / "trainer"; out.mkdir(parents=True)
    metrics = out / "metrics.json"; metrics.write_text(json.dumps({"optimizer_step_count": 1, "device_count": 8, "weight_update_performed": True, "loss_metrics": {"loss": 0.1}}))
    for backend in ("verl_ppo", "verl_grpo"):
        before = out / f"{backend}-before.pt"; after = out / f"{backend}-after.pt"
        before.write_text("a"); after.write_text("b")
        report = build_phase3_trainer_update_report(Phase3TrainerRunSpec(TARGET, backend, "model", out / "rows.json", out, 1), command_log_manifest=manifest, checkpoint_before=before, checkpoint_after=after, metrics_path=metrics)
        assert validate_phase3_trainer_update_report(report) == []
