from __future__ import annotations
from builtins import BaseExceptionGroup

import asyncio
import dataclasses
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from pydantic import ValidationError

from breadboard_engine.compilation.contracts import canonical_json_bytes
from breadboard.rl.harness import contracts as c
from breadboard.rl.harness.evidence import EvidenceValidationError, canonical_digest
from breadboard.rl.harness.service import (
    BreadBoardV2EpisodeService,
    EpisodeCleanupDisposition,
    EpisodeLifecycleState,
    EpisodePrimaryDisposition,
    V2EpisodeConflict,
    V2EpisodeAuditSpec,
    V2EpisodeUnavailable,
    V2FaultClass,
    V2FaultInjectionSpec,
    V2FaultInjectionAuthority,
)
import scripts.rl_phase5.run_f5_target_faults as f5_runner
from breadboard.rl.phase5.f5_fault_campaign import F5CampaignInput, F5ObservedOutcome
from scripts.rl_phase5.run_f5_target_faults import (
    BreadBoardServiceRuntime,
    F5TargetCaseInput,
    F5TargetCaseObservation,
    F5TargetFaultsError,
    F5TargetFaultsInput,
    _canonical_rows,
    _ProductionObservationProjector,
    _component_report_line,
    _service_fault_authority,
    run_f5_target_faults,
)
from tests.rl.harness.test_v2_service import _service_with_real_repository
from tests.rl.harness.v2_service_fixtures import conductor_compatible_case
from tests.rl.harness.test_v2_protocol_integration import _resolved_for_episode
from tests.rl.phase5.test_f3_composition import _composition_spec
from tests.rl.phase5.test_f5_fault_campaign import _payload


def _digest(label: str) -> str:
    return "sha256:" + hashlib.sha256(label.encode()).hexdigest()


def _identity(kind: str, label: str) -> dict[str, str]:
    digest = _digest(f"{kind}:{label}")
    return {
        "identity_id": f"{kind}-{label}",
        "digest": digest,
        "immutable_ref": f"cas://f5-target/{kind}/{label}@{digest}",
    }


def _replace_identity_digest(identity: dict[str, str], digest: str) -> None:
    identity["digest"] = digest
    identity["immutable_ref"] = f"cas://f5-target/{identity['identity_id']}@{digest}"


def _spec(tmp_path: Path) -> F5TargetFaultsInput:
    composition, _ = _composition_spec(tmp_path)
    fixture, request, _, _ = conductor_compatible_case()
    del fixture
    task_digest = request.task.canonical_digest()
    model = c.ModelIdentity(
        model_id="f5-target-model",
        model_digest=_digest("target-model"),
        tokenizer_digest=_digest("target-tokenizer"),
        checkpoint_digest=_digest("target-checkpoint"),
    )
    payload = _payload()
    payload["campaign_id"] = "target-fault-campaign"
    rows: list[dict[str, Any]] = []
    for pair in payload["fault_pairs"]:
        rows.extend((pair["fault"], pair["twin"]))
    rows.extend(payload["concurrent_rows"])
    for row in rows:
        selection_digest = _digest(f"selection-record:{row['row_id']}")
        plan_digest = _digest(f"effective-plan:{selection_digest}")
        _replace_identity_digest(row["identities"]["effective_plan"], plan_digest)
        row["target"]["join"]["effective_plan_digest"] = plan_digest
        _replace_identity_digest(row["identities"]["task"], task_digest)
        row["target"]["join"]["task_digest"] = task_digest
        _replace_identity_digest(row["identities"]["model"], model.model_digest)
        row["target"]["join"]["model_digest"] = model.model_digest

    campaign = F5CampaignInput.model_validate_json(
        canonical_json_bytes(payload), strict=True
    )
    cases = []
    for row in _canonical_rows(campaign):
        fault_ref = (
            None
            if row.fault_injection is None
            else row.fault_injection.injection_spec.immutable_ref
        )
        cases.append(
            F5TargetCaseInput(
                case_id=row.row_id,
                row_id=row.row_id,
                request=c.ResolveEpisodeRequest(
                    episode_id=row.target.episode.episode_id,
                    subject=request.subject,
                    selector=request.selector,
                    selection_nonce=request.selection_nonce,
                    task=request.task,
                    policy_binding=request.policy_binding,
                    episode_overlays=request.episode_overlays,
                ),
                task_input={"prompt": f"target case {row.row_id}"},
                context={
                    "f5_case_id": row.row_id,
                    "f5_attempt_id": row.target.attempt.attempt_id,
                    "f5_canary": row.canary,
                    "f5_fault_injection_ref": fault_ref,
                },
                model=model,
                verifier_image_digest=_digest(f"verifier-image:{row.row_id}"),
                selection_record=_identity("selection-record", row.row_id),
            )
        )
    return F5TargetFaultsInput(
        schema_version="bb.rl.phase5-f5-target-faults-input.v1",
        composition=composition,
        composition_output_dir=str((tmp_path / "composition-output").resolve()),
        report_path=str((tmp_path / "f5-target-report.json").resolve()),
        campaign=campaign,
        cases=tuple(cases),
    )


def _authorize_service(
    service: BreadBoardV2EpisodeService,
    authority: F5TargetFaultsInput | V2FaultInjectionAuthority,
) -> BreadBoardV2EpisodeService:
    installed = (
        _service_fault_authority(authority)
        if type(authority) is F5TargetFaultsInput
        else authority
    )
    return BreadBoardV2EpisodeService(
        dataclasses.replace(
            service._dependencies,
            fault_injection_authority=installed,
        )
    )


