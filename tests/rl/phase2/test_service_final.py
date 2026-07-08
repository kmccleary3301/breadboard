from __future__ import annotations

from dataclasses import replace

from breadboard.rl.phase2.final_report import (
    EXPECTED_MILESTONE_CLAIM_BOUNDARIES,
    PHASE2_COMPONENT_MILESTONES,
    PHASE2_FINAL_CLAIM_BOUNDARY,
    PHASE2_FINAL_REPORT_ID,
    build_phase2_component_report,
    build_phase2_final_report,
    validate_phase2_final_report,
)
from breadboard.rl.phase2.hardening import (
    ArtifactEgressRequest,
    DestructiveActionRequest,
    EgressPolicy,
    build_hardening_report,
    validate_hardening_report,
)
from breadboard.rl.phase2.observability import (
    ObservabilityCaps,
    ObservabilitySample,
    build_observability_report,
    validate_observability_report,
)
from breadboard.rl.phase2.promotion_audit import (
    build_phase2_promotion_audit,
    validate_phase2_promotion_audit,
)
from breadboard.rl.phase2.service import (
    ArtifactRecord,
    RLRunServiceContract,
    ResourceCaps,
    RunSubmission,
    validate_service_surface_report,
)


def _submission(run_id: str = "phase2-service-run") -> RunSubmission:
    return RunSubmission(
        run_id=run_id,
        tenant_id="tenant-a",
        workspace_id="workspace-a",
        env_package_ref="sha256:env-package",
        target_run_id="20260622T190029Z-slurm-231441",
        requested_tasks=10,
        requested_gpus=2,
        requested_budget_usd=25.0,
        requested_duration_seconds=600,
    )


def test_submit_status_cancel_collect_replay_audit_lifecycle() -> None:
    service = RLRunServiceContract(
        ResourceCaps(
            max_tasks=100,
            max_gpus=8,
            max_budget_usd=100.0,
            max_duration_seconds=3600,
            max_artifact_bytes=4096,
        )
    )

    submitted = service.submit(_submission())
    assert submitted.state == "queued"
    assert submitted.accepted is True
    assert service.status("phase2-service-run").state == "queued"
    rejected = service.submit(replace(_submission("over-cap-run"), requested_tasks=101))
    assert rejected.state == "rejected"
    assert rejected.accepted is False
    assert "requested_tasks exceeds max_tasks" in rejected.reason


    assert service.start("phase2-service-run").state == "running"
    cancel_requested = service.cancel("phase2-service-run", operator_id="operator-a", reason="operator stop")
    assert cancel_requested.state == "cancel_requested"
    assert cancel_requested.cancellation_state == "operator_requested"
    assert service.acknowledge_cancelled("phase2-service-run").state == "cancelled"

    service.add_artifact(
        ArtifactRecord(
            run_id="phase2-service-run",
            artifact_id="replay-1",
            relative_path="workspace-a/artifacts/replay.jsonl",
            sha256="sha256:abc",
            bytes=128,
            egress_allowed=True,
        )
    )
    collected = service.collect("phase2-service-run")
    assert collected["artifacts"][0]["artifact_id"] == "replay-1"
    assert service.replay("phase2-service-run", artifact_id="replay-1")["replay_available"] is True
    assert [event["event_type"] for event in service.stream("phase2-service-run")] == [
        "run_submitted",
        "run_started",
        "cancel_requested",
        "run_cancelled",
        "artifact_recorded",
    ]

    audit = service.audit("phase2-service-run")
    assert validate_service_surface_report(audit) == []
    assert audit["scorecard_update_allowed"] is False
    assert audit["target_run_id"] == "20260622T190029Z-slurm-231441"


def test_observability_report_records_cap_rejection() -> None:
    caps = ObservabilityCaps(
        max_budget_usd=50.0,
        max_gpu_hours=4.0,
        max_queue_wait_seconds=120.0,
        max_verifier_latency_ms=500.0,
        max_failure_rate=0.10,
    )
    report = build_observability_report(
        run_id="phase2-observe-run",
        target_run_id="target-run",
        caps=caps,
        requested_budget_usd=75.0,
        projected_gpu_hours=8.0,
        samples=[
            ObservabilitySample(30.0, 70.0, 10, 5.0, 100.0),
            ObservabilitySample(240.0, 80.0, 5, 5.0, 900.0, failure_class="verifier_timeout"),
        ],
    )

    assert validate_observability_report(report) == []
    assert report["cap_evaluation"]["accepted"] is False
    assert report["failure_taxonomy"] == {"verifier_timeout": 1}
    assert report["task_throughput"]["tasks_per_second"] == 1.5
    assert "requested_budget_usd exceeds max_budget_usd" in report["cap_evaluation"]["rejections"]
    assert "projected_gpu_hours exceeds max_gpu_hours" in report["cap_evaluation"]["rejections"]


