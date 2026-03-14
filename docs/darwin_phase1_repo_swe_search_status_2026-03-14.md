# DARWIN Phase-1 Repo-SWE, Scheduling, Research, and Typed Search Status

Date: 2026-03-14

## Scope completed

- `lane.repo_swe` added as a live DARWIN baseline lane
- `lane.scheduling` added as a live DARWIN baseline lane
- `lane.research` added as a live DARWIN baseline lane
- repo_swe baseline evaluator slice frozen
- scheduling baseline evaluator slice frozen
- research baseline evaluator slice frozen
- typed mutation registry emitted
- first search-enabled lane selection frozen
- first promotion-capable typed-search cycles executed on:
  - `lane.harness`
  - `lane.repo_swe`
  - `lane.scheduling`
  - `lane.research`
- archive snapshot emitted
- promotion decision records emitted
- replay audit emitted for promoted scheduling and research candidates
- promotion history emitted
- transfer ledger emitted
- invalid comparison ledger emitted
- second internal DARWIN evidence bundle emitted for typed search

## Produced artifacts

- `artifacts/darwin/live_baselines/live_baseline_summary_v1.json`
- `artifacts/darwin/search/mutation_operator_registry_v1.json`
- `artifacts/darwin/search/search_enabled_lane_selection_v1.json`
- `artifacts/darwin/search/search_smoke_summary_v1.json`
- `artifacts/darwin/search/archive_snapshot_v1.json`
- `artifacts/darwin/search/promotion_history_v1.json`
- `artifacts/darwin/search/transfer_ledger_v1.json`
- `artifacts/darwin/search/invalid_comparison_ledger_v1.json`
- `artifacts/darwin/evidence/darwin_phase1_t2_search_bundle_v1.json`

## Current read

- DARWIN now has six live lanes:
  - ATP
  - harness
  - systems
  - repo_swe
  - scheduling
  - research
- typed search is operational on four lanes
- weekly packet and scorecard can now consume comparative search outputs
- first comparative dossier draft is emitted

## Still deferred

- broader transfer families
- stronger multi-tranche promotion logic
- final Phase-1 evidence packet
