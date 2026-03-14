# DARWIN Phase-1 T1 Baseline Layer Status

Date: 2026-03-13

## Scope completed

- baseline topology-family runner scaffold emitted
- structural T1 baseline scorecard emitted for:
  - `lane.atp`
  - `lane.harness`
  - `lane.systems`
- first weekly evidence packet emitted from live bootstrap metadata

## Validation

- `pytest -q tests/test_darwin_contract_pack_v0.py tests/test_bootstrap_darwin_campaign_specs_v0.py tests/test_build_darwin_topology_family_runner_v0.py tests/test_run_darwin_t1_baseline_scorecard_v1.py tests/test_build_darwin_weekly_packet_v1.py`
- `python scripts/build_darwin_topology_family_runner_v0.py --json`
- `python scripts/run_darwin_t1_baseline_scorecard_v1.py --json`
- `python scripts/build_darwin_weekly_packet_v1.py --json`
- `python -m py_compile scripts/build_darwin_topology_family_runner_v0.py scripts/run_darwin_t1_baseline_scorecard_v1.py scripts/build_darwin_weekly_packet_v1.py`

## Produced artifacts

- topology matrix:
  - `artifacts/darwin/topology/topology_family_runner_v0.json`
- baseline scorecard:
  - `artifacts/darwin/scorecards/t1_baseline_scorecard.latest.json`
  - `artifacts/darwin/scorecards/t1_baseline_scorecard.latest.md`
- weekly packet:
  - `artifacts/darwin/weekly/weekly_evidence_packet.latest.json`
  - `artifacts/darwin/weekly/weekly_evidence_packet.latest.md`

## Current readout

- scorecard type: `structural_bootstrap_readiness`
- lane count: `3`
- `overall_ok=true`

## Immediate next step

Move from structural readiness to executable lane baselines:

1. bind live lane-specific evaluators and baseline metrics
2. emit first `EvidenceBundle` and `ClaimRecord` instances from real run data
3. add the first non-ATP comparative scorecard surface
