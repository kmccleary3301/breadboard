from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping


CLAIM_BOUNDARY = "p2_m4_scale_ladder_not_arbitrary_production_scale"
SCALE_LEVELS = (100, 250, 500, 1000)
FAILURE_TAXONOMY = (
    "scheduler",
    "verifier",
    "replay",
    "trainer_handoff",
    "infrastructure",
)


@dataclass(frozen=True)
class ScaleLevelMetrics:
    level: int
    attempted: int
    completed: int
    quarantined: int
    retry_count: int
    p95_latency_seconds: float
    throughput_tasks_per_minute: float
    scheduler_failures: int = 0
    verifier_failures: int = 0
    replay_failures: int = 0
    trainer_handoff_failures: int = 0
    infrastructure_failures: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "level": self.level,
            "attempted": self.attempted,
            "completed": self.completed,
            "quarantined": self.quarantined,
            "retry_count": self.retry_count,
            "p95_latency_seconds": self.p95_latency_seconds,
            "throughput_tasks_per_minute": self.throughput_tasks_per_minute,
            "scheduler_failures": self.scheduler_failures,
            "verifier_failures": self.verifier_failures,
            "replay_failures": self.replay_failures,
            "trainer_handoff_failures": self.trainer_handoff_failures,
            "infrastructure_failures": self.infrastructure_failures,
        }


@dataclass(frozen=True)
class ScaleLevelGate:
    level: int
    gate: str
    passed: bool
    observed: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "level": self.level,
            "gate": self.gate,
            "passed": self.passed,
            "observed": self.observed,
        }


@dataclass(frozen=True)
class ScaleLadderReport:
    report_id: str
    target_run_id: str
    levels: list[ScaleLevelMetrics]
    gates: list[ScaleLevelGate]
    failure_taxonomy: dict[str, int]
    claim_boundary: str = CLAIM_BOUNDARY
    scorecard_update_allowed: bool = False
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def overall_passed(self) -> bool:
        return all(gate.passed for gate in self.gates)

    def to_dict(self) -> dict[str, Any]:
        return {
            "report_id": self.report_id,
            "target_run_id": self.target_run_id,
            "claim_boundary": self.claim_boundary,
            "scorecard_update_allowed": self.scorecard_update_allowed,
            "passed": self.overall_passed,
            "overall_passed": self.overall_passed,
            "levels": [level.to_dict() for level in self.levels],
            "gates": [gate.to_dict() for gate in self.gates],
            "failure_taxonomy": dict(self.failure_taxonomy),
            "metadata": dict(self.metadata),
        }


def _metric_from_mapping(level: int, data: Mapping[str, Any]) -> ScaleLevelMetrics:
    return ScaleLevelMetrics(
        level=level,
        attempted=int(data.get("attempted", level)),
        completed=int(data.get("completed", 0)),
        quarantined=int(data.get("quarantined", 0)),
        retry_count=int(data.get("retry_count", 0)),
        p95_latency_seconds=float(data.get("p95_latency_seconds", 0.0)),
        throughput_tasks_per_minute=float(data.get("throughput_tasks_per_minute", 0.0)),
        scheduler_failures=int(data.get("scheduler_failures", 0)),
        verifier_failures=int(data.get("verifier_failures", 0)),
        replay_failures=int(data.get("replay_failures", 0)),
        trainer_handoff_failures=int(data.get("trainer_handoff_failures", 0)),
        infrastructure_failures=int(data.get("infrastructure_failures", 0)),
    )


def build_scale_level_gates(metrics: ScaleLevelMetrics) -> list[ScaleLevelGate]:
    closed = metrics.completed + metrics.quarantined
    return [
        ScaleLevelGate(
            level=metrics.level,
            gate="level_size_exact",
            passed=metrics.level == metrics.attempted,
            observed=f"attempted={metrics.attempted}",
        ),
        ScaleLevelGate(
            level=metrics.level,
            gate="all_attempts_accounted",
            passed=closed == metrics.attempted,
            observed=f"completed_plus_quarantined={closed}",
        ),
        ScaleLevelGate(
            level=metrics.level,
            gate="scheduler_resilience",
            passed=metrics.scheduler_failures == 0 and metrics.infrastructure_failures == 0,
            observed=f"scheduler={metrics.scheduler_failures},infrastructure={metrics.infrastructure_failures}",
        ),
        ScaleLevelGate(
            level=metrics.level,
            gate="replay_closure",
            passed=metrics.replay_failures == 0,
            observed=f"replay_failures={metrics.replay_failures}",
        ),
        ScaleLevelGate(
            level=metrics.level,
            gate="trainer_handoff_integrity",
            passed=metrics.trainer_handoff_failures == 0,
            observed=f"trainer_handoff_failures={metrics.trainer_handoff_failures}",
        ),
        ScaleLevelGate(
            level=metrics.level,
            gate="positive_resource_metrics",
            passed=metrics.p95_latency_seconds > 0.0 and metrics.throughput_tasks_per_minute > 0.0,
            observed=(
                f"p95_latency_seconds={metrics.p95_latency_seconds},"
                f"throughput_tasks_per_minute={metrics.throughput_tasks_per_minute}"
            ),
        ),
    ]


def build_scale_ladder_v2_report(
    fixture_metrics: Mapping[int, Mapping[str, Any]],
    *,
    target_run_id: str = "fixture-phase2-scale-ladder",
) -> ScaleLadderReport:
    levels = [_metric_from_mapping(level, fixture_metrics.get(level, {})) for level in SCALE_LEVELS]
    gates: list[ScaleLevelGate] = []
    for level_metrics in levels:
        gates.extend(build_scale_level_gates(level_metrics))
    failure_taxonomy = {
        "scheduler": sum(level.scheduler_failures for level in levels),
        "verifier": sum(level.verifier_failures for level in levels),
        "replay": sum(level.replay_failures for level in levels),
        "trainer_handoff": sum(level.trainer_handoff_failures for level in levels),
        "infrastructure": sum(level.infrastructure_failures for level in levels),
    }
    return ScaleLadderReport(
        report_id="bb_zyphra_rl_phase2_scale_ladder_v2",
        target_run_id=target_run_id,
        levels=levels,
        gates=gates,
        failure_taxonomy=failure_taxonomy,
        metadata={
            "fixture_scope": "deterministic_metrics_only",
            "levels": list(SCALE_LEVELS),
            "failure_taxonomy_keys": list(FAILURE_TAXONOMY),
        },
    )


def fixture_scale_metrics() -> dict[int, dict[str, Any]]:
    return {
        100: {
            "attempted": 100,
            "completed": 98,
            "quarantined": 2,
            "retry_count": 3,
            "p95_latency_seconds": 12.5,
            "throughput_tasks_per_minute": 24.0,
            "verifier_failures": 2,
        },
        250: {
            "attempted": 250,
            "completed": 244,
            "quarantined": 6,
            "retry_count": 8,
            "p95_latency_seconds": 18.0,
            "throughput_tasks_per_minute": 35.0,
            "verifier_failures": 6,
        },
        500: {
            "attempted": 500,
            "completed": 487,
            "quarantined": 13,
            "retry_count": 17,
            "p95_latency_seconds": 26.0,
            "throughput_tasks_per_minute": 44.0,
            "verifier_failures": 13,
        },
        1000: {
            "attempted": 1000,
            "completed": 971,
            "quarantined": 29,
            "retry_count": 41,
            "p95_latency_seconds": 41.0,
            "throughput_tasks_per_minute": 52.0,
            "verifier_failures": 29,
        },
    }