class DeterministicTargetRuntime:
    def __init__(self, mutation: str | None = None) -> None:
        self.mutation = mutation
        self.order: list[str] = []
        self.started = False
        self.closed = False
        self.first_selection = None

    async def start(self) -> None:
        self.started = True

    async def execute_case(
        self, case: F5TargetCaseInput, row: Any
    ) -> F5TargetCaseObservation:
        assert self.started and not self.closed
        self.order.append(case.case_id)
        selection = case.selection_record
        if self.first_selection is None:
            self.first_selection = selection
        elif self.mutation == "reused-receipt":
            selection = self.first_selection
        observed = row.observed
        if self.mutation == "unexpected-outcome" and row.expected.lifecycle == "failed":
            observed = F5ObservedOutcome(
                lifecycle="succeeded",
                error_class=None,
                failure_boundary="post-allocation",
                reward=1,
                reward_quarantined=False,
                lease_opened=True,
                unexpected_outcomes=(),
            )
        if (
            self.mutation == "unexpected-failure"
            and len(self.order) == 1
            and row.expected.lifecycle == "succeeded"
        ):
            observed = F5ObservedOutcome(
                lifecycle="failed",
                error_class="RUNNER_FAILED",
                failure_boundary="post-allocation",
                reward=None,
                reward_quarantined=True,
                lease_opened=True,
                unexpected_outcomes=(),
            )
        join = row.target.join
        task_digest = row.identities.task.digest
        model_digest = case.model.model_digest
        checkpoint_digest = case.model.checkpoint_digest
        verifier_digest = row.identities.verifier.digest
        primary_image_digest = row.identities.image.digest
        canary_reads = row.canary_reads
        cleanup = row.cleanup
        if self.mutation == "task-join" and len(self.order) == 1:
            task_digest = _digest("wrong-task")
        if self.mutation == "model-join" and len(self.order) == 1:
            model_digest = _digest("wrong-model")
        if self.mutation == "checkpoint-join" and len(self.order) == 1:
            checkpoint_digest = _digest("wrong-checkpoint")
        if self.mutation == "verifier-join" and len(self.order) == 1:
            verifier_digest = _digest("wrong-verifier")
        if self.mutation == "image-join" and len(self.order) == 1:
            primary_image_digest = _digest("wrong-image")
        if self.mutation == "authority-leak" and len(self.order) == 1:
            canary_reads = (row.canary, "foreign-canary")
        if self.mutation == "cleanup-drift" and len(self.order) == 1:
            cleanup = row.cleanup.model_copy(
                update={"cleanup_attempts": row.cleanup.cleanup_attempts + 1}
            )
        return F5TargetCaseObservation(
            case_id=case.case_id,
            attempt_id=row.target.attempt.attempt_id,
            episode_id=row.target.episode.episode_id,
            selection_record=selection,
            compiled_receipt_digest=_digest(f"compiled-receipt:{case.case_id}"),
            semantic_config_digest=row.identities.config.digest,
            task_digest=task_digest,
            model_digest=model_digest,
            tokenizer_digest=case.model.tokenizer_digest,
            checkpoint_digest=checkpoint_digest,
            primary_image_digest=primary_image_digest,
            verifier_image_digest=case.verifier_image_digest,
            verifier_digest=verifier_digest,
            join=join,
            observed=observed,
            cleanup=cleanup,
            canary_reads=canary_reads,
            episode_output=row.target.episode.output,
            evidence=row.target.evidence,
            fallback_used=False,
            terminal_state="closed",
        )

    async def close(self) -> None:
        self.closed = True


class _OverlapTargetRuntime(DeterministicTargetRuntime):
    def __init__(self) -> None:
        super().__init__()
        self.active = 0
        self.peak = 0
        self.release = asyncio.Event()

    async def execute_case(
        self, case: F5TargetCaseInput, row: Any
    ) -> F5TargetCaseObservation:
        if row.row_id.startswith("concurrent-"):
            self.active += 1
            self.peak = max(self.peak, self.active)
            if self.active == 3:
                self.release.set()
            try:
                await asyncio.wait_for(self.release.wait(), timeout=1)
                await asyncio.sleep(0)
                return await super().execute_case(case, row)
            finally:
                self.active -= 1
        return await super().execute_case(case, row)


def test_concurrent_rows_really_overlap_but_reports_remain_canonical(
    tmp_path: Path,
) -> None:
    spec = _spec(tmp_path)
    runtime = _OverlapTargetRuntime()
    report = run_f5_target_faults(
        spec, input_digest=_digest("overlap-target-input"), runtime=runtime
    )
    assert runtime.peak == 3
    assert report.case_order == tuple(case.case_id for case in spec.cases)
    assert tuple(case.case_id for case in report.cases) == report.case_order


def test_executes_frozen_heterogeneous_interleaving_and_writes_canonical_report(
    tmp_path: Path,
) -> None:
    spec = _spec(tmp_path)
    runtime = DeterministicTargetRuntime()
    input_digest = _digest("canonical-target-input")

    report = run_f5_target_faults(spec, input_digest=input_digest, runtime=runtime)

    expected_order = [case.case_id for case in spec.cases]
    assert runtime.started and runtime.closed
    assert sorted(runtime.order) == sorted(expected_order)
    assert report.case_order == tuple(expected_order)
    assert report.summary == {
        "case_count": 19,
        "succeeded_count": 11,
        "failed_count": 8,
        "fresh_selection_receipts": True,
        "exact_identity_joins": True,
        "zero_cross_episode_authority_leakage": True,
        "cleanup_complete": True,
        "unexpected_outcomes": [],
    }
    assert [case.observed["lifecycle"] for case in report.cases[:7]] == [
        "succeeded",
        "succeeded",
        "failed",
        "succeeded",
        "succeeded",
        "failed",
        "succeeded",
    ]
    assert report.promotion_authority is False
    assert report.scorecard_authority is False
    raw = Path(spec.report_path).read_bytes()
    assert raw == canonical_json_bytes(json.loads(raw))
    assert json.loads(raw) == report.model_dump(mode="json")
    assert all(case.cleanup["no_orphan"] for case in report.cases)
    assert all(case.joins["exact"] for case in report.cases)


def test_component_stdout_envelope_is_persistable_and_permanently_non_authoritative(
    tmp_path: Path,
) -> None:
    spec = _spec(tmp_path)
    report = run_f5_target_faults(
        spec,
        input_digest=_digest("component-envelope-input"),
        runtime=DeterministicTargetRuntime(),
    )
    line = _component_report_line(report, spec.report_path)

    prefix = b"PHASE3_COMPONENT_REPORT_JSON="
    assert line.startswith(prefix) and line.endswith(b"\n")
    payload_raw = line[len(prefix) : -1]
    envelope = json.loads(payload_raw)
    assert payload_raw == canonical_json_bytes(envelope)
    assert envelope == {
        "schema_version": "bb.rl.phase5-f5-target-component-report.v1",
        "report_id": "f5-target-faults-target-fault-campaign",
        "component": "rl_phase5_f5_target_faults",
        "passed": True,
        "permanent_non_authority": True,
        "promotion_authority": False,
        "scorecard_authority": False,
        "scorecard_update_allowed": False,
        "report_sha256": "sha256:"
        + hashlib.sha256(Path(spec.report_path).read_bytes()).hexdigest(),
        "report_path": spec.report_path,
        "summary": report.summary,
    }


