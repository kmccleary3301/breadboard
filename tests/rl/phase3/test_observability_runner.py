from __future__ import annotations

import json
import subprocess
import sys
import pytest
from pathlib import Path

TARGET = "20260623T000000Z-slurm-234555"


def test_observability_runner_blocks_without_live_inputs(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("BREADBOARD_VERIFIER_BASE_URL", "https://controller-only.example/verifier")
    monkeypatch.setenv("BREADBOARD_VERIFIER_TOKEN", "controller-token")
    phase_dir = tmp_path / "docs_tmp" / "ZYPHRA" / "RL_PHASE_3"
    result = subprocess.run(
        [
            sys.executable,
            "scripts/rl_phase3/run_phase3_observability_scheduler_store.py",
            "--phase-dir",
            str(phase_dir),
            "--target-run-id",
            TARGET,
        ],
        cwd=Path(__file__).resolve().parents[3],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    assert result.returncode == 2
    report = json.loads((phase_dir / "runs" / "observability_scheduler_store_runner" / "P3-M11_observability_scheduler_store.json").read_text())
    assert report["passed"] is False
    assert "verifier_metrics" in report["blocked_reason"]
    assert report["required_artifact_keys"] == ["blocker_evidence"]
    assert Path(report["artifact_paths"]["blocker_evidence"]).exists()
    blocker = json.loads(Path(report["artifact_paths"]["blocker_evidence"]).read_text())
    assert blocker["missing_inputs"] == [
        "slurm_metrics",
        "gpu_metrics",
        "verifier_metrics",
        "service_metrics",
        "object_store_metrics",
        "scheduler_metrics",
        "budget_caps",
    ]
    assert blocker["controller_env_verifier_base_url_present"] is True
    assert blocker["controller_env_verifier_token_present"] is True
    assert "env_live_verifier_endpoint_present" not in blocker
    assert blocker["object_store_metrics_present"] is False
    assert blocker["production_object_store_endpoint_present"] is False


@pytest.mark.parametrize("backend", ["LocalObjectStore", "target_workspace_local_object_store", "local_object_store"])
def test_observability_runner_rejects_local_object_store(backend: str, tmp_path: Path) -> None:
    phase_dir = tmp_path / "docs_tmp" / "ZYPHRA" / "RL_PHASE_3"
    inputs = tmp_path / "inputs"
    inputs.mkdir()
    payloads = {
        "slurm": {"source": "slurm_sacct", "sacct_stdout": "ok", "queue_wait_seconds": 1, "scheduler_retry_count": 0},
        "gpu": {"source": "rocm_smi", "gpu_utilization": {"card0": {"GPU use (%)": "1"}}},
        "verifier": {"source": "verifier_client", "verifier_latency_seconds": [0.1]},
        "service": {"source": "service_event_log", "task_throughput": 1, "failure_taxonomy": {}},
        "object_store": {"source": "object_store", "object_store": backend, "object_store_writes": 1, "artifact_bytes": 1},
        "scheduler": {"source": "scheduler_control", "scheduler_control": {"endpoint_present": True, "token_present": True}},
        "budget": {"remaining_usd": 1},
    }
    paths = {}
    for name, payload in payloads.items():
        path = inputs / f"{name}.json"
        path.write_text(json.dumps(payload))
        paths[name] = path
    result = subprocess.run(
        [
            sys.executable,
            "scripts/rl_phase3/run_phase3_observability_scheduler_store.py",
            "--phase-dir",
            str(phase_dir),
            "--target-run-id",
            TARGET,
            "--slurm-metrics",
            str(paths["slurm"]),
            "--gpu-metrics",
            str(paths["gpu"]),
            "--verifier-metrics",
            str(paths["verifier"]),
            "--service-metrics",
            str(paths["service"]),
            "--object-store-metrics",
            str(paths["object_store"]),
            "--scheduler-metrics",
            str(paths["scheduler"]),
            "--budget-caps",
            str(paths["budget"]),
        ],
        cwd=Path(__file__).resolve().parents[3],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    assert result.returncode == 2
    report = json.loads((phase_dir / "runs" / "observability_scheduler_store_runner" / "P3-M11_observability_scheduler_store.json").read_text())
    assert report["passed"] is False
    assert "production_object_store_endpoint_missing" in report["blocked_reason"]
    assert "live_observability_report" in report["required_artifact_keys"]
    live = json.loads((phase_dir / "runs" / "observability_scheduler_store_runner" / "live_observability_report.json").read_text())
    assert live["passed"] is False
    assert "production_object_store_endpoint_missing" in live["errors"]


def test_observability_runner_rejects_empty_object_store_metrics(tmp_path: Path) -> None:
    phase_dir = tmp_path / "docs_tmp" / "ZYPHRA" / "RL_PHASE_3"
    inputs = tmp_path / "inputs"
    inputs.mkdir()
    payloads = {
        "slurm": {"source": "slurm_sacct", "sacct_stdout": "ok", "queue_wait_seconds": 1, "scheduler_retry_count": 0},
        "gpu": {"source": "rocm_smi", "gpu_utilization": {"card0": {"GPU use (%)": "1"}}},
        "verifier": {"source": "verifier_client", "verifier_latency_seconds": [0.1]},
        "service": {"source": "service_event_log", "task_throughput": 1, "failure_taxonomy": {}},
        "object_store": {},
        "scheduler": {"source": "scheduler_control", "scheduler_control": {"endpoint_present": True, "token_present": True}},
        "budget": {"remaining_usd": 1},
    }
    paths = {}
    for name, payload in payloads.items():
        path = inputs / f"{name}.json"
        path.write_text(json.dumps(payload))
        paths[name] = path
    result = subprocess.run(
        [
            sys.executable,
            "scripts/rl_phase3/run_phase3_observability_scheduler_store.py",
            "--phase-dir",
            str(phase_dir),
            "--target-run-id",
            TARGET,
            "--slurm-metrics",
            str(paths["slurm"]),
            "--gpu-metrics",
            str(paths["gpu"]),
            "--verifier-metrics",
            str(paths["verifier"]),
            "--service-metrics",
            str(paths["service"]),
            "--object-store-metrics",
            str(paths["object_store"]),
            "--scheduler-metrics",
            str(paths["scheduler"]),
            "--budget-caps",
            str(paths["budget"]),
        ],
        cwd=Path(__file__).resolve().parents[3],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    assert result.returncode == 2
    live = json.loads((phase_dir / "runs" / "observability_scheduler_store_runner" / "live_observability_report.json").read_text())
    assert live["passed"] is False
    assert "object_store_metrics_missing" in live["errors"]


def test_observability_runner_preserves_invalid_metric_sources(tmp_path: Path) -> None:
    phase_dir = tmp_path / "docs_tmp" / "ZYPHRA" / "RL_PHASE_3"
    inputs = tmp_path / "inputs"
    inputs.mkdir()
    payloads = {
        "slurm": {"source": "target_probe", "sacct_stdout": "ok", "queue_wait_seconds": 1, "scheduler_retry_count": 0},
        "gpu": {"source": "rocm_smi", "gpu_utilization": {"card0": {"GPU use (%)": "1"}}},
        "verifier": {"source": "verifier_client", "verifier_latency_seconds": [0.1]},
        "service": {"source": "service_event_log", "task_throughput": 1, "failure_taxonomy": {}},
        "object_store": {"source": "object_store", "object_store": "S3ObjectStore", "object_store_writes": 1, "artifact_bytes": 1},
        "scheduler": {"source": "scheduler_control", "scheduler_control": {"endpoint_present": True, "token_present": True}},
        "budget": {"remaining_usd": 1},
    }
    paths = {}
    for name, payload in payloads.items():
        path = inputs / f"{name}.json"
        path.write_text(json.dumps(payload))
        paths[name] = path
    result = subprocess.run(
        [
            sys.executable,
            "scripts/rl_phase3/run_phase3_observability_scheduler_store.py",
            "--phase-dir",
            str(phase_dir),
            "--target-run-id",
            TARGET,
            "--slurm-metrics",
            str(paths["slurm"]),
            "--gpu-metrics",
            str(paths["gpu"]),
            "--verifier-metrics",
            str(paths["verifier"]),
            "--service-metrics",
            str(paths["service"]),
            "--object-store-metrics",
            str(paths["object_store"]),
            "--scheduler-metrics",
            str(paths["scheduler"]),
            "--budget-caps",
            str(paths["budget"]),
        ],
        cwd=Path(__file__).resolve().parents[3],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    assert result.returncode == 2
    report_path = phase_dir / "runs" / "observability_scheduler_store_runner" / "P3-M11_observability_scheduler_store.json"
    report = json.loads(report_path.read_text())
    live = json.loads((phase_dir / "runs" / "observability_scheduler_store_runner" / "live_observability_report.json").read_text())
    assert live["passed"] is False
    assert "slurm_metrics.source must be 'slurm_sacct'" in ";".join(live["errors"])
    assert live["metric_sections"]["slurm_metrics"]["source"] == "target_probe"


def test_observability_runner_fills_only_missing_metric_sources() -> None:
    from breadboard.rl.phase3.evidence import normalize_phase3_metric_sources

    metrics = {
        "slurm": {"sacct_stdout": "ok"},
        "gpu": {"source": "target_probe", "gpu_utilization": {}},
    }

    normalized = normalize_phase3_metric_sources(metrics)

    assert normalized["slurm"]["source"] == "slurm_sacct"
    assert normalized["gpu"]["source"] == "target_probe"
    assert normalized["scheduler"]["source"] == "scheduler_control"

