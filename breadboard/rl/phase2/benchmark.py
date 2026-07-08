from __future__ import annotations

from dataclasses import dataclass, field
from hashlib import sha256
from typing import Any, Mapping


CLAIM_BOUNDARY = "p2_m5_named_benchmark_slice_not_general_benchmark_claim"
REQUIRED_CONTAMINATION_CONTROLS = (
    "source_hash_pin",
    "train_overlap_manifest",
    "prompt_solution_leakage_scan",
)


def source_sha256(payload: bytes | str) -> str:
    if isinstance(payload, str):
        payload = payload.encode("utf-8")
    return sha256(payload).hexdigest()


@dataclass(frozen=True)
class BenchmarkSourcePin:
    benchmark_id: str
    benchmark_version: str
    slice_id: str
    source_uri: str
    expected_source_sha256: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "benchmark_id": self.benchmark_id,
            "benchmark_version": self.benchmark_version,
            "slice_id": self.slice_id,
            "source_uri": self.source_uri,
            "expected_source_sha256": self.expected_source_sha256,
        }


@dataclass(frozen=True)
class BenchmarkSliceReport:
    report_id: str
    source_pin: BenchmarkSourcePin
    observed_source_sha256: str
    contamination_controls: list[str]
    failure_replay_refs: list[str]
    metrics: dict[str, Any]
    target_run_id: str
    status: str
    errors: list[str]
    claim_boundary: str = CLAIM_BOUNDARY
    scorecard_update_allowed: bool = False
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def accepted_for_claim(self) -> bool:
        return not self.errors and self.status == "benchmark_slice_fixture_accepted"

    def to_dict(self) -> dict[str, Any]:
        return {
            "report_id": self.report_id,
            "target_run_id": self.target_run_id,
            "claim_boundary": self.claim_boundary,
            "scorecard_update_allowed": self.scorecard_update_allowed,
            "passed": self.accepted_for_claim,
            "status": self.status,
            "accepted_for_claim": self.accepted_for_claim,
            "source_pin": self.source_pin.to_dict(),
            "observed_source_sha256": self.observed_source_sha256,
            "contamination_controls": list(self.contamination_controls),
            "failure_replay_refs": list(self.failure_replay_refs),
            "metrics": dict(self.metrics),
            "errors": list(self.errors),
            "metadata": dict(self.metadata),
        }


def build_benchmark_slice_report(
    source_pin: BenchmarkSourcePin,
    *,
    observed_source_sha256: str,
    contamination_controls: list[str],
    failure_replay_refs: list[str],
    metrics: Mapping[str, Any],
    target_run_id: str = "fixture-phase2-benchmark-slice",
) -> BenchmarkSliceReport:
    errors: list[str] = []
    if observed_source_sha256 != source_pin.expected_source_sha256:
        errors.append("source_hash_mismatch")
    missing_controls = [control for control in REQUIRED_CONTAMINATION_CONTROLS if control not in contamination_controls]
    for control in missing_controls:
        errors.append("missing_contamination_control:" + control)
    if not failure_replay_refs:
        errors.append("missing_failure_replay")

    if "source_hash_mismatch" in errors:
        status = "rejected_hash_mismatch"
    elif errors:
        status = "rejected_incomplete_controls"
    else:
        status = "benchmark_slice_fixture_accepted"

    return BenchmarkSliceReport(
        report_id="bb_zyphra_rl_phase2_benchmark_slice_v1",
        source_pin=source_pin,
        observed_source_sha256=observed_source_sha256,
        contamination_controls=list(contamination_controls),
        failure_replay_refs=list(failure_replay_refs),
        metrics=dict(metrics),
        target_run_id=target_run_id,
        status=status,
        errors=errors,
        metadata={
            "fixture_scope": "hash_pinned_benchmark_slice",
            "required_contamination_controls": list(REQUIRED_CONTAMINATION_CONTROLS),
        },
    )


def build_fixture_benchmark_source_pin() -> BenchmarkSourcePin:
    payload_hash = source_sha256("swe-rebench-v2.fixture.slice.001\n")
    return BenchmarkSourcePin(
        benchmark_id="swe-rebench-v2",
        benchmark_version="fixture-v2",
        slice_id="slice-001",
        source_uri="fixtures/benchmarks/swe-rebench-v2/slice-001.jsonl",
        expected_source_sha256=payload_hash,
    )
