from __future__ import annotations

import argparse
import asyncio
import dataclasses
import hashlib
import os
import stat
import sys
from enum import Enum
from pathlib import Path
from typing import Any, Literal, Mapping, Protocol, runtime_checkable

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from breadboard_engine.compilation.contracts import (
    canonical_json_bytes,
    canonical_json_loads,
)
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from breadboard.rl.harness import contracts as c
from breadboard.rl.harness.evidence import canonical_digest
from breadboard.rl.harness.service import (
    BreadBoardV2EpisodeService,
    V2FaultInjectionAuthority,
    V2EpisodeAuditSpec,
    V2FaultClass,
    V2FaultInjectionSpec,
)
from breadboard.rl.phase5.f3_composition import (
    F3ProductionCompositionInput,
    build_f3_production_composition,
    load_f3_production_composition,
    sha256_bytes,
)
from breadboard.rl.phase5.f5_fault_campaign import (
    ENUMERATED_FAULT_NON_CLAIM,
    F5CampaignInput,
    F5CleanupObservation,
    F5ExecutionRow,
    F5ObservedOutcome,
    F5PinnedIdentity,
    F5TargetIdentityJoin,
)

_REPORT_NON_CLAIM = (
    "Target execution of the frozen F5 cases only; promotion and scorecard authority remain false, "
    "and no broader reliability or model-quality claim is made."
)


class F5TargetFaultsError(RuntimeError):
    pass


class _ExactModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


def _digest(value: str) -> str:
    if (
        type(value) is not str
        or len(value) != 71
        or not value.startswith("sha256:")
        or any(character not in "0123456789abcdef" for character in value[7:])
    ):
        raise ValueError("target execution requires a lowercase sha256 digest")
    return value


def _absolute(value: str) -> str:
    if (
        type(value) is not str
        or not value.startswith("/")
        or os.path.normpath(value) != value
    ):
        raise ValueError("target path must be absolute and normalized")
    return value


def _wire(value: Any) -> Any:
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json")
    if dataclasses.is_dataclass(value):
        return _wire(dataclasses.asdict(value))
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, Mapping):
        return {str(key): _wire(child) for key, child in value.items()}
    if isinstance(value, (list, tuple)):
        return [_wire(child) for child in value]
    return value


def _canonical_rows(campaign: F5CampaignInput) -> tuple[F5ExecutionRow, ...]:
    rows: list[F5ExecutionRow] = []
    controls = iter(campaign.concurrent_rows)
    for pair in campaign.fault_pairs:
        control = next(controls, None)
        if control is not None:
            rows.append(control)
        rows.extend((pair.twin, pair.fault))
    rows.extend(controls)
    return tuple(rows)


class F5TargetCaseInput(_ExactModel):
    case_id: str = Field(min_length=1, max_length=512)
    row_id: str = Field(min_length=1, max_length=512)
    request: c.ResolveEpisodeRequest
    task_input: dict[str, Any]
    context: dict[str, Any]
    model: c.ModelIdentity
    verifier_image_digest: str
    selection_record: F5PinnedIdentity

    _verifier_image = field_validator("verifier_image_digest")(_digest)

    @model_validator(mode="after")
    def closed_runner_request(self) -> "F5TargetCaseInput":
        if self.case_id != self.row_id:
            raise ValueError("case and campaign row IDs must be exact")
        reserved = {
            "f5_case_id": self.case_id,
            "f5_attempt_id": self.context.get("f5_attempt_id"),
            "f5_canary": self.context.get("f5_canary"),
            "f5_fault_injection_ref": self.context.get("f5_fault_injection_ref"),
        }
        if reserved["f5_attempt_id"] is None or reserved["f5_canary"] is None:
            raise ValueError(
                "target context must bind case attempt and canary identities"
            )
        canonical_json_bytes(self.task_input)
        canonical_json_bytes(self.context)
        return self


class F5TargetFaultsInput(_ExactModel):
    schema_version: Literal["bb.rl.phase5-f5-target-faults-input.v1"]
    composition: F3ProductionCompositionInput
    composition_output_dir: str
    report_path: str
    campaign: F5CampaignInput
    cases: tuple[F5TargetCaseInput, ...] = Field(min_length=18, max_length=48)

    _paths = field_validator("composition_output_dir", "report_path")(_absolute)

    @model_validator(mode="after")
    def exact_frozen_case_list(self) -> "F5TargetFaultsInput":
        rows = _canonical_rows(self.campaign)
        if tuple(case.row_id for case in self.cases) != tuple(
            row.row_id for row in rows
        ):
            raise ValueError(
                "target cases must exactly follow the frozen heterogeneous interleaving"
            )
        if (
            self.report_path == self.composition_output_dir
            or self.report_path.startswith(self.composition_output_dir + os.sep)
        ):
            raise ValueError(
                "target report must not be written into mutable composition output"
            )
        selection_digests: set[str] = set()
        effective_plan_digests: set[str] = set()
        for case, row in zip(self.cases, rows, strict=True):
            if case.request.episode_id != row.target.episode.episode_id:
                raise ValueError(f"case {case.case_id} does not bind its exact episode")
            if case.request.task.canonical_digest() != row.identities.task.digest:
                raise ValueError(f"case {case.case_id} task join drifted")
            if case.model.model_digest != row.identities.model.digest:
                raise ValueError(f"case {case.case_id} model join drifted")
            if case.context.get("f5_attempt_id") != row.target.attempt.attempt_id:
                raise ValueError(f"case {case.case_id} attempt context drifted")
            if case.context.get("f5_canary") != row.canary:
                raise ValueError(f"case {case.case_id} canary context drifted")
            expected_fault_ref = (
                None
                if row.fault_injection is None
                else row.fault_injection.injection_spec.immutable_ref
            )
            if case.context.get("f5_fault_injection_ref") != expected_fault_ref:
                raise ValueError(f"case {case.case_id} fault injection context drifted")
            if case.selection_record.digest in selection_digests:
                raise ValueError("target cases reuse a selection record receipt")
            selection_digests.add(case.selection_record.digest)
            if row.identities.effective_plan.digest in effective_plan_digests:
                raise ValueError("target cases reuse an effective plan identity")
            effective_plan_digests.add(row.identities.effective_plan.digest)
        return self


