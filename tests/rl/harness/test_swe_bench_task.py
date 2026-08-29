from __future__ import annotations

import json
from pathlib import Path

import pytest

from breadboard.rl.harness.swe_bench_task import (
    DATASET_REVISION,
    EVALUATOR_COMMIT,
    IMAGE_INDEX_DIGEST,
    IMAGE_LEAF_DIGEST,
    INSTANCE_ID,
    PINNED_SYMPY_20590,
    SweBenchTaskError,
    official_evaluator_command,
    prediction_jsonl,
    score_official_reports,
    verify_evaluator_installation,
    verify_image_tag_metadata,
)


def test_pinned_task_identity_contains_exact_public_authorities() -> None:
    identity = PINNED_SYMPY_20590.identity_dict()

    assert identity["instance_id"] == INSTANCE_ID
    assert identity["dataset_revision"] == DATASET_REVISION
    assert identity["evaluator_commit"] == EVALUATOR_COMMIT
    assert identity["image_leaf_digest"] == IMAGE_LEAF_DIGEST
    assert PINNED_SYMPY_20590.model_visible_task()["problem_statement"].startswith(
        "Symbol instances have __dict__"
    )
    assert identity["image_index_digest"] == IMAGE_INDEX_DIGEST
    assert PINNED_SYMPY_20590.identity_digest.startswith("sha256:")
    assert "patch" not in identity
    assert "test_patch" not in identity


def test_prediction_and_official_command_are_one_row_and_bounded(
    tmp_path: Path,
) -> None:
    prediction = json.loads(
        prediction_jsonl("diff --git a/a.py b/a.py\n", model_name="breadboard-e4")
    )
    assert prediction == {
        "instance_id": INSTANCE_ID,
        "model_name_or_path": "breadboard-e4",
        "model_patch": "diff --git a/a.py b/a.py\n",
    }

    command = official_evaluator_command(
        dataset_path=str(tmp_path / "verified.parquet"),
        predictions_path=str(tmp_path / "predictions.jsonl"),
        report_directory=str(tmp_path / "reports"),
        run_id="sympy20590",
    )
    assert command == (
        "swebench",
        "eval",
        str(tmp_path / "verified.parquet"),
        "--predictions",
        str(tmp_path / "predictions.jsonl"),
        "--run-id",
        "sympy20590",
        "--instance",
        INSTANCE_ID,
        "--split",
        "test",
        "--workers",
        "1",
        "--timeout",
        "1800",
        "--report-dir",
        str(tmp_path / "reports"),
    )


def test_image_and_evaluator_authorities_reject_mutable_or_wrong_inputs() -> None:
    verify_evaluator_installation(
        installed_version="5.0.1",
        source_commit=EVALUATOR_COMMIT,
    )
    verify_image_tag_metadata(
        {
            "digest": IMAGE_INDEX_DIGEST,
            "images": [
                {
                    "os": "linux",
                    "architecture": "amd64",
                    "digest": IMAGE_LEAF_DIGEST,
                }
            ],
        }
    )

    with pytest.raises(SweBenchTaskError, match="evaluator identity"):
        verify_evaluator_installation(
            installed_version="5.0.1",
            source_commit="0" * 40,
        )
    with pytest.raises(SweBenchTaskError, match="leaf digest"):
        verify_image_tag_metadata(
            {
                "digest": IMAGE_INDEX_DIGEST,
                "images": [
                    {
                        "os": "linux",
                        "architecture": "amd64",
                        "digest": "sha256:" + "0" * 64,
                    }
                ],
            }
        )


@pytest.mark.parametrize("resolved, expected_reward", [(False, 0.0), (True, 1.0)])
def test_reward_requires_consistent_official_reports(
    resolved: bool,
    expected_reward: float,
) -> None:
    aggregate = {
        "schema_version": 2,
        "total_instances": 1,
        "submitted_instances": 1,
        "completed_instances": 1,
        "resolved_instances": int(resolved),
        "unresolved_instances": int(not resolved),
        "submitted_ids": [INSTANCE_ID],
        "completed_ids": [INSTANCE_ID],
        "resolved_ids": [INSTANCE_ID] if resolved else [],
        "unresolved_ids": [] if resolved else [INSTANCE_ID],
        "infra_failure_instances": 0,
        "error_instances": 0,
        "error_ids": [],
    }
    instance = {
        "patch_is_None": False,
        "patch_exists": True,
        "patch_successfully_applied": True,
        "resolved": resolved,
        "infra_failure": False,
    }

    assert (
        score_official_reports(
            aggregate_report=aggregate,
            instance_report=instance,
        )
        == expected_reward
    )
    aggregate["resolved_instances"] = int(not resolved)
    with pytest.raises(SweBenchTaskError, match="disagree on resolution"):
        score_official_reports(
            aggregate_report=aggregate,
            instance_report=instance,
        )
    aggregate["resolved_instances"] = int(resolved)

    instance["infra_failure"] = True
    with pytest.raises(SweBenchTaskError, match="infrastructure failure"):
        score_official_reports(
            aggregate_report=aggregate,
            instance_report=instance,
        )