def test_all_fault_twins_bind_distinct_selection_receipts_to_distinct_plans(
    tmp_path: Path,
) -> None:
    spec = _spec(tmp_path)
    rows = {row.row_id: row for row in _canonical_rows(spec.campaign)}
    cases = {case.row_id: case for case in spec.cases}

    for pair in spec.campaign.fault_pairs:
        fault_case = cases[pair.fault.row_id]
        twin_case = cases[pair.twin.row_id]
        assert fault_case.selection_record != twin_case.selection_record
        assert (
            pair.fault.identities.effective_plan != pair.twin.identities.effective_plan
        )
        for case in (fault_case, twin_case):
            assert rows[case.row_id].identities.effective_plan.digest == _digest(
                f"effective-plan:{case.selection_record.digest}"
            )


def test_input_rejects_reused_effective_plan_identity(tmp_path: Path) -> None:
    spec = _spec(tmp_path)
    payload = spec.model_dump(mode="json")
    first = payload["campaign"]["fault_pairs"][0]
    first["fault"]["identities"]["effective_plan"] = first["twin"]["identities"][
        "effective_plan"
    ]
    first["fault"]["target"]["join"]["effective_plan_digest"] = first["twin"][
        "identities"
    ]["effective_plan"]["digest"]

    with pytest.raises(ValidationError, match="reuse an effective plan identity"):
        F5TargetFaultsInput.model_validate_json(
            canonical_json_bytes(payload), strict=True
        )


def test_runtime_rejects_cross_pair_effective_plan_swap(tmp_path: Path) -> None:
    original = _spec(tmp_path)
    original_joins = {
        row.row_id: row.target.join for row in _canonical_rows(original.campaign)
    }
    payload = original.model_dump(mode="json")
    first = payload["campaign"]["fault_pairs"][0]["fault"]
    second = payload["campaign"]["fault_pairs"][1]["fault"]
    first_plan = first["identities"]["effective_plan"]
    second_plan = second["identities"]["effective_plan"]
    first["identities"]["effective_plan"] = second_plan
    second["identities"]["effective_plan"] = first_plan
    first["target"]["join"]["effective_plan_digest"] = second_plan["digest"]
    second["target"]["join"]["effective_plan_digest"] = first_plan["digest"]
    swapped = F5TargetFaultsInput.model_validate_json(
        canonical_json_bytes(payload), strict=True
    )

    class ObservedPlanRuntime(DeterministicTargetRuntime):
        async def execute_case(
            self, case: F5TargetCaseInput, row: Any
        ) -> F5TargetCaseObservation:
            observation = await super().execute_case(case, row)
            return observation.model_copy(update={"join": original_joins[case.case_id]})

    with pytest.raises(
        F5TargetFaultsError,
        match="exact task/model/checkpoint/verifier/image join mismatch",
    ):
        run_f5_target_faults(
            swapped,
            input_digest=_digest("input-cross-pair-plan-swap"),
            runtime=ObservedPlanRuntime(),
        )


def test_input_rejects_reused_selection_receipt(tmp_path: Path) -> None:
    spec = _spec(tmp_path)
    payload = spec.model_dump(mode="json")
    payload["cases"][1]["selection_record"] = payload["cases"][0]["selection_record"]

    with pytest.raises(ValidationError, match="reuse a selection record receipt"):
        F5TargetFaultsInput.model_validate_json(
            canonical_json_bytes(payload), strict=True
        )


def test_runtime_rejects_stale_selection_observation(tmp_path: Path) -> None:
    spec = _spec(tmp_path)

    with pytest.raises(F5TargetFaultsError, match="selection receipt mismatch"):
        run_f5_target_faults(
            spec,
            input_digest=_digest("input-reused-receipt"),
            runtime=DeterministicTargetRuntime("reused-receipt"),
        )


@pytest.mark.parametrize(
    "mutation",
    ["task-join", "model-join", "checkpoint-join", "verifier-join", "image-join"],
)
def test_exact_task_model_checkpoint_verifier_and_image_joins_fail_closed(
    tmp_path: Path, mutation: str
) -> None:
    spec = _spec(tmp_path)

    with pytest.raises(
        F5TargetFaultsError,
        match="exact task/model/checkpoint/verifier/image join mismatch",
    ):
        run_f5_target_faults(
            spec,
            input_digest=_digest(f"input-{mutation}"),
            runtime=DeterministicTargetRuntime(mutation),
        )


@pytest.mark.parametrize("mutation", ["unexpected-outcome", "unexpected-failure"])
def test_unexpected_success_or_failure_fails_closed(
    tmp_path: Path, mutation: str
) -> None:
    spec = _spec(tmp_path)

    with pytest.raises(F5TargetFaultsError, match="unexpected success or failure"):
        run_f5_target_faults(
            spec,
            input_digest=_digest(f"input-{mutation}"),
            runtime=DeterministicTargetRuntime(mutation),
        )


def test_cross_episode_authority_read_fails_closed(tmp_path: Path) -> None:
    spec = _spec(tmp_path)

    with pytest.raises(F5TargetFaultsError, match="crossed episode authority"):
        run_f5_target_faults(
            spec,
            input_digest=_digest("input-authority-leak"),
            runtime=DeterministicTargetRuntime("authority-leak"),
        )


def test_cleanup_observation_mismatch_gates_report_publication(tmp_path: Path) -> None:
    spec = _spec(tmp_path)

    with pytest.raises(F5TargetFaultsError, match="cleanup observation mismatch"):
        run_f5_target_faults(
            spec,
            input_digest=_digest("input-cleanup-drift"),
            runtime=DeterministicTargetRuntime("cleanup-drift"),
        )
    assert not Path(spec.report_path).exists()


def _isolated_runtime_authority(
    spec: F5TargetFaultsInput,
    row_id: str,
    *,
    episode_id: str,
    selection_digest: str,
) -> tuple[F5TargetFaultsInput, F5TargetCaseInput, Any]:
    payload = spec.model_dump(mode="json")
    case_payload = next(case for case in payload["cases"] if case["case_id"] == row_id)
    case_payload["request"]["episode_id"] = episode_id
    case_payload["selection_record"]["digest"] = selection_digest
    case_payload["selection_record"]["immutable_ref"] = (
        f"cas://f5-target/selection-record/{row_id}@{selection_digest}"
    )
    campaign_rows = list(payload["campaign"]["concurrent_rows"])
    for pair in payload["campaign"]["fault_pairs"]:
        campaign_rows.extend((pair["fault"], pair["twin"]))
    row_payload = next(row for row in campaign_rows if row["row_id"] == row_id)
    row_payload["target"]["episode"]["episode_id"] = episode_id
    isolated = F5TargetFaultsInput.model_validate_json(
        canonical_json_bytes(payload), strict=True
    )
    rows = _canonical_rows(isolated.campaign)
    index = next(index for index, row in enumerate(rows) if row.row_id == row_id)
    return isolated, isolated.cases[index], rows[index]


