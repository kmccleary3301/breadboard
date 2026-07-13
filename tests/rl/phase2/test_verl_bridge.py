from __future__ import annotations

from types import SimpleNamespace

import pytest

from breadboard.rl.phase2.bridge import (
    VERL_BATCH_CLAIM_BOUNDARY,
    build_verl_batch_from_projection_rows,
    build_verl_dataproto_like_payload,
    detect_verl_dataproto_api,
)
from breadboard.rl.phase2.trainer import build_trainer_dry_run_report, report_to_json


def _row(**overrides):
    row = {
        "rollout_id": "run.phase2",
        "trajectory_id": "run.phase2.task-1.trajectory",
        "episode_id": "run.phase2.task-1.episode",
        "task_id": "task-1",
        "split_id": "train",
        "env_package_id": "env.pkg",
        "env_package_hash": "sha256:env",
        "group_id": "group-a",
        "policy_snapshot_id": "policy-snapshot-001",
        "policy": {"policy_id": "policy-a", "policy_snapshot_id": "policy-snapshot-001"},
        "prompt_ids": [11, 12],
        "completion_ids": [21, 22],
        "input_ids": [11, 12, 21, 22],
        "attention_mask": [1, 1, 1, 1],
        "loss_mask": [False, False, True, True],
        "assistant_mask": [False, False, True, True],
        "tool_action_mask": [False, False, False, True],
        "reward_mask": [False, False, False, True],
        "completion_logprobs": [-0.25, -0.5],
        "completion_logprob_status": "native_available",
        "renderer": {"renderer_id": "renderer-a"},
        "reward": {"scalar": 1.0, "reward_vector": {"unit": 1.0}},
        "runtime": {"runtime_backend": "local"},
        "admission": {
            "row_status": "accepted",
            "quarantine_status": "clear",
            "trainable": True,
        },
        "projection_manifest_id": "projection-001",
        "trainable_candidate": True,
        "metadata": {"source": "unit"},
    }
    row.update(overrides)
    return row


def test_valid_projection_rows_build_verl_batch() -> None:
    batch = build_verl_batch_from_projection_rows([_row()], target_run_id="target-run-1")
    payload = batch.to_dict()

    assert payload["claim_boundary"] == VERL_BATCH_CLAIM_BOUNDARY
    assert payload["scorecard_update_allowed"] is False
    assert payload["target_run_id"] == "target-run-1"
    assert payload["policy_snapshot_id"] == "policy-snapshot-001"
    assert payload["tensor_shape_metadata"]["input_ids"]["shape"] == [1, 4]
    assert payload["tensor_shape_metadata"]["completion_logprobs"]["shape"] == [1, 2]
    assert payload["masks"]["reward_mask"] == [[False, False, False, True]]
    assert payload["logprobs"]["completion_logprobs"] == [[-0.25, -0.5]]
    assert payload["rewards"]["sequence_rewards"] == [1.0]
    assert payload["verl_dataproto_api"]["required"] is False
    dataproto_like = build_verl_dataproto_like_payload(batch)
    assert dataproto_like["batch"]["old_log_probs"] == [[-0.25, -0.5]]
    assert dataproto_like["non_tensor_batch"]["target_run_id"] == "target-run-1"
    assert dataproto_like["meta_info"]["scorecard_update_allowed"] is False


def test_bad_mask_lengths_are_rejected() -> None:
    row = _row(attention_mask=[1, 1, 1])

    with pytest.raises(ValueError, match="attention_mask length must equal input_ids length"):
        build_verl_batch_from_projection_rows([row], target_run_id="target-run-1")


def test_missing_policy_snapshot_is_rejected() -> None:
    row = _row(policy={"policy_id": "policy-a"})
    row.pop("policy_snapshot_id")

    with pytest.raises(ValueError, match="policy_snapshot_id must be present"):
        build_verl_batch_from_projection_rows([row], target_run_id="target-run-1")


def test_quarantined_rows_are_rejected() -> None:
    row = _row(
        admission={"row_status": "quarantined", "quarantine_status": "quarantined", "trainable": False},
        trainable_candidate=False,
    )

    with pytest.raises(ValueError, match="quarantined"):
        build_verl_batch_from_projection_rows([row], target_run_id="target-run-1")


def test_preserved_and_lost_field_ledger_is_recorded() -> None:
    batch = build_verl_batch_from_projection_rows(
        [_row(full_workspace_bytes="not materialized into trainer batch")],
        target_run_id="target-run-1",
    )
    ledger = batch.to_dict()["field_ledger"]

    assert "policy_snapshot_id" in ledger["preserved_fields"]
    assert "task_id" in ledger["preserved_fields"]
    assert "full_workspace_bytes" in ledger["lost_fields"]
    assert ledger["row_ledgers"][0]["lost_fields"] == ["full_workspace_bytes"]
    assert ledger["provenance_loss_detected"] is False


def test_optional_real_verl_dataproto_detection_does_not_require_dependency() -> None:
    def importer(module_name: str):
        if module_name == "verl.protocol":
            return SimpleNamespace(DataProto=object)
        raise ImportError(module_name)

    evidence = detect_verl_dataproto_api(importer)

    assert evidence["available"] is True
    assert evidence["required"] is False
    assert evidence["module"] == "verl.protocol"


def test_trainer_dry_run_report_records_modes_and_device_evidence() -> None:
    batch = build_verl_batch_from_projection_rows([_row()], target_run_id="target-run-1")

    no_update_report = build_trainer_dry_run_report(
        batch,
        target_run_id="target-run-1",
        mode="no_update",
        device="cpu",
    )
    one_step_report = build_trainer_dry_run_report(
        batch,
        target_run_id="target-run-1",
        mode="one_step",
        device="cpu",
    )

    assert no_update_report["scorecard_update_allowed"] is False
    assert no_update_report["mode"] == "no_update"
    assert no_update_report["dry_run_result"]["planned_step_count"] == 0
    assert one_step_report["mode"] == "one_step"
    assert one_step_report["dry_run_result"]["planned_step_count"] == 1
    assert one_step_report["target_run_id"] == "target-run-1"
    assert one_step_report["device_evidence"]["requested_device"] == "cpu"
    assert one_step_report["dry_run_result"]["weight_update_performed"] is False
    assert report_to_json(one_step_report).endswith("\n")


def test_trainer_dry_run_rejects_provenance_loss() -> None:
    batch_payload = build_verl_batch_from_projection_rows([_row()], target_run_id="target-run-1").to_dict()
    batch_payload["field_ledger"]["lost_fields"] = ["policy_snapshot_id"]
    batch_payload["field_ledger"]["critical_lost_fields"] = []
    batch_payload["field_ledger"]["provenance_loss_detected"] = False

    with pytest.raises(ValueError, match="rejects provenance loss"):
        build_trainer_dry_run_report(batch_payload, target_run_id="target-run-1")