def test_hardening_redaction_egress_and_destructive_guards() -> None:
    report = build_hardening_report(
        run_id="phase2-hardening-run",
        target_run_id="target-run",
        tenant_id="tenant-a",
        workspace_id="workspace-a",
        egress_policy=EgressPolicy(allowed_prefixes=("workspace-a/artifacts",), max_artifact_bytes=1024),
        egress_requests=[
            ArtifactEgressRequest("workspace-a/artifacts/public.json", 64, "public"),
            ArtifactEgressRequest("../escape/private.txt", 64, "secret"),
        ],
        destructive_actions=[
            DestructiveActionRequest("safe", "python run.py", "workspace-a/jobs/run.py"),
            DestructiveActionRequest("bad", "rm -rf /", "/"),
        ],
        environment={"API_TOKEN": "secret-token", "CACHE_DIR": "/Users/operator/cache", "SAFE_FLAG": "1"},
        adversarial_package_results=[{"package_id": "path_escape", "passed": False}],
    )

    assert validate_hardening_report(report) == []
    assert report["redacted_environment"]["API_TOKEN"] == "<redacted>"
    assert report["redacted_environment"]["CACHE_DIR"] == "<redacted>"
    assert report["artifact_egress_results"][0]["allowed"] is True
    assert report["artifact_egress_results"][1]["allowed"] is False
    assert report["destructive_action_guards"][0]["allowed"] is True
    assert report["destructive_action_guards"][1]["allowed"] is False
    assert report["hardening_passed"] is False


def _complete_milestone_reports(target_run_id: str) -> dict[str, dict]:
    return {
        milestone_id: build_phase2_component_report(
            milestone_id=milestone_id,
            report_id=f"report-{milestone_id}",
            claim_boundary=EXPECTED_MILESTONE_CLAIM_BOUNDARIES[milestone_id],
            target_run_id=target_run_id,
            passed=True,
        )
        for milestone_id in PHASE2_COMPONENT_MILESTONES
    }


def test_final_report_fails_when_any_milestone_report_missing() -> None:
    target_run_id = "target-run"
    reports = _complete_milestone_reports(target_run_id)
    del reports["P2-M10"]

    final_report = build_phase2_final_report(
        target_run_id=target_run_id,
        milestone_reports=reports,
        command_log_manifest={"target_run_id": target_run_id, "commands": []},
    )

    assert final_report["final_report_ready"] is False
    assert final_report["missing_milestones"] == ["P2-M10"]
    assert "missing_milestones must be empty" in validate_phase2_final_report(final_report)


def test_promotion_audit_ready_only_when_scorecard_claim_ledger_and_final_report_agree() -> None:
    target_run_id = "target-run"
    final_report = build_phase2_final_report(
        target_run_id=target_run_id,
        milestone_reports=_complete_milestone_reports(target_run_id),
        command_log_manifest={"target_run_id": target_run_id, "commands": ["archived"]},
    )
    assert validate_phase2_final_report(final_report) == []

    scorecard = {
        "claim_boundary": PHASE2_FINAL_CLAIM_BOUNDARY,
        "final_report_id": PHASE2_FINAL_REPORT_ID,
        "scorecard_update_allowed": False,
        "target_run_id": target_run_id,
        "total_points": 1000,
    }
    claim_ledger = {
        "allowed_claim_boundary": PHASE2_FINAL_CLAIM_BOUNDARY,
        "final_report_id": PHASE2_FINAL_REPORT_ID,
        "target_run_id": target_run_id,
    }

    ready_audit = build_phase2_promotion_audit(
        target_run_id=target_run_id,
        scorecard=scorecard,
        claim_ledger=claim_ledger,
        final_report=final_report,
    )
    assert ready_audit["promotion_review_ready"] is True
    assert validate_phase2_promotion_audit(ready_audit) == []

    bad_scorecard = dict(scorecard, target_run_id="other-run")
    blocked_audit = build_phase2_promotion_audit(
        target_run_id=target_run_id,
        scorecard=bad_scorecard,
        claim_ledger=claim_ledger,
        final_report=final_report,
    )
    assert blocked_audit["promotion_review_ready"] is False
    assert "scorecard.target_run_id_matches" in blocked_audit["missing_requirements"]