class _LifecycleObserver:
    def __init__(self, service: Any, repository: Any, selection_digest: str) -> None:
        self.service = service
        self.repository = repository
        self.selection_digest = selection_digest
        self.failure: Any | None = None
        self.lease_opened: bool | None = None

    def __call__(
        self,
        case: F5TargetCaseInput,
        row: Any,
        created: Any | None,
        run: Any | None,
        closed: Any | None,
        failure: BaseException | None,
    ) -> F5TargetCaseObservation:
        coordinator = self.service._coordinators[case.request.episode_id]
        create_response = (
            created.response if created is not None else coordinator.create_result
        )
        assert create_response is not None
        assert create_response.episode_id == case.request.episode_id
        assert create_response.selection_record_ref.sha256 == self.selection_digest
        recovered = self.repository.recover(case.request.episode_id)
        assert recovered is not None
        assert recovered.closed_tombstone is not None
        assert recovered.evidence_manifest_ref is not None
        assert recovered.evidence_manifest is not None
        assert recovered.evidence_manifest.authority_canary_reads == row.canary_reads
        assert not recovered.evidence_manifest.authority_cross_episode_reads
        assert closed is not None
        assert closed.response.state is EpisodeLifecycleState.CLOSED
        assert closed.response.cleanup_disposition is EpisodeCleanupDisposition.RELEASED
        run_response = run.response if run is not None else None
        primary = (
            run_response.primary_disposition
            if run_response is not None
            else coordinator.primary_disposition
        )
        self.failure = coordinator.primary_failure
        self.lease_opened = coordinator.primary_lease_id is not None
        if primary is EpisodePrimaryDisposition.SUCCEEDED:
            observed = F5ObservedOutcome(
                lifecycle="succeeded",
                error_class=None,
                failure_boundary="post-allocation",
                reward=1,
                reward_quarantined=False,
                lease_opened=True,
                unexpected_outcomes=(),
            )
        else:
            assert coordinator.primary_failure is not None
            observed = F5ObservedOutcome(
                lifecycle="failed",
                error_class=coordinator.primary_failure.code,
                failure_boundary=(
                    "post-allocation"
                    if coordinator.primary_lease_id is not None
                    else "pre-allocation"
                ),
                reward=None,
                reward_quarantined=True,
                lease_opened=coordinator.primary_lease_id is not None,
                unexpected_outcomes=(),
            )
        durable_observed = _ProductionObservationProjector._durable_outcome(
            case.case_id,
            run_response,
            failure,
            coordinator,
            recovered,
        )
        assert durable_observed == observed
        durable_cleanup = _ProductionObservationProjector._durable_cleanup(
            case.case_id,
            closed,
            coordinator,
            recovered,
            self.service._dependencies.sandbox_runtime,
        )
        assert durable_cleanup == row.cleanup
        canary_reads, cross_episode_reads = (
            _ProductionObservationProjector._durable_authority_audit(
                case.case_id, recovered
            )
        )
        return F5TargetCaseObservation(
            case_id=case.case_id,
            attempt_id=row.target.attempt.attempt_id,
            episode_id=case.request.episode_id,
            selection_record=case.selection_record,
            compiled_receipt_digest=self.selection_digest,
            semantic_config_digest=row.identities.config.digest,
            task_digest=row.identities.task.digest,
            model_digest=case.model.model_digest,
            tokenizer_digest=case.model.tokenizer_digest,
            checkpoint_digest=case.model.checkpoint_digest,
            primary_image_digest=row.identities.image.digest,
            verifier_image_digest=case.verifier_image_digest,
            verifier_digest=row.identities.verifier.digest,
            join=row.target.join,
            observed=observed,
            cleanup=durable_cleanup,
            canary_reads=canary_reads,
            episode_output=row.target.episode.output,
            evidence=row.target.evidence,
            cross_episode_reads=cross_episode_reads,
            fallback_used=False,
            terminal_state="closed",
        )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("fault_class", "error_code", "boundary"),
    [
        ("timeout", "TIMEOUT", "post-allocation"),
        ("cancel", "CANCELLED", "post-allocation"),
        ("revocation", "REVOKED", "pre-allocation"),
        ("egress", "EGRESS_DENIED", "post-allocation"),
        ("resource", "RESOURCE_EXHAUSTED", "post-allocation"),
        ("verifier", "VERIFIER_FAILED", "post-allocation"),
        ("artifact", "ARTIFACT_FAILED", "post-allocation"),
        ("transport", "TRANSPORT_FAILED", "pre-allocation"),
    ],
)
async def test_all_eight_faults_execute_through_the_typed_service_lifecycle(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    fault_class: str,
    error_code: str,
    boundary: str,
) -> None:
    service, case, repository = await _service_with_real_repository(monkeypatch)
    base = _spec(tmp_path)
    row_id = next(
        row.row_id
        for row in _canonical_rows(base.campaign)
        if row.fault_injection is not None
        and row.fault_injection.fault_class == fault_class
    )
    authority, target_case, row = _isolated_runtime_authority(
        base,
        row_id,
        episode_id=case.request.episode_id,
        selection_digest=case.resolved.selection_record_ref.sha256,
    )
    service = _authorize_service(service, authority)
    observer = _LifecycleObserver(
        service, repository, case.resolved.selection_record_ref.sha256
    )
    runtime = BreadBoardServiceRuntime(service, observer, service.close, authority)

    await runtime.start()
    observation = await runtime.execute_case(target_case, row)
    await runtime.close()

    assert observation.observed.error_class == error_code
    assert observation.observed.failure_boundary == boundary
    assert observation.observed.lease_opened is (boundary == "post-allocation")
    assert observer.failure is not None
    assert observer.failure.code == error_code
    assert case.calls.count("sandbox.open") == (
        1 if boundary == "post-allocation" else 0
    )
    assert case.calls.count("lease.close") == (
        1 if boundary == "post-allocation" else 0
    )
    recovered = repository.recover(case.request.episode_id)
    assert recovered is not None and recovered.closed_envelope is not None
    assert (recovered.closed_envelope.cleanup_receipt is not None) is (
        boundary == "post-allocation"
    )
    manifest = recovered.evidence_manifest
    assert manifest is not None
    assert manifest.primary_failure_digest is not None
    lineage_kinds = {node.kind for node in manifest.lineage_nodes}
    if boundary == "post-allocation":
        coordinator = service._coordinators[case.request.episode_id]
        assert coordinator.lease is not None
        assert manifest.primary_measurement_digest == canonical_digest(
            coordinator.lease.measurement
        )
        assert "primary_measurement" in lineage_kinds
    else:
        assert manifest.primary_measurement_digest is None
        assert "primary_measurement" not in lineage_kinds
    assert "primary_failure" in lineage_kinds


