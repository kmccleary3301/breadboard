# DARWIN Phase-1 Repo-SWE, Scheduling, and Typed Search Status

Date: 2026-03-14

## Scope completed

- `lane.repo_swe` added as a live DARWIN baseline lane
- `lane.scheduling` added as a live DARWIN baseline lane
- repo_swe baseline evaluator slice frozen
- scheduling baseline evaluator slice frozen
- typed mutation registry emitted
- first search-enabled lane selection frozen
- first promotion-capable typed-search cycles executed on:
  - `lane.harness`
  - `lane.repo_swe`
  - `lane.scheduling`
- archive snapshot emitted
- promotion decision records emitted
- replay audit emitted for the promoted scheduling candidate
- invalid comparison ledger emitted
- second internal DARWIN evidence bundle emitted for typed search

## Produced artifacts

- `artifacts/darwin/live_baselines/live_baseline_summary_v1.json`
- `artifacts/darwin/search/mutation_operator_registry_v1.json`
- `artifacts/darwin/search/search_enabled_lane_selection_v1.json`
- `artifacts/darwin/search/search_smoke_summary_v1.json`
- `artifacts/darwin/search/archive_snapshot_v1.json`
- `artifacts/darwin/search/invalid_comparison_ledger_v1.json`
- `artifacts/darwin/evidence/darwin_phase1_t2_search_bundle_v1.json`

## Current read

- DARWIN now has five live lanes:
  - ATP
  - harness
  - systems
  - repo_swe
  - scheduling
- typed search is operational on three lanes
- weekly packet and scorecard can now consume comparative search outputs

## Still deferred

- research live baseline
- cross-lane transfer
- lineage promotion logic beyond single-tranche archive updates
