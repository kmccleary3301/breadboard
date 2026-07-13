from __future__ import annotations

from breadboard.rl.adapters.probe import AdapterProbeReport


def build_benchflow_fixture_probe_report() -> AdapterProbeReport:
    return AdapterProbeReport(
        adapter_id="benchflow.fixture.v1",
        adapter_kind="benchflow_import_fixture",
        support_level="fixture_probe",
        workload_family="swe",
        preserved_fields=[
            "env_package_id",
            "task_id",
            "runtime_backend",
            "hardening_policy",
            "verifier_command_shape",
        ],
        field_mapping={
            "env_package_id": "EnvPackage.metadata.id",
            "task_id": "EnvPackage.tasks[].id",
            "runtime_backend": "EnvPackage.runtime.backend",
            "hardening_policy": "EnvPackage.security.hardening_policy",
            "verifier_command_shape": "EnvPackage.evaluation.verifier.command",
        },
        lost_fields=[
            "real_benchflow_harbor_runtime",
            "live_benchflow_sandbox_attestation",
        ],
        unsupported_fields=["production_benchflow_execution"],
        source_artifacts=["examples/rl_env_packages/swe_toy_patch/env_package.yaml"],
        fidelity_notes=[
            "This probe checks whether BreadBoard EnvPackage fields can be represented in a BenchFlow-shaped fixture.",
            "It does not invoke Harbor or BenchFlow sandbox execution, so sandbox attestation remains intentionally absent.",
        ],
        promotion_requirements=[
            "Run an actual BenchFlow/Harbor task using this mapping and capture sandbox identity plus verifier evidence.",
            "Compare BreadBoard replay/admission results against BenchFlow task completion semantics on at least one real SWE task.",
        ],
        metadata={
            "fixture_scope": "static_env_package_mapping",
            "minimum_real_promotion_level": "live_probe",
        },
    )