@pytest.mark.asyncio
async def test_eight_fault_owners_overlap_and_all_reach_durable_terminal_state(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    base_service, case, repository = await _service_with_real_repository(monkeypatch)
    request_payload = case.request.model_dump(mode="json")
    fault_classes = tuple(V2FaultClass)
    requests = tuple(
        type(case.request).model_validate_json(
            canonical_json_bytes(
                {
                    **request_payload,
                    "episode_id": f"episode-f5-eight-way-{fault_class.value}",
                }
            ),
            strict=True,
        )
        for fault_class in fault_classes
    )
    resolved_by_episode = {
        request.episode_id: _resolved_for_episode(
            case.resolved,
            request.episode_id,
        )
        for request in requests
    }

    def resolve_episode(request: Any) -> Any:
        case.calls.append("resolve")
        return resolved_by_episode[request.episode_id]

    case.config.resolve_episode = resolve_episode
    specs = tuple(
        V2FaultInjectionSpec(
            request.episode_id,
            f"cas://f5-target/fault/{fault_class.value}@"
            + _digest(f"eight-way-{fault_class.value}"),
            fault_class,
        )
        for request, fault_class in zip(requests, fault_classes, strict=True)
    )
    authority = V2FaultInjectionAuthority(
        source_ref="cas://f5-target/input@" + _digest("eight-way-authority"),
        fault_specs=specs,
        audit_specs=(),
    )
    service = _authorize_service(base_service, authority)
    await service.start()
    admissions = tuple([await service.admit_fault_injection(spec) for spec in specs])
    ready = 0
    ready_lock = asyncio.Lock()
    release = asyncio.Event()
    observed: dict[str, str] = {}

    async def execute(
        request: Any,
        spec: V2FaultInjectionSpec,
        admission: Any,
    ) -> None:
        nonlocal ready
        async with ready_lock:
            ready += 1
            if ready == len(requests):
                release.set()
        await release.wait()
        try:
            created = await service.create(request, fault_injection=admission)
            await service.run(
                request.episode_id,
                create_fingerprint=created.response.create_fingerprint,
                task_input={"fault": spec.fault_class.value},
            )
        except V2EpisodeUnavailable as exc:
            observed[request.episode_id] = exc.failure.code
        finally:
            if request.episode_id in service._coordinators:
                await service.close_episode(request.episode_id)
        recovered = repository.recover(request.episode_id)
        assert recovered is not None
        assert recovered.closed_envelope is not None
        durable_codes = {
            event.primary_fact.code
            for event in recovered.events
            if event.primary_fact is not None
        }
        assert spec.error_code in durable_codes
        observed.setdefault(request.episode_id, spec.error_code)

    async with asyncio.TaskGroup() as group:
        for request, spec, admission in zip(
            requests,
            specs,
            admissions,
            strict=True,
        ):
            group.create_task(execute(request, spec, admission))
    await service.close()

    assert observed == {
        request.episode_id: spec.error_code
        for request, spec in zip(requests, specs, strict=True)
    }
    assert not service._active_tasks
    assert not service._unclaimed_task_failures


@pytest.mark.asyncio
async def test_untrusted_context_cannot_manufacture_an_injection_and_control_stays_live(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    service, case, repository = await _service_with_real_repository(monkeypatch)
    base = _spec(tmp_path)
    control = next(
        row for row in _canonical_rows(base.campaign) if row.fault_injection is None
    )
    authority, target_case, row = _isolated_runtime_authority(
        base,
        control.row_id,
        episode_id=case.request.episode_id,
        selection_digest=case.resolved.selection_record_ref.sha256,
    )
    service = _authorize_service(service, authority)
    observer = _LifecycleObserver(
        service, repository, case.resolved.selection_record_ref.sha256
    )
    runtime = BreadBoardServiceRuntime(service, observer, service.close, authority)
    target_case.context["f5_fault_injection_ref"] = "cas://untrusted/fault@" + _digest(
        "untrusted-context-fault"
    )
    target_case.context["fault_class"] = "transport"

    await runtime.start()
    observation = await runtime.execute_case(target_case, row)
    await runtime.close()

    assert observation.observed.lifecycle == "succeeded"
    assert observation.observed.error_class is None
    assert case.calls.count("sandbox.open") == 1
    assert case.calls.count("runner.open") == 1
    assert case.calls.count("lease.close") == 1
    assert not service._fault_injection_admissions


@pytest.mark.asyncio
async def test_fault_admission_rejects_unknown_mismatched_and_stale_refs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service, case, _ = await _service_with_real_repository(monkeypatch)
    immutable_ref = "cas://f5-target/fault/timeout@" + _digest("typed-timeout")
    with pytest.raises(ValueError, match="fault injection spec"):
        V2FaultInjectionSpec(
            case.request.episode_id,
            immutable_ref,
            "unknown",  # type: ignore[arg-type]
        )
    spec = V2FaultInjectionSpec(
        case.request.episode_id,
        immutable_ref,
        V2FaultClass.TIMEOUT,
    )
    authority = V2FaultInjectionAuthority(
        source_ref="cas://f5-target/input@" + _digest("direct-authority"),
        fault_specs=(spec,),
        audit_specs=(),
    )
    service = _authorize_service(service, authority)
    unknown_spec = V2FaultInjectionSpec(
        case.request.episode_id,
        "cas://f5-target/fault/unknown@" + _digest("well-formed-unknown"),
        V2FaultClass.TIMEOUT,
    )
    with pytest.raises(V2EpisodeConflict) as unknown:
        await service.admit_fault_injection(unknown_spec)
    assert unknown.value.failure.code == "fault_injection_unknown_authority"
    assert not service._fault_injection_admissions
    assert case.request.episode_id not in service._coordinators
    admission = await service.admit_fault_injection(spec)
    with pytest.raises(V2EpisodeConflict) as stale:
        await service.admit_fault_injection(spec)
    assert stale.value.failure.code == "fault_injection_stale"

    other_service, _, _ = await _service_with_real_repository(monkeypatch)
    with pytest.raises(V2EpisodeConflict) as mismatched:
        await other_service.create(
            case.request,
            fault_injection=admission,
        )
    assert mismatched.value.failure.code == "fault_injection_mismatch"
    assert case.request.episode_id not in other_service._coordinators
    await other_service.close()


@pytest.mark.asyncio
async def test_real_foreign_authority_read_event_gates_durable_publication(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _service, case, repository = await _service_with_real_repository(monkeypatch)
    own = V2EpisodeAuditSpec(
        case.request.episode_id,
        "cas://f5-target/authority/own@" + _digest("own-authority"),
        "canary-own",
    )
    foreign = V2EpisodeAuditSpec(
        "episode-f5-foreign",
        "cas://f5-target/authority/foreign@" + _digest("foreign-authority"),
        "canary-foreign",
    )
    authority = V2FaultInjectionAuthority(
        source_ref="cas://f5-target/input@" + _digest("foreign-read-authority"),
        fault_specs=(),
        audit_specs=(own, foreign),
    )
    authority.read_episode_canary(
        actor_episode_id=own.episode_id,
        authority_episode_id=own.episode_id,
    )
    authority.read_episode_canary(
        actor_episode_id=own.episode_id,
        authority_episode_id=foreign.episode_id,
    )
    events = authority.access_events(own.episode_id)
    assert tuple(event.authority_episode_id for event in events) == (
        own.episode_id,
        foreign.episode_id,
    )
    with pytest.raises(
        EvidenceValidationError,
        match="cross-episode authority access",
    ):
        repository._publish_authority_access_ledger(
            own.episode_id,
            events,
        )


@pytest.mark.asyncio
async def test_fault_and_control_overlap_on_one_service_without_cross_injection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service, case, repository = await _service_with_real_repository(monkeypatch)
    case.policy_resolver.release = asyncio.Event()
    request_payload = case.request.model_dump(mode="json")
    fault_request = type(case.request).model_validate_json(
        canonical_json_bytes(
            {**request_payload, "episode_id": "episode-f5-overlap-fault"}
        ),
        strict=True,
    )
    control_request = type(case.request).model_validate_json(
        canonical_json_bytes(
            {**request_payload, "episode_id": "episode-f5-overlap-control"}
        ),
        strict=True,
    )
    resolved_by_episode = {
        fault_request.episode_id: _resolved_for_episode(
            case.resolved, fault_request.episode_id
        ),
        control_request.episode_id: _resolved_for_episode(
            case.resolved, control_request.episode_id
        ),
    }

    def resolve_episode(request: Any) -> Any:
        case.calls.append("resolve")
        return resolved_by_episode[request.episode_id]

    case.config.resolve_episode = resolve_episode
    fault_spec = V2FaultInjectionSpec(
        fault_request.episode_id,
        "cas://f5-target/fault/transport@" + _digest("overlap-transport"),
        V2FaultClass.TRANSPORT,
    )
    fault_audit_spec = V2EpisodeAuditSpec(
        fault_request.episode_id,
        "cas://f5-target/authority/overlap-fault@" + _digest("overlap-fault-authority"),
        "canary-overlap-fault",
    )
    control_audit_spec = V2EpisodeAuditSpec(
        control_request.episode_id,
        "cas://f5-target/authority/overlap-control@"
        + _digest("overlap-control-authority"),
        "canary-overlap-control",
    )
    authority = V2FaultInjectionAuthority(
        source_ref="cas://f5-target/input@" + _digest("overlap-authority"),
        fault_specs=(fault_spec,),
        audit_specs=(fault_audit_spec, control_audit_spec),
    )
    service = _authorize_service(service, authority)
    await service.start()
    fault_admission = await service.admit_fault_injection(fault_spec)
    fault_audit = await service.admit_episode_audit(fault_audit_spec)
    control_audit = await service.admit_episode_audit(control_audit_spec)
    fault_task = asyncio.create_task(
        service.create(
            fault_request,
            fault_injection=fault_admission,
            episode_audit=fault_audit,
        )
    )
    control_task = asyncio.create_task(
        service.create(control_request, episode_audit=control_audit)
    )
    for _ in range(100):
        if case.calls.count("policy.resolve") == 2:
            break
        await asyncio.sleep(0)
    assert case.calls.count("policy.resolve") == 2
    assert not fault_task.done() and not control_task.done()
    case.policy_resolver.release.set()
    fault_result, control_result = await asyncio.gather(
        fault_task, control_task, return_exceptions=True
    )
    assert isinstance(fault_result, V2EpisodeUnavailable)
    assert fault_result.failure.code == "TRANSPORT_FAILED"
    assert not isinstance(control_result, BaseException)
    run = await service.run(
        control_request.episode_id,
        create_fingerprint=control_result.response.create_fingerprint,
        task_input={"query": "overlap control"},
        context={},
    )
    assert run.response.primary_disposition is EpisodePrimaryDisposition.SUCCEEDED
    await asyncio.gather(
        service.close_episode(fault_request.episode_id),
        service.close_episode(control_request.episode_id),
    )
    fault_recovered = repository.recover(fault_request.episode_id)
    control_recovered = repository.recover(control_request.episode_id)
    assert fault_recovered.evidence_manifest.authority_canary_reads == (
        "canary-overlap-fault",
    )
    assert control_recovered.evidence_manifest.authority_canary_reads == (
        "canary-overlap-control",
    )
    assert not fault_recovered.evidence_manifest.authority_cross_episode_reads
    assert not control_recovered.evidence_manifest.authority_cross_episode_reads
    await service.close()


@pytest.mark.asyncio
async def test_service_close_joins_failed_create_before_locator_close(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service, case, repository = await _service_with_real_repository(monkeypatch)
    case.sandbox.open_error = RuntimeError("deterministic runtime preflight failure")
    close_owner_entered = asyncio.Event()
    release_close_owner = asyncio.Event()
    locator_closed = False
    append_after_locator_close: list[str] = []
    original_append_transition = repository.append_transition
    original_close_owner = service._close_owner

    def observed_append_transition(event: Any) -> Any:
        if locator_closed:
            append_after_locator_close.append(event.event_kind)
        return original_append_transition(event)

    async def blocked_close_owner(coordinator: Any, failure: Any) -> Any:
        close_owner_entered.set()
        await release_close_owner.wait()
        return await original_close_owner(coordinator, failure)

    monkeypatch.setattr(repository, "append_transition", observed_append_transition)
    monkeypatch.setattr(service, "_close_owner", blocked_close_owner)
    create_waiter = asyncio.create_task(service.create(case.request))
    await close_owner_entered.wait()
    create_waiter.cancel()
    with pytest.raises(asyncio.CancelledError):
        await create_waiter

    shutdown = asyncio.create_task(service.close())
    asyncio.get_running_loop().call_soon(release_close_owner.set)
    await shutdown
    locator_closed = True

    coordinator = service._coordinators[case.request.episode_id]
    assert coordinator.create_task is not None
    assert coordinator.create_task.done()
    assert service._lifecycle_state.value == "closed"
    assert not service._active_tasks
    assert not service._unclaimed_task_failures
    assert not append_after_locator_close
    assert case.calls.count("sandbox.manager.close") == 1


@pytest.mark.asyncio
async def test_closing_fence_rejects_cached_admission_and_reuses_shutdown_owner(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service, case, _ = await _service_with_real_repository(monkeypatch)
    created = await service.create(case.request)
    case.sandbox.lease.close_release = asyncio.Event()
    first_close = asyncio.create_task(service.close())
    await case.sandbox.lease.close_entered.wait()
    owner = service._close_task

    with pytest.raises(V2EpisodeUnavailable, match="service_closing") as create_error:
        await service.create(case.request)
    with pytest.raises(V2EpisodeUnavailable, match="service_closing") as run_error:
        await service.run(
            case.request.episode_id,
            create_fingerprint=created.response.create_fingerprint,
            task_input={"race": "closing"},
        )
    second_close = asyncio.create_task(service.close())
    assert service._close_task is owner
    assert create_error.value.failure.code == "service_closing"
    assert run_error.value.failure.code == "service_closing"

    case.sandbox.lease.close_release.set()
    await asyncio.gather(first_close, second_close)
    assert service._lifecycle_state.value == "closed"
    assert case.calls.count("lease.close") == 1
    assert case.calls.count("sandbox.manager.close") == 1


@pytest.mark.asyncio
async def test_operation_and_cleanup_failures_are_aggregated_and_replayed_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service, case, _ = await _service_with_real_repository(monkeypatch)
    created = await service.create(case.request)
    run_entered = asyncio.Event()
    release_run = asyncio.Event()
    original_close_owner = service._close_owner

    async def evidence_failure(coordinator: Any, request: Any) -> Any:
        run_entered.set()
        await release_run.wait()
        raise RuntimeError("deterministic evidence transition failure")

    async def cleanup_failure(coordinator: Any, failure: Any) -> Any:
        await original_close_owner(coordinator, failure)
        raise RuntimeError("deterministic cleanup failure")

    monkeypatch.setattr(service, "_run_fresh", evidence_failure)
    monkeypatch.setattr(service, "_close_owner", cleanup_failure)
    run_waiter = asyncio.create_task(
        service.run(
            case.request.episode_id,
            create_fingerprint=created.response.create_fingerprint,
            task_input={"race": "evidence-and-cleanup"},
        )
    )
    await run_entered.wait()
    run_waiter.cancel()
    with pytest.raises(asyncio.CancelledError):
        await run_waiter
    release_run.set()

    with pytest.raises(BaseExceptionGroup) as first:
        await service.close_episode(case.request.episode_id)
    with pytest.raises(BaseExceptionGroup) as repeated:
        await service.close_episode(case.request.episode_id)

    assert repeated.value is first.value
    assert [str(error) for error in first.value.exceptions] == [
        "deterministic evidence transition failure",
        "deterministic cleanup failure",
    ]
    assert case.calls.count("lease.close") == 1
    assert not service._unclaimed_task_failures
    await service.close()


@pytest.mark.asyncio
async def test_pending_fault_admission_cannot_be_omitted_or_reused(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service, case, _ = await _service_with_real_repository(monkeypatch)
    spec = V2FaultInjectionSpec(
        case.request.episode_id,
        "cas://f5-target/fault/transport@" + _digest("race-transport"),
        V2FaultClass.TRANSPORT,
    )
    authority = V2FaultInjectionAuthority(
        source_ref="cas://f5-target/input@" + _digest("pending-authority"),
        fault_specs=(spec,),
        audit_specs=(),
    )
    service = _authorize_service(service, authority)
    await service.start()
    admission = await service.admit_fault_injection(spec)

    async def create_with(admitted: Any | None) -> str:
        try:
            await service.create(
                case.request,
                fault_injection=admitted,
            )
        except (V2EpisodeConflict, V2EpisodeUnavailable) as exc:
            return exc.failure.code
        raise AssertionError("faulted create unexpectedly succeeded")

    codes = await asyncio.gather(
        create_with(None),
        create_with(admission),
    )
    assert sorted(codes) == ["TRANSPORT_FAILED", "fault_injection_missing"]
    with pytest.raises(V2EpisodeConflict) as reused:
        await service.create(case.request, fault_injection=admission)
    assert reused.value.failure.code == "fault_injection_mismatch"
    await service.close_episode(case.request.episode_id)
    await service.close()


@pytest.mark.asyncio
async def test_recovered_create_retires_fault_admission_before_cached_return(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service, case, repository = await _service_with_real_repository(monkeypatch)
    await service.start()
    created = await service.create(case.request)
    await service.run(
        case.request.episode_id,
        create_fingerprint=created.response.create_fingerprint,
        task_input={"query": "durable control"},
        context={},
    )
    await service.close_episode(case.request.episode_id)
    assert repository.recover(case.request.episode_id) is not None
    await service.close()

    spec = V2FaultInjectionSpec(
        case.request.episode_id,
        "cas://f5-target/fault/transport@" + _digest("recovered-transport"),
        V2FaultClass.TRANSPORT,
    )
    authority = V2FaultInjectionAuthority(
        source_ref="cas://f5-target/input@" + _digest("recovered-authority"),
        fault_specs=(spec,),
        audit_specs=(),
    )
    recovered_service = BreadBoardV2EpisodeService(
        dataclasses.replace(
            service._dependencies,
            fault_injection_authority=authority,
        )
    )
    admission = await recovered_service.admit_fault_injection(spec)
    with pytest.raises(V2EpisodeConflict) as stale:
        await recovered_service.create(
            case.request,
            fault_injection=admission,
        )
    assert stale.value.failure.code == "fault_injection_stale"
    assert not recovered_service._fault_injection_admissions
    assert case.request.episode_id not in recovered_service._coordinators
    with pytest.raises(V2EpisodeConflict) as reused:
        await recovered_service.create(
            case.request,
            fault_injection=admission,
        )
    assert reused.value.failure.code == "fault_injection_mismatch"


def test_projector_rejects_omitted_or_corrupt_durable_evidence() -> None:
    with pytest.raises(F5TargetFaultsError, match="durable reward"):
        _ProductionObservationProjector._durable_outcome(
            "fault-timeout", None, None, None, SimpleNamespace()
        )
    manifest = SimpleNamespace(
        primary_disposition="succeeded",
        reward_disposition="ineligible",
        reward_components={},
    )
    with pytest.raises(F5TargetFaultsError, match="reward evidence diverged"):
        _ProductionObservationProjector._durable_outcome(
            "control",
            SimpleNamespace(
                primary_disposition=EpisodePrimaryDisposition.SUCCEEDED,
                reward=1,
            ),
            None,
            SimpleNamespace(primary_lease_id="lease-control"),
            SimpleNamespace(evidence_manifest=manifest),
        )
    envelope = SimpleNamespace(
        cleanup_receipt=None,
        cleanup_receipt_digest=None,
        cleanup_required_resources=(
            "child_verifier",
            "runtime",
            "workspace",
            "cache_holder",
            "lease_record",
        ),
    )
    recovered = SimpleNamespace(
        closed_envelope=envelope,
        closed_tombstone=object(),
    )
    with pytest.raises(F5TargetFaultsError, match="cleanup receipt identity"):
        _ProductionObservationProjector._durable_cleanup(
            "fault-timeout",
            SimpleNamespace(
                response={
                    "state": "closed",
                    "cleanup_disposition": "released",
                }
            ),
            SimpleNamespace(primary_lease_id="lease-timeout"),
            recovered,
            SimpleNamespace(_leases={}),
        )


def test_cleanup_projector_detects_actual_workspace_secret_and_actor_residue(
    tmp_path: Path,
) -> None:
    lease_id = "lease-residue"
    workspace = tmp_path / "workspace-residue"
    workspace.mkdir()
    (workspace / "secret-token").write_text("residue", encoding="utf-8")
    lease_root = tmp_path / "leases"
    lease_root.mkdir()
    lease = SimpleNamespace(
        _materialized=SimpleNamespace(workspace_path=workspace),
        measurement=SimpleNamespace(workspace_id=workspace.name),
        _runtime=SimpleNamespace(_groups=set(), _closed=True),
        plan=SimpleNamespace(runtime=SimpleNamespace(runtime_class="trusted_process")),
    )
    coordinator = SimpleNamespace(primary_lease_id=lease_id, lease=lease)
    sandbox_runtime = SimpleNamespace(
        _leases={lease_id: lease},
        lease_root=lease_root,
        materialization_store=SimpleNamespace(workspace_root=tmp_path),
    )
    required = (
        "child_verifier",
        "runtime",
        "workspace",
        "cache_holder",
        "lease_record",
    )
    receipt = {
        "lease_id": lease_id,
        "steps": [
            {"resource": resource, "state": "released", "detail": ""}
            for resource in required
        ],
        "state": "released",
    }
    recovered = SimpleNamespace(
        closed_envelope=SimpleNamespace(
            cleanup_receipt=receipt,
            cleanup_receipt_digest=canonical_digest(receipt),
            cleanup_required_resources=required,
        ),
        closed_tombstone=object(),
    )
    residue = f5_runner._cleanup_residue_snapshot(
        coordinator, sandbox_runtime, lease_id, receipt
    )
    assert len(residue.actors) == 1
    assert len(residue.workspaces) == 1
    assert len(residue.secret_files) == 1
    with pytest.raises(F5TargetFaultsError, match="retained runtime residue"):
        _ProductionObservationProjector._durable_cleanup(
            "residue-case",
            SimpleNamespace(
                response={
                    "state": "closed",
                    "cleanup_disposition": "released",
                }
            ),
            coordinator,
            recovered,
            sandbox_runtime,
        )


def test_cleanup_projector_rejects_post_cleanup_probe_race(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    clean = f5_runner._F5CleanupResidueSnapshot((), (), (), (), (), (), (), (), (), ())
    raced = dataclasses.replace(clean, lease_records=("lease-record:raced",))
    snapshots = iter((clean, raced))
    monkeypatch.setattr(
        f5_runner,
        "_cleanup_residue_snapshot",
        lambda *_args: next(snapshots),
    )
    with pytest.raises(F5TargetFaultsError, match="probe raced"):
        _ProductionObservationProjector._durable_cleanup(
            "race-case",
            SimpleNamespace(
                response={
                    "state": "closed",
                    "cleanup_disposition": "released",
                }
            ),
            SimpleNamespace(primary_lease_id=None),
            SimpleNamespace(
                closed_envelope=SimpleNamespace(
                    cleanup_receipt=None,
                    cleanup_receipt_digest=None,
                    cleanup_required_resources=(),
                ),
                closed_tombstone=object(),
            ),
            SimpleNamespace(_leases={}, lease_root=tmp_path),
        )


def test_projector_preserves_durable_cross_episode_audit() -> None:
    recovered = SimpleNamespace(
        evidence_manifest=SimpleNamespace(
            authority_access_ledger_ref=object(),
            authority_canary_reads=("canary-a",),
            authority_cross_episode_reads=("episode-b",),
        )
    )
    assert _ProductionObservationProjector._durable_authority_audit(
        "case-a", recovered
    ) == (("canary-a",), ("episode-b",))


@pytest.mark.asyncio
async def test_runtime_rejects_reused_or_divergent_immutable_case_authority(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    service, case, repository = await _service_with_real_repository(monkeypatch)
    base = _spec(tmp_path)
    fault = next(
        row for row in _canonical_rows(base.campaign) if row.fault_injection is not None
    )
    authority, target_case, row = _isolated_runtime_authority(
        base,
        fault.row_id,
        episode_id=case.request.episode_id,
        selection_digest=case.resolved.selection_record_ref.sha256,
    )
    service = _authorize_service(service, authority)
    observer = _LifecycleObserver(
        service, repository, case.resolved.selection_record_ref.sha256
    )
    runtime = BreadBoardServiceRuntime(service, observer, service.close, authority)
    await runtime.start()
    assert row.fault_injection is not None
    unknown_identity = row.fault_injection.injection_spec.model_copy(
        update={
            "immutable_ref": (
                "cas://unknown/f5-injection@" + _digest("unknown-injection-ref")
            )
        }
    )
    divergent = row.model_copy(
        update={
            "fault_injection": row.fault_injection.model_copy(
                update={"injection_spec": unknown_identity}
            )
        }
    )
    with pytest.raises(F5TargetFaultsError, match="stale or mismatched"):
        await runtime.execute_case(target_case, divergent)

    await runtime.execute_case(target_case, row)
    with pytest.raises(F5TargetFaultsError, match="stale or mismatched"):
        await runtime.execute_case(target_case, row)
    await runtime.close()