class F5TargetCaseObservation(_ExactModel):
    case_id: str = Field(min_length=1, max_length=512)
    attempt_id: str = Field(min_length=1, max_length=512)
    episode_id: str = Field(min_length=1, max_length=512)
    selection_record: F5PinnedIdentity
    compiled_receipt_digest: str
    semantic_config_digest: str
    task_digest: str
    model_digest: str
    tokenizer_digest: str
    checkpoint_digest: str
    primary_image_digest: str
    verifier_image_digest: str
    verifier_digest: str
    join: F5TargetIdentityJoin
    observed: F5ObservedOutcome
    cleanup: F5CleanupObservation
    canary_reads: tuple[str, ...]
    cross_episode_reads: tuple[str, ...] = ()
    episode_output: F5PinnedIdentity
    evidence: F5PinnedIdentity
    fallback_used: Literal[False]
    terminal_state: Literal["closed"]

    _digests = field_validator(
        "compiled_receipt_digest",
        "semantic_config_digest",
        "task_digest",
        "model_digest",
        "tokenizer_digest",
        "checkpoint_digest",
        "primary_image_digest",
        "verifier_image_digest",
        "verifier_digest",
    )(_digest)


class F5TargetCaseReport(_ExactModel):
    case_id: str
    row_id: str
    fault_class: str | None
    attempt: dict[str, Any]
    episode: dict[str, Any]
    selection: dict[str, Any]
    joins: dict[str, Any]
    expected: dict[str, Any]
    observed: dict[str, Any]
    cleanup: dict[str, Any]
    authority_isolation: dict[str, Any]
    evidence: dict[str, Any]

    @model_validator(mode="after")
    def successful_case_proof(self) -> "F5TargetCaseReport":
        if self.expected != self.observed:
            raise ValueError(
                "target case terminal disposition differs from expectation"
            )
        if (
            self.selection.get("fresh") is not True
            or self.selection.get("fallback_used") is not False
        ):
            raise ValueError(
                "target case selection receipt is stale, reused, or fallback-selected"
            )
        if self.joins.get("exact") is not True:
            raise ValueError("target case has a mismatched identity join")
        if self.cleanup.get("no_orphan") is not True:
            raise ValueError(
                "target case cleanup proof contains an orphan or cleanup failure"
            )
        if self.authority_isolation.get("cross_episode_reads") != []:
            raise ValueError("target case leaked cross-episode authority")
        return self


class F5TargetFaultsReport(_ExactModel):
    schema_version: Literal["bb.rl.phase5-f5-target-faults-report.v1"]
    campaign_id: str
    input_digest: str
    case_order: tuple[str, ...]
    cases: tuple[F5TargetCaseReport, ...]
    summary: dict[str, Any]
    promotion_authority: Literal[False]
    scorecard_authority: Literal[False]
    enumerated_fault_non_claim: Literal[
        "Enumerated timeout, cancel, revocation, egress, resource, verifier, artifact, and transport fault containment only; no general high-availability claim."
    ]
    report_non_claim: Literal[
        "Target execution of the frozen F5 cases only; promotion and scorecard authority remain false, and no broader reliability or model-quality claim is made."
    ]

    _input_digest = field_validator("input_digest")(_digest)

    @model_validator(mode="after")
    def complete_target_proof(self) -> "F5TargetFaultsReport":
        if self.case_order != tuple(case.case_id for case in self.cases):
            raise ValueError("target report case order drifted")
        if len(set(self.case_order)) != len(self.case_order):
            raise ValueError("target report repeats a case")
        expected_summary = {
            "case_count": len(self.cases),
            "succeeded_count": sum(
                case.observed.get("lifecycle") == "succeeded" for case in self.cases
            ),
            "failed_count": sum(
                case.observed.get("lifecycle") == "failed" for case in self.cases
            ),
            "fresh_selection_receipts": True,
            "exact_identity_joins": True,
            "zero_cross_episode_authority_leakage": True,
            "cleanup_complete": True,
            "unexpected_outcomes": [],
        }
        if self.summary != expected_summary:
            raise ValueError("target report summary is not the exact case projection")
        return self


class F5TargetComponentEnvelope(_ExactModel):
    schema_version: Literal["bb.rl.phase5-f5-target-component-report.v1"]
    report_id: str
    component: Literal["rl_phase5_f5_target_faults"]
    passed: Literal[True]
    permanent_non_authority: Literal[True]
    promotion_authority: Literal[False]
    scorecard_authority: Literal[False]
    scorecard_update_allowed: Literal[False]
    report_sha256: str
    report_path: str
    summary: dict[str, Any]

    _report_digest = field_validator("report_sha256")(_digest)
    _report_path = field_validator("report_path")(_absolute)

    @model_validator(mode="after")
    def exact_component_summary(self) -> "F5TargetComponentEnvelope":
        if self.summary.get("unexpected_outcomes") != []:
            raise ValueError(
                "passing F5 component envelope contains an unexpected outcome"
            )
        if (
            self.summary.get("fresh_selection_receipts") is not True
            or self.summary.get("exact_identity_joins") is not True
            or self.summary.get("zero_cross_episode_authority_leakage") is not True
            or self.summary.get("cleanup_complete") is not True
        ):
            raise ValueError(
                "passing F5 component envelope omits a required target gate"
            )
        return self


def _component_envelope(
    report: F5TargetFaultsReport, report_path: str
) -> F5TargetComponentEnvelope:
    if type(report) is not F5TargetFaultsReport:
        raise TypeError("report must be an exact F5TargetFaultsReport")
    normalized_path = _absolute(os.fspath(Path(report_path).resolve()))
    report_raw = canonical_json_bytes(report.model_dump(mode="json"))
    persisted_raw = Path(normalized_path).read_bytes()
    if persisted_raw != report_raw:
        raise F5TargetFaultsError(
            "persisted F5 target report differs from the strict report"
        )
    return F5TargetComponentEnvelope(
        schema_version="bb.rl.phase5-f5-target-component-report.v1",
        report_id=f"f5-target-faults-{report.campaign_id}",
        component="rl_phase5_f5_target_faults",
        passed=True,
        permanent_non_authority=True,
        promotion_authority=False,
        scorecard_authority=False,
        scorecard_update_allowed=False,
        report_sha256=sha256_bytes(report_raw),
        report_path=normalized_path,
        summary=canonical_json_loads(canonical_json_bytes(report.summary)),
    )


