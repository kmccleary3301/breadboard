# ATP Contract Break Checklist (V1)

Use this checklist when changes touch ATP contract schemas or ATP protocol validators.

## Preconditions

- [ ] ACR document added under `docs/contracts/policies/acr/` with valid `ACR-...` ID.
- [ ] ADR document added under `docs/contracts/policies/adr/` with valid `ADR-...` ID.
- [ ] Rollback protocol reviewed:
  - `docs/contracts/policies/ROLLBACK_PROTOCOL_CHECKLIST_V1.md`

## Compatibility checks

- [ ] New schema version introduced (no silent in-place break).
- [ ] Existing fixtures validated against old and new schema behavior.
- [ ] ATP capability validator updated:
  - `scripts/validate_atp_capability_contracts.py`
- [ ] Benchmark validator updated if row contracts changed:
  - `scripts/validate_benchmark_reports_v1.py`

## Evidence checks

- [ ] Contract validation artifact regenerated:
  - `artifacts/atp_capabilities/contract_validation_report.latest.json`
- [ ] KPI + alarm artifacts regenerated and green:
  - `artifacts/kpi/p2_kpi_snapshot.latest.json`
  - `artifacts/kpi/p2_kpi_alarm_report.latest.json`
