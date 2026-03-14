# DARWIN Phase-1 Live Baseline Status

Date: 2026-03-14

## Scope completed

- DARWIN / EvoLake compatibility posture frozen
- architecture boundary contract frozen
- DARWIN artifact layout convention documented
- live T1 micro baselines emitted for:
  - `lane.atp`
  - `lane.harness`
  - `lane.systems`
- live baseline artifacts normalized into:
  - `CandidateArtifact`
  - `EvaluationRecord`
- first DARWIN evidence bundle emitted
- first DARWIN claim records emitted
- first claim ledger emitted
- lane readiness gates emitted
- bootstrap rollup emitted
- end-to-end T1 smoke runner added

## Produced artifacts

- live baseline summary:
  - `artifacts/darwin/live_baselines/live_baseline_summary_v1.json`
- evaluation records:
  - `artifacts/darwin/evaluations/lane.atp.baseline_evaluation_v1.json`
  - `artifacts/darwin/evaluations/lane.harness.baseline_evaluation_v1.json`
  - `artifacts/darwin/evaluations/lane.systems.baseline_evaluation_v1.json`
- scorecard:
  - `artifacts/darwin/scorecards/t1_baseline_scorecard.latest.json`
- weekly packet:
  - `artifacts/darwin/weekly/weekly_evidence_packet.latest.json`
- evidence:
  - `artifacts/darwin/evidence/darwin_phase1_t1_live_baselines_bundle_v1.json`
  - `artifacts/darwin/evidence/evidence_bundle_manifest_v2.json`
- claims:
  - `artifacts/darwin/claims/claim_ledger_v1.json`
- readiness:
  - `artifacts/darwin/readiness/lane_readiness_v0.json`
- rollup:
  - `artifacts/darwin/bootstrap/bootstrap_rollup_v0.json`

## Current read

- bootstrap spec count: `3`
- topology family matrix count: `9`
- live lane count: `3`
- scorecard `overall_ok=true`
- readiness `overall_ok=true`

This is the first point where DARWIN is no longer only a contract pack. ATP, harness, and systems now emit shared DARWIN control/evaluation artifacts on a common baseline flow.

## What is still structural

- the ATP lane baseline is still a lightweight ops-digest wrapper, not a real benchmark-level DARWIN baseline
- harness and systems baselines are still micro evaluators, not richer lane-native comparative baselines
- no repo-scale SWE lane baseline yet
- no scheduling lane baseline yet
- no transfer experiments, mutation library, or lineage/archive manager yet

## Next sequencing decision

The next non-ATP lane to launch should be:

1. `lane.repo_swe`
2. `lane.scheduling`
3. `lane.research`

Rationale:

- repo-scale SWE is the best next test of cross-domain transfer and real evaluator richness
- scheduling stays valuable but can follow once repo-scale baseline conventions are proven
- research/evidence synthesis remains highest governance risk and should stay after the more objective lanes