def _component_report_line(report: F5TargetFaultsReport, report_path: str) -> bytes:
    envelope = _component_envelope(report, report_path)
    return (
        b"PHASE3_COMPONENT_REPORT_JSON="
        + canonical_json_bytes(envelope.model_dump(mode="json"))
        + b"\n"
    )


@runtime_checkable
class F5TargetRuntime(Protocol):
    async def start(self) -> None: ...
    async def execute_case(
        self, case: F5TargetCaseInput, row: F5ExecutionRow
    ) -> F5TargetCaseObservation: ...
    async def close(self) -> None: ...


def _case_report(
    case: F5TargetCaseInput,
    row: F5ExecutionRow,
    observation: F5TargetCaseObservation,
    seen_selection_records: set[str],
) -> F5TargetCaseReport:
    if observation.case_id != case.case_id:
        raise F5TargetFaultsError(f"case {case.case_id} observation case ID mismatch")
    if observation.attempt_id != row.target.attempt.attempt_id:
        raise F5TargetFaultsError(
            f"case {case.case_id} observation attempt ID mismatch"
        )
    if observation.episode_id != row.target.episode.episode_id:
        raise F5TargetFaultsError(
            f"case {case.case_id} observation episode ID mismatch"
        )
    if observation.selection_record != case.selection_record:
        raise F5TargetFaultsError(f"case {case.case_id} selection receipt mismatch")
    if observation.selection_record.digest in seen_selection_records:
        raise F5TargetFaultsError(
            f"case {case.case_id} reused a stale selection receipt"
        )
    seen_selection_records.add(observation.selection_record.digest)

    exact_model = (
        observation.model_digest,
        observation.tokenizer_digest,
        observation.checkpoint_digest,
    ) == (
        case.model.model_digest,
        case.model.tokenizer_digest,
        case.model.checkpoint_digest,
    )
    exact_join = (
        observation.semantic_config_digest == row.identities.config.digest
        and observation.task_digest == row.identities.task.digest
        and observation.primary_image_digest == row.identities.image.digest
        and observation.verifier_image_digest == case.verifier_image_digest
        and observation.verifier_digest == row.identities.verifier.digest
        and observation.join == row.target.join
        and exact_model
    )
    if not exact_join:
        raise F5TargetFaultsError(
            f"case {case.case_id} exact task/model/checkpoint/verifier/image join mismatch"
        )
    expected = {
        **row.expected.model_dump(mode="json"),
        "unexpected_outcomes": [],
    }
    observed = observation.observed.model_dump(mode="json")
    if observed != expected or observed != row.observed.model_dump(mode="json"):
        raise F5TargetFaultsError(
            f"case {case.case_id} had an unexpected success or failure"
        )
    if observation.cleanup != row.cleanup:
        raise F5TargetFaultsError(f"case {case.case_id} cleanup observation mismatch")
    if observation.canary_reads != row.canary_reads:
        raise F5TargetFaultsError(f"case {case.case_id} crossed episode authority")
    if observation.cross_episode_reads:
        raise F5TargetFaultsError(f"case {case.case_id} crossed episode authority")
    if (
        observation.episode_output != row.target.episode.output
        or observation.evidence != row.target.evidence
    ):
        raise F5TargetFaultsError(
            f"case {case.case_id} target output/evidence join mismatch"
        )
    if observation.fallback_used or observation.terminal_state != "closed":
        raise F5TargetFaultsError(f"case {case.case_id} used fallback or did not close")

    cleanup = observation.cleanup.model_dump(mode="json")
    return F5TargetCaseReport(
        case_id=case.case_id,
        row_id=row.row_id,
        fault_class=None
        if row.fault_injection is None
        else row.fault_injection.fault_class,
        attempt=row.target.attempt.model_dump(mode="json"),
        episode={
            **row.target.episode.model_dump(mode="json"),
            "observed_terminal_state": observation.terminal_state,
        },
        selection={
            "selection_record": observation.selection_record.model_dump(mode="json"),
            "compiled_receipt_digest": observation.compiled_receipt_digest,
            "semantic_config_digest": observation.semantic_config_digest,
            "fresh": True,
            "fallback_used": observation.fallback_used,
        },
        joins={
            "expected": row.target.join.model_dump(mode="json"),
            "observed": observation.join.model_dump(mode="json"),
            "model": case.model.model_dump(mode="json"),
            "primary_image_digest": observation.primary_image_digest,
            "verifier_image_digest": observation.verifier_image_digest,
            "exact": True,
        },
        expected=expected,
        observed=observed,
        cleanup={
            **cleanup,
            "no_orphan": not cleanup["remaining_orphan_ids"]
            and not cleanup["cleanup_error_classes"],
        },
        authority_isolation={
            "canary": row.canary,
            "canary_reads": list(observation.canary_reads),
            "cross_episode_reads": list(observation.cross_episode_reads),
        },
        evidence={
            "target": observation.evidence.model_dump(mode="json"),
            "episode_output": observation.episode_output.model_dump(mode="json"),
        },
    )


async def _execute_concurrent_cohort(
    runtime: F5TargetRuntime,
    cohort: tuple[tuple[int, F5TargetCaseInput, F5ExecutionRow], ...],
) -> dict[int, F5TargetCaseObservation]:
    if not cohort:
        return {}
    release = asyncio.Event()
    ready = 0
    ready_lock = asyncio.Lock()
    results: dict[int, F5TargetCaseObservation] = {}

    async def execute(index: int, case: F5TargetCaseInput, row: F5ExecutionRow) -> None:
        nonlocal ready
        async with ready_lock:
            ready += 1
            if ready == len(cohort):
                release.set()
        await release.wait()
        observation = await runtime.execute_case(case, row)
        if type(observation) is not F5TargetCaseObservation:
            raise TypeError(
                "target runtime must return exact F5TargetCaseObservation values"
            )
        results[index] = observation

    try:
        async with asyncio.TaskGroup() as group:
            for index, case, row in cohort:
                group.create_task(execute(index, case, row))
    except BaseExceptionGroup as exc:
        raise F5TargetFaultsError(
            "concurrent F5 cohort produced an unexplained exception or stale drop"
        ) from exc
    if set(results) != {index for index, _, _ in cohort}:
        raise F5TargetFaultsError("concurrent F5 cohort omitted a required result")
    return results


