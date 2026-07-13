from __future__ import annotations

import ipaddress
import os
import urllib.parse
from collections.abc import Mapping
from typing import Any
EXPECTED_METRIC_SOURCES = {
    "slurm_metrics": "slurm_sacct",
    "gpu_metrics": "rocm_smi",
    "verifier_metrics": "verifier_client",
    "service_metrics": "service_event_log",
    "object_store_metrics": "object_store",
    "scheduler_metrics": "scheduler_control",
}

LOCAL_OBJECT_STORE_BACKENDS = frozenset(
    {"LocalObjectStore", "target_workspace_local_object_store", "local_object_store"}
)

def endpoint_is_local(value: str | None) -> bool:
    if not value:
        return False
    text = value.strip()
    parsed = urllib.parse.urlsplit(text)
    if not parsed.hostname and "://" not in text:
        parsed = urllib.parse.urlsplit(f"//{text}")
    host = parsed.hostname
    if not host:
        return False
    normalized = host.lower().rstrip(".")
    if normalized in {"localhost", "localhost.localdomain"}:
        return True
    local_names = {
        str(os.environ.get("HOSTNAME") or "").lower().rstrip("."),
        str(os.environ.get("SLURMD_NODENAME") or "").lower().rstrip("."),
    }
    if normalized in local_names - {""}:
        return True
    try:
        return ipaddress.ip_address(normalized).is_loopback
    except ValueError:
        return False


def collect_slurm_metrics(job_id: str) -> dict[str, Any]:
    return {"source": "slurm_sacct", "job_id": job_id, "queue_wait_seconds": 0.0, "scheduler_retry_count": 0}


def collect_gpu_metrics() -> dict[str, Any]:
    return {"source": "rocm_smi", "gpu_utilization": []}


def collect_verifier_metrics() -> dict[str, Any]:
    return {"source": "verifier_client", "verifier_latency_seconds": []}


def _validate_source(name: str, section: Mapping[str, Any], errors: list[str]) -> None:
    expected = EXPECTED_METRIC_SOURCES[name]
    if section.get("source") != expected:
        errors.append(f"{name}.source must be {expected!r}")


def _has_metric_value(section: Mapping[str, Any], key: str) -> bool:
    value = section.get(key)
    return value is not None and value != "" and value != [] and value != {}


def _presence_flag(section: Mapping[str, Any], key: str) -> bool | None:
    if key not in section:
        return None
    value = section[key]
    if isinstance(value, Mapping):
        if "present" not in value:
            return None
        return value["present"] is True
    if isinstance(value, bool):
        return value
    return None


def _env_presence_flag(section: Mapping[str, Any], env_name: str) -> bool | None:
    env_presence = section.get("env_presence")
    if not isinstance(env_presence, Mapping):
        return None
    return _presence_flag(env_presence, env_name)


def _scheduler_presence(scheduler_metrics: Mapping[str, Any], keys: tuple[str, ...], env_name: str) -> bool:
    candidates: list[bool | None] = []
    control = scheduler_metrics.get("scheduler_control")
    for section in (scheduler_metrics, control):
        if not isinstance(section, Mapping):
            continue
        candidates.extend(_presence_flag(section, key) for key in keys)
        candidates.append(_env_presence_flag(section, env_name))
    return any(value is True for value in candidates)


def validate_scheduler_metrics_readiness(scheduler_metrics: Mapping[str, Any]) -> list[str]:
    errors: list[str] = []
    if not _has_metric_value(scheduler_metrics, "scheduler_control"):
        errors.append("scheduler_control_missing")
    if not _scheduler_presence(scheduler_metrics, ("endpoint_present", "base_url_present", "scheduler_base_url_present"), "BREADBOARD_SCHEDULER_BASE_URL"):
        errors.append("scheduler_control_endpoint_missing")
    if not _scheduler_presence(scheduler_metrics, ("token_present", "scheduler_token_present"), "BREADBOARD_SCHEDULER_TOKEN"):
        errors.append("scheduler_control_token_missing")
    return errors

def _object_store_backend(object_store_metrics: Mapping[str, Any]) -> str:
    return str(object_store_metrics.get("object_store") or object_store_metrics.get("backend") or "")



