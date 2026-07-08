from __future__ import annotations

from breadboard.rl.phase4.infra_hardening import InfraHardeningInputs, evaluate_infra_hardening


def test_infra_hardening_does_not_count_runsc_binary_as_registered_runtime() -> None:
    report = evaluate_infra_hardening(
        InfraHardeningInputs(
            firecracker_version="Firecracker v1.16.1",
            firecracker_non_root_boot=True,
            firecracker_jailer_available=True,
            firecracker_networking_configured=True,
            firecracker_concurrent_microvms=2,
            gvisor_runsc_version="runsc version release-20260622",
            gvisor_registered_runtime=False,
            cpu_squeeze_workers=48,
            cpu_squeeze_success=True,
        )
    )

    assert report["passed"] is False
    assert report["checks"]["firecracker"]["passed"] is True
    assert report["checks"]["gvisor"]["passed"] is False
    assert "gvisor_registered_runtime_missing" in report["blockers"]
    assert report["scorecard_update_allowed"] is False


def test_infra_hardening_requires_firecracker_rootless_jailer_networking_and_concurrency() -> None:
    report = evaluate_infra_hardening(
        InfraHardeningInputs(
            firecracker_version="Firecracker v1.16.1",
            firecracker_non_root_boot=False,
            firecracker_jailer_available=False,
            firecracker_networking_configured=False,
            firecracker_concurrent_microvms=1,
            gvisor_runsc_version="runsc version release-20260622",
            gvisor_registered_runtime=True,
            gvisor_runtime_name="runsc",
            cpu_squeeze_workers=48,
            cpu_squeeze_success=True,
        )
    )

    assert report["passed"] is False
    assert report["checks"]["gvisor"]["passed"] is True
    assert report["checks"]["firecracker"]["blockers"] == [
        "firecracker_non_root_boot_missing",
        "firecracker_jailer_missing",
        "firecracker_networking_missing",
        "firecracker_concurrency_missing",
    ]


def test_infra_hardening_passes_only_when_all_production_hardening_checks_pass() -> None:
    report = evaluate_infra_hardening(
        InfraHardeningInputs(
            firecracker_version="Firecracker v1.16.1",
            firecracker_non_root_boot=True,
            firecracker_jailer_available=True,
            firecracker_networking_configured=True,
            firecracker_concurrent_microvms=3,
            gvisor_runsc_version="runsc version release-20260622",
            gvisor_registered_runtime=True,
            gvisor_runtime_name="runsc",
            cpu_squeeze_workers=48,
            cpu_squeeze_success=True,
        )
    )

    assert report["passed"] is True
    assert report["blockers"] == []
    assert report["promotional"] is False
    assert report["claim_boundary"] == "phase4_production_infra_hardening_non_promotional_scope"