async def _execute_f5_target_faults(
    spec: F5TargetFaultsInput,
    *,
    input_digest: str,
    runtime: F5TargetRuntime,
) -> F5TargetFaultsReport:
    if not isinstance(runtime, F5TargetRuntime):
        raise TypeError("runtime must implement the exact F5 target lifecycle seam")
    rows = _canonical_rows(spec.campaign)
    observations: list[F5TargetCaseObservation | None] = [None] * len(rows)
    concurrent_ids = {row.row_id for row in spec.campaign.concurrent_rows}
    cohort = tuple(
        (index, case, row)
        for index, (case, row) in enumerate(zip(spec.cases, rows, strict=True))
        if row.row_id in concurrent_ids
    )
    await runtime.start()
    try:
        for index, observation in (
            await _execute_concurrent_cohort(runtime, cohort)
        ).items():
            observations[index] = observation
        for index, (case, row) in enumerate(zip(spec.cases, rows, strict=True)):
            if row.row_id in concurrent_ids:
                continue
            observation = await runtime.execute_case(case, row)
            if type(observation) is not F5TargetCaseObservation:
                raise TypeError(
                    "target runtime must return exact F5TargetCaseObservation values"
                )
            observations[index] = observation
    finally:
        await runtime.close()
    if any(observation is None for observation in observations):
        raise F5TargetFaultsError("F5 execution omitted a required case result")
    reports: list[F5TargetCaseReport] = []
    seen_selection_records: set[str] = set()
    for case, row, observation in zip(spec.cases, rows, observations, strict=True):
        if type(observation) is not F5TargetCaseObservation:
            raise F5TargetFaultsError("F5 execution returned a missing case result")
        reports.append(_case_report(case, row, observation, seen_selection_records))

    report = F5TargetFaultsReport(
        schema_version="bb.rl.phase5-f5-target-faults-report.v1",
        campaign_id=spec.campaign.campaign_id,
        input_digest=input_digest,
        case_order=tuple(case.case_id for case in spec.cases),
        cases=tuple(reports),
        summary={
            "case_count": len(reports),
            "succeeded_count": sum(
                case.observed["lifecycle"] == "succeeded" for case in reports
            ),
            "failed_count": sum(
                case.observed["lifecycle"] == "failed" for case in reports
            ),
            "fresh_selection_receipts": True,
            "exact_identity_joins": True,
            "zero_cross_episode_authority_leakage": True,
            "cleanup_complete": True,
            "unexpected_outcomes": [],
        },
        promotion_authority=False,
        scorecard_authority=False,
        enumerated_fault_non_claim=ENUMERATED_FAULT_NON_CLAIM,
        report_non_claim=_REPORT_NON_CLAIM,
    )
    raw = canonical_json_bytes(report.model_dump(mode="json"))
    output = Path(spec.report_path)
    descriptor = os.open(
        output,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_CLOEXEC", 0),
        0o440,
    )
    try:
        view = memoryview(raw)
        while view:
            written = os.write(descriptor, view)
            if written <= 0:
                raise OSError("short F5 target report write")
            view = view[written:]
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    return report


def run_f5_target_faults(
    spec: F5TargetFaultsInput,
    *,
    input_digest: str,
    runtime: F5TargetRuntime,
) -> F5TargetFaultsReport:
    if type(spec) is not F5TargetFaultsInput:
        raise TypeError("spec must be an exact F5TargetFaultsInput")
    _digest(input_digest)
    return asyncio.run(
        _execute_f5_target_faults(spec, input_digest=input_digest, runtime=runtime)
    )


@dataclasses.dataclass(frozen=True, slots=True)
class _F5CaseFaultAuthority:
    case_id: str
    episode_id: str
    request_digest: str
    row_digest: str
    fault_injection: V2FaultInjectionSpec | None
    episode_audit: V2EpisodeAuditSpec


def _case_fault_authority(
    case: F5TargetCaseInput, row: F5ExecutionRow
) -> _F5CaseFaultAuthority:
    if (
        case.case_id != row.row_id
        or case.request.episode_id != row.target.episode.episode_id
    ):
        raise F5TargetFaultsError(
            f"case {case.case_id} cannot bind mismatched fault authority"
        )
    fault = row.fault_injection
    fault_spec = (
        None
        if fault is None
        else V2FaultInjectionSpec(
            episode_id=case.request.episode_id,
            immutable_ref=fault.injection_spec.immutable_ref,
            fault_class=V2FaultClass(fault.fault_class),
        )
    )
    audit_spec = V2EpisodeAuditSpec(
        episode_id=case.request.episode_id,
        authority_ref=row.identities.authority.immutable_ref,
        canary=row.canary,
    )
    if fault_spec is not None and (
        row.expected.error_class != fault_spec.error_code
        or row.expected.failure_boundary != fault_spec.boundary.value
        or row.expected.lease_opened != (fault_spec.boundary.value == "post-allocation")
    ):
        raise F5TargetFaultsError(
            f"case {case.case_id} fault authority outcome drifted"
        )
    return _F5CaseFaultAuthority(
        case_id=case.case_id,
        episode_id=case.request.episode_id,
        request_digest=sha256_bytes(
            canonical_json_bytes(case.request.model_dump(mode="json"))
        ),
        row_digest=sha256_bytes(canonical_json_bytes(row.model_dump(mode="json"))),
        fault_injection=fault_spec,
        episode_audit=audit_spec,
    )


def _service_fault_authority(
    spec: F5TargetFaultsInput,
) -> V2FaultInjectionAuthority:
    rows = _canonical_rows(spec.campaign)
    case_authorities = tuple(
        _case_fault_authority(case, row)
        for case, row in zip(spec.cases, rows, strict=True)
    )
    source_digest = sha256_bytes(canonical_json_bytes(spec.model_dump(mode="json")))
    return V2FaultInjectionAuthority(
        source_ref=f"cas://f5-target/input@{source_digest}",
        fault_specs=tuple(
            authority.fault_injection
            for authority in case_authorities
            if authority.fault_injection is not None
        ),
        audit_specs=tuple(authority.episode_audit for authority in case_authorities),
    )