def _validate_live_semantics(
    *,
    slurm_metrics: Mapping[str, Any],
    gpu_metrics: Mapping[str, Any],
    verifier_metrics: Mapping[str, Any],
    service_metrics: Mapping[str, Any],
    object_store_metrics: Mapping[str, Any],
    scheduler_metrics: Mapping[str, Any],
    errors: list[str],
) -> None:
    if not _has_metric_value(slurm_metrics, "sacct_stdout"):
        errors.append("sacct_metrics_missing")
    if not _has_metric_value(gpu_metrics, "gpu_utilization"):
        errors.append("gpu_utilization_missing")
    if not _has_metric_value(service_metrics, "events") and not _has_metric_value(service_metrics, "task_throughput"):
        errors.append("service_event_metrics_missing")
    if not _has_metric_value(verifier_metrics, "verifier_latency_seconds"):
        errors.append("verifier_latency_seconds_missing")
    if endpoint_is_local(str(verifier_metrics.get("endpoint") or "")):
        errors.append("verifier_endpoint_is_local")
    object_store_backend = _object_store_backend(object_store_metrics)
    if not (
        object_store_backend
        and _has_metric_value(object_store_metrics, "object_store_writes")
        and _has_metric_value(object_store_metrics, "artifact_bytes")
    ):
        errors.append("object_store_metrics_missing")
    elif object_store_backend in LOCAL_OBJECT_STORE_BACKENDS:
        errors.append("production_object_store_endpoint_missing")
    if endpoint_is_local(str(object_store_metrics.get("endpoint") or "")):
        errors.append("production_object_store_endpoint_local")
    if scheduler_metrics:
        scheduler_control = scheduler_metrics.get("scheduler_control")
        if isinstance(scheduler_control, Mapping) and endpoint_is_local(str(scheduler_control.get("endpoint") or "")):
            errors.append("scheduler_control_endpoint_local")
        errors.extend(validate_scheduler_metrics_readiness(scheduler_metrics))


def build_live_observability_report(
    *,
    target_run_id: str,
    slurm_metrics: Mapping[str, Any],
    gpu_metrics: Mapping[str, Any],
    verifier_metrics: Mapping[str, Any],
    service_metrics: Mapping[str, Any],
    object_store_metrics: Mapping[str, Any],
    budget_caps: Mapping[str, Any],
    scheduler_metrics: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    errors: list[str] = []
    scheduler_metrics = scheduler_metrics or {}
    sections = {
        "slurm_metrics": slurm_metrics,
        "gpu_metrics": gpu_metrics,
        "verifier_metrics": verifier_metrics,
        "service_metrics": service_metrics,
        "object_store_metrics": object_store_metrics,
        "scheduler_metrics": scheduler_metrics,
    }
    for name, section in sections.items():
        _validate_source(name, section, errors)
    _validate_live_semantics(
        slurm_metrics=slurm_metrics,
        gpu_metrics=gpu_metrics,
        verifier_metrics=verifier_metrics,
        service_metrics=service_metrics,
        object_store_metrics=object_store_metrics,
        scheduler_metrics=scheduler_metrics,
        errors=errors,
    )
    remaining_usd = budget_caps.get("remaining_usd")
    if remaining_usd is None or remaining_usd == "":
        errors.append("budget_caps.remaining_usd_missing")
    else:
        try:
            remaining_usd_value = float(remaining_usd)
        except (TypeError, ValueError):
            errors.append("budget_caps.remaining_usd_invalid")
        else:
            if remaining_usd_value < 0:
                errors.append("budget caps exceeded")
    return {
        "schema_version": "bb.rl.phase3.live_observability.v1",
        "report_id": "phase3_live_observability",
        "claim_boundary": "phase3_live_observability_object_store_scheduler_scope",
        "target_run_id": target_run_id,
        "queue_wait": slurm_metrics.get("queue_wait_seconds"),
        "gpu_utilization": gpu_metrics.get("gpu_utilization"),
        "task_throughput": service_metrics.get("task_throughput"),
        "verifier_latency": verifier_metrics.get("verifier_latency_seconds"),
        "failure_taxonomy": service_metrics.get("failure_taxonomy", {}),
        "budget_caps": dict(budget_caps),
        "artifact_bytes": object_store_metrics.get("artifact_bytes"),
        "object_store_writes": object_store_metrics.get("object_store_writes"),
        "scheduler_retry_count": slurm_metrics.get("scheduler_retry_count"),
        "scheduler_control": scheduler_metrics.get("scheduler_control"),
        "metric_sections": {key: dict(value) for key, value in sections.items()},
        "errors": errors,
        "scorecard_update_allowed": False,
        "passed": not errors,
    }
