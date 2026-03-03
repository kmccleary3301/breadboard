# ADR-20260225-atp-tier2-contract-governance

- `adr_id`: `ADR-20260225-atp-tier2-contract-governance`
- `status`: accepted
- `owners`: [codex]
- `date`: 2026-02-25
- `related_acr`: `ACR-20260223-generalization-substrate-v1`

## Context

ATP contract schemas, capability validators, and Tier-2 benchmark/evidence scripts were expanded to support multi-seed decomposition, falsification, specialist fallback checks, and claim-linked evidence packets. Governance policy requires an ADR alongside the ACR when ATP contract surfaces are changed.

## Decision

Adopt the ATP Tier-2 contract additions as additive surfaces under existing generalization constraints:

- keep contract changes additive (`*_v1` artifacts and validators),
- preserve cross-harness generalization boundaries (no harness-specific narrowing in core contracts),
- require claim-linked evidence packet generation and scaffold validation for Tier-2 readiness.

No kernel danger-zone runtime behavior is changed by this ADR.

## Alternatives Considered

- Delay ATP Tier-2 contract surfaces until all parity fixtures are restored: rejected; blocks evidence governance progress without reducing risk.
- Introduce breaking schema version bumps now: rejected; current additions are compatible and do not require a migration break.

## Consequences

- Benefits: explicit governance traceability for ATP contract evolution, CI-enforceable policy compliance, clearer Tier-2 evidence readiness.
- Risks: contract surface area increases review burden and maintenance overhead.
- Rollback trigger: repeated governance check instability or evidence scaffold incompatibility across two consecutive validation passes.

## Validation Plan

- `python scripts/check_atp_contract_governance.py --changed-files-file artifacts/governance/changed_files.latest.txt --json`
- `python scripts/validate_atp_capability_contracts.py --json`
- `python scripts/validate_benchmark_reports_v1.py --require-claim-linkage --json`
- Evidence paths:
  - `artifacts/evidence_packets/claim.atp.retrieval.multiseed.tier2.v1/`
  - `artifacts/benchmarks/atp_retrieval_multiseed_summary_20260225.latest.json`

## Follow-up

- Continue parity fixture recovery and strict replay validation.
- Keep ATP Tier-2 claims in `artifacts/benchmarks/claim_ledger_v1.p3.latest.json`.
- Reassess schema versioning only if a non-additive ATP contract change is required.