@dataclasses.dataclass(frozen=True, slots=True)
class _F5CleanupResidueSnapshot:
    actors: tuple[str, ...]
    processes: tuple[str, ...]
    containers: tuple[str, ...]
    cgroups: tuple[str, ...]
    mounts: tuple[str, ...]
    workspaces: tuple[str, ...]
    secret_files: tuple[str, ...]
    sockets: tuple[str, ...]
    lease_records: tuple[str, ...]
    quarantines: tuple[str, ...]


def _bounded_residue_paths(root: Path) -> tuple[Path, ...]:
    if not root.exists():
        return ()
    selected: list[Path] = []
    for current_root, directory_names, file_names in os.walk(
        root, topdown=True, followlinks=False
    ):
        directory_names[:] = sorted(directory_names)[:256]
        for name in (*directory_names, *sorted(file_names)):
            selected.append(Path(current_root) / name)
            if len(selected) > 2048:
                raise F5TargetFaultsError(
                    "cleanup residue probe exceeded its traversal bound"
                )
    return tuple(selected)


def _cleanup_residue_snapshot(
    coordinator: Any | None,
    sandbox_runtime: Any,
    lease_id: str | None,
    receipt: Mapping[str, Any] | None,
) -> _F5CleanupResidueSnapshot:
    lease = None if coordinator is None else getattr(coordinator, "lease", None)
    live_leases = getattr(sandbox_runtime, "_leases", {})
    actors = (
        ()
        if lease_id is None or lease_id not in live_leases
        else (f"actor:{lease_id}",)
    )

    runtime = None if lease is None else getattr(lease, "_runtime", None)
    live_groups: list[str] = []
    for group in sorted(getattr(runtime, "_groups", ())):
        try:
            os.killpg(group, 0)
        except ProcessLookupError:
            continue
        except PermissionError:
            pass
        live_groups.append(f"process-group:{group}")
    processes = tuple(live_groups)

    runtime_class = _wire(
        getattr(
            getattr(getattr(lease, "plan", None), "runtime", None),
            "runtime_class",
            None,
        )
    )
    runtime_closed = getattr(runtime, "_closed", True) is True
    containers = (
        ()
        if runtime is None or runtime_class != "docker" or runtime_closed
        else (f"container:{getattr(runtime, 'runtime_id', lease_id)}",)
    )

    cgroups: list[str] = []
    for process in processes:
        group = process.rsplit(":", 1)[-1]
        cgroup_path = Path("/proc") / group / "cgroup"
        try:
            raw = cgroup_path.read_bytes()
        except (FileNotFoundError, OSError):
            continue
        cgroups.append("cgroup:" + hashlib.sha256(raw).hexdigest())

    workspace_candidates: set[Path] = set()
    if lease is not None:
        materialized = getattr(lease, "_materialized", None)
        workspace_path = getattr(materialized, "workspace_path", None)
        if workspace_path is not None:
            workspace_candidates.add(Path(workspace_path))
        workspace_id = getattr(
            getattr(lease, "measurement", None), "workspace_id", None
        )
        workspace_root = getattr(
            getattr(sandbox_runtime, "materialization_store", None),
            "workspace_root",
            None,
        )
        if workspace_root is not None and workspace_id:
            workspace_candidates.add(Path(workspace_root) / str(workspace_id))
    workspaces = tuple(
        sorted(
            f"workspace:{path}"
            for path in workspace_candidates
            if os.path.lexists(path)
        )
    )

    mount_points: list[str] = []
    try:
        mountinfo = Path("/proc/self/mountinfo").read_text(
            encoding="utf-8", errors="replace"
        )
    except (FileNotFoundError, OSError):
        mountinfo = ""
    for line in mountinfo.splitlines():
        fields = line.split()
        if len(fields) < 5:
            continue
        mount_path = Path(fields[4].replace("\\040", " "))
        if any(
            mount_path == workspace or workspace in mount_path.parents
            for workspace in workspace_candidates
        ):
            mount_points.append(f"mount:{mount_path}")

    secret_files: list[str] = []
    sockets: list[str] = []
    for workspace in workspace_candidates:
        for path in _bounded_residue_paths(workspace):
            try:
                mode = path.lstat().st_mode
            except FileNotFoundError:
                continue
            if stat.S_ISSOCK(mode):
                sockets.append(f"socket:{path}")
            if stat.S_ISREG(mode) and "secret" in path.name.lower():
                secret_files.append(f"secret:{path}")

    lease_root = getattr(sandbox_runtime, "lease_root", None)
    lease_records = (
        ()
        if lease_id is None or lease_root is None
        else tuple(
            sorted(
                f"lease-record:{item.name}"
                for item in os.scandir(lease_root)
                if lease_id in item.name
            )
        )
    )
    quarantines = (
        ()
        if receipt is None
        else tuple(
            sorted(
                f"quarantine:{step.get('resource')}:{step.get('state')}"
                for step in receipt.get("steps", ())
                if isinstance(step, Mapping)
                and step.get("state") not in {"released", "already_released"}
            )
        )
    )
    return _F5CleanupResidueSnapshot(
        actors=tuple(sorted(actors)),
        processes=tuple(sorted(processes)),
        containers=tuple(sorted(containers)),
        cgroups=tuple(sorted(cgroups)),
        mounts=tuple(sorted(set(mount_points))),
        workspaces=workspaces,
        secret_files=tuple(sorted(secret_files)),
        sockets=tuple(sorted(sockets)),
        lease_records=lease_records,
        quarantines=quarantines,
    )


