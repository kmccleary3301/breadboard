# DARWIN Artifact Layout V0

Date: 2026-03-14

All Phase-1 DARWIN artifacts live under `artifacts/darwin/`.

## Canonical directories

- `bootstrap/`
  - campaign spec manifest
  - bootstrap rollups
- `topology/`
  - topology-family runner manifests
- `live_baselines/`
  - per-lane raw command outputs
  - normalized live-baseline summary
- `candidates/`
  - baseline or evolved `CandidateArtifact` records
- `evaluations/`
  - `EvaluationRecord` rows or JSON artifacts
- `scorecards/`
  - DARWIN scorecard JSON/Markdown
- `weekly/`
  - weekly evidence packets
- `evidence/`
  - DARWIN `EvidenceBundle` payloads
  - compatible evidence manifests where needed
- `claims/`
  - DARWIN `ClaimRecord`s
  - claim ledger rollups
- `readiness/`
  - lane readiness gate outputs

## Artifact naming

- Use `.latest` only for current operational views.
- Use versioned filenames for historical or claim-bearing outputs.
- Avoid lane-specific directory structures that break cross-lane tooling.
