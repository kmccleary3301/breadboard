from __future__ import annotations

from dataclasses import dataclass
from typing import Any

INFRA_HARDENING_SCHEMA = "bb.rl.phase4.production_infra_hardening.v1"


@dataclass(frozen=True)
class InfraHardeningInputs:
    firecracker_version: str = ""
    firecracker_non_root_boot: bool = False
    firecracker_jailer_available: bool = False
    firecracker_networking_configured: bool = False
    firecracker_concurrent_microvms: int = 0
    gvisor_runsc_version: str = ""
    gvisor_registered_runtime: bool = False
    gvisor_runtime_name: str = ""
    cpu_squeeze_workers: int = 0
    cpu_squeeze_success: bool = False


def evaluate_infra_hardening(inputs: InfraHardeningInputs) -> dict[str, Any]:
    """Evaluate the requested production infra-hardening contract without overclaiming.

    Firecracker is production-hardening evidence only when the probe proves the hard
    parts together: non-root boot, jailer, networking, and more than one concurrent
    microVM. gVisor is production-hardening evidence only when it is registered as a
    container runtime, not merely when the `runsc` binary exists.
    """

    firecracker_blockers: list[str] = []
    if not inputs.firecracker_version:
        firecracker_blockers.append("firecracker_version_missing")
    if not inputs.firecracker_non_root_boot:
        firecracker_blockers.append("firecracker_non_root_boot_missing")
    if not inputs.firecracker_jailer_available:
        firecracker_blockers.append("firecracker_jailer_missing")
    if not inputs.firecracker_networking_configured:
        firecracker_blockers.append("firecracker_networking_missing")
    if inputs.firecracker_concurrent_microvms < 2:
        firecracker_blockers.append("firecracker_concurrency_missing")

    gvisor_blockers: list[str] = []
    if not inputs.gvisor_runsc_version:
        gvisor_blockers.append("gvisor_runsc_version_missing")
    if not inputs.gvisor_registered_runtime:
        gvisor_blockers.append("gvisor_registered_runtime_missing")
    if inputs.gvisor_registered_runtime and not inputs.gvisor_runtime_name:
        gvisor_blockers.append("gvisor_runtime_name_missing")

    cpu_blockers: list[str] = []
    if not inputs.cpu_squeeze_success:
        cpu_blockers.append("cpu_squeeze_missing")
    if inputs.cpu_squeeze_workers < 2:
        cpu_blockers.append("cpu_squeeze_concurrency_missing")

    blockers = firecracker_blockers + gvisor_blockers + cpu_blockers
    return {
        "schema_version": INFRA_HARDENING_SCHEMA,
        "component": "phase4_production_infra_hardening",
        "claim_boundary": "phase4_production_infra_hardening_non_promotional_scope",
        "promotional": False,
        "scorecard_update_allowed": False,
        "inputs": {
            "firecracker_version": inputs.firecracker_version,
            "firecracker_non_root_boot": inputs.firecracker_non_root_boot,
            "firecracker_jailer_available": inputs.firecracker_jailer_available,
            "firecracker_networking_configured": inputs.firecracker_networking_configured,
            "firecracker_concurrent_microvms": inputs.firecracker_concurrent_microvms,
            "gvisor_runsc_version": inputs.gvisor_runsc_version,
            "gvisor_registered_runtime": inputs.gvisor_registered_runtime,
            "gvisor_runtime_name": inputs.gvisor_runtime_name,
            "cpu_squeeze_workers": inputs.cpu_squeeze_workers,
            "cpu_squeeze_success": inputs.cpu_squeeze_success,
        },
        "checks": {
            "firecracker": {"passed": not firecracker_blockers, "blockers": firecracker_blockers},
            "gvisor": {"passed": not gvisor_blockers, "blockers": gvisor_blockers},
            "cpu_squeeze": {"passed": not cpu_blockers, "blockers": cpu_blockers},
        },
        "blockers": blockers,
        "passed": not blockers,
    }