class BreadBoardServiceRuntime:
    """Family-neutral adapter around one production BreadBoard V2 lifecycle.

    It snapshots the closed campaign authority, asks the service to admit the
    episode-scoped capability, and projects the resulting lifecycle evidence.
    Config and task context never select a fault.
    """

    def __init__(
        self,
        service: BreadBoardV2EpisodeService,
        observer: Any,
        close_runtime: Any,
        authority: F5TargetFaultsInput,
    ) -> None:
        if type(service) is not BreadBoardV2EpisodeService:
            raise TypeError("service must be an exact BreadBoardV2EpisodeService")
        if not callable(observer) or not callable(close_runtime):
            raise TypeError("observer and runtime close must be callable")
        if type(authority) is not F5TargetFaultsInput:
            raise TypeError("authority must be an exact F5TargetFaultsInput")
        rows = _canonical_rows(authority.campaign)
        self._fault_authority = {
            case.case_id: _case_fault_authority(case, row)
            for case, row in zip(authority.cases, rows, strict=True)
        }
        installed_authority = service._dependencies.fault_injection_authority
        expected_authority = _service_fault_authority(authority)
        expected_rows = tuple(self._fault_authority.values())
        if installed_authority is None or not installed_authority.matches(
            source_ref=expected_authority.source_ref,
            fault_specs=tuple(
                item.fault_injection
                for item in expected_rows
                if item.fault_injection is not None
            ),
            audit_specs=tuple(item.episode_audit for item in expected_rows),
        ):
            raise F5TargetFaultsError(
                "service composition did not install the exact F5 authority"
            )
        self._executed_cases: set[str] = set()
        self._service = service
        self._observer = observer
        self._close_runtime = close_runtime

    async def start(self) -> None:
        await self._service.start()

    async def execute_case(
        self, case: F5TargetCaseInput, row: F5ExecutionRow
    ) -> F5TargetCaseObservation:
        if type(case) is not F5TargetCaseInput or type(row) is not F5ExecutionRow:
            raise TypeError("case and row must be exact F5 authority values")
        authority = self._fault_authority.get(case.case_id)
        if (
            authority is None
            or case.case_id in self._executed_cases
            or authority.case_id != row.row_id
            or authority.episode_id != case.request.episode_id
            or authority.request_digest
            != sha256_bytes(canonical_json_bytes(case.request.model_dump(mode="json")))
            or authority.row_digest
            != sha256_bytes(canonical_json_bytes(row.model_dump(mode="json")))
        ):
            raise F5TargetFaultsError(
                f"case {case.case_id} supplied stale or mismatched fault authority"
            )
        self._executed_cases.add(case.case_id)
        audit_admission = await self._service.admit_episode_audit(
            authority.episode_audit
        )
        admission = (
            None
            if authority.fault_injection is None
            else await self._service.admit_fault_injection(authority.fault_injection)
        )
        created: Any | None = None
        run: Any | None = None
        closed: Any | None = None
        failure: BaseException | None = None
        try:
            created = await self._service.create(
                case.request,
                fault_injection=admission,
                episode_audit=audit_admission,
            )
            run = await self._service.run(
                case.request.episode_id,
                create_fingerprint=created.response.create_fingerprint,
                task_input=case.task_input,
                context=case.context,
            )
        except BaseException as exc:
            failure = exc
        finally:
            if case.request.episode_id in self._service._coordinators:
                try:
                    closed = await self._service.close_episode(case.request.episode_id)
                except BaseException as close_error:
                    if failure is None:
                        failure = close_error
                    else:
                        raise BaseExceptionGroup(
                            "F5 target case execution and cleanup failed",
                            [failure, close_error],
                        ) from failure
        observed = self._observer(case, row, created, run, closed, failure)
        if hasattr(observed, "__await__"):
            observed = await observed
        if type(observed) is not F5TargetCaseObservation:
            raise TypeError(
                "target observer must return an exact F5TargetCaseObservation"
            )
        return observed

    async def close(self) -> None:
        result = self._close_runtime()
        if hasattr(result, "__await__"):
            await result


