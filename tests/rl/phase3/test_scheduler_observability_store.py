from __future__ import annotations

from pathlib import Path

import pytest

from breadboard.rl.phase3.object_store import LocalObjectStore
from breadboard.rl.phase3.observability_live import build_live_observability_report
from breadboard.rl.phase3.scheduler import parse_sbatch_job_id

TARGET = "20260623T000000Z-slurm-234555"

def _live_inputs(**overrides):
    inputs = {
        "target_run_id": TARGET,
        "slurm_metrics": {"source": "slurm_sacct", "sacct_stdout": "ok", "queue_wait_seconds": 0, "scheduler_retry_count": 0},
        "gpu_metrics": {"source": "rocm_smi", "gpu_utilization": {"card0": {"GPU use (%)": "1"}}},
        "verifier_metrics": {"source": "verifier_client", "verifier_latency_seconds": [0.1]},
        "service_metrics": {"source": "service_event_log", "task_throughput": 1},
        "object_store_metrics": {"source": "object_store", "object_store": "production_object_store", "object_store_writes": 1, "artifact_bytes": 1},
        "budget_caps": {"remaining_usd": 1},
        "scheduler_metrics": {
            "source": "scheduler_control",
            "scheduler_control": {"endpoint_present": True, "token_present": True, "status": "ready"},
            "env_presence": {
                "BREADBOARD_SCHEDULER_BASE_URL": True,
                "BREADBOARD_SCHEDULER_TOKEN": True,
            },
        },
    }
    inputs.update(overrides)
    return inputs


def test_slurm_parser_behavior() -> None:
    assert parse_sbatch_job_id("Submitted batch job 12345") == "12345"
    assert parse_sbatch_job_id("12345;gpu") == "12345"


def test_object_store_hash_validation(tmp_path: Path) -> None:
    source = tmp_path / "source.txt"; source.write_text("payload")
    store = LocalObjectStore(tmp_path / "store")
    stat = store.put_file(source, artifact_id="artifact", metadata={"kind": "test"})
    restored = store.get_file("artifact", tmp_path / "restored.txt")
    assert stat["sha256"] == store.stat("artifact")["sha256"]
    assert restored.read_text() == "payload"


def test_metric_source_rejection() -> None:
    report = build_live_observability_report(**_live_inputs(slurm_metrics={"source": "caller"}))
    assert report["passed"] is False
    assert any("source" in error for error in report["errors"])


def test_metric_source_rejects_valid_but_wrong_section_source() -> None:
    report = build_live_observability_report(
        **_live_inputs(slurm_metrics={"source": "rocm_smi", "sacct_stdout": "ok", "queue_wait_seconds": 0, "scheduler_retry_count": 0})
    )

    assert report["passed"] is False
    assert "slurm_metrics.source must be 'slurm_sacct'" in report["errors"]



def test_scheduler_metrics_requires_endpoint_and_token() -> None:
    report = build_live_observability_report(
        **_live_inputs(
            scheduler_metrics={
                "source": "scheduler_control",
                "scheduler_control": {"status": "connected"},
            }
        )
    )

    assert report["passed"] is False
    assert "scheduler_control_endpoint_missing" in report["errors"]
    assert "scheduler_control_token_missing" in report["errors"]

def test_budget_cap_enforcement() -> None:
    report = build_live_observability_report(**_live_inputs(budget_caps={"remaining_usd": -1}))
    assert report["passed"] is False
    assert any("budget" in error for error in report["errors"])


def test_budget_cap_requires_remaining_usd() -> None:
    report = build_live_observability_report(**_live_inputs(budget_caps={}))

    assert report["passed"] is False
    assert "budget_caps.remaining_usd_missing" in report["errors"]


def test_budget_cap_rejects_invalid_remaining_usd() -> None:
    report = build_live_observability_report(**_live_inputs(budget_caps={"remaining_usd": "not-a-number"}))

    assert report["passed"] is False
    assert "budget_caps.remaining_usd_invalid" in report["errors"]
