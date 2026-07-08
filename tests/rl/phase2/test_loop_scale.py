from __future__ import annotations

from breadboard.rl.phase2.benchmark import (
    build_benchmark_slice_report,
    build_fixture_benchmark_source_pin,
    source_sha256,
)
from breadboard.rl.phase2.benchflow import fixture_benchflow_import_report
from breadboard.rl.phase2.closed_loop import build_closed_loop_fixture_ledger
from breadboard.rl.phase2.env_family import build_lean_console_fixture_probe_report
from breadboard.rl.phase2.scale import build_scale_ladder_v2_report, fixture_scale_metrics
from breadboard.rl.phase2.verifier import (
    VerifierCallEvidence,
    build_live_verifier_integration_report,
)


def test_closed_loop_fixture_closes_accepted_and_rejected_replays() -> None:
    ledger = build_closed_loop_fixture_ledger(target_run_id="target-fixture-loop")

    assert ledger["claim_boundary"] == "p2_m3_closed_loop_prototype_not_production_rl_claim"
    assert ledger["scorecard_update_allowed"] is False
    assert ledger["passed"] is True
    assert ledger["target_run_id"] == "target-fixture-loop"
    assert ledger["policy_snapshot"]["policy_snapshot_id"].startswith("policy_snapshot:")

    closures = {closure["replay_status"]: closure for closure in ledger["replay_closures"]}
    assert set(closures) == {"accepted_replay_closed", "rejected_replay_closed"}
    assert closures["accepted_replay_closed"]["admission_accepted"] is True
    assert closures["accepted_replay_closed"]["trainer_handoff_id"] == "trainer_handoff:accepted-only"
    assert closures["accepted_replay_closed"]["errors"] == []
    assert closures["rejected_replay_closed"]["admission_accepted"] is False
    assert closures["rejected_replay_closed"]["trainer_handoff_id"] is None
    assert closures["rejected_replay_closed"]["errors"] == []


def test_scale_ladder_v2_level_gates_and_failure_taxonomy() -> None:
    metrics = fixture_scale_metrics()
    metrics[250] = {
        **metrics[250],
        "scheduler_failures": 1,
    }

    report = build_scale_ladder_v2_report(metrics, target_run_id="target-fixture-scale").to_dict()

    assert report["claim_boundary"] == "p2_m4_scale_ladder_not_arbitrary_production_scale"
    assert report["scorecard_update_allowed"] is False
    assert report["passed"] is False
    assert [level["level"] for level in report["levels"]] == [100, 250, 500, 1000]
    assert report["failure_taxonomy"]["scheduler"] == 1
    assert report["failure_taxonomy"]["verifier"] == 50
    scheduler_250 = [
        gate
        for gate in report["gates"]
        if gate["level"] == 250 and gate["gate"] == "scheduler_resilience"
    ][0]
    assert scheduler_250["passed"] is False
    assert report["overall_passed"] is False


def test_benchmark_slice_rejects_source_hash_mismatch() -> None:
    source_pin = build_fixture_benchmark_source_pin()
    report = build_benchmark_slice_report(
        source_pin,
        observed_source_sha256=source_sha256("tampered fixture\n"),
        contamination_controls=[
            "source_hash_pin",
            "train_overlap_manifest",
            "prompt_solution_leakage_scan",
        ],
        failure_replay_refs=["cas://benchmark/failure-replay/001"],
        metrics={"attempted": 3, "completed": 2, "failed": 1},
        target_run_id="target-fixture-benchmark",
    ).to_dict()

    assert report["status"] == "rejected_hash_mismatch"
    assert report["claim_boundary"] == "p2_m5_named_benchmark_slice_not_general_benchmark_claim"
    assert report["accepted_for_claim"] is False
    assert report["passed"] is False
    assert report["scorecard_update_allowed"] is False
    assert report["source_pin"]["expected_source_sha256"] != report["observed_source_sha256"]
    assert report["errors"] == ["source_hash_mismatch"]


def test_live_verifier_instability_quarantines_report() -> None:
    calls = [
        VerifierCallEvidence(
            call_id="ors-call-1",
            provider="ors",
            endpoint_id="ors.fixture.local",
            verifier_version="openreward-fixture-v1",
            request_hash="a" * 64,
            response_hash="b" * 64,
            reward_scalar=0.70,
            latency_ms=40,
        ),
        VerifierCallEvidence(
            call_id="openreward-call-1",
            provider="openreward",
            endpoint_id="openreward.fixture.local",
            verifier_version="openreward-fixture-v2",
            request_hash="a" * 64,
            response_hash="c" * 64,
            reward_scalar=0.95,
            latency_ms=55,
        ),
    ]

    report = build_live_verifier_integration_report(
        calls,
        baseline_reward=0.70,
        drift_tolerance=0.05,
        target_run_id="target-fixture-verifier",
    ).to_dict()

    assert report["status"] == "quarantined_verifier_instability"
    assert report["claim_boundary"] == "p2_m6_live_verifier_probe_not_general_verifier_claim"
    assert report["quarantined"] is True
    assert report["passed"] is False
    assert report["scorecard_update_allowed"] is False
    assert report["max_abs_drift"] == 0.25
    assert "reward_drift_exceeded" in report["quarantine_reasons"]
    assert "verifier_version_drift" in report["quarantine_reasons"]


def test_benchflow_hardening_import_preserves_and_loses_fields() -> None:
    report = fixture_benchflow_import_report().to_dict()

    assert report["claim_boundary"] == "p2_m7_benchflow_probe_not_full_security_coverage_claim"
    assert report["scorecard_update_allowed"] is False
    assert report["preserved_fields"] == [
        "workspace_isolation",
        "network_egress_block",
        "path_traversal_probe",
    ]
    assert report["lost_fields"] == ["benchflow_harbor_attestation"]
    assert report["field_mapping"]["path_traversal_probe"] == "RewardHackProbeSuite.path_escape"
    assert report["imported_probe_catches_fixture"] is True


def test_second_environment_family_target_smoke_report_schema() -> None:
    report = build_lean_console_fixture_probe_report().to_dict()

    assert report["report_id"] == "bb_zyphra_rl_phase2_second_env_family_v1"
    assert report["family_id"] == "lean_console"
    assert report["env_package_id"] == "lean_console_fixture_env"
    assert report["target_run_id"] == "fixture-phase2-second-env-family"
    assert report["claim_boundary"] == "p2_m8_second_environment_probe_not_general_env_support_claim"
    assert report["scorecard_update_allowed"] is False
    assert report["smoke_ready"] is True
    assert set(report) >= {
        "renderer_probe",
        "replay_probe",
        "export_probe",
        "target_smoke",
        "preserved_fields",
        "lost_fields",
    }
    assert report["target_smoke"] == {
        "name": "target_smoke",
        "status": "passed",
        "evidence_ref": "cas://env-family/target-smoke/lean_console",
    }