class _ProductionObservationProjector:
    def __init__(self, composition: Any, spec: F5TargetFaultsInput) -> None:
        self._composition = composition
        self._spec = spec
        self._service = composition.app.state.episode_service
        self._repository = self._service._dependencies.evidence_repository

    def _load(self, digest: str, kind: c.ArtifactKind) -> bytes:
        return self._composition.authority_graph.store.load(
            digest,
            kind=kind,
            max_bytes=16 * 1024 * 1024,
        )

    @staticmethod
    def _durable_outcome(
        case_id: str,
        run_response: Any | None,
        failure: BaseException | None,
        coordinator: Any | None,
        recovered: Any,
    ) -> F5ObservedOutcome:
        manifest = getattr(recovered, "evidence_manifest", None)
        if manifest is None:
            raise F5TargetFaultsError(
                f"case {case_id} has no durable reward disposition"
            )
        durable_primary = getattr(manifest, "primary_disposition", None)
        run_primary = _wire(getattr(run_response, "primary_disposition", None))
        reward = getattr(run_response, "reward", None)
        run_components = _wire(getattr(run_response, "reward_components", {}))
        manifest_components = _wire(manifest.reward_components)
        scalar_reward = (
            manifest_components.get("score")
            if reward is None and isinstance(manifest_components, Mapping)
            else reward
        )
        if durable_primary == "succeeded":
            if (
                run_primary != "succeeded"
                or isinstance(scalar_reward, bool)
                or scalar_reward != 1
                or failure is not None
                or manifest.reward_disposition != "eligible"
                or manifest_components != run_components
            ):
                raise F5TargetFaultsError(
                    f"case {case_id} success reward evidence diverged"
                )
            lifecycle: Literal["succeeded", "failed"] = "succeeded"
            error_class = None
            published_reward: Literal[1] | None = 1
            reward_quarantined = False
        else:
            if (
                durable_primary not in {"failed", "cancelled", "interrupted"}
                or (run_primary is not None and run_primary != durable_primary)
                or reward is not None
                or manifest.reward_disposition != "ineligible"
                or manifest_components != {}
            ):
                raise F5TargetFaultsError(
                    f"case {case_id} failure reward evidence diverged"
                )
            lifecycle = "failed"
            fact = getattr(failure, "failure", None)
            if fact is None and coordinator is not None:
                fact = coordinator.primary_failure
            error_class = getattr(fact, "code", None)
            if type(error_class) is not str or not error_class:
                raise F5TargetFaultsError(
                    f"case {case_id} failure lacks a durable typed error"
                ) from failure
            published_reward = None
            reward_quarantined = True
        lease_opened = (
            coordinator is not None and coordinator.primary_lease_id is not None
        )
        return F5ObservedOutcome(
            lifecycle=lifecycle,
            error_class=error_class,
            failure_boundary=("post-allocation" if lease_opened else "pre-allocation"),
            reward=published_reward,
            reward_quarantined=reward_quarantined,
            lease_opened=lease_opened,
            unexpected_outcomes=(),
        )

    @staticmethod
    def _durable_cleanup(
        case_id: str,
        closed: Any | None,
        coordinator: Any | None,
        recovered: Any,
        sandbox_runtime: Any,
    ) -> F5CleanupObservation:
        closed_wire = _wire(getattr(closed, "response", None))
        envelope = getattr(recovered, "closed_envelope", None)
        if (
            closed_wire.get("state") != "closed"
            or closed_wire.get("cleanup_disposition") != "released"
            or envelope is None
            or getattr(recovered, "closed_tombstone", None) is None
        ):
            raise F5TargetFaultsError(
                f"case {case_id} has no trusted closed cleanup evidence"
            )
        lease_id = None if coordinator is None else coordinator.primary_lease_id
        receipt = envelope.cleanup_receipt
        required = tuple(envelope.cleanup_required_resources)
        cleanup_errors: tuple[str, ...] = ()
        if lease_id is None:
            if (
                receipt is not None
                or envelope.cleanup_receipt_digest is not None
                or required
            ):
                raise F5TargetFaultsError(
                    f"case {case_id} has a pre-allocation cleanup graft"
                )
            cleanup_required = False
            cleanup_attempts = 0
        else:
            if (
                not isinstance(receipt, Mapping)
                or envelope.cleanup_receipt_digest != canonical_digest(receipt)
                or receipt.get("lease_id") != lease_id
                or required
                != (
                    "child_verifier",
                    "runtime",
                    "workspace",
                    "cache_holder",
                    "lease_record",
                )
            ):
                raise F5TargetFaultsError(
                    f"case {case_id} cleanup receipt identity diverged"
                )
            raw_steps = receipt.get("steps")
            if type(raw_steps) is not list:
                raise F5TargetFaultsError(
                    f"case {case_id} cleanup receipt steps are missing"
                )
            states = {
                str(step.get("resource")): str(step.get("state"))
                for step in raw_steps
                if isinstance(step, Mapping)
            }
            if set(states) != set(required) or len(raw_steps) != len(states):
                raise F5TargetFaultsError(
                    f"case {case_id} cleanup resource set diverged"
                )
            cleanup_errors = tuple(
                f"{resource}:{state}"
                for resource, state in sorted(states.items())
                if state not in {"released", "already_released"}
            )
            if cleanup_errors or receipt.get("state") not in {
                "released",
                "already_released",
            }:
                raise F5TargetFaultsError(
                    f"case {case_id} cleanup resources were not released"
                )
            cleanup_required = True
            cleanup_attempts = 1
        first_probe = _cleanup_residue_snapshot(
            coordinator, sandbox_runtime, lease_id, receipt
        )
        second_probe = _cleanup_residue_snapshot(
            coordinator, sandbox_runtime, lease_id, receipt
        )
        if first_probe != second_probe:
            raise F5TargetFaultsError(f"case {case_id} cleanup residue probe raced")
        residue = second_probe
        orphans = tuple(
            sorted(
                {
                    *residue.actors,
                    *residue.processes,
                    *residue.containers,
                    *residue.cgroups,
                    *residue.mounts,
                    *residue.workspaces,
                    *residue.secret_files,
                    *residue.sockets,
                    *residue.lease_records,
                    *residue.quarantines,
                }
            )
        )
        if orphans:
            raise F5TargetFaultsError(
                f"case {case_id} retained runtime residue: {orphans}"
            )
        return F5CleanupObservation(
            authority="breadboard_episode_service",
            envelope_state="closed",
            cleanup_required=cleanup_required,
            cleanup_attempts=cleanup_attempts,
            remaining_actors=len(residue.actors),
            remaining_processes=len(residue.processes),
            remaining_containers=len(residue.containers),
            remaining_cgroups=len(residue.cgroups),
            remaining_mounts=len(residue.mounts),
            remaining_workspaces=len(residue.workspaces),
            remaining_secret_files=len(residue.secret_files),
            remaining_orphan_ids=orphans,
            cleanup_error_classes=tuple(
                sorted((*cleanup_errors, *residue.quarantines))
            ),
        )

    @staticmethod
    def _durable_authority_audit(
        case_id: str, recovered: Any
    ) -> tuple[tuple[str, ...], tuple[str, ...]]:
        manifest = getattr(recovered, "evidence_manifest", None)
        if manifest is None:
            raise F5TargetFaultsError(
                f"case {case_id} has no durable authority access audit"
            )
        canary_reads = manifest.authority_canary_reads
        cross_reads = manifest.authority_cross_episode_reads
        if (
            getattr(manifest, "authority_access_ledger_ref", None) is None
            or type(canary_reads) is not tuple
            or type(cross_reads) is not tuple
            or not canary_reads
        ):
            raise F5TargetFaultsError(
                f"case {case_id} authority access audit is malformed"
            )
        return canary_reads, cross_reads

    def __call__(
        self,
        case: F5TargetCaseInput,
        row: F5ExecutionRow,
        created: Any | None,
        run: Any | None,
        closed: Any | None,
        failure: BaseException | None,
    ) -> F5TargetCaseObservation:
        coordinator = self._service._coordinators.get(case.request.episode_id)
        create_response = getattr(created, "response", None)
        if create_response is None and coordinator is not None:
            create_response = coordinator.create_result
        if create_response is None:
            raise F5TargetFaultsError(
                f"case {case.case_id} produced no committed selection receipt"
            ) from failure
        disposition = _wire(getattr(created, "disposition", None))
        if created is not None and disposition != "fresh":
            raise F5TargetFaultsError(
                f"case {case.case_id} did not commit a fresh selection"
            )

        selection_ref = create_response.selection_record_ref
        selection = c.SelectionRecord.model_validate_json(
            self._load(selection_ref.sha256, c.ArtifactKind.SELECTION_RECORD),
            strict=True,
        )
        selection_digest = selection.canonical_digest()
        if (
            selection_digest != selection_ref.sha256
            or selection_digest != case.selection_record.digest
            or selection.episode_id != case.request.episode_id
            or selection.task_contract_digest != case.request.task.canonical_digest()
            or create_response.selection_commit.binding.selection_record_digest
            != selection_digest
        ):
            raise F5TargetFaultsError(
                f"case {case.case_id} persisted selection receipt mismatch or redraw"
            )

        plan_ref = create_response.effective_plan_ref
        plan = c.EffectiveExecutionPlan.model_validate_json(
            self._load(plan_ref.sha256, c.ArtifactKind.EFFECTIVE_EXECUTION_PLAN),
            strict=True,
        )
        plan_digest = plan.canonical_digest()
        slots = tuple(
            slot
            for slot in plan.policy_slots
            if (
                slot.model_digest,
                slot.tokenizer_digest,
                slot.checkpoint_digest,
            )
            == (
                case.model.model_digest,
                case.model.tokenizer_digest,
                case.model.checkpoint_digest,
            )
        )
        if (
            plan_digest != plan_ref.sha256
            or plan_digest != create_response.effective_plan_digest
            or plan.selection_record_digest != selection_digest
            or plan.final_semantic_digest != row.identities.config.digest
            or plan.task_eligibility_digest != row.identities.task.digest
            or plan.sandbox.image_digest != row.identities.image.digest
            or plan.verifier.image_digest != case.verifier_image_digest
            or plan.verifier.implementation_digest != row.identities.verifier.digest
            or len(slots) != 1
        ):
            raise F5TargetFaultsError(
                f"case {case.case_id} resolved plan identity join mismatch"
            )

        recovered = self._repository.recover(case.request.episode_id)
        if recovered is None or recovered.evidence_manifest_ref is None:
            raise F5TargetFaultsError(
                f"case {case.case_id} durable evidence is missing"
            )
        run_response = getattr(run, "response", None)
        observed = self._durable_outcome(
            case.case_id, run_response, failure, coordinator, recovered
        )
        cleanup = self._durable_cleanup(
            case.case_id,
            closed,
            coordinator,
            recovered,
            self._service._dependencies.sandbox_runtime,
        )

        canary_reads, cross_episode_reads = self._durable_authority_audit(
            case.case_id, recovered
        )
        closed_ref = recovered.closed_tombstone.envelope_ref
        evidence_ref = recovered.evidence_manifest_ref
        if (
            closed_ref.sha256 != row.target.episode.output.digest
            or evidence_ref.sha256 != row.target.evidence.digest
        ):
            raise F5TargetFaultsError(
                f"case {case.case_id} closed output/evidence digest mismatch"
            )

        observed_job = os.environ.get("SLURM_JOB_ID", "")
        observed_node = os.environ.get(
            "SLURM_JOB_NODELIST", os.environ.get("SLURM_NODELIST", "")
        )
        preflight = create_response.sandbox_preflight
        if (
            observed_job != row.target.attempt.job_id
            or observed_node != row.target.attempt.node_id
            or preflight.runtime != row.identities.runtime.identity_id
        ):
            raise F5TargetFaultsError(
                f"case {case.case_id} target job/node/runtime identity mismatch"
            )
        join = F5TargetIdentityJoin(
            authority_digest=self._composition.manifest.authority_bundle_digest,
            effective_plan_digest=plan_digest,
            config_digest=plan.final_semantic_digest,
            task_digest=plan.task_eligibility_digest,
            model_digest=slots[0].model_digest,
            image_digest=plan.sandbox.image_digest,
            runtime_digest=preflight.runtime_binary_digest,
            verifier_digest=preflight.verifier_digest,
            job_id=observed_job,
            node_id=observed_node,
            runtime_id=preflight.runtime,
        )
        if join != row.target.join:
            raise F5TargetFaultsError(
                f"case {case.case_id} target identity join differs from lifecycle observation"
            )

        return F5TargetCaseObservation(
            case_id=case.case_id,
            attempt_id=row.target.attempt.attempt_id,
            episode_id=case.request.episode_id,
            selection_record=case.selection_record,
            compiled_receipt_digest=selection.selected_receipt_digest,
            semantic_config_digest=plan.final_semantic_digest,
            task_digest=plan.task_eligibility_digest,
            model_digest=slots[0].model_digest,
            tokenizer_digest=slots[0].tokenizer_digest,
            checkpoint_digest=slots[0].checkpoint_digest,
            primary_image_digest=plan.sandbox.image_digest,
            verifier_image_digest=plan.verifier.image_digest,
            verifier_digest=plan.verifier.implementation_digest,
            join=join,
            observed=observed,
            cleanup=cleanup,
            canary_reads=canary_reads,
            cross_episode_reads=cross_episode_reads,
            episode_output=row.target.episode.output,
            evidence=row.target.evidence,
            fallback_used=False,
            terminal_state="closed",
        )


def _read_input(path: str) -> tuple[F5TargetFaultsInput, str]:
    source = Path(path).resolve(strict=True)
    raw = source.read_bytes()
    value = canonical_json_loads(raw)
    if canonical_json_bytes(value) != raw:
        raise F5TargetFaultsError("F5 target input is not canonical JSON")
    return F5TargetFaultsInput.model_validate_json(raw, strict=True), sha256_bytes(raw)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Execute the closed heterogeneous F5 target fault campaign"
    )
    parser.add_argument("--input", required=True)
    args = parser.parse_args()
    spec, input_digest = _read_input(args.input)
    build = build_f3_production_composition(
        spec.composition, spec.composition_output_dir
    )
    service_authority = _service_fault_authority(spec)
    composition = load_f3_production_composition(
        build,
        spec.composition.secrets.files,
        fault_injection_authority=service_authority,
    )
    observer = _ProductionObservationProjector(composition, spec)
    runtime = BreadBoardServiceRuntime(
        composition.app.state.episode_service,
        observer,
        composition.close,
        spec,
    )
    report = run_f5_target_faults(spec, input_digest=input_digest, runtime=runtime)
    os.write(1, _component_report_line(report, spec.report_path))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
