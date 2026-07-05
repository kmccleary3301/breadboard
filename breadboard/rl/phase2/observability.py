from __future__ import annotations
from collections.abc import Mapping

import json

import statistics
from dataclasses import dataclass
from typing import Any


OBSERVABILITY_REPORT_ID = "bb_zyphra_rl_phase2_observability_v1"
OBSERVABILITY_CLAIM_BOUNDARY = "p2_m10_observability_contract_not_target_throughput_claim"


@dataclass(frozen=True)
class ObservabilityCaps:
    max_budget_usd: float
    max_gpu_hours: float
    max_queue_wait_seconds: float
    max_verifier_latency_ms: float
    max_failure_rate: float

    def to_dict(self) -> dict[str, float]:
        return {
            "max_budget_usd": self.max_budget_usd,
            "max_failure_rate": self.max_failure_rate,
            "max_gpu_hours": self.max_gpu_hours,
            "max_queue_wait_seconds": self.max_queue_wait_seconds,
            "max_verifier_latency_ms": self.max_verifier_latency_ms,
        }


@dataclass(frozen=True)
class ObservabilitySample:
    queue_wait_seconds: float
    gpu_utilization_percent: float
    tasks_completed: int
    elapsed_seconds: float
    verifier_latency_ms: float
    failure_class: str = "none"

    def to_dict(self) -> dict[str, Any]:
        return {
            "elapsed_seconds": self.elapsed_seconds,
            "failure_class": self.failure_class,
            "gpu_utilization_percent": self.gpu_utilization_percent,
            "queue_wait_seconds": self.queue_wait_seconds,
            "tasks_completed": self.tasks_completed,
            "verifier_latency_ms": self.verifier_latency_ms,
        }


def _percentile(values: list[float], percentile: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = min(len(ordered) - 1, int(round(percentile * (len(ordered) - 1))))
    return float(ordered[index])


def _failure_taxonomy(samples: list[ObservabilitySample]) -> dict[str, int]:
    taxonomy: dict[str, int] = {}
    for sample in samples:
        if sample.failure_class and sample.failure_class != "none":
            taxonomy[sample.failure_class] = taxonomy.get(sample.failure_class, 0) + 1
    return dict(sorted(taxonomy.items()))


def evaluate_observability_caps(
    *,
    caps: ObservabilityCaps,
    requested_budget_usd: float,
    projected_gpu_hours: float,
    samples: list[ObservabilitySample],
) -> dict[str, Any]:
    rejections: list[str] = []
    queue_waits = [sample.queue_wait_seconds for sample in samples]
    verifier_latencies = [sample.verifier_latency_ms for sample in samples]
    failure_count = sum(1 for sample in samples if sample.failure_class and sample.failure_class != "none")
    failure_rate = failure_count / len(samples) if samples else 0.0
    if requested_budget_usd > caps.max_budget_usd:
        rejections.append("requested_budget_usd exceeds max_budget_usd")
    if projected_gpu_hours > caps.max_gpu_hours:
        rejections.append("projected_gpu_hours exceeds max_gpu_hours")
    if queue_waits and max(queue_waits) > caps.max_queue_wait_seconds:
        rejections.append("queue_wait_seconds exceeds max_queue_wait_seconds")
    if verifier_latencies and max(verifier_latencies) > caps.max_verifier_latency_ms:
        rejections.append("verifier_latency_ms exceeds max_verifier_latency_ms")
    if failure_rate > caps.max_failure_rate:
        rejections.append("failure_rate exceeds max_failure_rate")
    return {
        "accepted": not rejections,
        "failure_rate": failure_rate,
        "projected_gpu_hours": projected_gpu_hours,
        "rejections": rejections,
        "requested_budget_usd": requested_budget_usd,
    }


def build_observability_report(
    *,
    run_id: str,
    target_run_id: str,
    caps: ObservabilityCaps,
    requested_budget_usd: float,
    projected_gpu_hours: float,
    samples: list[ObservabilitySample],
) -> dict[str, Any]:
    queue_waits = [sample.queue_wait_seconds for sample in samples]
    gpu_utils = [sample.gpu_utilization_percent for sample in samples]
    verifier_latencies = [sample.verifier_latency_ms for sample in samples]
    elapsed_seconds = sum(max(0.0, sample.elapsed_seconds) for sample in samples)
    tasks_completed = sum(max(0, sample.tasks_completed) for sample in samples)
    cap_evaluation = evaluate_observability_caps(
        caps=caps,
        requested_budget_usd=requested_budget_usd,
        projected_gpu_hours=projected_gpu_hours,
        samples=samples,
    )
    return {
        "budget_caps": caps.to_dict(),
        "cap_evaluation": cap_evaluation,
        "claim_boundary": OBSERVABILITY_CLAIM_BOUNDARY,
        "milestone_id": "P2-M10",
        "passed": cap_evaluation["accepted"],
        "failure_taxonomy": _failure_taxonomy(samples),
        "gpu_utilization": {
            "average_percent": float(statistics.fmean(gpu_utils)) if gpu_utils else 0.0,
            "peak_percent": max(gpu_utils) if gpu_utils else 0.0,
            "sample_count": len(gpu_utils),
        },
        "queue_wait": {
            "max_seconds": max(queue_waits) if queue_waits else 0.0,
            "p50_seconds": _percentile(queue_waits, 0.50),
            "p95_seconds": _percentile(queue_waits, 0.95),
        },
        "report_id": OBSERVABILITY_REPORT_ID,
        "run_id": run_id,
        "samples": [sample.to_dict() for sample in samples],
        "scorecard_update_allowed": False,
        "target_run_id": target_run_id,
        "task_throughput": {
            "elapsed_seconds": elapsed_seconds,
            "tasks_completed": tasks_completed,
            "tasks_per_second": (tasks_completed / elapsed_seconds) if elapsed_seconds else 0.0,
        },
        "verifier_latency": {
            "max_ms": max(verifier_latencies) if verifier_latencies else 0.0,
            "p50_ms": _percentile(verifier_latencies, 0.50),
            "p95_ms": _percentile(verifier_latencies, 0.95),
        },
    }


def validate_observability_report(report: Mapping[str, Any]) -> list[str]:
    errors: list[str] = []
    if report.get("report_id") != OBSERVABILITY_REPORT_ID:
        errors.append("report_id must be observability v1")
    if report.get("claim_boundary") != OBSERVABILITY_CLAIM_BOUNDARY:
        errors.append("claim_boundary must be observability boundary")
    if report.get("scorecard_update_allowed") is not False:
        errors.append("scorecard_update_allowed must be false")
    if not str(report.get("target_run_id") or ""):
        errors.append("target_run_id must be non-empty")
    cap_evaluation = report.get("cap_evaluation") if isinstance(report.get("cap_evaluation"), Mapping) else {}
    if "accepted" not in cap_evaluation:
        errors.append("cap_evaluation.accepted must be present")
    for section in ["queue_wait", "gpu_utilization", "task_throughput", "verifier_latency", "failure_taxonomy", "budget_caps"]:
        if section not in report:
            errors.append(f"{section} section must be present")
    return errors

def report_to_json(report: Mapping[str, Any]) -> str:
    return json.dumps(dict(report), sort_keys=True, separators=(",", ":")) + "\n"
