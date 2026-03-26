# DARWIN Phase-1 Research and Transfer Status

Date: 2026-03-14

## Scope completed

- `lane.research` added as a live DARWIN baseline lane
- research baseline evaluator slice frozen
- research lane added to:
  - bootstrap specs
  - readiness gates
  - topology compatibility
  - live scorecard
  - weekly packet
- typed search extended to four lanes:
  - `lane.harness`
  - `lane.repo_swe`
  - `lane.scheduling`
  - `lane.research`
- multi-cycle promotion history emitted
- first transfer ledger emitted
- first transfer replay audit emitted
- compute-normalized comparison companion emitted
- first comparative dossier draft emitted

## Produced artifacts

- `artifacts/darwin/live_baselines/lane.research/research_baseline.json`
- `artifacts/darwin/search/promotion_history_v1.json`
- `artifacts/darwin/search/transfer_ledger_v1.json`
- `artifacts/darwin/scorecards/compute_normalized_view_v1.json`
- `artifacts/darwin/dossiers/comparative_dossier_v1.json`

## Current read

- DARWIN now has six live lanes
- typed search is operational on four lanes
- multi-cycle promotion is demonstrated on `lane.scheduling`
- the first valid transfer experiment is recorded from `lane.harness` to `lane.research`

## Still deferred

- stronger multi-tranche promotion logic beyond the current archive history
- broader transfer families beyond the first harness-to-research path
- compute-normalized claim-bearing comparative packets
