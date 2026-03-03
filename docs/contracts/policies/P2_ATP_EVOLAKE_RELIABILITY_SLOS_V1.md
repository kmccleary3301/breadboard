# P2 ATP/EvoLake Reliability SLOs (V1)

Date: 2026-02-23

## Scope

Applies to:
- ATP retrieval/decomposition lane
- ATP specialist fallback lane
- EvoLake minimal campaign lane
- Shared benchmark + KPI + evidence bundle lane

## SLO definitions

### SLO-ATP-ALWAYS (hard gate)

- Contract validation report `ok=true`.
- ATP decomposition loop: event + trace validation `true`.
- ATP decomposition loop replay invariants `replay_ok=true`.
- ATP specialist fallback trace validation `ok=true`.

Failure policy: block release and require rollback protocol.

### SLO-ATP-USUALLY (quality gate)

- ATP retrieval subset solve rate `>= 0.70`.
- ATP specialist fallback coverage rate `> 0.0`.
- ATP retrieval falsification signal: `retrieval_falsification_max_delta <= -0.10`.

Failure policy: warning on first breach; block on repeated breach.

### SLO-EVOLAKE-ALWAYS

- EvoLake minimal campaign report generated.
- Checkpoint/resume artifacts generated and validated.

### SLO-SHARED-EVIDENCE

- Benchmark validation report `ok=true`.
- KPI alarm report `ok=true`.
- ATP/EvoLake evidence bundle validation `ok=true`.

## Required artifacts

- `artifacts/atp_capabilities/contract_validation_report.latest.json`
- `artifacts/atp_retrieval_decomposition_v1/atp_retrieval_decomposition_report.latest.json`
- `artifacts/atp_specialist_solver_fallback_matrix_v1/solver_fallback_matrix_report.latest.json`
- `artifacts/benchmarks/atp_retrieval_decomposition_subset_result.latest.json`
- `artifacts/benchmarks/atp_retrieval_falsification_matrix.latest.json`
- `artifacts/benchmarks/benchmark_validation_report_v1.latest.json`
- `artifacts/kpi/p2_kpi_snapshot.latest.json`
- `artifacts/kpi/p2_kpi_alarm_report.latest.json`
